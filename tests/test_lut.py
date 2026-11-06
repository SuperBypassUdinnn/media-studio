"""Tests for media_studio.lut: bundled presets, apply, preview, and search."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio.lut import (
    BUNDLED_LUTS_DIR,
    LutError,
    apply_lut,
    preview_lut,
    search_luts,
)

CINEMATIC = BUNDLED_LUTS_DIR / "cinematic-teal-orange.cube"


def _ffprobe(path: Path, entry: str) -> str:
    """Return one ffprobe stream/format entry value for ``path``."""

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            f"stream={entry}",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _render_test_clip(destination: Path) -> Path:
    """Render a 1 s 1920x1080 testsrc2 h264 clip."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1920x1080:duration=1",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(destination),
    ]
    subprocess.run(argv, check=True)
    return destination


class TestSearchLuts:
    def test_bundled_only(self, tmp_path: Path) -> None:
        results = search_luts(library_root=tmp_path)
        assert len(results) == 5
        assert all(path.suffix == ".cube" for path in results)
        assert all(path in list(BUNDLED_LUTS_DIR.glob("*.cube")) for path in results)

    def test_query_filter(self, tmp_path: Path) -> None:
        results = search_luts("cinematic", library_root=tmp_path)
        assert len(results) == 1
        assert results[0].name == "cinematic-teal-orange.cube"


class TestApplyLutErrors:
    def test_non_cube_raises(self, tmp_path: Path) -> None:
        source = _render_test_clip(tmp_path / "source.mp4")
        not_a_lut = tmp_path / "grade.txt"
        not_a_lut.write_text("not a lut", encoding="utf-8")
        with pytest.raises(LutError, match=r"\.cube"):
            apply_lut(source, not_a_lut, tmp_path / "out.mp4")

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        with pytest.raises(LutError, match=r"no such file"):
            apply_lut(tmp_path / "ghost.mp4", CINEMATIC, tmp_path / "out.mp4")


@pytest.mark.slow
class TestApplyLut:
    def test_h264(self, tmp_path: Path) -> None:
        source = _render_test_clip(tmp_path / "source.mp4")
        destination = tmp_path / "graded.mp4"
        result = apply_lut(source, CINEMATIC, destination, codec="h264")
        assert result == destination
        assert destination.is_file()
        assert _ffprobe(destination, "codec_name") == "h264"
        duration = float(_ffprobe(destination, "duration"))
        assert abs(duration - 1.0) <= 0.2


@pytest.mark.slow
class TestPreviewLut:
    def test_width_doubles(self, tmp_path: Path) -> None:
        source = _render_test_clip(tmp_path / "source.mp4")
        reference_width = int(_ffprobe(source, "width"))
        out = tmp_path / "preview.png"
        result = preview_lut(CINEMATIC, source, out)
        assert result == out
        assert out.is_file()
        out_width = int(_ffprobe(out, "width"))
        assert out_width == 2 * reference_width
