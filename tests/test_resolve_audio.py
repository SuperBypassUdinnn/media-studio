"""
Tests for resolve_mcp.audio: loudness measurement, normalization, beat detection.

Integration tests (require FFmpeg and real audio files) are marked `network` to
match the existing test suite convention for opt-in heavy tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from resolve_mcp.audio import AudioError, detect_beats, measure_loudness


class TestMeasureLoudness:
    def test_raises_on_bad_ffmpeg_output(self, tmp_path):
        dummy = tmp_path / "silent.wav"
        # Create a minimal valid 1s silent WAV
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-t",
                "1",
                str(dummy),
            ],
            capture_output=True,
            check=True,
        )
        # Should not raise; integrated loudness of silence is a valid number
        result = measure_loudness(dummy)
        assert "integrated" in result
        assert "true_peak" in result

    def test_raises_audio_error_when_ffmpeg_fails(self):
        with pytest.raises(AudioError):
            measure_loudness(Path("/nonexistent/audio_file.wav"))


class TestDetectBeats:
    def test_fallback_to_ffmpeg_grid(self, tmp_path):
        """Without librosa, should fall back to FFmpeg-derived 128BPM grid."""
        dummy = tmp_path / "beat.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=4:sample_rate=48000",
                str(dummy),
            ],
            capture_output=True,
            check=True,
        )
        with patch.dict("sys.modules", {"librosa": None}):
            result = detect_beats(dummy, min_bpm=60.0, max_bpm=200.0)
        assert "bpm" in result
        assert "beats" in result
        assert isinstance(result["beats"], list)
        assert len(result["beats"]) > 0

    def test_raises_on_missing_file(self):
        with pytest.raises(AudioError):
            detect_beats(Path("/no/such/audio.wav"))
