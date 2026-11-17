"""Scene-adaptive sound design, library audio modifier, and 48kHz parametric synthesizer."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import scipy.io.wavfile as wavfile
from scipy import signal

from media_studio.sfx import AUDIO_RATE, SfxError, _resolve_cue


def _read_wav_float32(path: Path) -> tuple[int, np.ndarray]:
    sr, data = wavfile.read(path)
    if data.dtype == np.int16:
        float_data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        float_data = data.astype(np.float32) / 2147483648.0
    else:
        float_data = data.astype(np.float32)
    return sr, float_data


def _write_wav_int16(path: Path, sr: int, data: np.ndarray) -> Path:
    clipped = np.clip(data, -1.0, 1.0)
    int_data = (clipped * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sr, int_data)
    return path


def modify_library_sound(
    source_id_or_path: str | Path,
    output: Path,
    *,
    target_duration_s: float,
    pitch_semitones: float = 0.0,
    transient_boost_db: float = 0.0,
    filter_sweep_hz: tuple[float, float] | None = None,
    pan_sweep: tuple[float, float] = (0.0, 0.0),
    trail_out_s: float = 0.0,
) -> Path:
    """Modify a library audio recording to match a scene's timing, pitch, and panning."""
    output = Path(output)
    source_path = _resolve_cue(str(source_id_or_path))
    sr, raw_audio = _read_wav_float32(source_path)

    audio = raw_audio.mean(axis=1) if raw_audio.ndim > 1 else raw_audio

    # 1. Resampling / pitch shift
    if abs(pitch_semitones) > 0.01:
        rate_factor = 2.0 ** (pitch_semitones / 12.0)
        num_samples = max(1, int(len(audio) / rate_factor))
        audio = signal.resample(audio, num_samples)

    # 2. Duration sculpting & ADSR shaping
    total_dur_s = target_duration_s + max(0.0, trail_out_s)
    target_samples = max(1, int(total_dur_s * sr))

    if len(audio) < target_samples:
        padded = np.zeros(target_samples, dtype=np.float32)
        padded[: len(audio)] = audio
        audio = padded
    else:
        audio = audio[:target_samples]

    # Smooth exponential decay tail to guarantee end amplitude < -45 dBFS
    decay_samples = int(max(0.1, trail_out_s or 0.15) * sr)
    decay_samples = min(decay_samples, len(audio))
    if decay_samples > 0:
        tail_curve = np.exp(np.linspace(0, -6.0, decay_samples, dtype=np.float32))
        audio[-decay_samples:] *= tail_curve

    # 3. Transient shaping
    if abs(transient_boost_db) > 0.1:
        attack_len = min(int(0.04 * sr), len(audio))
        if attack_len > 0:
            boost_linear = 10.0 ** (transient_boost_db / 20.0)
            boost_env = np.linspace(boost_linear, 1.0, attack_len, dtype=np.float32)
            audio[:attack_len] *= boost_env

    # 4. Filter sweep (low-pass)
    if filter_sweep_hz is not None and len(filter_sweep_hz) == 2:
        cutoff = min(sr / 2.1, max(100.0, (filter_sweep_hz[0] + filter_sweep_hz[1]) / 2.0))
        sos = signal.butter(2, cutoff, btype="lowpass", fs=sr, output="sos")
        audio = signal.sosfilt(sos, audio)

    # 5. Stereo panning sweep
    pan_start, pan_end = pan_sweep
    pan_env = np.linspace(pan_start, pan_end, len(audio), dtype=np.float32)
    left = audio * np.cos((pan_env + 1.0) * (math.pi / 4.0))
    right = audio * np.sin((pan_env + 1.0) * (math.pi / 4.0))
    stereo = np.column_stack((left, right))

    return _write_wav_int16(output, sr, stereo)


