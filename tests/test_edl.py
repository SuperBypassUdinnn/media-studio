"""Tests for media_studio.edl: EDL cut-plan tool.

Unit tests cover EDL schema round-tripping, generate_edl with mocked probe,
and malformed JSON / in_ms > out_ms validation. The slow E2E group renders
real 1920x1080 testsrc2 clips through ffmpeg.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from media_studio.edl import (
    EDL_VERSION,
    EdlCut,
    EdlDocument,
    EdlError,
    generate_edl,
    load_edl,
    preview_edl_cut,
    render_edl,
    save_edl,
)
from media_studio.ingest import probe_media

FPS = 60.0
RESOLUTION = "1920x1080"


def _make_clip(path: Path, *, size: str = "1920x1080", duration: int = 1) -> Path:
    """Render a tiny real clip so E2E tests exercise real ffmpeg."""

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
            f"testsrc2=size={size}:rate=60:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _write_edl(path: Path, doc: EdlDocument) -> None:
    path.write_text(json.dumps(doc.__dict__, indent=2))


class TestEdlVersion:
    def test_edl_version_is_integer_one(self) -> None:
        assert EDL_VERSION == 1
        assert isinstance(EDL_VERSION, int)


class TestLoadSaveEdl:
    def test_round_trip_preserves_all_fields(self, tmp_path: Path) -> None:
        doc = EdlDocument(
            version=1,
            music="music.wav",
            beats_json="beats.json",
            total_duration_s=3.0,
            cuts=[
                EdlCut(
                    id="cut-1",
                    shot="shot1.mp4",
                    in_ms=0,
                    out_ms=1000,
                    music_offset_ms=0,
                    beatfx=[],
                    ramp=[],
                    color_lut=None,
                    notes="",
                )
            ],
        )
        target = tmp_path / "edl.json"
        save_edl(doc, target)
        loaded = load_edl(target)
        assert loaded == doc
        assert loaded.cuts[0].color_lut is None
        assert loaded.cuts[0].beatfx == []
        assert loaded.cuts[0].ramp == []

    def test_malformed_json_raises_edl_error(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.json"
        target.write_text("{not valid json")
        with pytest.raises(EdlError, match="invalid EDL"):
            load_edl(target)


class TestGenerateEdl:
    def test_generates_three_consecutive_cuts(self, tmp_path: Path) -> None:
        shots = [tmp_path / f"shot{i}.mp4" for i in range(3)]
        beats = {
            "bpm": 120,
            "beats": [0.5, 1.0, 1.5],
            "downbeats": [0.5, 1.5],
            "energy": [0.5, 0.9],
            "window": 0.5,
            "sections": [
                {"start": 0.0, "end": 1.5, "level": "high"},
                {"start": 1.5, "end": 3.0, "level": "low"},
            ],
            "duration": 3.0,
        }
        beats_json = tmp_path / "beats.json"
        beats_json.write_text(json.dumps(beats))
        music = tmp_path / "music.wav"

        # probe each shot for duration: 1 second each
        fake_infos = {
            str(s): type(
                "M",
                (),
                {"duration": 1.0, "width": 1920, "height": 1080},
            )()
            for s in shots
        }

        def fake_probe(path: Path):
            info = fake_infos[str(path)]
            return type(
                "MI",
                (),
                {
                    "duration": info.duration,
                    "width": info.width,
                    "height": info.height,
                    "has_audio": False,
                },
            )()

        with patch("media_studio.edl.probe_media", side_effect=fake_probe):
            doc = generate_edl(
                shots,
                beats_json,
                music,
                total_duration_s=3.0,
                out=tmp_path / "edl.json",
            )

        assert doc.version == 1
        assert len(doc.cuts) == 3
        # consecutive music offsets
        offsets = [c.music_offset_ms for c in doc.cuts]
        assert offsets == sorted(offsets)
        # each cut within density range and within shot duration
        for cut in doc.cuts:
            duration_s = (cut.out_ms - cut.in_ms) / 1000
            assert 0.2 <= duration_s <= 2.0
            assert duration_s <= 1.0  # shot duration cap

    def test_default_density_when_no_sections(self, tmp_path: Path) -> None:
        shots = [tmp_path / "shot.mp4"]
        beats = {
            "bpm": 120,
            "beats": [0.5],
            "downbeats": [0.5],
            "energy": [0.5],
            "window": 0.5,
            "sections": [],
            "duration": 3.0,
        }
        beats_json = tmp_path / "beats.json"
        beats_json.write_text(json.dumps(beats))
        music = tmp_path / "music.wav"

        def fake_probe(path: Path):
            return type(
                "MI",
                (),
                {"duration": 1.0, "width": 1920, "height": 1080, "has_audio": False},
            )()

        with patch("media_studio.edl.probe_media", side_effect=fake_probe):
            doc = generate_edl(
                shots, beats_json, music, total_duration_s=3.0, out=tmp_path / "edl.json"
            )

        assert len(doc.cuts) == 1


class TestRenderEdl:
    @pytest.mark.slow
    def test_render_no_effects_real_ffmpeg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shots = [_make_clip(tmp_path / f"shot{i}.mp4") for i in range(3)]
        doc = EdlDocument(
            version=1,
            music="",
            beats_json="",
            total_duration_s=3.0,
            cuts=[
                EdlCut(
                    id=f"cut-{i}",
                    shot=str(s),
                    in_ms=0,
                    out_ms=1000,
                    music_offset_ms=0,
                    beatfx=[],
                    ramp=[],
                    color_lut=None,
                    notes="",
                )
                for i, s in enumerate(shots)
            ],
        )
        out = tmp_path / "out.mp4"
        result = render_edl(doc, out=out, fps=FPS, codec="h264")
        assert result == out
        assert out.is_file()
        assert abs(probe_media(out).duration - 3.0) < 0.5

    @pytest.mark.slow
    def test_render_one_cut_with_beatfx(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "source.mp4")
        doc = EdlDocument(
            version=1,
            music="",
            beats_json="",
            total_duration_s=1.0,
            cuts=[
                EdlCut(
                    id="cut-1",
                    shot=str(source),
                    in_ms=0,
                    out_ms=1000,
                    music_offset_ms=0,
                    beatfx=["flash@0.2"],
                    ramp=[],
                    color_lut=None,
                    notes="",
                )
            ],
        )
        out = tmp_path / "out.mp4"
        result = render_edl(doc, out=out, fps=FPS, codec="h264")
        assert result == out
        assert out.is_file()

    def test_in_ms_greater_than_out_ms_raises(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "source.mp4")
        doc = EdlDocument(
            version=1,
            music="",
            beats_json="",
            total_duration_s=1.0,
            cuts=[
                EdlCut(
                    id="cut-1",
                    shot=str(source),
                    in_ms=2000,
                    out_ms=1000,
                    music_offset_ms=0,
                    beatfx=[],
                    ramp=[],
                    color_lut=None,
                    notes="",
                )
            ],
        )
        out = tmp_path / "out.mp4"
        with pytest.raises(EdlError, match="in_ms must be less than out_ms"):
            render_edl(doc, out=out, fps=FPS, codec="h264")


class TestPreviewEdlCut:
    @pytest.mark.slow
    def test_preview_renders_only_specified_cut(self, tmp_path: Path) -> None:
        shots = [_make_clip(tmp_path / f"shot{i}.mp4") for i in range(3)]
        doc = EdlDocument(
            version=1,
            music="",
            beats_json="",
            total_duration_s=3.0,
            cuts=[
                EdlCut(
                    id=f"cut-{i}",
                    shot=str(s),
                    in_ms=0,
                    out_ms=1000,
                    music_offset_ms=i * 1000,
                    beatfx=[],
                    ramp=[],
                    color_lut=None,
                    notes="",
                )
                for i, s in enumerate(shots)
            ],
        )
        out = tmp_path / "preview.mp4"
        result = preview_edl_cut(doc, "cut-1", out=out, fps=FPS)
        assert result == out
        assert out.is_file()
        assert abs(probe_media(out).duration - 1.0) < 0.3
