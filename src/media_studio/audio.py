"""
Audio utilities for media-studio.

Loudness normalization, beat detection, sidechain ducking, and silence removal
via FFmpeg / ffprobe and optional librosa.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import media


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


def measure_loudness(audio_path: Path) -> dict[str, float]:
    """
    Measure integrated loudness (LUFS), true peak (dBTP), and LRA of an audio file.

    Returns a dict with keys: integrated, true_peak, lra, threshold.
    """
    # Delegate to the shared loudnorm invocation and JSON parser in media.py so
    # there is exactly one implementation of ffmpeg's loudnorm first pass.
    loudness = media.measure_loudness(audio_path, target=-23.0)
    return {
        "integrated": loudness.integrated_lufs,
        "true_peak": loudness.true_peak_dbtp,
        "lra": loudness.loudness_range,
        "threshold": loudness.threshold,
    }


def normalize_loudness(
    input_path: Path,
    output_path: Path,
    target_lufs: float = -14.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> Path:
    """Two-pass EBU R128 loudness normalization via ffmpeg loudnorm filter."""
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
) -> dict[str, float | list[float]]:
    """
    Detect beat timestamps (in seconds) and BPM using librosa if available,
    falling back to duration/tempo estimator via ffmpeg.
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
        return _ffmpeg_beat_fallback(audio_path, min_bpm, max_bpm)


def analyze_rhythm(
    audio_path: Path,
    *,
    window: float = 0.5,
) -> dict:
    """
    Analyze the rhythm of an audio file using librosa.

    Returns a dict with keys: bpm, beats, downbeats, energy, window, duration,
    sections. Times are rounded to 4 decimals and every numeric is a plain
    Python float/int so the result is JSON-serializable.

    Each section is a dict with keys: start, end (seconds), level ("high" |
    "low"). Section boundaries fall on window multiples; a run is "high" when
    its RMS energy is above the 60th percentile of all windows.
    """
    if not audio_path.exists():
        raise AudioError(f"Audio file not found: {audio_path}")
    try:
        import librosa  # type: ignore[import]
        import numpy as np  # type: ignore[import]
    except ImportError as error:
        raise AudioError(
            f'librosa is not installed; install with: pip install -e ".[rhythm]" ({error})'
        ) from error
    try:
        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        if len(beat_frames) == 0:
            onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
            tempo, beat_frames = librosa.beat.beat_track(
                onset_envelope=onset_env, sr=sr, hop_length=512
            )
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        bpm = float(np.ravel(tempo)[0])
        beats = [round(float(t), 4) for t in beat_times]

        hop_length = int(window * sr)
        frame_length = 2 * hop_length
        rms = librosa.feature.rms(y=y, hop_length=hop_length, frame_length=frame_length)[0]
        energy = [round(float(v), 4) for v in rms.tolist()]
        duration = round(len(y) / sr, 4)

        sections: list[dict] = []
        if energy:
            threshold = float(np.percentile(energy, 60))
            run_start = 0
            run_high = energy[0] > threshold
            for i in range(1, len(energy)):
                high = energy[i] > threshold
                if high != run_high:
                    sections.append(
                        {
                            "start": round(run_start * window, 4),
                            "end": round(i * window, 4),
                            "level": "high" if run_high else "low",
                        }
                    )
                    run_start = i
                    run_high = high
            sections.append(
                {
                    "start": round(run_start * window, 4),
                    "end": round(len(energy) * window, 4),
                    "level": "high" if run_high else "low",
                }
            )

        downbeats: list[float] = []
        if beats and energy:
            highest_window_start = float(energy.index(max(energy))) * window
            phase = min(range(len(beats)), key=lambda i: abs(beats[i] - highest_window_start)) % 4
            downbeats = [round(float(t), 4) for t in beats[phase::4]]

        return {
            "bpm": round(bpm, 2),
            "beats": beats,
            "downbeats": downbeats,
            "energy": energy,
            "window": float(window),
            "duration": duration,
            "sections": sections,
        }
    except AudioError:
        raise
    except Exception as error:
        raise AudioError(f"rhythm analysis failed for {audio_path}: {error}") from error


def _ffmpeg_beat_fallback(
    audio_path: Path,
    min_bpm: float = 60.0,
    max_bpm: float = 200.0,
) -> dict[str, float | list[float]]:
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(audio_path)],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise AudioError(f"ffprobe failed for {audio_path}:\n{probe.stderr[-1000:]}")
    data = json.loads(probe.stdout)
    duration = float(data["format"]["duration"])

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
    """Sidechain ducking: attenuate music when voice is present."""
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
    """Remove silent portions from an audio file using ffmpeg silenceremove."""
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


def _deepfilter_available() -> bool:
    """True when DeepFilterNet is importable or its CLI is on PATH."""

    try:  # pragma: no cover - environment probe
        import deepfilternet  # noqa: F401

        return True
    except ImportError:
        pass
    return shutil.which("deepFilter") is not None


def _ffmpeg_voice_chain(source: Path, output: Path) -> None:
    """Deterministic broadcast vocal-cleanup chain via FFmpeg."""

    filter_str = (
        "highpass=f=85,"
        "afftdn=nf=-25,"
        "acompressor=threshold=0.05:ratio=4:attack=200:release=500:makeup=1,"
        "equalizer=f=3000:t=h:w=200:g=3"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-af",
            filter_str,
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )


def clean_voiceover(
    source: Path,
    *,
    output: Path,
    engine: str = "auto",
    target_lufs: float = -16.0,
) -> Path:
    """Clean up a user-recorded voiceover.

    Uses DeepFilterNet when available, otherwise a deterministic FFmpeg broadcast
    voice chain (highpass, afftdn, acompressor, equalizer presence boost) followed
    by two-pass loudness normalization to ``target_lufs``.
    """

    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    chosen = engine
    if engine == "auto":
        chosen = "deepfilter" if _deepfilter_available() else "ffmpeg"

    if chosen == "deepfilter":
        return _clean_deepfilter(source, output, target_lufs)
    if chosen == "ffmpeg":
        return _clean_ffmpeg(source, output, target_lufs)
    raise AudioError(f"unknown clean engine {engine!r}")


def _clean_deepfilter(source: Path, output: Path, target_lufs: float) -> Path:
    """Run DeepFilterNet via its venv python, then normalize loudness."""

    import subprocess as _sp

    venv = Path.home() / ".clone" / "Github" / "DeepFilterNet" / ".venv"
    python = venv / "bin" / "python"
    tmp = output.with_suffix(".df.wav")
    try:
        completed = _sp.run(
            [str(python), "-m", "deepfilternet", "filter", str(source), "-o", str(tmp)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AudioError(
                f"DeepFilterNet failed (exit {completed.returncode}):\n{completed.stderr[-1000:]}"
            )
        normalize_loudness(tmp, output, target_lufs=target_lufs)
    finally:
        tmp.unlink(missing_ok=True)
    return output


def _clean_ffmpeg(source: Path, output: Path, target_lufs: float) -> Path:
    """FFmpeg broadcast voice chain then two-pass loudness normalization."""

    staged = output.with_suffix(".stage.wav")
    try:
        _ffmpeg_voice_chain(source, staged)
        normalize_loudness(staged, output, target_lufs=target_lufs)
    finally:
        staged.unlink(missing_ok=True)
    return output
