"""Tests for resolve_mcp.timeline: project.yaml to Resolve timeline planning."""

from __future__ import annotations

from pathlib import Path

import pytest
from resolve_mcp.timeline import (
    BuildResult,
    TimelineError,
    build_timeline,
    plan_from_project,
)

MANIFEST = """\
slug: timeline-test
title: Timeline Test
status: draft
deliverables:
  - id: master
    kind: video
    resolution: 1280x720
    fps: 30
    duration: 6.0
    container: mp4
    loudness_lufs: -14.0
assets:
  a: assets/a.mp4
  b: assets/b.mp4
  music: assets/music.wav
video:
  shots:
    - { asset: a, in: 0.0, duration: 3.0 }
    - { asset: b, in: 1.0, duration: 3.0, transition: crossfade }
  music:
    asset: music
    gain: 0.8
    duck: true
  titles:
    - { text: "HELLO", start: 0.5, duration: 2.0, size: 72 }
effects:
  - id: video-warm-grade
    at: a
    red: 1.1
  - id: video-glitch-wave
    at: b
  - id: video-cross-dissolve
    at: b
"""


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    for name in ("a.mp4", "b.mp4", "music.wav"):
        (tmp_path / "assets" / name).write_bytes(b"stub")
    (tmp_path / "project.yaml").write_text(MANIFEST, encoding="utf-8")
    return tmp_path


class TestPlanFromProject:
    def test_shots_become_frames(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert len(plan.clips) == 2
        first, second = plan.clips
        assert (first.in_frame, first.out_frame) == (0, 89)
        assert (second.in_frame, second.out_frame) == (30, 119)

    def test_resolution_and_fps(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert (plan.width, plan.height) == (1280, 720)
        assert plan.fps == 30.0

    def test_crossfade_becomes_transition(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert plan.transitions == ((1, "Cross Dissolve"),)

    def test_music_becomes_audio_bed(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert len(plan.audio) == 1
        assert plan.audio[0].asset_id == "music"
        assert plan.audio[0].gain == 0.8

    def test_titles_become_frames(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert len(plan.titles) == 1
        assert plan.titles[0].start_frame == 15
        assert plan.titles[0].duration_frames == 60

    def test_grade_preset_collected(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert plan.grade_targets == ("a",)

    def test_unsupported_effect_reported_not_silent(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert any("video-glitch-wave" in note for note in plan.notes)

    def test_ducking_reported_as_manual_step(self, project_dir: Path):
        plan = plan_from_project(project_dir)
        assert any("ducking" in note for note in plan.notes)

    def test_missing_asset_raises(self, project_dir: Path):
        (project_dir / "assets" / "b.mp4").unlink()
        with pytest.raises(TimelineError, match=r"does not exist"):
            plan_from_project(project_dir)

    def test_unknown_asset_id_raises(self, tmp_path: Path):
        (tmp_path / "project.yaml").write_text(
            MANIFEST.replace("asset: a,", "asset: ghost,"), encoding="utf-8"
        )
        (tmp_path / "assets").mkdir()
        with pytest.raises(TimelineError, match="assets does not define"):
            plan_from_project(tmp_path)

    def test_no_video_plan_raises(self, tmp_path: Path):
        (tmp_path / "project.yaml").write_text(MANIFEST.split("video:")[0], encoding="utf-8")
        with pytest.raises(TimelineError, match=r"no video\.shots"):
            plan_from_project(tmp_path)


class FakeMediaPool:
    """Minimal stand-in for a Resolve MediaPool."""

    def __init__(self, refuse: set[str] | None = None):
        self.imported: list[str] = []
        self.appended: list[dict] = []
        self.refuse = refuse or set()
        self.created: list[str] = []

    def GetRootFolder(self):
        return "root"

    def SetCurrentFolder(self, folder):
        return True

    def ImportMedia(self, paths):
        for path in paths:
            if Path(path).name in self.refuse:
                return []
            self.imported.append(path)
        return [FakeItem(Path(path)) for path in paths]

    def CreateEmptyTimeline(self, name):
        self.created.append(name)
        return FakeTimeline()

    def AppendToTimeline(self, entries):
        self.appended.extend(entries)
        return entries


class FakeItem:
    def __init__(self, path: Path):
        self.path = path


class FakeTimeline:
    def ApplyTransition(self, name, duration):
        return True

    def InsertFusionTitleIntoTimeline(self, name):
        return FakeTitleItem()


class FakeTitleItem:
    def SetProperty(self, key, value):
        return True


class FakeProject:
    def __init__(self):
        self.settings: dict[str, str] = {}

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True


class TestBuildTimeline:
    def test_imports_appends_and_creates(self, project_dir: Path):
        pool = FakeMediaPool()
        result = build_timeline(project_dir, media_pool=pool, project=FakeProject())
        assert isinstance(result, BuildResult)
        assert result.clips_added == 2
        assert result.audio_added == 1
        assert result.titles_added == 1
        assert pool.created == ["timeline-test"]

    def test_video_and_audio_use_separate_tracks(self, project_dir: Path):
        pool = FakeMediaPool()
        build_timeline(project_dir, media_pool=pool, project=FakeProject())
        media_types = {entry["mediaType"] for entry in pool.appended}
        assert media_types == {1, 2}

    def test_project_settings_set(self, project_dir: Path):
        project = FakeProject()
        build_timeline(project_dir, media_pool=FakeMediaPool(), project=project)
        assert project.settings["timelineResolutionWidth"] == "1280"
        assert project.settings["timelineFrameRate"] == "30.0"

    def test_refused_clip_reported(self, project_dir: Path):
        result = build_timeline(
            project_dir,
            media_pool=FakeMediaPool(refuse={"b.mp4"}),
            project=FakeProject(),
        )
        assert result.clips_added == 1
        assert any("b.mp4" in note for note in result.skipped)

    def test_grades_skipped_without_lut(self, project_dir: Path):
        result = build_timeline(project_dir, media_pool=FakeMediaPool(), project=FakeProject())
        assert any("apply_grades=True" in note for note in result.skipped)

    def test_notes_carried_into_result(self, project_dir: Path):
        result = build_timeline(project_dir, media_pool=FakeMediaPool(), project=FakeProject())
        assert any("video-glitch-wave" in note for note in result.notes)
