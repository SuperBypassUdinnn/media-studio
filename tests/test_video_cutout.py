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
    _chunk_frames,
    _chunk_ranges,
    _encode_command,
    _extract_command,
    _feather_alpha,
    _frame_name,
    _mask_centroid,
    _MaskStore,
    _pack_mask,
    _propagate_masks,
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


class TestFeather:
    def test_zero_radius_keeps_binary_mask(self) -> None:
        mask = np.zeros((8, 10), dtype=bool)
        mask[2:6, 3:7] = True
        alpha = _feather_alpha(mask, 0)
        assert alpha.dtype == np.uint8
        assert set(np.unique(alpha)) == {0, 255}

    def test_positive_radius_softens_edges(self) -> None:
        mask = np.zeros((32, 32), dtype=bool)
        mask[8:24, 8:24] = True
        alpha = _feather_alpha(mask, 4)
        assert alpha.dtype == np.uint8
        assert alpha.max() == 255
        assert alpha.min() == 0
        intermediate = ((alpha > 0) & (alpha < 255)).sum()
        assert intermediate > 0, "a blurred edge must produce in-between alphas"

    def test_negative_radius_behaves_like_zero(self) -> None:
        mask = np.zeros((4, 4), dtype=bool)
        mask[1:3, 1:3] = True
        assert np.array_equal(_feather_alpha(mask, -2), mask.astype(np.uint8) * 255)


class TestMaskCentroid:
    def test_centroid_of_a_square(self) -> None:
        mask = np.zeros((24, 32), dtype=bool)
        mask[8:16, 12:20] = True
        packed = np.packbits(mask.reshape(-1))
        assert _mask_centroid(packed, 32, 24) == (16, 12)

    def test_empty_mask_has_no_centroid(self) -> None:
        packed = np.packbits(np.zeros(24 * 32, dtype=bool))
        assert _mask_centroid(packed, 32, 24) is None


class TestChunkPlan:
    def test_default_sizes_from_state_budget(self) -> None:
        # 2 GB / (W*H*12 bytes) — verified against sam2's float32 frame loading.
        assert _chunk_frames(1280, 720) == 194
        assert _chunk_frames(1920, 1080) == 86

    def test_override_wins(self) -> None:
        assert _chunk_frames(1280, 720, chunk_frames=10) == 10

    def test_invalid_override_raises(self) -> None:
        with pytest.raises(VideoCutoutError, match="chunk size must be at least 1"):
            _chunk_frames(1280, 720, chunk_frames=0)


class TestChunkRanges:
    def test_partial_final_chunk(self) -> None:
        assert _chunk_ranges(216, 80) == [(0, 79), (80, 159), (160, 215)]

    def test_exact_division(self) -> None:
        assert _chunk_ranges(60, 20) == [(0, 19), (20, 39), (40, 59)]

    def test_single_chunk(self) -> None:
        assert _chunk_ranges(30, 194) == [(0, 29)]


class TestFrameName:
    def test_six_digit_pattern(self) -> None:
        assert _frame_name(7) == "000007.jpg"
        assert _frame_name(123456) == "123456.jpg"


class TestMaskStore:
    def test_roundtrip_and_coverage(self, tmp_path: Path) -> None:
        store = _MaskStore(4, 8, 6, tmp_path)
        first = np.packbits(np.ones(8 * 6, dtype=bool))
        second = np.packbits(np.zeros(8 * 6, dtype=bool))
        store.put(0, first)
        store.put(3, second)
        assert store.covered_count() == 2
        assert store.covered.tolist() == [True, False, False, True]
        assert _unpack_mask(store.get(0), 8, 6).all()
        assert not _unpack_mask(store.get(3), 8, 6).any()
        store.discard()
        assert not (tmp_path / "masks.bin").exists()


