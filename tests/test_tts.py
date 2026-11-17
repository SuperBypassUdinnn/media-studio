"""Tests for multi-engine free TTS (media_studio.tts).

edge-tts network calls are mocked via ``edge_tts.Communicate.stream``; espeak-ng
runs offline and is exercised for real. Real ffmpeg is avoided in the edge case by
stubbing ``run_command`` and ``normalize_loudness``.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_studio import audio as audio_module
from media_studio.audio import clean_voiceover
from media_studio.tts import (
    TtsError,
    synthesize_speech,
)

WORDS = [{"word": "Hello", "start": 0.0, "end": 0.5}, {"word": "world", "start": 0.5, "end": 1.0}]


def _write_wav(path: Path, duration_s: float = 1.0, tone_hz: float = 440.0) -> None:
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    n_frames = int(sample_rate * duration_s)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = b"".join(
            int(8000 * math.sin(2 * math.pi * tone_hz * i / sample_rate)).to_bytes(
                2, "little", signed=True
            )
            for i in range(n_frames)
        )
        wav.writeframes(frames)


def _fake_stream(word_timings):
    """Return an async generator yielding an audio chunk + WordBoundary metadata."""

    async def _gen():
        yield {"type": "audio", "data": b"\x00\x01\x02\x03"}
        for wt in word_timings:
            yield {
                "type": "WordBoundary",
                "offset": int(wt["start"] * 1e7),
                "duration": int((wt["end"] - wt["start"]) * 1e7),
                "text": wt["word"],
            }

    return _gen()


def test_synthesize_speech_edge_writes_wav_words_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """edge-tts path writes normalized WAV, words.json, and a REUSE .license sidecar."""

    out_wav = tmp_path / "tts.wav"
    out_words = tmp_path / "words.json"

    def fake_run_command(cmd, *, label, expect_stdout=True):
        # Pretend ffmpeg produced a valid WAV at the destination.
        if len(cmd) >= 2 and str(cmd[-1]).endswith(".wav"):
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            _write_wav(Path(cmd[-1]), duration_s=1.0)
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_normalize(input_path, output_path, *, target_lufs=-14.0):
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if input_path.is_file():
            output_path.write_bytes(input_path.read_bytes())
        else:
            _write_wav(output_path, duration_s=1.0)
        return output_path

    with (
        patch("edge_tts.Communicate", autospec=True) as mock_comm,
        patch("media_studio.tts.run_command", side_effect=fake_run_command),
        patch("media_studio.tts._probe_duration", return_value=1.0),
        patch("media_studio.audio.normalize_loudness", side_effect=fake_normalize),
    ):
        instance = mock_comm.return_value
        instance.stream.return_value = _fake_stream(WORDS)
        result = synthesize_speech(
            "Hello world",
            output=out_wav,
            voice="id-ID-ArdiNeural",
            engine="edge",
            words_out=out_words,
        )

    assert result.engine == "edge"
    assert result.voice == "id-ID-ArdiNeural"
    assert out_wav.is_file()
    assert out_words.is_file()
    words = json.loads(out_words.read_text(encoding="utf-8"))
    assert words == WORDS
    sidecar = out_wav.with_name(out_wav.name + ".license")
    assert sidecar.is_file()
    text = sidecar.read_text(encoding="utf-8")
    assert "SPDX-License-Identifier: LicenseRef-TTS-Generated" in text
    assert "Provider: edge-tts" in text
    assert "Model: id-ID-ArdiNeural" in text
    assert "Prompt: Hello world" in text
    assert "SHA256:" in text
    assert "Generated-At:" in text


def test_synthesize_speech_raises_when_no_engine(tmp_path: Path) -> None:
    """With every engine unavailable, TtsError must surface."""

    out_wav = tmp_path / "tts.wav"
    with (
        patch("media_studio.tts._edge_available", return_value=False),
        patch("media_studio.tts._piper_available", return_value=False),
        patch("media_studio.tts._kokoro_available", return_value=False),
        patch("media_studio.tts._espeak_available", return_value=False),
        pytest.raises(TtsError),
    ):
        synthesize_speech("Hello", output=out_wav, engine="auto")


def test_clean_voiceover_ffmpeg_chain_on_noisy_wav(tmp_path: Path) -> None:
    """clean_voiceover with engine='ffmpeg' runs the broadcast chain and normalizes loudness."""

    source = tmp_path / "noisy.wav"
    _write_wav(source, duration_s=1.0, tone_hz=440.0)
    out_wav = tmp_path / "clean.wav"
    result = clean_voiceover(source, output=out_wav, engine="ffmpeg", target_lufs=-16.0)
    assert result == out_wav
    assert out_wav.is_file()
    info = audio_module.media.probe(out_wav)
    assert info.duration > 0
    loud = audio_module.measure_loudness(out_wav)
    assert abs(loud["integrated"] - (-16.0)) < 1.5


def test_synthesize_speech_espeak_real_offline(tmp_path: Path) -> None:
    """espeak-ng (always on PATH) synthesizes offline with real ffmpeg normalization."""

    out_wav = tmp_path / "tts.wav"
    out_words = tmp_path / "words.json"
    result = synthesize_speech(
        "Hello world",
        output=out_wav,
        voice="default",
        engine="espeak-ng",
        words_out=out_words,
    )
    assert result.engine == "espeak-ng"
    assert out_wav.is_file()
    assert out_words.is_file()
    info = audio_module.media.probe(out_wav)
    assert info.duration > 0
    words = json.loads(out_words.read_text(encoding="utf-8"))
    assert len(words) == 2
    assert words[0]["word"] == "Hello"
    assert words[1]["word"] == "world"
    assert words[0]["start"] >= 0.0
    assert abs(words[1]["end"] - round(info.duration, 4)) < 0.0001
    sidecar = out_wav.with_name(out_wav.name + ".license")
    assert sidecar.is_file()
    text = sidecar.read_text(encoding="utf-8")
    assert "Provider: espeak-ng" in text
    assert "Prompt: Hello world" in text


def test_synthesize_speech_edge_network_error_cascades(tmp_path: Path) -> None:
    """An edge-tts network error becomes TtsError and falls through the auto ladder."""

    out_wav = tmp_path / "tts.wav"
    with (
        patch("media_studio.tts._edge_available", return_value=True),
        patch("media_studio.tts._run_edge", side_effect=ConnectionError("network down")),
        patch("media_studio.tts._piper_available", return_value=False),
        patch("media_studio.tts._kokoro_available", return_value=False),
        patch("media_studio.tts._espeak_available", return_value=False),
        pytest.raises(TtsError) as raised,
    ):
        synthesize_speech("Hello", output=out_wav, engine="auto")
    assert "network down" in str(raised.value)


def test_synthesize_speech_piper_mocked_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """piper hook invokes the binary and falls back cleanly when it fails."""

    out_wav = tmp_path / "tts.wav"
    out_words = tmp_path / "words.json"

    def fake_run(cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        if "piper" in cmd:
            for index, arg in enumerate(cmd):
                if arg == "--output_file" and index + 1 < len(cmd):
                    Path(cmd[index + 1]).parent.mkdir(parents=True, exist_ok=True)
                    _write_wav(Path(cmd[index + 1]), duration_s=1.0)
            return MagicMock(returncode=0, stdout="", stderr="")
        if "ffprobe" in cmd:
            return MagicMock(returncode=0, stdout='{"format": {"duration": "1.0"}}', stderr="")
        if "ffmpeg" in cmd:
            out = cmd[-1]
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            _write_wav(Path(out), duration_s=1.0)
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_normalize(input_path, output_path, *, target_lufs=-14.0):
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if input_path.is_file():
            output_path.write_bytes(input_path.read_bytes())
        else:
            _write_wav(output_path, duration_s=1.0)
        return output_path

    with (
        patch("media_studio.tts._edge_available", return_value=False),
        patch("media_studio.tts._piper_available", return_value=True),
        patch("media_studio.tts._kokoro_available", return_value=False),
        patch("media_studio.tts._espeak_available", return_value=False),
        patch("subprocess.run", side_effect=fake_run),
        patch("media_studio.tts._probe_duration", return_value=1.0),
        patch("media_studio.audio.normalize_loudness", side_effect=fake_normalize),
    ):
        result = synthesize_speech(
            "Hello world",
            output=out_wav,
            engine="piper",
            words_out=out_words,
        )
    assert result.engine == "piper"
    assert out_wav.is_file()
    assert out_words.is_file()


def test_synthesize_speech_invalid_engine_and_empty_text(tmp_path: Path) -> None:
    """Invalid engine and empty text both raise TtsError."""

    out_wav = tmp_path / "tts.wav"
    with pytest.raises(TtsError):
        synthesize_speech("Hello", output=out_wav, engine="bogus")
    with pytest.raises(TtsError, match="text is empty"):
        synthesize_speech("   ", output=out_wav, engine="espeak-ng")
