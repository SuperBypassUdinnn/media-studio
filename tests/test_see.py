"""Tests for media_studio.see: facts plus a visual sheet for any media file."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio.see import SeeError, peek


def generate(destination: Path, arguments: list[str]) -> Path:
    """Render a fixture with ffmpeg's lavfi sources."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments, str(destination)],
        check=True,
    )
    return destination


def _video(path: Path) -> Path:
    return generate(
        path,
        ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1", "-c:v", "libx264"],
    )


def _alpha_video(path: Path) -> Path:
    """A ProRes 4444 clip whose centre square is opaque white on transparent."""

    return generate(
        path,
        [
            "-f",
            "lavfi",
            "-i",
            "color=black@0.0:size=320x240:rate=30:duration=0.5,format=rgba",
            "-vf",
            "drawbox=x=100:y=80:w=60:h=40:color=white:t=fill",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p10le",
        ],
    )


def _rgb_image(path: Path) -> Path:
    return generate(
        path,
        ["-f", "lavfi", "-i", "testsrc2=size=320x240:duration=0.1", "-frames:v", "1"],
    )


def _alpha_image(path: Path) -> Path:
    return generate(
        path,
        [
            "-f",
            "lavfi",
            "-i",
            "color=black@0.0:size=320x240,format=rgba",
            "-vf",
            "drawbox=x=40:y=40:w=80:h=60:color=red:t=fill",
            "-frames:v",
            "1",
        ],
    )


def _audio(path: Path) -> Path:
    return generate(path, ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"])


class TestPeekVideo:
    def test_writes_a_contact_sheet(self, tmp_path: Path) -> None:
        result = peek(_video(tmp_path / "clip.mp4"), tmp_path / "sheet.png")
        assert result.kind == "video"
        assert result.sheet == tmp_path / "sheet.png"
        assert result.sheet.is_file() and result.sheet.stat().st_size > 0
        assert result.facts["codec"].startswith("h264")

    def test_alpha_video_gets_a_checkerboard(self, tmp_path: Path) -> None:
        result = peek(_alpha_video(tmp_path / "cut.mov"), tmp_path / "sheet.png")
        assert result.kind == "video (alpha, checkerboard)"
        assert result.sheet.is_file()
        # Regression: frames must be extracted with alpha intact (PNG, not
        # JPG), so the transparent background really shows the board.
        from PIL import Image

        corner = Image.open(result.sheet).convert("RGB").getpixel((4, 4))
        assert corner in {(170, 170, 170), (128, 128, 128)}

    def test_default_destination_beside_source(self, tmp_path: Path) -> None:
        source = _video(tmp_path / "clip.mp4")
        result = peek(source)
        assert result.sheet == tmp_path / "clip.see.png"

    def test_out_folder_is_accepted(self, tmp_path: Path) -> None:
        out = tmp_path / "sheets"
        result = peek(_video(tmp_path / "clip.mp4"), out)
        assert result.sheet == out / "clip.see.png"


class TestPeekImage:
    def test_rgb_image_gets_a_thumbnail(self, tmp_path: Path) -> None:
        result = peek(_rgb_image(tmp_path / "still.png"), tmp_path / "sheet.png")
        assert result.kind == "image"
        assert result.facts["size"] == "320x240"

    def test_alpha_image_gets_a_checkerboard(self, tmp_path: Path) -> None:
        result = peek(_alpha_image(tmp_path / "cut.png"), tmp_path / "sheet.png")
        assert result.kind == "image (alpha, checkerboard)"


class TestPeekAudio:
    def test_waveform_and_loudness(self, tmp_path: Path) -> None:
        result = peek(_audio(tmp_path / "track.wav"), tmp_path / "sheet.png")
        assert result.kind == "audio (waveform)"
        assert result.sheet.is_file() and result.sheet.stat().st_size > 0
        assert "loudness" in result.facts


class TestErrors:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SeeError, match="no such file"):
            peek(tmp_path / "ghost.mp4")


def _project_with_render(tmp_path: Path) -> Path:
    """A scaffolded project with one declared asset and one rendered video."""

    from media_studio.project import scaffold_project

    scaffold_project("demo", root=tmp_path)
    root = tmp_path / "demo"
    (root / "assets" / "video").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "video" / "clip.mp4").write_bytes(b"stub")
    manifest = root / "project.yaml"
    manifest.write_text(
        manifest.read_text().replace(
            "assets: {}",
            "assets:\n  clip: assets/video/clip.mp4",
        )
    )
    (root / "out" / "video").mkdir(parents=True, exist_ok=True)
    _video(root / "out" / "video" / "master-16x9.mp4")
    return root


class TestPeekProject:
    def test_reports_deliverable_state_and_sheet(self, tmp_path: Path) -> None:
        root = _project_with_render(tmp_path)
        result = peek(root)
        assert result.kind == "project"
        assert result.sheet == root / "project.see.png"
        assert result.sheet is not None and result.sheet.is_file()
        assert result.facts["project"] == "demo"
        assert "master-16x9.mp4" in result.facts.get("master-16x9", "")

    def test_out_folder_accepted(self, tmp_path: Path) -> None:
        root = _project_with_render(tmp_path)
        result = peek(root, tmp_path / "sheets")
        assert result.sheet == tmp_path / "sheets" / "project.see.png"

    def test_unrendered_deliverable_reported(self, tmp_path: Path) -> None:
        from media_studio.project import scaffold_project

        root = tmp_path / "blank"
        scaffold_project("blank", root=tmp_path)
        result = peek(root)
        assert result.sheet is None
        assert "not rendered" in result.facts.get("master-16x9", "")

    def test_missing_asset_reported(self, tmp_path: Path) -> None:
        root = _project_with_render(tmp_path)
        (root / "assets" / "video" / "clip.mp4").unlink()
        result = peek(root)
        assert result.facts["assets"] == "1 declared, 1 missing"

    def test_directory_without_manifest_raises(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(SeeError, match="cannot read project"):
            peek(plain)
