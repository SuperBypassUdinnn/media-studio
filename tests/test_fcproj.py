"""Tests for FilmCraft timeline compiler (fcproj.py)."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_studio.fcproj import (
    CompilerError,
    compile_manifest_to_fcproj,
    db_from_gain,
    diff_timelines,
    plan_manifest_compile,
    seconds_from_ticks,
    snapshot_timeline,
    ticks_from_seconds,
)
from media_studio.filmcraft import CommandResult, FilmCraftRunner
from media_studio.manifest import (
    AudioCue,
    Brand,
    Budget,
    Deliverable,
    Project,
    Shot,
    TitleText,
    VideoPlan,
)


@pytest.fixture(autouse=True)
def _stub_filmcraft_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("filmcraft-cli") is None and shutil.which("filmcraft") is None:
        monkeypatch.setenv("MS_FILMCRAFT_BIN", "/bin/true")


def test_db_from_gain() -> None:
    assert db_from_gain(1.0) == 0.0
    assert abs(db_from_gain(0.5) - (-6.0206)) < 0.01
    assert db_from_gain(0.0) == -60.0
    assert db_from_gain(-1.0) == -60.0


def test_ticks_conversion() -> None:
    ticks = ticks_from_seconds(1.0)
    assert ticks == 254_016_000_000
    sec = seconds_from_ticks(ticks)
    assert sec == 1.0


def test_snapshot_and_diff_timelines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj_file = tmp_path / "test.fcproj"
    proj_file.write_text("dummy project content")

    snap = snapshot_timeline(proj_file)
    assert snap.is_file()
    assert snap.read_text() == "dummy project content"

    # Mock inspect for diff
    snap_inspect = {
        "sequence": {
            "duration": 5.0,
            "clips": [{"id": 1, "name": "clip_a"}, {"id": 2, "name": "clip_b"}],
        }
    }
    curr_inspect = {
        "sequence": {
            "duration": 7.0,
            "clips": [{"id": 1, "name": "clip_a_renamed"}, {"id": 3, "name": "clip_c"}],
        }
    }

    mock_runner = MagicMock()
    mock_runner.inspect.side_effect = [curr_inspect, snap_inspect]
    monkeypatch.setattr("media_studio.fcproj.FilmCraftRunner", lambda: mock_runner)

    diff = diff_timelines(proj_file, snap)
    assert diff["duration_delta"] == 2.0
    expected_added = [
        {"clip": 3, "name": "clip_c", "track": "Unknown", "start": 0.0, "duration": 0.0}
    ]
    expected_removed = [
        {"clip": 2, "name": "clip_b", "track": "Unknown", "start": 0.0, "duration": 0.0}
    ]
    assert diff["added"] == expected_added
    assert diff["removed"] == expected_removed
    assert len(diff["modified"]) == 1


def test_compiler_missing_asset_raises(tmp_path: Path) -> None:
    project = Project(
        root=tmp_path,
        slug="test-proj",
        title="Test Project",
        status="draft",
        deliverables=(
            Deliverable(
                id="main",
                kind="video",
                resolution="1920x1080",
                fps=30.0,
            ),
        ),
        brand=Brand(),
        budget=Budget(),
        assets={"shot_1": "footage/nonexistent.mp4"},
        timeline=(),
        video=VideoPlan(
            shots=(Shot(asset="shot_1", in_point=0.0, duration=2.0),),
        ),
        design=None,
        effects=(),
        raw={},
    )

    with pytest.raises(CompilerError, match="does not exist"):
        compile_manifest_to_fcproj(project, deliverable_id="main")


def test_compile_manifest_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Prepare dummy asset files
    footage_file = tmp_path / "footage.mp4"
    footage_file.write_text("fake video")
    audio_file = tmp_path / "voice.wav"
    audio_file.write_text("fake voice")

    project = Project(
        root=tmp_path,
        slug="mock-proj",
        title="Mock Project",
        status="draft",
        deliverables=(
            Deliverable(
                id="master",
                kind="video",
                resolution="1920x1080",
                fps=30.0,
            ),
        ),
        brand=Brand(),
        budget=Budget(),
        assets={"v1": "footage.mp4", "a1": "voice.wav"},
        timeline=(),
        video=VideoPlan(
            shots=(Shot(asset="v1", in_point=0.0, duration=3.0),),
            voice=AudioCue(asset="a1", in_point=0.0, gain=0.8),
            titles=(TitleText(text="Hello World", start=1.0, duration=2.0),),
        ),
        design=None,
        effects=(),
        raw={},
    )

    commands_executed: list[tuple[str, dict | None]] = []

    mock_runner = MagicMock(spec=FilmCraftRunner)

    def mock_exec(cmd_id: str, params: dict | None = None, **kwargs: object) -> CommandResult:
        commands_executed.append((cmd_id, params))
        if cmd_id == "file.newSequence":
            return CommandResult(
                command=cmd_id,
                returncode=0,
                stdout="",
                stderr="",
                result={"sequence": 42},
                ok=True,
            )
        if cmd_id == "file.import":
            return CommandResult(
                command=cmd_id,
                returncode=0,
                stdout="",
                stderr="",
                result={"items": [101]},
                ok=True,
            )
        return CommandResult(
            command=cmd_id,
            returncode=0,
            stdout="",
            stderr="",
            result={"status": "ok"},
            ok=True,
        )

    def mock_run_batch(cmds: list[dict[str, Any]], **kwargs: object) -> list[CommandResult]:
        res_list = []
        for cmd in cmds:
            cid = cmd.get("id", "")
            params = cmd.get("params")
            commands_executed.append((cid, params))
            if cid == "file.newSequence":
                res_list.append(
                    CommandResult(
                        command=cid,
                        returncode=0,
                        stdout="",
                        stderr="",
                        result={"sequence": 42},
                        ok=True,
                    )
                )
            else:
                res_list.append(
                    CommandResult(
                        command=cid,
                        returncode=0,
                        stdout="",
                        stderr="",
                        result={"status": "ok"},
                        ok=True,
                    )
                )
        return res_list

    mock_runner.run_batch.side_effect = mock_run_batch
    mock_runner.exec.side_effect = mock_exec
    monkeypatch.setattr("media_studio.fcproj.FilmCraftRunner", lambda: mock_runner)

    result = compile_manifest_to_fcproj(project, deliverable_id="master")

    assert result.shots_placed == 1
    assert result.titles_placed == 1
    assert result.audio_cues_placed == 1
    assert result.sequence_id == 42
    assert result.output_path == tmp_path / "mock-proj.fcproj"

    cmd_ids = [c[0] for c in commands_executed]
    assert "file.newProject" in cmd_ids
    assert "file.newSequence" in cmd_ids
    assert "timeline.setTrack" in cmd_ids
    assert "file.import" in cmd_ids
    assert "timeline.place" in cmd_ids
    assert "graphics.newText" in cmd_ids


def _make_project(root: Path, slug: str = "plan-proj", duck: bool = True) -> Project:
    """Build a small but complete project for compiler tests."""
    (root / "footage.mp4").write_text("fake video")
    (root / "voice.wav").write_text("fake voice")
    (root / "music.wav").write_text("fake music")
    return Project(
        root=root,
        slug=slug,
        title="Plan Project",
        status="draft",
        deliverables=(Deliverable(id="master", kind="video", resolution="1920x1080", fps=30.0),),
        brand=Brand(),
        budget=Budget(),
        assets={"v1": "footage.mp4", "a1": "voice.wav", "m1": "music.wav"},
        timeline=(),
        video=VideoPlan(
            shots=(Shot(asset="v1", in_point=0.0, duration=3.0),),
            voice=AudioCue(asset="a1", in_point=0.0, gain=0.8),
            music=AudioCue(asset="m1", in_point=0.0, gain=0.35, duck=duck),
            titles=(TitleText(text="Hello", start=1.0, duration=2.0),),
        ),
        design=None,
        effects=(),
        raw={},
    )


def test_plan_manifest_compile_dry_run(tmp_path: Path) -> None:
    """plan() reports geometry, imports and counts without touching engine or disk."""
    project = _make_project(tmp_path)
    mock_runner = MagicMock(spec=FilmCraftRunner)

    plan = plan_manifest_compile(project, runner=mock_runner)

    assert plan.fcproj_path == (tmp_path / "plan-proj.fcproj").resolve()
    assert (plan.width, plan.height, plan.fps) == (1920, 1080, 30.0)
    assert plan.shots == 1
    assert plan.titles == 1
    assert plan.voice_cues == 1
    assert plan.music_cues == 1
    assert plan.bake_duck is True  # music.duck and voice both present
    assert plan.duration_seconds == 3.0
    assert set(plan.imports) == {"v1", "a1", "m1"}
    assert plan.imports["v1"] == (tmp_path / "footage.mp4").resolve()

    # Dry run side-effect contract: no engine interaction, no file created.
    assert not plan.fcproj_path.exists()
    assert mock_runner.mock_calls == []


def test_compile_refuses_existing_target(tmp_path: Path) -> None:
    """D46: an existing .fcproj is never silently overwritten without --force."""
    project = _make_project(tmp_path)
    target = tmp_path / "plan-proj.fcproj"
    target.write_text("manual GUI edits live here")
    mock_runner = MagicMock(spec=FilmCraftRunner)

    with pytest.raises(CompilerError, match="already exists"):
        compile_manifest_to_fcproj(project, runner=mock_runner)

    assert target.read_text() == "manual GUI edits live here"
    assert mock_runner.mock_calls == []


def test_compile_force_snapshots_before_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D46: --force archives the existing timeline as a snapshot, then rebuilds."""
    project = _make_project(tmp_path, duck=False)  # keep the fake wavs out of ffmpeg
    target = tmp_path / "plan-proj.fcproj"
    target.write_text("manual GUI edits live here")

    snapshotted: list[Path] = []
    real_snapshot = snapshot_timeline

    def recording_snapshot(path: Path) -> Path:
        snapshotted.append(path)
        return real_snapshot(path)

    monkeypatch.setattr("media_studio.fcproj.snapshot_timeline", recording_snapshot)

    mock_runner = MagicMock(spec=FilmCraftRunner)

    def mock_exec(cmd_id: str, params: dict | None = None, **kwargs: object) -> CommandResult:
        if cmd_id == "file.import":
            return CommandResult(
                command=cmd_id,
                returncode=0,
                stdout="",
                stderr="",
                result={"items": [101]},
                ok=True,
            )
        return CommandResult(command=cmd_id, returncode=0, stdout="", stderr="", result={}, ok=True)

    def mock_run_batch(cmds: list[dict[str, Any]], **kwargs: object) -> list[CommandResult]:
        return [
            CommandResult(
                command=c.get("id", ""),
                returncode=0,
                stdout="",
                stderr="",
                result={"sequence": 7} if c.get("id") == "file.newSequence" else {},
                ok=True,
            )
            for c in cmds
        ]

    mock_runner.exec.side_effect = mock_exec
    mock_runner.run_batch.side_effect = mock_run_batch

    result = compile_manifest_to_fcproj(project, runner=mock_runner, force=True)

    assert snapshotted == [target.resolve()]
    assert result.output_path == target


