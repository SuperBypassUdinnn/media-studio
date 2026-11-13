"""Voiceover cleanup profiles (broadcast vs tts) in media_studio.audio."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio import audio


def _make_wav(tmp_path: Path, name: str = "source.wav", duration: float = 1.0) -> Path:
    path = tmp_path / name
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
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _capture_chain(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None):
        commands.append(list(cmd))

        class _Done:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Done()

    monkeypatch.setattr(audio, "_run", fake_run)
    monkeypatch.setattr(
        audio,
        "normalize_loudness",
        lambda inp, out, **_kw: Path(out).write_bytes(b""),
    )
    return commands


def _af_filter(commands: list[list[str]]) -> str:
    return commands[-1][commands[-1].index("-af") + 1]


def test_clean_voiceover_broadcast_chain_keeps_afftdn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default broadcast profile keeps highpass + afftdn + compressor + EQ."""
    source = _make_wav(tmp_path)
    output = tmp_path / "out.wav"
    commands = _capture_chain(monkeypatch)

    result = audio.clean_voiceover(source, output=output, engine="ffmpeg", profile="broadcast")
    assert result == output

    af = _af_filter(commands)
    assert "highpass=f=85" in af
    assert "afftdn=nf=-25" in af
    assert "acompressor=threshold=0.05:ratio=4" in af
    assert "equalizer=f=3000" in af


def test_clean_voiceover_tts_profile_has_no_denoise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tts profile drops afftdn denoise; light EQ and peak control remain."""
    source = _make_wav(tmp_path)
    output = tmp_path / "out.wav"
    commands = _capture_chain(monkeypatch)

    audio.clean_voiceover(source, output=output, profile="tts")

    af = _af_filter(commands)
    assert "afftdn" not in af
    assert "highpass" in af
    assert "equalizer" in af


def test_clean_voiceover_unknown_profile_raises_audio_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown profile raises AudioError before touching ffmpeg."""
    source = _make_wav(tmp_path)
    output = tmp_path / "out.wav"
    commands = _capture_chain(monkeypatch)

    with pytest.raises(audio.AudioError, match="unknown voiceover profile"):
        audio.clean_voiceover(source, output=output, profile="weird")
    assert commands == [], "unknown profile must not invoke ffmpeg"
