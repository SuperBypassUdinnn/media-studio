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

from media_studio.cli import main
from media_studio.edl import (
    EDL_VERSION,
    EdlCut,
    EdlDocument,
    EdlError,
    check_cuts,
    check_sync,
    generate_edl,
    load_edl,
    preview_edl_cut,
    render_edl,
    save_edl,
)
from media_studio.ingest import probe_media
from media_studio.scene import SceneError

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

    def test_render_applies_music_offset_via_ss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shot0 = tmp_path / "shot0.mp4"
        shot0.touch()
        shot1 = tmp_path / "shot1.mp4"
        shot1.touch()
        music = tmp_path / "music.wav"

        doc = EdlDocument(
            version=1,
            music=str(music),
            beats_json="",
            total_duration_s=2.0,
            cuts=[
                EdlCut(
                    id="cut-0",
                    shot=str(shot0),
                    in_ms=0,
                    out_ms=1000,
                    music_offset_ms=1000,
                    beatfx=[],
                    ramp=[],
                    color_lut=None,
                    notes="",
                ),
                EdlCut(
                    id="cut-1",
                    shot=str(shot1),
                    in_ms=0,
                    out_ms=1000,
                    music_offset_ms=2000,
                    beatfx=[],
                    ramp=[],
                    color_lut=None,
                    notes="",
                ),
            ],
        )

        captured: list[list[str]] = []

        def fake_run_ffmpeg(argv: list[str]) -> None:
            captured.append(list(argv))
            output = Path(argv[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.touch()

        monkeypatch.setattr("media_studio.edl._run_ffmpeg", fake_run_ffmpeg)
        monkeypatch.setattr(
            "media_studio.edl.probe_media",
            lambda path: type(
                "MI", (), {"duration": 1.0, "width": 1920, "height": 1080, "has_audio": False}
            )(),
        )

        out = tmp_path / "out.mp4"
        render_edl(doc, out=out, fps=FPS, codec="h264")

        mix_calls = [argv for argv in captured if "-f" in argv and "concat" in argv]
        assert mix_calls, "expected a concat ffmpeg call"
        argv = mix_calls[-1]
        assert "-ss" in argv, f"-ss flag missing from {argv}"
        ss_index = argv.index("-ss")
        # First cut music_offset_ms=1000 -> 1.000 s
        assert argv[ss_index + 1] == "1.000", f"unexpected -ss value: {argv[ss_index + 1]}"
        assert "-shortest" in argv, f"-shortest missing from {argv}"

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


def _make_sync_doc(
    offsets_ms: list[int],
    *,
    beats: dict | None = None,
    total_duration_s: float = 3.0,
) -> EdlDocument:
    """Build an EdlDocument with cuts at the given music offsets (ms)."""

    if beats is None:
        beats = {"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0}
    return EdlDocument(
        version=1,
        music="music.wav",
        beats_json="beats.json",
        total_duration_s=total_duration_s,
        cuts=[
            EdlCut(
                id=f"cut-{i}",
                shot=f"shot{i}.mp4",
                in_ms=0,
                out_ms=1000,
                music_offset_ms=offset,
                beatfx=[],
                ramp=[],
                color_lut=None,
                notes="",
            )
            for i, offset in enumerate(offsets_ms)
        ],
    )


def _write_beats(path: Path, beats: dict) -> Path:
    path.write_text(json.dumps(beats), encoding="utf-8")
    return path


class TestCheckSync:
    def test_perfect_alignment_is_synced(self) -> None:
        beats = {"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0}
        doc = _make_sync_doc([500, 1000, 1500], beats=beats)
        report = check_sync(doc, beats)
        assert report.cuts == 3
        assert report.onsets == 5
        assert report.tolerance == 0.1
        assert report.share_on_onset == 1.0
        assert report.median_distance == 0.0
        assert report.p90_distance == 0.0
        assert report.verdict == "synced"

    def test_off_beat_is_unsynced(self) -> None:
        beats = {"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0}
        # Midpoints between onsets: distance 0.25 s > tolerance 0.1 s.
        doc = _make_sync_doc([750, 1250, 1750], beats=beats)
        report = check_sync(doc, beats)
        assert report.share_on_onset == 0.0
        assert report.verdict == "unsynced"

    def test_too_few_points_is_inconclusive(self) -> None:
        beats = {"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0}
        doc = _make_sync_doc([500, 1000], beats=beats)
        report = check_sync(doc, beats)
        assert report.share_on_onset == 1.0
        assert report.verdict == "inconclusive"

    def test_too_few_onsets_is_inconclusive(self) -> None:
        beats = {"beats": [0.5, 1.0], "duration": 3.0}
        doc = _make_sync_doc([500, 1000, 1500], beats=beats)
        report = check_sync(doc, beats)
        assert report.verdict == "inconclusive"

    def test_empty_cuts_raises_edl_error(self) -> None:
        doc = _make_sync_doc([])
        with pytest.raises(EdlError, match="no cuts in the EDL"):
            check_sync(doc, {"beats": [0.5, 1.0, 1.5], "duration": 3.0})

    def test_median_and_p90_on_known_distances(self) -> None:
        # Onsets at every integer second; cuts sit 0.1..0.5 s after onset 0.
        beats = {"beats": [float(i) for i in range(10)], "duration": 10.0}
        doc = _make_sync_doc([100, 200, 300, 400, 500], beats=beats)
        report = check_sync(doc, beats, tolerance=0.1)
        assert report.median_distance == 0.3
        assert report.p90_distance == 0.5

    def test_zero_duration_falls_back_to_one(self) -> None:
        # No beats duration and no EDL duration -> the 1.0 fallback.
        beats = {"beats": [0.5, 1.0, 1.5, 2.0, 2.5]}
        doc = _make_sync_doc([500, 1000, 1500], beats=beats, total_duration_s=0.0)
        report = check_sync(doc, beats)
        assert report.baseline == 1.0
        # share 1.0 is not > baseline 1.0, and share <= baseline -> unsynced.
        assert report.verdict == "unsynced"

    def test_empty_beats_is_inconclusive(self) -> None:
        # Empty onset list: no distance loop, inconclusive verdict, zeroed metrics.
        beats = {"beats": [], "duration": 27.0}
        doc = _make_sync_doc([500, 1000, 1500], beats=beats)
        report = check_sync(doc, beats)
        assert report.cuts == 3
        assert report.onsets == 0
        assert report.tolerance == 0.1
        assert report.share_on_onset == 0.0
        assert report.baseline == 0.0
        assert report.median_distance == 0.0
        assert report.p90_distance == 0.0
        assert report.verdict == "inconclusive"

    def test_missing_beats_key_is_inconclusive(self) -> None:
        # Absent "beats" key: treated as empty, no KeyError, inconclusive verdict.
        beats = {"duration": 27.0}
        doc = _make_sync_doc([500, 1000, 1500], beats=beats)
        report = check_sync(doc, beats)
        assert report.cuts == 3
        assert report.onsets == 0
        assert report.share_on_onset == 0.0
        assert report.baseline == 0.0
        assert report.median_distance == 0.0
        assert report.p90_distance == 0.0
        assert report.verdict == "inconclusive"


class TestCheckCuts:
    def _make_cut(self, cut_id: str, shot: str, in_ms: int, out_ms: int) -> EdlCut:
        return EdlCut(
            id=cut_id,
            shot=shot,
            in_ms=in_ms,
            out_ms=out_ms,
            music_offset_ms=in_ms,
            beatfx=[],
            ramp=[],
            color_lut=None,
            notes="",
        )

    def _make_doc(self, cuts: list[EdlCut]) -> EdlDocument:
        return EdlDocument(
            version=1,
            music="m.wav",
            beats_json="b.json",
            total_duration_s=3.0,
            cuts=cuts,
        )

    def test_flags_spanning_cut_with_timeline(self) -> None:
        doc = self._make_doc(
            [
                self._make_cut("cut-0", "shot1.mp4", 0, 1000),
                self._make_cut("cut-1", "shot2.mp4", 1000, 2000),
            ]
        )
        detected = {"shot1.mp4": [0.5], "shot2.mp4": []}

        def detector(source: Path, **kwargs: object) -> list[float]:
            return detected[str(source)]

        report = check_cuts(doc, detector=detector)
        assert report.cuts == 2
        assert report.spanning == 1
        assert report.verdict == "spanning"
        f = report.findings[0]
        assert f.cut_id == "cut-0"
        assert f.shot == "shot1.mp4"
        assert f.timeline_start_s == 0.0
        assert f.window == "0.000-1.000"
        assert f.boundary_s == 0.5

    def test_clean_when_boundaries_outside_windows(self) -> None:
        doc = self._make_doc([self._make_cut("cut-0", "shot1.mp4", 0, 1000)])

        def detector(source: Path, **kwargs: object) -> list[float]:
            return [5.0]

        report = check_cuts(doc, detector=detector)
        assert report.cuts == 1
        assert report.spanning == 0
        assert report.verdict == "clean"
        assert report.findings == []

    def test_boundary_at_window_edge_respects_margin(self) -> None:
        # cut window [0.0, 1.0], margin 0.02 -> (0.02, 0.98); edges excluded.
        doc = self._make_doc([self._make_cut("cut-0", "shot1.mp4", 0, 1000)])
        for boundary in (0.0, 0.02, 0.98, 1.0):

            def detector(source: Path, b: float = boundary, **kwargs: object) -> list[float]:
                return [b]

            report = check_cuts(doc, detector=detector)
            assert report.spanning == 0, f"boundary {boundary} should not be a finding"
            assert report.verdict == "clean"

    def test_boundary_inside_margin_is_a_finding(self) -> None:
        doc = self._make_doc([self._make_cut("cut-0", "shot1.mp4", 0, 1000)])

        def detector(source: Path, **kwargs: object) -> list[float]:
            return [0.03]

        report = check_cuts(doc, detector=detector)
        assert report.spanning == 1
        assert report.verdict == "spanning"

    def test_empty_cuts_raises_edl_error(self) -> None:
        doc = self._make_doc([])
        with pytest.raises(EdlError, match="no cuts in the EDL"):
            check_cuts(doc)

    def test_detector_scene_error_wraps_into_edl_error(self) -> None:
        doc = self._make_doc([self._make_cut("cut-0", "shot1.mp4", 0, 1000)])

        def detector(source: Path, **kwargs: object) -> list[float]:
            raise SceneError("boom")

        with pytest.raises(EdlError, match="boom"):
            check_cuts(doc, detector=detector)


class TestCliCheckCuts:
    def test_check_cuts_cli_clean(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        doc = EdlDocument(
            version=1,
            music="m.wav",
            beats_json="b.json",
            total_duration_s=3.0,
            cuts=[
                EdlCut(
                    id="cut-0",
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
        edl_path = tmp_path / "edl.json"
        save_edl(doc, edl_path)

        with patch(
            "media_studio.edl.sustained_scene_changes",
            lambda source, **kw: [],
        ):
            assert main(["video", "edl", "check-cuts", str(edl_path)]) == 0
        out = capsys.readouterr().out
        assert "cuts 1" in out
        assert "spanning 0" in out
        assert out.rstrip().endswith("verdict: clean")

    def test_check_cuts_cli_fail_on_spanning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        doc = EdlDocument(
            version=1,
            music="m.wav",
            beats_json="b.json",
            total_duration_s=3.0,
            cuts=[
                EdlCut(
                    id="cut-0",
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
        edl_path = tmp_path / "edl.json"
        save_edl(doc, edl_path)

        with patch(
            "media_studio.edl.sustained_scene_changes",
            lambda source, **kw: [0.5],
        ):
            assert (
                main(
                    [
                        "video",
                        "edl",
                        "check-cuts",
                        str(edl_path),
                        "--fail-on-spanning",
                    ]
                )
                == 1
            )
            assert main(["video", "edl", "check-cuts", str(edl_path)]) == 0
        out = capsys.readouterr().out
        assert "spanning 1" in out
        assert "verdict: spanning" in out


class TestCliSyncCheck:
    def test_sync_check_synced(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write_beats(
            tmp_path / "beats.json",
            {"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0},
        )
        doc = _make_sync_doc(
            [500, 1000, 1500],
            beats={"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0},
        )
        edl_path = tmp_path / "edl.json"
        save_edl(doc, edl_path)

        assert main(["video", "edl", "sync-check", str(edl_path)]) == 0
        out = capsys.readouterr().out
        assert "cuts 3" in out
        assert "onsets 5" in out
        assert "tolerance 0.1s" in out
        assert "share on onset 1.0" in out
        assert "verdict: synced" in out

    def test_sync_check_missing_beats_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        doc = _make_sync_doc([500, 1000, 1500])
        edl_path = tmp_path / "edl.json"
        save_edl(doc, edl_path)  # beats_json = "beats.json", absent beside the EDL

        assert main(["video", "edl", "sync-check", str(edl_path)]) == 1
        err = capsys.readouterr().err
        assert "error:" in err
        assert str(edl_path.parent / "beats.json") in err

    def test_sync_check_fail_on_unsynced(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        beats_path = _write_beats(
            tmp_path / "beats.json",
            {"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0},
        )
        doc = _make_sync_doc(
            [750, 1250, 1750],
            beats={"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "duration": 3.0},
        )
        edl_path = tmp_path / "edl.json"
        save_edl(doc, edl_path)

        assert (
            main(
                [
                    "video",
                    "edl",
                    "sync-check",
                    str(edl_path),
                    "--beats",
                    str(beats_path),
                    "--fail-on-unsynced",
                ]
            )
            == 1
        )
        out = capsys.readouterr().out
        assert "verdict: unsynced" in out

    def test_sync_check_bad_beats_json_errors(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        beats_path = tmp_path / "beats.json"
        beats_path.write_text("{not valid json", encoding="utf-8")
        doc = _make_sync_doc([500, 1000, 1500])
        edl_path = tmp_path / "edl.json"
        save_edl(doc, edl_path)

        assert main(["video", "edl", "sync-check", str(edl_path), "--beats", str(beats_path)]) == 1
        err = capsys.readouterr().err
        assert "error:" in err

    def test_sync_check_empty_beats_is_inconclusive(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        beats_path = _write_beats(tmp_path / "beats.json", {"beats": [], "duration": 27.0})
        doc = _make_sync_doc([500, 1000, 1500])
        edl_path = tmp_path / "edl.json"
        save_edl(doc, edl_path)

        assert (
            main(
                [
                    "video",
                    "edl",
                    "sync-check",
                    str(edl_path),
                    "--beats",
                    str(beats_path),
                ]
            )
            == 0
        )
        out = capsys.readouterr().out
        assert "cuts 3" in out
        assert "onsets 0" in out
        assert "verdict: inconclusive" in out
