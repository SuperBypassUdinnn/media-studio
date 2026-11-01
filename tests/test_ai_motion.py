from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_studio import ai, audio, effectcraft
from media_studio.cli import main
from media_studio.filmcraft import CommandResult


def test_measure_loudness_parses_json(tmp_path: Path):
    audio_file = tmp_path / "test.wav"
    audio_file.write_bytes(b"RIFF....WAVE")
    sample_stderr = """
[Parsed_loudnorm_0 @ 0x55555]
{
    "input_i" : "-15.80",
    "input_tp" : "-1.20",
    "input_lra" : "6.50",
    "input_thresh" : "-26.10"
}
"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=sample_stderr)
        stats = audio.measure_loudness(audio_file)
        assert stats["integrated"] == -15.80
        assert stats["true_peak"] == -1.20
        assert stats["lra"] == 6.50
        assert stats["threshold"] == -26.10


def test_detect_beats_fallback_ffmpeg(tmp_path: Path):
    audio_file = tmp_path / "beat.wav"
    audio_file.write_bytes(b"RIFF....WAVE")
    probe_stdout = json.dumps({"format": {"duration": "3.0"}})

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=probe_stdout, stderr="")
        res = audio.detect_beats(audio_file, min_bpm=120.0, max_bpm=120.0)
        assert res["bpm"] == 120.0
        beats = res["beats"]
        assert len(beats) > 0
        assert beats[0] == 0.0


def test_ai_import_transcript(tmp_path: Path):
    fcproj = tmp_path / "project.fcproj"
    fcproj.write_text("{}")
    transcript_file = tmp_path / "transcript.json"
    transcript_data = {
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.6, "end": 1.0},
        ]
    }
    transcript_file.write_text(json.dumps(transcript_data))

    mock_runner = MagicMock()
    mock_runner.exec.return_value = CommandResult(
        command="transcript.set",
        returncode=0,
        stdout='{"words": 2}',
        stderr="",
        result={"words": 2},
    )
    mock_runner.inspect.return_value = {
        "root": {"children": [{"item": 10, "name": "voice.wav", "type": "Audio"}]}
    }

    res = ai.import_transcript(fcproj, transcript_file, runner=mock_runner)
    assert res.ok
    mock_runner.exec.assert_called_once_with(
        "transcript.set",
        params={"transcript": transcript_data, "item": 10},
        project_path=fcproj,
        save=True,
    )


def test_ai_generate_captions(tmp_path: Path):
    fcproj = tmp_path / "project.fcproj"
    fcproj.write_text("{}")

    mock_runner = MagicMock()
    mock_runner.exec.return_value = CommandResult(
        command="transcript.createCaptions",
        returncode=0,
        stdout='{"captions": 10}',
        stderr="",
        result={"captions": 10},
    )

    res = ai.generate_captions(fcproj, runner=mock_runner)
    assert res.ok
    mock_runner.exec.assert_called_once_with(
        "transcript.createCaptions",
        project_path=fcproj,
        save=True,
    )


def test_ai_remove_fillers(tmp_path: Path):
    fcproj = tmp_path / "project.fcproj"
    fcproj.write_text("{}")

    mock_runner = MagicMock()
    mock_runner.exec.return_value = CommandResult(
        command="transcript.removeFillers",
        returncode=0,
        stdout='{"removed": 2, "rippled": 1.5}',
        stderr="",
        result={"removed": 2, "rippled": 1.5},
    )

    res = ai.remove_fillers(fcproj, fillers=["um", "ah"], runner=mock_runner)
    assert res.ok
    mock_runner.exec.assert_called_once_with(
        "transcript.removeFillers",
        params={"fillers": ["um", "ah"]},
        project_path=fcproj,
        save=True,
    )


def test_ai_snap_cuts_to_beats(tmp_path: Path):
    fcproj = tmp_path / "project.fcproj"
    fcproj.write_text("{}")
    music = tmp_path / "music.wav"
    music.write_bytes(b"RIFF")

    mock_runner = MagicMock()
    mock_runner.run_batch.return_value = [
        CommandResult(command="markers.add", returncode=0, stdout="{}", stderr=""),
        CommandResult(command="markers.add", returncode=0, stdout="{}", stderr=""),
    ]

    with patch("media_studio.ai.detect_beats", return_value={"bpm": 120.0, "beats": [0.5, 1.0]}):
        results = ai.snap_cuts_to_beats(fcproj, music, runner=mock_runner)
        assert len(results) == 2
        assert mock_runner.run_batch.called


def test_lower_third_generation(tmp_path: Path):
    out_mov = tmp_path / "lower.mov"
    out_mov.write_bytes(b"mov")
    ecp_file = tmp_path / "lower.ecproj"
    report_file = tmp_path / "report.json"
    report_file.write_text("{}")

    with patch("media_studio.effectcraft.render") as mock_render:
        mock_render.return_value = effectcraft.MotionResult(
            output=out_mov,
            ecp_path=ecp_file,
            report_path=report_file,
            report={"ok": True},
        )
        res = effectcraft.lower_third(
            title="Dr. Jane Doe",
            subtitle="Chief AI Officer",
            output=out_mov,
            ecp_path=ecp_file,
        )
        assert res.ok
        assert ecp_file.is_file()
        content = json.loads(ecp_file.read_text())
        layers = content.get("layers", [])
        assert layers[0]["params"]["text"] == "Dr. Jane Doe"
        assert layers[1]["params"]["text"] == "Chief AI Officer"


def test_cli_motion_and_ai_commands(tmp_path: Path):
    fcproj = tmp_path / "smoke.fcproj"
    fcproj.write_text("{}")
    transcript_file = tmp_path / "transcript.json"
    transcript_file.write_text('{"words": []}')

    with patch("media_studio.ai.import_transcript") as mock_import:
        mock_import.return_value = CommandResult(
            command="transcript.set", returncode=0, stdout="ok", stderr=""
        )
        code = main(["video", "transcript", str(fcproj), "--file", str(transcript_file)])
        assert code == 0
        assert mock_import.called


def _cut_transcript_file(tmp_path: Path) -> Path:
    transcript_file = tmp_path / "transcript.json"
    transcript_file.write_text(
        json.dumps(
            {
                "words": [
                    {"word": "um", "start": 0.0, "end": 0.4},
                    {"word": "hello", "start": 1.6, "end": 2.2},
                ]
            }
        )
    )
    return transcript_file


def _cut_runner(monkeypatch: pytest.MonkeyPatch, events: list[tuple[str, str]]):
    """Runner mock recording exec order; snapshot_timeline is stubbed by caller."""
    runner = MagicMock()
    responses = {
        "transcript.set": {"words": 2},
        "transcript.removeFillers": {"removed": 1},
        "transcript.removePauses": {"compressed": 1},
    }

    def fake_exec(cmd_id, params=None, **kwargs):
        events.append(("exec", cmd_id))
        return CommandResult(
            command=cmd_id,
            returncode=0,
            stdout="",
            stderr="",
            result=responses.get(cmd_id, {}),
            ok=True,
        )

    runner.exec.side_effect = fake_exec
    runner.inspect.return_value = {
        "root": {"children": [{"item": 10, "name": "voice.wav", "type": "Audio"}]}
    }
    monkeypatch.setattr(ai, "FilmCraftRunner", lambda *a, **k: runner)
    return runner


def test_cut_by_transcript_snapshots_before_changes(tmp_path, monkeypatch):
    """D46: the snapshot is archived before the first engine command runs."""
    fcproj = tmp_path / "project.fcproj"
    fcproj.write_text("{}")
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "media_studio.ai.snapshot_timeline",
        lambda p: events.append(("snapshot", str(p))),
    )
    _cut_runner(monkeypatch, events)

    ai.cut_by_transcript(fcproj, _cut_transcript_file(tmp_path), remove_fillers_words=True)

    kinds = [kind for kind, _ in events]
    assert kinds[0] == "snapshot"
    assert kinds[1:] == ["exec", "exec"]  # import, then fillers


def test_cut_by_transcript_pass_order_and_params(tmp_path, monkeypatch):
    fcproj = tmp_path / "project.fcproj"
    fcproj.write_text("{}")
    calls: list[tuple[str, dict | None]] = []
    runner = MagicMock()
    runner.exec.side_effect = lambda cmd_id, params=None, **kw: (
        calls.append((cmd_id, params))
        or CommandResult(command=cmd_id, returncode=0, stdout="", stderr="", result={}, ok=True)
    )
    runner.inspect.return_value = {
        "root": {"children": [{"item": 10, "name": "voice.wav", "type": "Audio"}]}
    }
    monkeypatch.setattr(ai, "FilmCraftRunner", lambda *a, **k: runner)
    monkeypatch.setattr("media_studio.ai.snapshot_timeline", lambda p: None)

    ai.cut_by_transcript(
        fcproj,
        _cut_transcript_file(tmp_path),
        remove_fillers_words=True,
        fillers=["erm"],
        remove_pauses=True,
        min_pause=0.9,
    )

    assert [c[0] for c in calls] == [
        "transcript.set",
        "transcript.removeFillers",
        "transcript.removePauses",
    ]
    assert calls[1][1] == {"fillers": ["erm"]}
    assert calls[2][1] == {"minSeconds": 0.9}


def test_cut_by_transcript_no_flags_imports_only(tmp_path, monkeypatch):
    fcproj = tmp_path / "project.fcproj"
    fcproj.write_text("{}")
    calls: list[str] = []
    runner = MagicMock()
    runner.exec.side_effect = lambda cmd_id, params=None, **kw: (
        calls.append(cmd_id)
        or CommandResult(command=cmd_id, returncode=0, stdout="", stderr="", result={}, ok=True)
    )
    runner.inspect.return_value = {
        "root": {"children": [{"item": 10, "name": "voice.wav", "type": "Audio"}]}
    }
    monkeypatch.setattr(ai, "FilmCraftRunner", lambda *a, **k: runner)
    monkeypatch.setattr("media_studio.ai.snapshot_timeline", lambda p: None)

    results = ai.cut_by_transcript(fcproj, _cut_transcript_file(tmp_path))

    assert calls == ["transcript.set"]
    assert len(results) == 1
