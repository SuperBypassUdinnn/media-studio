"""Unit tests for the FilmCraft runner core."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest import mock

import pytest

from media_studio.filmcraft import (
    FilmCraftCommandError,
    FilmCraftError,
    FilmCraftOfflineMediaError,
    FilmCraftRunner,
    command_exists,
    resolve_binary,
)


@pytest.fixture(autouse=True)
def _stub_filmcraft_binary(monkeypatch):
    if shutil.which("filmcraft-cli") is None and shutil.which("filmcraft") is None:
        monkeypatch.setenv("MS_FILMCRAFT_BIN", "/bin/true")


def test_filmcraft_runner_init_custom_binary(tmp_path):
    custom_bin = tmp_path / "custom-cli"
    custom_bin.touch()
    custom_bin.chmod(0o755)
    runner = FilmCraftRunner(cli_path=custom_bin)
    assert runner.cli_path == custom_bin


def test_resolve_binary_env_override(tmp_path, monkeypatch):
    custom_bin = tmp_path / "env-cli"
    custom_bin.touch()
    custom_bin.chmod(0o755)
    monkeypatch.setenv("MS_FILMCRAFT_BIN", str(custom_bin))
    assert resolve_binary() == custom_bin
    assert command_exists() is True


def test_resolve_binary_missing_raises(monkeypatch):
    monkeypatch.delenv("MS_FILMCRAFT_BIN", raising=False)
    monkeypatch.setattr("media_studio.filmcraft.PINNED_BIN", Path("/nonexistent/filmcraft-cli"))
    monkeypatch.setattr("media_studio.filmcraft.ENGINES_CONFIG", Path("/nonexistent/engines.json"))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(FilmCraftError):
        resolve_binary()
    assert command_exists() is False


def test_get_commands_catalog_success(tmp_path, monkeypatch):
    runner = FilmCraftRunner()
    fake_catalog = [
        {"id": "file.newProject", "label": "New Project", "params": "{}"},
        {"id": "timeline.place", "label": "Place Clip", "params": "{}"},
    ]
    # Isolate the on-disk catalogue cache so tests never poison the real one.
    monkeypatch.setattr("media_studio.filmcraft.CATALOG_CACHE_DIR", tmp_path)

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps(fake_catalog),
            stderr="",
        )
        catalog = runner.get_commands_catalog(cached=True)
        assert len(catalog) == 2
        assert catalog[0]["id"] == "file.newProject"
        assert runner._commands_cache is not None

        # Second call with cached=True should not run subprocess again
        catalog2 = runner.get_commands_catalog(cached=True)
        assert len(catalog2) == 2
        # 2 total calls: one --version probe (cache key) + one commands fetch;
        # the second get_commands_catalog is served from the in-memory cache.
        assert mock_run.call_count == 2


def test_exec_single_command_success(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "sequence": 7}),
            stderr="MESA-EGL: warning: DRI2: failed to create screen\n",
        )
        res = runner.exec("file.newSequence", {"fps": 30}, project_path=project)

        assert res.command == "file.newSequence"
        assert res.ok is True
        assert res.result == {"ok": True, "sequence": 7}
        # DRI2 warning filtered
        assert "DRI2" not in res.stderr

        call_args = mock_run.call_args[0][0]
        assert "--project" in call_args
        assert str(project) in call_args
        assert "--save" in call_args
        assert "file.newSequence" in call_args
        assert '{"fps": 30}' in call_args


def test_exec_offline_media_error(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Error: clip 10 has offline media /missing/clip.mp4",
        )
        with pytest.raises(FilmCraftOfflineMediaError) as exc_info:
            runner.exec("timeline.place", {"item": 10}, project_path=project)
        assert "offline media" in str(exc_info.value).lower()


def test_exec_command_error(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Command parameter error: missing track",
        )
        with pytest.raises(FilmCraftCommandError) as exc_info:
            runner.exec("timeline.place", {}, project_path=project)
        assert "missing track" in str(exc_info.value)


def test_run_batch_json_lines(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()

    commands = [
        {"command": "file.newSequence", "params": {"fps": 30}},
        {"command": "timeline.place", "params": {"track": "V1", "seconds": 0}},
    ]

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout='{"ok": true, "sequence": 1}\n{"ok": true, "clip": 2}\n',
            stderr="",
        )
        results = runner.run_batch(commands, project_path=project)
        assert len(results) == 2
        assert results[0].command == "file.newSequence"
        assert results[1].command == "timeline.place"

        stdin_data = mock_run.call_args[1]["input"]
        lines = [line for line in stdin_data.split("\n") if line]
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"id": "file.newSequence", "params": {"fps": 30}}
        assert json.loads(lines[1]) == {
            "id": "timeline.place",
            "params": {"track": "V1", "seconds": 0},
        }


def test_render_frame(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()
    out_png = tmp_path / "frame.png"

    with mock.patch("subprocess.run") as mock_run:
        # Simulate creating the png file in subprocess
        def side_effect(*args, **kwargs):
            out_png.touch()
            return mock.Mock(returncode=0, stdout="Frame rendered in 150ms\n", stderr="")

        mock_run.side_effect = side_effect

        res = runner.render_frame(project, out_png, seconds=2.5, scale=0.5)
        assert res.output == out_png
        assert res.seconds == 2.5
        assert res.scale == 0.5


def test_export_gpu_success(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()
    out_mp4 = tmp_path / "out.mp4"

    with mock.patch("subprocess.run") as mock_run:

        def side_effect(*args, **kwargs):
            out_mp4.touch()
            return mock.Mock(
                returncode=0, stdout="Export complete 150 frames at 82 fps\n", stderr=""
            )

        mock_run.side_effect = side_effect

        res = runner.export(project, out_mp4, format="h264", gpu="auto", loudness_lufs=-14.0)
        assert res.output == out_mp4
        assert res.render_path == "gpu"
        assert res.gpu_fallback_to_cpu is False

        call_args = mock_run.call_args[0][0]
        assert "--gpu-rendering" in call_args
        idx = call_args.index("--gpu-rendering")
        assert call_args[idx + 1] == "auto"
        assert "exec" in call_args
        idx_exec = call_args.index("exec")
        assert call_args[idx_exec + 1] == "file.exportMedia"
        params = json.loads(call_args[idx_exec + 2])
        assert params["loudnessLufs"] == -14.0
        assert params["wait"] is True


def test_export_gpu_failure_falls_back_to_cpu(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()
    out_mp4 = tmp_path / "out.mp4"

    call_count = 0

    with mock.patch("subprocess.run") as mock_run:

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock.Mock(
                    returncode=1,
                    stdout="",
                    stderr="wgpu error: Device lost / out of memory",
                )
            out_mp4.touch()
            return mock.Mock(
                returncode=0,
                stdout="CPU Export complete 150 frames at 58 fps\n",
                stderr="",
            )

        mock_run.side_effect = side_effect

        res = runner.export(project, out_mp4, format="h264", gpu="auto")
        assert res.output == out_mp4
        assert res.render_path == "cpu"
        assert res.gpu_fallback_to_cpu is True
        assert call_count == 2

        # Second call should have had --gpu-rendering off
        second_call_args = mock_run.call_args_list[1][0][0]
        idx = second_call_args.index("--gpu-rendering")
        assert second_call_args[idx + 1] == "off"


def test_inspect_project_and_sequence(tmp_path):
    runner = FilmCraftRunner()
    project = tmp_path / "test.fcproj"
    project.touch()

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps({"sequences": [{"id": 1, "name": "Main"}]}),
            stderr="",
        )
        data = runner.inspect(project, target="project")
        assert "sequences" in data
