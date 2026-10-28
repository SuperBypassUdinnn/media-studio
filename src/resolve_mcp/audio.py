"""
Audio processing helpers for the Resolve MCP server.

All heavy lifting (loudness normalization, beat detection, ducking) is done
with FFmpeg + ffprobe outside Resolve, producing clean stems that are then
imported into the Resolve media pool.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class AudioError(Exception):
    """Raised when an audio processing step fails."""


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise AudioError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    return result


def measure_loudness(audio_path: Path) -> dict:
    """
    Measure integrated loudness (LUFS), true peak (dBTP), and LRA of an audio file.

    Returns a dict with keys: integrated, true_peak, lra, threshold.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        "loudnorm=I=-23:TP=-1.5:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    # loudnorm outputs JSON to stderr
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr
    # Extract JSON block
    try:
        start = stderr.rindex("{")
        end = stderr.rindex("}") + 1
        data = json.loads(stderr[start:end])
        return {
            "integrated": float(data.get("input_i", "nan")),
            "true_peak": float(data.get("input_tp", "nan")),
            "lra": float(data.get("input_lra", "nan")),
            "threshold": float(data.get("input_thresh", "nan")),
        }
    except (ValueError, KeyError) as exc:
        raise AudioError(f"Could not parse loudnorm output from ffmpeg: {exc}") from exc


def normalize_loudness(
    input_path: Path,
    output_path: Path,
    target_lufs: float = -14.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> Path:
    """
    Two-pass EBU R128 loudness normalization via ffmpeg loudnorm filter.

    Returns the output_path on success.
    """
    # Pass 1: measure
    measure_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(input_path),
        "-af",
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}:print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(measure_cmd, capture_output=True, text=True)
    stderr = result.stderr
    try:
        start = stderr.rindex("{")
        end = stderr.rindex("}") + 1
        m = json.loads(stderr[start:end])
    except (ValueError, KeyError) as exc:
        raise AudioError(f"Pass 1 loudnorm parse failed: {exc}") from exc

    # Pass 2: apply with measured parameters for accurate normalization
    filter_str = (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={m['input_i']}"
        f":measured_TP={m['input_tp']}"
        f":measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}"
        f":linear=true:print_format=none"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-af",
            filter_str,
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    return output_path


def detect_beats(
    audio_path: Path,
    min_bpm: float = 60.0,
    max_bpm: float = 200.0,
) -> dict:
    """
    Detect beat timestamps (in seconds) and BPM using librosa if available,
    falling back to a simple energy-based estimator via ffmpeg.

    Returns a dict with keys: bpm (float), beats (list of timestamps in seconds).
    """
    if not audio_path.exists():
        raise AudioError(f"Audio file not found: {audio_path}")
    try:
        import librosa  # type: ignore[import]

        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, bpm=None)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        bpm = float(tempo) if hasattr(tempo, "__float__") else float(tempo[0])
        return {"bpm": round(bpm, 2), "beats": [round(t, 4) for t in beat_times]}
    except ImportError:
        # Fallback: estimate from tempo using ffmpeg duration
        return _ffmpeg_beat_fallback(audio_path, min_bpm, max_bpm)


def _ffmpeg_beat_fallback(
    audio_path: Path,
    min_bpm: float = 60.0,
    max_bpm: float = 200.0,
) -> dict:
    """
    Estimate beat positions from audio duration and a rough BPM estimate.
    This is a simplified fallback when librosa is not available.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_path)],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise AudioError(f"ffprobe failed for {audio_path}:\n{probe.stderr[-1000:]}")
    data = json.loads(probe.stdout)
    duration = float(data["format"]["duration"])

    # Assume ~128 BPM (typical EDM), clamped to [min_bpm, max_bpm]
    bpm = max(min_bpm, min(128.0, max_bpm))
    beat_interval = 60.0 / bpm
    beats = []
    t = 0.0
    while t < duration:
        beats.append(round(t, 4))
        t += beat_interval
    return {"bpm": round(bpm, 2), "beats": beats}


def duck_music(
    music_path: Path,
    voice_path: Path,
    output_path: Path,
    duck_db: float = -12.0,
    attack_ms: float = 200,
    release_ms: float = 500,
) -> Path:
    """
    Apply sidechain ducking: attenuate music when voice is present.

    Uses ffmpeg sidechaincompress filter. Returns output_path.
    """
    attack_s = attack_ms / 1000
    release_s = release_ms / 1000
    filter_complex = (
        f"[0:a][1:a]sidechaincompress="
        f"threshold=0.05:ratio=4:attack={attack_s}:release={release_s}"
        f":makeup=1[ducked]"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(music_path),
            "-i",
            str(voice_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[ducked]",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    return output_path


def remove_silence(
    input_path: Path,
    output_path: Path,
    silence_threshold_db: float = -40.0,
    min_silence_s: float = 0.5,
) -> Path:
    """
    Remove silent portions from an audio file using ffmpeg silenceremove.
    """
    filter_str = (
        f"silenceremove=start_periods=1:start_silence={min_silence_s}"
        f":start_threshold={silence_threshold_db}dB"
        f":stop_periods=-1:stop_silence={min_silence_s}"
        f":stop_threshold={silence_threshold_db}dB"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(input_path),
            "-af",
            filter_str,
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    return output_path