class _FakeSegmentPredictor:
    """Stands in for the SAM 2 predictor during segmented propagation.

    Yields a constant mask per chunk (or a per-chunk mask when ``masks``
    maps chunk starts), and records every init and prompt so tests can
    assert the seeding behaviour.
    """

    def __init__(self, masks: dict[int, np.ndarray]) -> None:
        self._masks = masks
        self.inits: list[str] = []
        self.prompts: list[tuple[int, tuple[int, int]]] = []

    def init_state(self, path: str, offload_video_to_cpu: bool = True):
        self.inits.append(str(path))
        return object()

    def add_new_points_or_box(
        self, *, inference_state, frame_idx: int, obj_id: int, points, labels
    ) -> None:
        self.prompts.append((frame_idx, (points[0][0], points[0][1])))

    def propagate_in_video(self, inference_state, reverse: bool = False):
        directory = Path(self.inits[-1])
        mask = self._masks[int(directory.name.split("-")[1])]
        frames = sorted(directory.glob("*.jpg"))
        order = range(len(frames)) if not reverse else reversed(range(len(frames)))
        for local in order:
            yield local, [1], _FakeTensor(mask)


class TestSegmentedPropagation:
    """The seeding protocol, exercised with a predictor stub."""

    SQUARE = np.zeros((24, 32), dtype=bool)
    SQUARE[8:16, 12:20] = True
    EMPTY = np.zeros((24, 32), dtype=bool)

    def _store(self, tmp_path: Path) -> _MaskStore:
        return _MaskStore(60, 32, 24, tmp_path)

    def test_anchor_forward_and_backward_coverage(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        predictor = _FakeSegmentPredictor({0: self.SQUARE, 20: self.SQUARE, 40: self.SQUARE})
        ranges = [(0, 19), (20, 39), (40, 59)]

        segments = _propagate_masks(
            predictor, tmp_path, ranges, 30, (16, 12), store, full_vram=False
        )

        assert segments == 3
        assert store.covered_count() == 60
        # Anchor chunk first, then forward, then backward.
        assert [Path(p).name for p in predictor.inits] == [
            "chunk-000020",
            "chunk-000040",
            "chunk-000000",
        ]
        # The anchor keeps the raw point; neighbours inherit the centroid.
        assert predictor.prompts[0] == (10, (16, 12))
        assert predictor.prompts[1] == (0, (16, 12))
        assert predictor.prompts[2] == (19, (16, 12))

    def test_gone_object_stops_forward_seeding(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        predictor = _FakeSegmentPredictor({0: self.SQUARE, 20: self.EMPTY, 40: self.EMPTY})
        ranges = [(0, 19), (20, 39), (40, 59)]

        _propagate_masks(predictor, tmp_path, ranges, 10, (16, 12), store, full_vram=False)

        # Chunk 1 was propagated (its seed existed) but yielded an empty
        # mask; chunk 2 is skipped entirely because chunk 1's boundary has
        # no centroid to seed from.
        assert [Path(p).name for p in predictor.inits] == ["chunk-000000", "chunk-000020"]
        assert store.covered[:40].all()
        assert not store.covered[40:].any()


class TestEndToEndSegmented:
    """Real SAM 2 across segment seams, including the reverse pass."""

    @NEEDS_SAM2_TINY
    def test_static_object_tracked_across_three_segments(self, tmp_path: Path) -> None:
        clip = tmp_path / "seg.mp4"
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
                "color=black:s=320x240:d=1:rate=30",
                "-vf",
                "drawbox=x=100:y=80:w=60:h=40:color=white:t=fill",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ],
            check=True,
        )

        result = remove_background_video(
            clip,
            select_point=(130, 100),
            anchor_frame=15,
            chunk_frames=10,
            cache_dir=Path.home() / ".cache/media-studio/sam2",
        )

        assert result.segments == 3
        assert result.frames == 30
        codec, _ = _ffprobe_video(result.destination)
        assert codec == "prores"

        # Both sides of the anchor must carry alpha: frame 5 lives in the
        # backward (reverse-propagated) segment, frame 25 in a forward one.
        for frame in (5, 25):
            raw = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "quiet",
                    "-i",
                    str(result.destination),
                    "-vf",
                    f"select=eq(n\\,{frame})",
                    "-vsync",
                    "0",
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgba",
                    "-f",
                    "rawvideo",
                    "-",
                ],
                check=True,
                capture_output=True,
            ).stdout
            alpha = np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 4)[:, :, 3]
            # The box is only ~3% of the frame, so a perfect mask averages
            # about 8; the prompt pixel itself must be fully opaque.
            assert alpha.mean() > 5, f"frame {frame} lost its object"
            assert alpha[100, 130] == 255, f"frame {frame} centre is not opaque"
