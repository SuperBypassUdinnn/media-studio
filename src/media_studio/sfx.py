"""Bundled CC0 promo/AMV SFX pack and transition-audio mixing.

The pack lives under ``src/media_studio/data/sfx/`` (six synthesised PCM-16 WAVs
plus ``sfx.yaml``). ``mix_transition_sfx`` places :class:`SfxCue` events over a
base audio bed with ``adelay``/``volume``/``amix`` and caps the mix with
``alimiter=limit=0.84`` (~ -1.5 dBTP) followed by loudness normalisation, so
the output never clips and lands at the target loudness.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from media_studio import media


class SfxError(RuntimeError):
    """Raised when the SFX pack or a mix cannot be built."""


@dataclass(frozen=True)
class SfxCue:
    """One SFX placement: a bundled id or file path, a time offset, gain, and J/L-cut offsets."""

    path_or_id: str
    at: float
    gain_db: float = 0.0
    lead_in_sec: float = 0.0
    trail_out_sec: float = 0.0
    layer: str = "transition"


@dataclass(frozen=True)
class SfxEntry:
    """Catalog entry for one bundled SFX asset."""

    id: str
    path: Path
    tags: tuple[str, ...]
    license: str
    duration: float


AUDIO_RATE = 48000
PACK_DIR = Path(__file__).resolve().parent / "data" / "sfx"
MANIFEST = PACK_DIR / "sfx.yaml"


def _load_manifest() -> dict:
    try:
        return yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as error:
        raise SfxError(f"sfx manifest not found: {MANIFEST}") from error
    except yaml.YAMLError as error:
        raise SfxError(f"sfx manifest is invalid YAML: {error}") from error


def _entry_for(effect: dict) -> SfxEntry:
    path = PACK_DIR / effect["file"]
    if not path.is_file():
        raise SfxError(f"bundled SFX asset not found: {path}")
    info = media.probe(path)
    return SfxEntry(
        id=str(effect["id"]),
        path=path,
        tags=tuple(str(t) for t in (effect.get("tags") or [])),
        license=str(_load_manifest().get("license", "CC0-1.0")),
        duration=info.duration or 0.0,
    )


def list_bundled_sfx(tag: str | None = None, layer: str | None = None) -> list[SfxEntry]:
    """Return bundled SFX entries, optionally filtered by tag or layer."""

    manifest = _load_manifest()
    entries = [_entry_for(effect) for effect in (manifest.get("effects") or [])]
    target_tag = layer or tag
    if target_tag:
        entries = [entry for entry in entries if target_tag in entry.tags]
    return entries


def _resolve_cue(path_or_id: str) -> Path:
    candidate = Path(path_or_id)
    if candidate.is_file():
        return candidate
    bundled = PACK_DIR / f"{path_or_id}.wav"
    if bundled.is_file():
        return bundled
    matches = [entry.path for entry in list_bundled_sfx() if entry.id == path_or_id]
    if matches:
        return matches[0]
    raise SfxError(f"SFX not found (not a file and no bundled id): {path_or_id}")


def mix_transition_sfx(
    base_audio: Path | None,
    events: list[SfxCue],
    *,
    output: Path,
    total_duration: float | None = None,
    duration: float | None = None,
    target_lufs: float = -14.0,
) -> Path:
    """Mix SFX cues over ``base_audio`` into ``output``.

    ``base_audio`` may be ``None`` or ``"none"`` for a pure SFX bed (silent
    base). Each cue is delayed to its effective placement time (honoring J-Cut
    lead_in_sec clamped at 0.0), gained by ``gain_db``, and optionally tailed out,
    then all sources are summed with ``amix``, hard-limited at ``0.84`` (~ -1.5
    dBTP) and loudness-normalised to ``target_lufs``. Output is exactly
    ``total_duration`` seconds of PCM-16 WAV at 48 kHz.
    """

    dur = total_duration if total_duration is not None else duration
    if dur is None or dur <= 0.0:
        raise SfxError(f"duration must be positive, got {dur}")
    total_duration = float(dur)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    base_path: Path | None = None
    if base_audio is not None and str(base_audio).strip().lower() not in {"", "none"}:
        base_path = Path(base_audio)
        if not base_path.is_file():
            raise SfxError(f"base audio not found: {base_path}")

    resolved_events: list[tuple[SfxCue, Path]] = []
    for cue in events:
        if cue.at < 0.0:
            raise SfxError(f"cue.at must be >= 0, got {cue.at}")
        if cue.at > total_duration:
            raise SfxError(f"cue.at={cue.at} exceeds total_duration={total_duration}")
        resolved_events.append((cue, _resolve_cue(cue.path_or_id)))

    inputs: list[str] = []
    parts: list[str] = []
    next_input_index = 0

    # Base audio bed (real file or synthesised silence).
    if base_path is not None:
        inputs.extend(["-i", str(base_path)])
        parts.append(
            f"[{next_input_index}:a]aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo"
            f",atrim=0:{total_duration:.6f}[base]"
        )
        next_input_index += 1
    else:
        parts.append(f"anullsrc=r={AUDIO_RATE}:cl=stereo:duration={total_duration:.6f}[base]")

    # Each SFX cue: normalise, delay to its placement time, apply gain and J/L-cuts.
    for cue_index, (cue, sfx_path) in enumerate(resolved_events):
        input_index = next_input_index
        inputs.extend(["-i", str(sfx_path)])
        next_input_index += 1
        effective_at = max(0.0, float(cue.at) - max(0.0, float(cue.lead_in_sec)))
        at_ms = max(0, round(effective_at * 1000.0))
        filter_chain = (
            f"[{input_index}:a]aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo"
            f",adelay={at_ms}ms:all=1,volume={cue.gain_db}dB"
        )
        if cue.trail_out_sec > 0:
            filter_chain += f",afade=t=out:st={effective_at:.3f}:d={cue.trail_out_sec:.3f}"
        parts.append(f"{filter_chain}[s{cue_index}]")

    # Sum everything, limit true peak, normalise loudness.
    amix_inputs = "[base]" + "".join(f"[s{i}]" for i in range(len(resolved_events)))
    parts.append(f"{amix_inputs}amix=inputs={len(resolved_events) + 1}:duration=longest[amix]")
    parts.append("[amix]alimiter=limit=0.84[limited]")
    parts.append(
        f"[limited]loudnorm=I={target_lufs}:TP=-1.5:LRA=11:linear=true:print_format=none[out]"
    )

    command: list[str] = []
    command.extend(inputs)
    command.extend(["-filter_complex", ";".join(parts)])
    command.extend(["-map", "[out]", "-t", f"{total_duration:.6f}"])
    command.extend(["-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2", str(output)])

    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", *command],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SfxError(
            f"ffmpeg mix failed with exit {completed.returncode}:\n{completed.stderr[-1500:]}"
        )
    if not output.is_file():
        raise SfxError(f"ffmpeg did not write {output}")
    return output
