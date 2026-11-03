"""Tests for media_studio.cutout: background removal and signature extraction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from media_studio.cutout import (
    _REMBG_NAMES,
    MODELS,
    CutoutError,
    _destination,
    _dilate,
    _infer,
    _otsu_threshold,
    extract_signature,
    remove_background,
)

#: rembg inference needs a cached model; skip those tests when it is absent.
U2NET_CACHE = Path.home() / ".rembg" / "models" / "u2net" / "u2net.onnx"

try:  # rembg pulls in onnxruntime; the venv has it, bare environments may not.
    import rembg  # noqa: F401

    HAS_REMBG = True
except ImportError:
    HAS_REMBG = False

NEEDS_U2NET = pytest.mark.skipif(
    not (HAS_REMBG and U2NET_CACHE.is_file()), reason="u2net model not cached"
)


def _white_canvas(size: tuple[int, int] = (300, 200)) -> Image.Image:
    return Image.new("RGB", size, (255, 255, 255))


def _draw_stroke(image: Image.Image, colour: tuple[int, int, int]) -> None:
    """Draw a zigzag stroke, standing in for a scanned signature."""

    draw = ImageDraw.Draw(image)
    points = [(30 + i * 20, 100 + (35 if i % 2 else -35)) for i in range(11)]
    draw.line(points, fill=colour, width=7, joint="curve")


def _bimodal_image(path: Path, ink: int, paper: int) -> Path:
    """A scan whose pixels take only two gray values: ink and paper."""

    array = np.full((60, 80), paper, dtype=np.uint8)
    array[20:40, 20:60] = ink
    Image.fromarray(array, "L").convert("RGB").save(path)
    return path


class TestOtsuThreshold:
    def test_separates_a_bimodal_histogram(self) -> None:
        gray = np.full((50, 50), 220, dtype=np.int16)
        gray[10:30, 10:30] = 40
        cutoff = _otsu_threshold(gray)
        assert 40 <= cutoff < 220

    def test_lands_exactly_on_the_ink_value(self) -> None:
        # When the darker class sits exactly at the cutoff, Otsu's first
        # maximum is that value; ink counting must be <=, not <.
        gray = np.full((50, 50), 219, dtype=np.int16)
        gray[10:30, 10:30] = 36
        assert _otsu_threshold(gray) == 36

    def test_flat_image_returns_midpoint(self) -> None:
        gray = np.full((10, 10), 128, dtype=np.int16)
        assert _otsu_threshold(gray) == 127


class TestDilate:
    def test_one_pixel_grows_into_a_plus(self) -> None:
        mask = np.zeros((5, 5), dtype=bool)
        mask[2, 2] = True
        wide = _dilate(mask)
        assert wide.sum() == 5
        assert wide[1, 2] and wide[3, 2] and wide[2, 1] and wide[2, 3]

    def test_empty_stays_empty(self) -> None:
        assert not _dilate(np.zeros((4, 4), dtype=bool)).any()


class TestExtractSignature:
    def test_lifts_blue_ink_onto_transparency(self, tmp_path: Path) -> None:
        source = tmp_path / "ttd.png"
        canvas = _white_canvas()
        _draw_stroke(canvas, (20, 20, 160))
        canvas.save(source)

        result = extract_signature(source)

        out = Image.open(result.destination)
        assert out.mode == "RGBA"
        channels = np.asarray(out)
        transparent = (channels[..., 3] == 0).mean()
        opaque = (channels[..., 3] == 255).mean()
        assert transparent > 0.5, "paper must become transparent"
        assert opaque > 0.0, "the stroke must survive"

        # The ink colour is preserved: some opaque pixel is still blue.
        solid = channels[channels[..., 3] == 255]
        assert ((solid[:, 2] > solid[:, 0]) & (solid[:, 2] > solid[:, 1])).any()

    def test_counts_ink_at_the_exact_otsu_cutoff(self, tmp_path: Path) -> None:
        # Regression: ink == cutoff was dropped by a strict < comparison.
        source = _bimodal_image(tmp_path / "scan.png", ink=36, paper=219)
        result = extract_signature(source)
        assert result.threshold == 36
        assert result.ink_pixels > 0

    def test_manual_threshold_is_respected(self, tmp_path: Path) -> None:
        source = tmp_path / "ttd.png"
        canvas = _white_canvas()
        _draw_stroke(canvas, (20, 20, 160))
        canvas.save(source)

        result = extract_signature(source, threshold=200)
        assert result.threshold == 200
        assert result.ink_pixels > 0

    def test_threshold_outside_range_raises(self, tmp_path: Path) -> None:
        source = _bimodal_image(tmp_path / "scan.png", ink=36, paper=219)
        with pytest.raises(CutoutError, match="threshold must be 0-255"):
            extract_signature(source, threshold=300)

    def test_threshold_that_finds_no_ink_raises(self, tmp_path: Path) -> None:
        source = _bimodal_image(tmp_path / "scan.png", ink=36, paper=219)
        with pytest.raises(CutoutError, match="no ink found"):
            extract_signature(source, threshold=0)

    def test_flat_image_raises(self, tmp_path: Path) -> None:
        source = tmp_path / "blank.png"
        _white_canvas().save(source)
        with pytest.raises(CutoutError, match="flat image"):
            extract_signature(source)

    def test_crop_trims_to_the_ink(self, tmp_path: Path) -> None:
        source = tmp_path / "ttd.png"
        canvas = _white_canvas()
        _draw_stroke(canvas, (20, 20, 160))
        canvas.save(source)

        cropped = extract_signature(source)
        assert cropped.cropped is True
        assert Image.open(cropped.destination).size < canvas.size

    def test_no_crop_keeps_the_full_size(self, tmp_path: Path) -> None:
        source = tmp_path / "ttd.png"
        canvas = _white_canvas()
        _draw_stroke(canvas, (20, 20, 160))
        canvas.save(source)

        result = extract_signature(source, crop=False)
        assert result.cropped is False
        assert Image.open(result.destination).size == canvas.size

    def test_default_destination_sits_beside_the_source(self, tmp_path: Path) -> None:
        source = tmp_path / "sign here.png"
        canvas = _white_canvas()
        _draw_stroke(canvas, (20, 20, 160))
        canvas.save(source)

        result = extract_signature(source)
        assert result.destination == tmp_path / "sign here.signature.png"

    def test_unreadable_file_raises(self, tmp_path: Path) -> None:
        source = tmp_path / "broken.png"
        source.write_bytes(b"not an image")
        with pytest.raises(CutoutError, match="cannot read image"):
            extract_signature(source)


class TestModelNames:
    def test_every_friendly_model_has_a_rembg_name(self) -> None:
        assert set(_REMBG_NAMES) == set(MODELS)

    def test_rembg_names_match_rembg_session_registry(self) -> None:
        # Regression: new_session("human_seg") raised "No session class found";
        # rembg's registry uses underscores and isnet-/birefnet- prefixes.
        assert _REMBG_NAMES["human_seg"] == "u2net_human_seg"
        assert _REMBG_NAMES["anime"] == "isnet-anime"
        assert _REMBG_NAMES["general-use"] == "isnet-general-use"
        assert _REMBG_NAMES["birefnet-lite"] == "birefnet-general-lite"


class TestDestination:
    def test_none_lands_beside_source(self) -> None:
        source = Path("/somewhere/photo.jpg")
        assert _destination(source, None, "nobg") == Path("/somewhere/photo.nobg.png")

    def test_directory_out_gets_the_default_name(self, tmp_path: Path) -> None:
        # Regression: a directory --out used to be passed to PIL as the file
        # path and died with "unknown file extension".
        target = _destination(Path("/x/photo.jpg"), tmp_path, "nobg")
        assert target == tmp_path / "photo.nobg.png"

    def test_explicit_file_is_kept(self, tmp_path: Path) -> None:
        target = tmp_path / "custom.png"
        assert _destination(Path("/x/photo.jpg"), target, "nobg") is target


class TestRemoveBackgroundValidation:
    def test_unknown_model_raises_with_choices(self, tmp_path: Path) -> None:
        source = tmp_path / "photo.png"
        _white_canvas((32, 32)).save(source)
        with pytest.raises(CutoutError, match="unknown cutout model 'nope'"):
            remove_background(source, model="nope")

    def test_select_point_outside_image_raises(self, tmp_path: Path) -> None:
        source = tmp_path / "photo.png"
        _white_canvas((64, 32)).save(source)
        with pytest.raises(CutoutError, match="outside the 64x32 image"):
            remove_background(source, select_point=(100, 10))
        with pytest.raises(CutoutError, match="outside"):
            remove_background(source, select_point=(-1, 5))

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(CutoutError, match="cannot read image"):
            remove_background(tmp_path / "ghost.png")


@NEEDS_U2NET
class TestRemoveBackgroundInference:
    def test_writes_an_rgba_cutout(self, tmp_path: Path) -> None:
        source = tmp_path / "subject.png"
        canvas = _white_canvas((128, 128))
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((34, 34, 94, 94), fill=(200, 30, 30))
        canvas.save(source)

        result = remove_background(source, tmp_path / "cut.png", model="u2net")

        out = Image.open(result.destination)
        assert out.mode == "RGBA"
        assert out.size == canvas.size
        assert result.model == "u2net"
        assert result.selective is False


class _FailingFactory:
    """Remembers session-creation calls so tests can assert the retry."""

    def __init__(self) -> None:
        self.calls: list[dict | None] = []


class TestInferFallback:
    """_infer retries on CPU when the accelerator reports an out-of-memory."""

    @staticmethod
    def _factory(recorder: _FailingFactory) -> Callable[..., object]:
        def new_session(_name: str, providers: list[str] | None = None) -> object:
            recorder.calls.append(providers)
            return object()

        return new_session

    @staticmethod
    def _remover(outcomes: list[object]) -> Callable[..., object]:
        def remove(_image: Image.Image, session: object, **_kwargs: object) -> object:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return remove

    def test_returns_gpu_when_default_providers_work(self) -> None:
        recorder = _FailingFactory()
        image = _white_canvas((8, 8))
        result, device = _infer(
            image,
            "u2net",
            {},
            new_session_fn=self._factory(recorder),
            remove_fn=self._remover([image]),
        )
        assert device == "gpu"
        assert result is image
        assert recorder.calls == [None]

    def test_oom_retries_with_explicit_cpu(self) -> None:
        recorder = _FailingFactory()
        image = _white_canvas((8, 8))
        oom = RuntimeError(
            "ONNXRuntimeError : FAIL : Failed to allocate memory for requested buffer"
        )
        result, device = _infer(
            image,
            "birefnet-general-lite",
            {},
            new_session_fn=self._factory(recorder),
            remove_fn=self._remover([oom, image]),
        )
        assert device == "cpu"
        assert result is image
        assert recorder.calls == [None, ["CPUExecutionProvider"]]

    def test_non_oom_errors_are_not_retried(self) -> None:
        recorder = _FailingFactory()
        with pytest.raises(RuntimeError, match="bad model"):
            _infer(
                _white_canvas((8, 8)),
                "u2net",
                {},
                new_session_fn=self._factory(recorder),
                remove_fn=self._remover([RuntimeError("bad model")]),
            )
        assert recorder.calls == [None]

    def test_cpu_retry_failure_propagates(self) -> None:
        recorder = _FailingFactory()
        oom = RuntimeError("ONNXRuntimeError : FAIL : Failed to allocate memory")
        with pytest.raises(RuntimeError, match="still out of memory"):
            _infer(
                _white_canvas((8, 8)),
                "birefnet-general-lite",
                {},
                new_session_fn=self._factory(recorder),
                remove_fn=self._remover([oom, RuntimeError("still out of memory")]),
            )
        assert recorder.calls == [None, ["CPUExecutionProvider"]]
