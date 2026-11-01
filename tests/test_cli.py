"""Tests for the command line surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import media_studio.cli as cli_module
from media_studio.cli import main
from media_studio.filmcraft import CommandResult
from media_studio.tools import ToolSpec, ToolStatus


def test_project_init_reports_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["project", "init", "cli-demo", "--root", str(tmp_path)])

    assert exit_code == 0
    assert (tmp_path / "cli-demo" / "project.yaml").is_file()
    assert "cli-demo" in capsys.readouterr().out


def test_project_init_rejects_bad_slug(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["project", "init", "Bad Slug", "--root", str(tmp_path)])

    assert exit_code == 1
    assert "invalid slug" in capsys.readouterr().err


def test_doctor_json_flags_missing_required_tool(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = ToolSpec("ghost", ("--version",), True, "nothing")
    monkeypatch.setattr(
        cli_module,
        "collect_toolchain",
        lambda: [ToolStatus(spec=spec, path=None, version=None)],
    )
    monkeypatch.setattr(cli_module, "path_order_problem", lambda _path: None)

    exit_code = main(["doctor", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["missing_required"] == ["ghost"]
    assert payload["checks"][0] == {"name": "PATH order", "ok": True, "detail": "ok"}
    assert payload["warnings"] == []


def test_doctor_plain_output_when_healthy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = ToolSpec("fake", ("--version",), False, "testing")
    monkeypatch.setattr(
        cli_module,
        "collect_toolchain",
        lambda: [ToolStatus(spec=spec, path="/usr/bin/fake", version="fake 1.0")],
    )
    monkeypatch.setattr(cli_module, "path_order_problem", lambda _path: "local bin first")

    exit_code = main(["doctor"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "all required tools present" in output
    assert "local bin first" in output


def test_video_cli_subparsers_registered() -> None:
    parser = cli_module.build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    video_parser = subparsers_action.choices["video"]
    video_sub_action = next(
        a for a in video_parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    video_sub = video_sub_action.choices
    for cmd in ["build", "cmd", "place", "trim", "inspect", "open", "snapshot", "diff"]:
        assert cmd in video_sub


def test_video_cmd_and_place_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fcproj = tmp_path / "test.fcproj"
    fcproj.write_text("{}")

    mock_runner = MagicMock()
    mock_runner.exec.return_value = CommandResult(
        command="timeline.place",
        returncode=0,
        stdout="ok",
        stderr="",
        result={"status": "ok", "clip": 42},
        ok=True,
    )
    monkeypatch.setattr(cli_module, "FilmCraftRunner", lambda: mock_runner)

    exit_code = main(
        ["video", "place", str(fcproj), "--item", "1", "--track", "V1", "--seconds", "2.5"]
    )
    assert exit_code == 0
    assert "placed item 1 on V1 at 2.5s" in capsys.readouterr().out
    mock_runner.exec.assert_called_once()


def test_video_inspect_json_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fcproj = tmp_path / "test.fcproj"
    fcproj.write_text("{}")

    mock_runner = MagicMock()
    mock_runner.inspect.return_value = {
        "project": {"name": "demo", "items": [{"id": 1}]},
        "sequence": {"name": "main", "clips": []},
    }
    monkeypatch.setattr(cli_module, "FilmCraftRunner", lambda: mock_runner)

    exit_code = main(["video", "inspect", str(fcproj), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project"]["name"] == "demo"
    mock_runner.inspect.assert_called_once()


def _review_project(tmp_path: Path):
    """A project whose out/video holds a master render and a decoy proxy."""
    from media_studio.manifest import Brand, Budget, Deliverable, Project

    out_video = tmp_path / "out" / "video"
    out_video.mkdir(parents=True)
    master = out_video / "main.mp4"
    master.write_text("fake master")
    (out_video / "main-proxy.mp4").write_text("fake decoy proxy")
    project = Project(
        root=tmp_path,
        slug="review-proj",
        title="Review Project",
        status="draft",
        deliverables=(Deliverable(id="main", kind="video", resolution="1920x1080", fps=30.0),),
        brand=Brand(),
        budget=Budget(),
        assets={},
        timeline=(),
        video=None,
        design=None,
        effects=(),
        raw={},
    )
    return project, master


def test_video_cli_review_subparsers_registered() -> None:
    parser = cli_module.build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    video_parser = subparsers_action.choices["video"]
    video_sub_action = next(
        a for a in video_parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    for cmd in ["proxy", "stills", "transcript", "captions", "remove-fillers", "beat-sync"]:
        assert cmd in video_sub_action.choices


def test_video_proxy_resolves_master_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, master = _review_project(tmp_path)
    monkeypatch.setattr(cli_module, "load_project", lambda _p: project)

    calls: dict[str, object] = {}

    def fake_proxy(source: Path, destination: Path, *, height: int = 540) -> Path:
        calls["args"] = (source, destination, height)
        destination.write_text("proxy")
        return destination

    monkeypatch.setattr(cli_module, "generate_proxy", fake_proxy)

    exit_code = main(["video", "proxy", str(tmp_path)])
    assert exit_code == 0
    source, destination, height = calls["args"]  # type: ignore[index]
    assert source == master  # the decoy proxy must never be picked
    assert destination == tmp_path / "out" / "main-proxy.mp4"
    assert height == 540
    assert "proxy main.mp4" in capsys.readouterr().out


def test_video_proxy_explicit_file_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, _master = _review_project(tmp_path)
    monkeypatch.setattr(cli_module, "load_project", lambda _p: project)
    explicit = tmp_path / "somewhere-else.mp4"
    explicit.write_text("explicit source")

    seen: list[Path] = []
    monkeypatch.setattr(
        cli_module,
        "generate_proxy",
        lambda source, destination, *, height=540: seen.append(source) or destination,
    )

    exit_code = main(["video", "proxy", str(tmp_path), "--file", str(explicit)])
    assert exit_code == 0
    assert seen == [explicit]
    assert f"proxy {explicit.name}" in capsys.readouterr().out


def test_video_stills_percent_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, master = _review_project(tmp_path)
    monkeypatch.setattr(cli_module, "load_project", lambda _p: project)

    probe_info = MagicMock()
    probe_info.duration = 10.0
    monkeypatch.setattr(cli_module, "probe", lambda _p: probe_info)

    calls: dict[str, object] = {}

    def fake_stills(source: Path, destination_dir: Path, timestamps=None):
        calls["args"] = (source, destination_dir, timestamps)
        return [destination_dir / "still_1.00s.png", destination_dir / "still_9.00s.png"]

    monkeypatch.setattr(cli_module, "extract_stills", fake_stills)

    exit_code = main(["video", "stills", str(tmp_path), "--at", "10,90"])
    assert exit_code == 0
    source, _destination, timestamps = calls["args"]  # type: ignore[index]
    assert source == master
    assert timestamps == [1.0, 9.0]  # percentages of the 10 s duration
    assert "stills main.mp4" in capsys.readouterr().out


def test_video_proxy_without_render_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from media_studio.manifest import Brand, Budget, Deliverable, Project

    project = Project(
        root=tmp_path,
        slug="empty-proj",
        title="Empty",
        status="draft",
        deliverables=(Deliverable(id="main", kind="video", resolution="1920x1080", fps=30.0),),
        brand=Brand(),
        budget=Budget(),
        assets={},
        timeline=(),
        video=None,
        design=None,
        effects=(),
        raw={},
    )
    monkeypatch.setattr(cli_module, "load_project", lambda _p: project)

    exit_code = main(["video", "proxy", str(tmp_path)])
    assert exit_code == 1
    assert "no rendered deliverable" in capsys.readouterr().err