def _recording_runner() -> tuple[MagicMock, list[dict[str, Any]]]:
    """A FilmCraft runner double that records every `timeline.place` payload."""

    placed: list[dict[str, Any]] = []
    runner = MagicMock(spec=FilmCraftRunner)

    def mock_exec(cmd_id: str, params: dict | None = None, **kwargs: object) -> CommandResult:
        if cmd_id == "file.import":
            return CommandResult(
                command=cmd_id,
                returncode=0,
                stdout="",
                stderr="",
                result={"items": [101]},
                ok=True,
            )
        return CommandResult(command=cmd_id, returncode=0, stdout="", stderr="", result={}, ok=True)

    def mock_run_batch(cmds: list[dict[str, Any]], **kwargs: object) -> list[CommandResult]:
        for command in cmds:
            if command.get("id") == "timeline.place":
                placed.append(command.get("params") or {})
        return [
            CommandResult(
                command=command.get("id", ""),
                returncode=0,
                stdout="",
                stderr="",
                result={"sequence": 7} if command.get("id") == "file.newSequence" else {},
                ok=True,
            )
            for command in cmds
        ]

    runner.exec.side_effect = mock_exec
    runner.run_batch.side_effect = mock_run_batch
    return runner, placed


def test_audio_cue_clamps_long_music_to_video_duration_with_offset(tmp_path: Path) -> None:
    """A cue entered 30 s into its source must stop at the end of a 3 s edit."""

    project = _make_project(tmp_path, duck=False)
    assert project.video is not None
    clamped = replace(
        project,
        video=replace(
            project.video,
            music=AudioCue(asset="m1", in_point=30.0, duration=45.0, gain=0.35, duck=False),
        ),
    )
    runner, placed = _recording_runner()

    compile_manifest_to_fcproj(clamped, runner=runner)

    music = next(params for params in placed if params.get("track") == "A2")
    assert music["sourceIn"] == {"seconds": 30.0}
    assert music["duration"] == {"seconds": 3.0}

    # clamp_to_video=False keeps the declared length (the source probe is unavailable
    # for the fake fixture, so no source-length clamp can shorten it either).
    unclamped = replace(
        clamped,
        video=replace(
            clamped.video,
            music=AudioCue(
                asset="m1",
                in_point=30.0,
                duration=45.0,
                gain=0.35,
                duck=False,
                clamp_to_video=False,
            ),
        ),
    )
    runner2, placed2 = _recording_runner()

    compile_manifest_to_fcproj(unclamped, runner=runner2)

    music2 = next(params for params in placed2 if params.get("track") == "A2")
    assert music2["duration"] == {"seconds": 45.0}
