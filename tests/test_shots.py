"""Tests for media_studio.shots: mine a long source into ranked shot candidates.

Pure tests monkeypatch the detector and motion_score so no ffmpeg is needed.
The slow E2E group builds real clips and renders candidates through ffmpeg.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from media_studio.cli import main
from media_studio.ingest import probe_media
from media_studio.shots import (
    ShotCandidate,
    ShotsError,
    load_candidates,
    mine_shots,
    render_candidates,
    write_candidates,
)

FPS = 60.0


def _make_clip(path: Path, *, duration: int = 2) -> Path:
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
            f"testsrc2=size=640x480:rate={int(FPS)}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _fake_probe(duration: float) -> SimpleNamespace:
    return SimpleNamespace(duration=duration)


def _candidate(rank: int, in_s: float, out_s: float, motion: float) -> ShotCandidate:
    return ShotCandidate(
        rank=rank,
        source="source.mp4",
        in_s=in_s,
        out_s=out_s,
        duration_s=round(out_s - in_s, 3),
        scene_start_s=in_s,
        scene_end_s=out_s,
        motion=motion,
    )


class TestMineShotsPure:
    def test_drops_short_segments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "source.mp4"
        source.touch()

        def fake_detector(src: Path, **kwargs: object) -> list[float]:
            return [0.2, 1.0]  # segments: (0,0.2),(0.2,1.0),(1.0,10.0)

        def fake_motion(src: Path, start: float, end: float, *, samples: int = 4) -> float:
            return float(start)

        monkeypatch.setattr("media_studio.shots.probe_media", lambda p: _fake_probe(10.0))
        monkeypatch.setattr("media_studio.shots.sustained_scene_changes", fake_detector)
        monkeypatch.setattr("media_studio.shots.motion_score", fake_motion)

        candidates = mine_shots(source, min_duration_s=0.5, max_duration_s=2.0)
        # (0,0.2) dropped (< 0.5); (0.2,1.0) kept; (1.0,10.0) kept
        assert len(candidates) == 2
        assert all(c.scene_end_s - c.scene_start_s >= 0.5 for c in candidates)

    def test_long_segment_slides_max_duration_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.mp4"
        source.touch()

        def fake_detector(src: Path, **kwargs: object) -> list[float]:
            return [5.0]  # segments: (0,5),(5,10)

        def fake_motion(src: Path, start: float, end: float, *, samples: int = 4) -> float:
            return float(start)

        monkeypatch.setattr("media_studio.shots.probe_media", lambda p: _fake_probe(10.0))
        monkeypatch.setattr("media_studio.shots.sustained_scene_changes", fake_detector)
        monkeypatch.setattr("media_studio.shots.motion_score", fake_motion)

        candidates = mine_shots(source, min_duration_s=0.3, max_duration_s=2.0)
        # segment (0,5) slides 2 s windows; at least one candidate from it
        seg05 = [c for c in candidates if c.scene_start_s == 0.0 and c.scene_end_s == 5.0]
        assert len(seg05) == 1
        assert seg05[0].duration_s == 2.0
        assert seg05[0].in_s >= 0.0 and seg05[0].out_s <= 5.0

    def test_ranked_by_motion_descending_starting_at_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.mp4"
        source.touch()

        def fake_detector(src: Path, **kwargs: object) -> list[float]:
            return [3.0, 7.0]  # segments: (0,3),(3,7),(7,10)

        def fake_motion(src: Path, start: float, end: float, *, samples: int = 4) -> float:
            return float(start)  # motion rises with start time

        monkeypatch.setattr("media_studio.shots.probe_media", lambda p: _fake_probe(10.0))
        monkeypatch.setattr("media_studio.shots.sustained_scene_changes", fake_detector)
        monkeypatch.setattr("media_studio.shots.motion_score", fake_motion)

        candidates = mine_shots(source, min_duration_s=0.3, max_duration_s=2.0)
        motions = [c.motion for c in candidates]
        assert motions == sorted(motions, reverse=True)
        assert [c.rank for c in candidates] == list(range(1, len(candidates) + 1))

    def test_no_window_crosses_scene_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.mp4"
        source.touch()

        def fake_detector(src: Path, **kwargs: object) -> list[float]:
            return [4.0]  # segments: (0,4),(4,10)

        def fake_motion(src: Path, start: float, end: float, *, samples: int = 4) -> float:
            return float(start)

        monkeypatch.setattr("media_studio.shots.probe_media", lambda p: _fake_probe(10.0))
        monkeypatch.setattr("media_studio.shots.sustained_scene_changes", fake_detector)
        monkeypatch.setattr("media_studio.shots.motion_score", fake_motion)

        candidates = mine_shots(source, min_duration_s=0.3, max_duration_s=2.0)
        for c in candidates:
            assert not (c.in_s + 1e-9 < 4.0 < c.out_s - 1e-9), f"window crosses boundary: {c}"

    def test_no_usable_scenes_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "source.mp4"
        source.touch()

        def fake_detector(src: Path, **kwargs: object) -> list[float]:
            return []  # one segment (0,10) but min_duration too high

        monkeypatch.setattr("media_studio.shots.probe_media", lambda p: _fake_probe(10.0))
        monkeypatch.setattr("media_studio.shots.sustained_scene_changes", fake_detector)
        monkeypatch.setattr(
            "media_studio.shots.motion_score",
            lambda src, start, end, *, samples=4: 0.0,
        )

        with pytest.raises(ShotsError, match="no usable scenes"):
            mine_shots(source, min_duration_s=20.0, max_duration_s=2.0)


class TestWriteLoadCandidates:
    def test_round_trip_preserves_fields(self, tmp_path: Path) -> None:
        candidates = [
            _candidate(1, 0.0, 2.0, 5.5),
            _candidate(2, 3.0, 5.0, 3.25),
        ]
        target = tmp_path / "candidates.json"
        write_candidates(candidates, target)
        loaded = load_candidates(target)
        assert loaded == candidates

    def test_malformed_json_raises_shots_error(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.json"
        target.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ShotsError):
            load_candidates(target)

    def test_missing_field_raises_shots_error(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.json"
        target.write_text(json.dumps([{"rank": 1}]), encoding="utf-8")
        with pytest.raises(ShotsError):
            load_candidates(target)


@pytest.mark.slow
class TestMineShotsSlow:
    def test_two_scene_clip_yields_candidates_inside_scenes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seg1 = tmp_path / "seg1.mp4"
        seg2 = tmp_path / "seg2.mp4"
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
                f"color=c=red:size=1920x1080:rate={int(FPS)}:duration=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(seg1),
            ],
            check=True,
        )
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
                f"testsrc2=size=1920x1080:rate={int(FPS)}:duration=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(seg2),
            ],
            check=True,
        )
        concat = tmp_path / "concat.txt"
        concat.write_text(f"file '{seg1}'\nfile '{seg2}'\n", encoding="utf-8")
        clip = tmp_path / "two_scene.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c",
                "copy",
                str(clip),
            ],
            check=True,
        )

        candidates = mine_shots(clip, min_duration_s=0.3, max_duration_s=2.0)
        assert len(candidates) >= 2, f"expected >= 2 candidates, got {len(candidates)}"
        # boundary at 2 s; no window may cross it
        for c in candidates:
            assert not (c.in_s + 1e-9 < 2.0 < c.out_s - 1e-9), f"window crosses boundary: {c}"

        rendered = render_candidates(candidates, tmp_path / "out", scale="1920x1080")
        assert len(rendered) == len(candidates)
        for out in rendered:
            assert out.is_file()
            info = probe_media(out)
            assert info.width == 1920 and info.height == 1080, f"{out}: {info.width}x{info.height}"


class TestCliShotsMine:
    def test_cli_no_render_writes_candidates_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "source.mp4"
        _make_clip(source, duration=2)

        def fake_detector(src: Path, **kwargs: object) -> list[float]:
            return []

        monkeypatch.setattr("media_studio.shots.sustained_scene_changes", fake_detector)
        monkeypatch.setattr(
            "media_studio.shots.motion_score",
            lambda src, start, end, *, samples=4: 1.0,
        )

        out_dir = tmp_path / "shots"
        code = main(
            [
                "video",
                "shots",
                "mine",
                str(source),
                "--out",
                str(out_dir),
                "--no-render",
            ]
        )
        assert code == 0
        assert (out_dir / "candidates.json").is_file()
