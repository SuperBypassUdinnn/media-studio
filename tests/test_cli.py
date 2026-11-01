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
