"""Tests for FilmCraft timeline compiler (fcproj.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_studio.fcproj import (
    CompilerError,
    compile_manifest_to_fcproj,
    db_from_gain,
    diff_timelines,
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