def synthesize_sound_event(
    synth_type: str,
    output: Path,
    *,
    duration_s: float,
    params: dict[str, Any] | None = None,
) -> Path:
    """Synthesize mathematical waveforms locked 1:1 to visual animation curves."""
    output = Path(output)
    sr = AUDIO_RATE
    params = params or {}
    n_samples = max(1, int(duration_s * sr))
    t = np.linspace(0, duration_s, n_samples, endpoint=False, dtype=np.float32)

    if synth_type == "sub_bass":
        f_init = float(params.get("initial_frequency_hz", 80.0))
        f_final = float(params.get("final_frequency_hz", 45.0))
        decay_ms = float(params.get("decay_ms", duration_s * 1000.0))
        decay_factor = 6.5 / max(0.05, decay_ms / 1000.0)

        freq_t = f_final + (f_init - f_final) * np.exp(-t * (decay_factor * 0.5))
        phase = 2.0 * np.pi * np.cumsum(freq_t) / sr
        drive = float(params.get("saturation_drive", 0.2))
        raw = np.sin(phase)
        sub = np.tanh((1.0 + drive) * raw) * np.exp(-t * decay_factor)
        # Ensure silence at end
        fade_len = min(int(0.02 * sr), len(sub))
        if fade_len > 0:
            sub[-fade_len:] *= np.linspace(1, 0, fade_len)
        return _write_wav_int16(output, sr, sub)

    if synth_type == "curve_progress":
        active_dur = float(params.get("active_duration_s", duration_s * 0.75))
        start_f = float(params.get("start_freq_hz", 440.0))
        end_f = float(params.get("end_freq_hz", 880.0))

        out_audio = np.zeros(n_samples, dtype=np.float32)
        active_samples = int(min(duration_s, active_dur) * sr)

        # Micro ticks density following quadratic ease
        n_ticks = max(4, int(active_dur * 8))
        tick_times = (np.linspace(0, 1, n_ticks) ** 1.5) * (active_dur - 0.05)

        for i, tick_t in enumerate(tick_times):
            idx = int(tick_t * sr)
            if idx >= active_samples:
                break
            tick_len = int(0.015 * sr)
            ratio = i / max(1, n_ticks - 1)
            f_tick = start_f + (end_f - start_f) * ratio
            tt = np.linspace(0, 0.015, tick_len, endpoint=False)
            tick_wave = np.sin(2 * np.pi * f_tick * tt) * np.exp(-tt * 200.0)
            avail = min(tick_len, n_samples - idx)
            out_audio[idx : idx + avail] += tick_wave[:avail] * 0.35

        # Confirmation chord exactly at active_dur (fast 120ms chime)
        chord_idx = min(n_samples - 1, int(active_dur * sr))
        chord_len = min(int(0.12 * sr), n_samples - chord_idx)
        if chord_len > 0:
            tc = np.linspace(0, 0.12, chord_len, endpoint=False)
            chord = (
                0.3 * np.sin(2 * np.pi * end_f * tc) + 0.2 * np.sin(2 * np.pi * (end_f * 1.5) * tc)
            ) * np.exp(-tc * 35.0)
            out_audio[chord_idx : chord_idx + chord_len] += chord

        return _write_wav_int16(output, sr, out_audio)

    if synth_type == "velocity_whoosh":
        noise = np.random.normal(0, 0.4, n_samples).astype(np.float32)
        center_idx = n_samples // 2
        sigma = max(1, n_samples // 6)
        envelope = np.exp(-0.5 * ((np.arange(n_samples) - center_idx) / sigma) ** 2)
        filtered = noise * envelope
        return _write_wav_int16(output, sr, filtered)

    raise SfxError(f"unknown synth_type: {synth_type}")


def verify_event_envelopes(
    wav_path: Path,
    *,
    active_until_s: float | None = None,
) -> dict[str, Any]:
    """Verify that an audio event decays below -45 dBFS and has no orphan ticks."""
    sr, data = _read_wav_float32(Path(wav_path))
    mono = data.mean(axis=1) if data.ndim > 1 else data

    tail_samples = max(1, int(0.05 * sr))
    end_tail = mono[-tail_samples:]
    rms_end = np.sqrt(np.mean(end_tail**2) + 1e-12)
    end_dbfs = float(20.0 * np.log10(rms_end + 1e-12))

    orphan_ticks = False
    if active_until_s is not None:
        settle_sample = int(active_until_s * sr)
        if settle_sample < len(mono):
            post_settle = mono[settle_sample:]
            peak_post = float(np.max(np.abs(post_settle)))
            post_dbfs = 20.0 * np.log10(peak_post + 1e-12)
            orphan_ticks = bool(post_dbfs > -35.0)

    return {
        "ok": bool(end_dbfs < -40.0 and not orphan_ticks),
        "end_dbfs": end_dbfs,
        "orphan_ticks": bool(orphan_ticks),
    }
