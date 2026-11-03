"""Tests for media_studio.video_cutout: SAM 2 video background removal.

The unit tests cover everything that runs without the model: checkpoint
planning, ffmpeg command construction, mask bit-packing and destination
resolution. Two guarded test groups use real footage: one exercises the
validation path (ffmpeg extraction only), and one runs the full SAM 2
pipeline when the tiny checkpoint is cached.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from media_studio.video_cutout import (
    _CHECKPOINT_URLS,
    _MODEL_SPECS,
    VIDEO_CUTOUT_MODELS,
    VideoCutoutError,
    _encode_command,
    _extract_command,
    _pack_mask,
    _unpack_mask,
    _video_destination,
    ensure_checkpoint,
    remove_background_video,
)


def _make_clip(path: Path, *, size: str = "320x240", duration: int = 1) -> Path:
    """Render a tiny real clip so validation tests exercise real extraction."""

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate=30:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


class _FakeTensor:
    """Mimics the torch tensor surface _pack_mask needs: index, gt, cpu, numpy."""

    def __init__(self, values: np.ndarray) -> None:
        self._values = values

    def __getitem__(self, index: int) -> _FakeTensor:
        return self

    def __gt__(self, other: float) -> _FakeTensor:
        return _FakeTensor(self._values > other)

    def cpu(self) -> _FakeTensor:
        return self

    def numpy(self) -> np.ndarray:
        return self._values


class TestCheckpointSpecs:
    def test_every_public_model_has_a_spec(self) -> None:
        assert set(_MODEL_SPECS) == set(VIDEO_CUTOUT_MODELS)

    def test_urls_point_at_the_official_release(self) -> None:
        for file, url in _CHECKPOINT_URLS.items():
            assert url.startswith("https://dl.fbaipublicfiles.com/segment_anything_2/")
            assert file in url

    def test_configs_target_sam21_hydra_files(self) -> None:
        for _, config in _MODEL_SPECS.values():
            assert config.startswith("configs/sam2.1/sam2.1_hiera_")

    def test_unknown_model_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VideoCutoutError, match="unknown video cutout model"):
            ensure_checkpoint("nope", download=False, cache_dir=tmp_path)

    def test_missing_checkpoint_without_download_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VideoCutoutError, match="checkpoint not cached"):
            ensure_checkpoint("sam2.1-tiny", download=False, cache_dir=tmp_path)

    def test_download_renames_the_partial_file(self, tmp_path: Path, monkeypatch) -> None:
        def fake_retrieve(url: str, target: Path) -> None:
            assert "dl.fbaipublicfiles.com" in url
            target.write_bytes(b"ckpt")

        monkeypatch.setattr("urllib.request.urlretrieve", fake_retrieve)
        path = ensure_checkpoint("sam2.1-tiny", cache_dir=tmp_path)

        assert path == tmp_path / "sam2.1_hiera_tiny.pt"
        assert path.read_bytes() == b"ckpt"
        assert not path.with_suffix(path.suffix + ".part").exists()

    def test_download_failure_cleans_the_partial_file(self, tmp_path: Path, monkeypatch) -> None:
        def failing_retrieve(url: str, target: Path) -> None:
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlretrieve", failing_retrieve)
        with pytest.raises(VideoCutoutError, match="cannot download"):
            ensure_checkpoint("sam2.1-tiny", cache_dir=tmp_path)

        assert not (tmp_path / "sam2.1_hiera_tiny.pt.part").exists()


class TestCommands:
    def test_extract_dumps_numbered_jpegs(self) -> None:
        argv = _extract_command(Path("/in/clip.mp4"), Path("/tmp/frames"))
        assert argv[0] == "ffmpeg"
        assert argv[argv.index("-start_number") + 1] == "0"
        assert argv[argv.index("-qscale:v") + 1] == "2"
        assert argv[-1].endswith("%06d.jpg")

    def test_encode_targets_prores_4444_with_alpha(self) -> None:
        argv = _encode_command(320, 240, 30.0, Path("/out/x.mov"))
        assert argv[argv.index("-f") + 1] == "rawvideo"
        assert argv[argv.index("-pix_fmt") + 1] == "rgba"
        assert argv[argv.index("-s") + 1] == "320x240"
        assert argv[argv.index("-c:v") + 1] == "prores_ks"
        assert argv[argv.index("-profile:v") + 1] == "4444"
        assert argv[-1] == "/out/x.mov"

    def test_encode_reports_frame_rate_compactly(self) -> None:
        argv = _encode_command(320, 240, 29.97, Path("/out/x.mov"))
        assert argv[argv.index("-r") + 1] == "29.97"


class TestMaskPacking:
    def test_round_trip_preserves_the_mask(self) -> None:
        height, width = 12, 40
        original = np.zeros((height, width), dtype=bool)
        original[3:9, 5:35] = True
        original[0, 0] = True

        packed = np.packbits(original.reshape(-1))
        restored = np.unpackbits(packed, count=width * height).reshape(height, width)
        assert np.array_equal(original, restored)

    def test_pack_mask_thresholds_logits_above_zero(self) -> None:
        logits = np.array([[[-1.0, 0.5, 2.0], [0.0, -0.1, 9.0]]], dtype=np.float32)
        packed = _pack_mask(_FakeTensor(logits))
        restored = _unpack_mask(packed, 3, 2)
        expected = np.array([[False, True, True], [False, False, True]])
        assert np.array_equal(restored, expected)

    def test_pack_mask_output_is_byte_sized(self) -> None:
        logits = np.zeros((1, 8, 8), dtype=np.float32)
        packed = _pack_mask(_FakeTensor(logits))
        assert packed.dtype == np.uint8
        assert packed.size == (8 * 8 + 7) // 8


class TestVideoDestination:
    def test_none_defaults_beside_the_source(self) -> None:
        source = Path("/in/clip.mp4")
        assert _video_destination(source, None) == Path("/in/clip.nobg.mov")

    def test_existing_folder_receives_a_default_name(self, tmp_path: Path) -> None:
        source = Path("/in/clip.mp4")
        assert _video_destination(source, tmp_path) == tmp_path / "clip.nobg.mov"

    def test_explicit_file_is_kept(self, tmp_path: Path) -> None:
        target = tmp_path / "custom.mov"
        assert _video_destination(Path("/in/clip.mp4"), target) == target


class TestValidation:
    def test_missing_source_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VideoCutoutError, match="no such file"):
            remove_background_video(
                tmp_path / "ghost.mp4", select_point=(10, 10), cache_dir=tmp_path
            )

    def test_point_outside_the_frame_raises(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        with pytest.raises(VideoCutoutError, match="outside the 320x240 frame"):
            remove_background_video(clip, select_point=(999, 10), cache_dir=tmp_path)

    def test_negative_point_raises(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        with pytest.raises(VideoCutoutError, match="outside the 320x240 frame"):
            remove_background_video(clip, select_point=(-1, 5), cache_dir=tmp_path)

    def test_anchor_beyond_the_clip_raises(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        with pytest.raises(VideoCutoutError, match="anchor frame 500 is outside"):
            remove_background_video(
                clip, select_point=(10, 10), anchor_frame=500, cache_dir=tmp_path
            )


def _ffprobe_video(path: Path) -> tuple[str, str]:
    """Return (codec_name, pix_fmt) of the first video stream."""

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    codec, _, pix_fmt = result.stdout.strip().partition(",")
    return codec, pix_fmt


try:  # pragma: no cover - environment probe
    import torch  # noqa: F401
    from sam2.build_sam import build_sam2_video_predictor  # noqa: F401

    HAS_SAM2 = True
except ImportError:
    HAS_SAM2 = False

TINY_CACHE = Path.home() / ".cache" / "media-studio" / "sam2" / "sam2.1_hiera_tiny.pt"
NEEDS_SAM2_TINY = pytest.mark.skipif(
    not (HAS_SAM2 and TINY_CACHE.is_file()), reason="SAM 2 tiny checkpoint not cached"
)


class TestEndToEnd:
    @NEEDS_SAM2_TINY
    def test_full_pipeline_writes_transparent_prores(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "e2e.mp4")
        result = remove_background_video(
            clip, select_point=(160, 120), cache_dir=Path.home() / ".cache/media-studio/sam2"
        )

        assert result.destination.name == "e2e.nobg.mov"
        assert result.destination.is_file()
        assert result.size_bytes > 0
        assert result.frames == 30
        assert result.select_point == (160, 120)

        codec, pix_fmt = _ffprobe_video(result.destination)
        assert codec == "prores"
        # 4444 keeps alpha; this ffmpeg build promotes the pixel format to
        # 12-bit, so accept any yuva444p depth rather than pinning one.
        assert pix_fmt.startswith("yuva444p")
