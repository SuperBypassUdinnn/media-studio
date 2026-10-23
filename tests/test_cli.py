"""Tests for the command line surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import media_studio.cli as cli_module
from media_studio.cli import main
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
