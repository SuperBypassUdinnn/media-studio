from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from media_studio.audio import AudioError, analyze_rhythm
from media_studio.cli import main

NEEDS_LIBROSA = pytest.mark.skipif(
    importlib.util.find_spec("librosa") is None, reason="librosa not installed"
)

CLICK_BPM = 120.0
CLICK_INTERVAL = 0.5  # seconds between clicks (120 BPM)
CLICK_DURATION = 4.0
SAMPLE_RATE = 44100


def _write_click_wav(path: Path) -> None:
    """Write a 4 s 120 BPM click track: 30 ms 1000 Hz burst every 0.5 s over a
    quiet 440 Hz sine bed, 44100 Hz mono 16-bit."""
    n = int(CLICK_DURATION * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    signal = 0.05 * np.sin(2 * np.pi * 440.0 * t)  # quiet bed
    click_n = int(0.030 * SAMPLE_RATE)
    click_t = np.arange(click_n) / SAMPLE_RATE
    click = np.sin(2 * np.pi * 1000.0 * click_t) * np.hanning(click_n)
    start = 0.0
    while start < CLICK_DURATION:
        i = int(start * SAMPLE_RATE)
        end = min(i + click_n, n)
        signal[i:end] += click[: end - i]
        start += CLICK_INTERVAL
    peak = float(np.max(np.abs(signal)))
    if peak > 0:
        signal = signal / peak * 0.9
    pcm = (signal * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())


@NEEDS_LIBROSA
def test_analyze_rhythm_requires_librosa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "librosa", None)
    audio_file = tmp_path / "x.wav"
    audio_file.write_bytes(b"RIFF....WAVE")
    with pytest.raises(AudioError, match=r"librosa"):
        analyze_rhythm(audio_file)


@NEEDS_LIBROSA
def test_analyze_rhythm_click_track(tmp_path: Path):
    wav = tmp_path / "click.wav"
    _write_click_wav(wav)
    result = analyze_rhythm(wav)
    assert 0.9 <= result["bpm"] / CLICK_BPM <= 1.1 or 3.5 <= len(result["beats"]) <= 9
    assert len(result["downbeats"]) <= len(result["beats"])
    assert set(result["downbeats"]) <= set(result["beats"])
    assert abs(result["duration"] - CLICK_DURATION) < 0.2
    assert len(result["sections"]) >= 1
    for value in result["beats"] + result["energy"] + result["downbeats"]:
        assert type(value) is float


@NEEDS_LIBROSA
def test_beats_json_roundtrip(tmp_path: Path):
    wav = tmp_path / "click.wav"
    _write_click_wav(wav)
    result = analyze_rhythm(wav)
    json.dumps(json.loads(json.dumps(result)))


@NEEDS_LIBROSA
def test_cli_beats_writes_json(tmp_path: Path):
    wav = tmp_path / "click.wav"
    _write_click_wav(wav)
    out = tmp_path / "out" / "beats.json"
    code = main(["video", "beats", str(wav), "--out", str(out)])
    assert code == 0
    assert out.is_file()
    data = json.loads(out.read_text())
    assert {"bpm", "beats", "downbeats", "energy", "sections"} <= set(data.keys())
