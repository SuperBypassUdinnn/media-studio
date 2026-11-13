"""FFmpeg filtergraph transitions between adjacent clips (promo/AMV whip pans,
zoom punches, RGB splits, flashes, light leaks, and slides).

Both inputs are normalised to an exact ``(width, height, fps, yuv420p, setsar=1)``
geometry before the transition filtergraph runs, so clips of any source format
cut cleanly. Audio is preserved when present (cross-concatenated with silence
filling any missing bed) or the output is emitted as silent, clean video.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from media_studio import media


class TransitionError(RuntimeError):
    """Raised when a transition cannot be built or executed."""


TRANSITION_KINDS: tuple[str, ...] = (
    "whip_pan_left",
    "whip_pan_right",
    "zoom_punch_in",
    "zoom_punch_out",
    "rgb_split_hit",
    "flash_white",
    "light_leak_warm",
    "slide_up",
)

# FFmpeg ``xfade`` transition names per kind. Kinds not listed here use a
# custom ``geq``/``scale`` post-effect instead of an xfade preset.
XFADE_BY_KIND: dict[str, str] = {
    "whip_pan_left": "slideleft",
    "whip_pan_right": "slideright",
    "zoom_punch_in": "zoomin",
    "zoom_punch_out": "fadefast",
    "flash_white": "fadewhite",
    "slide_up": "slideup",
}

# Kinds whose look is built with a custom geq/scale filtergraph after the
# crossfade, rather than an xfade preset.
CUSTOM_KINDS: frozenset[str] = frozenset({"rgb_split_hit", "light_leak_warm"})

# Paired SFX tag per transition kind, for ``sfx.mix_transition_sfx``.
SFX_TAG_BY_KIND: dict[str, str] = {
    "whip_pan_left": "whoosh",
    "whip_pan_right": "whoosh",
    "zoom_punch_in": "impact",
    "zoom_punch_out": "impact",
    "rgb_split_hit": "impact",
    "flash_white": "impact",
    "light_leak_warm": "riser",
    "slide_up": "whoosh",
}

AUDIO_RATE = 48000


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of :func:`apply_transition`."""

    output: Path
    kind: str
    duration_ms: int
    width: int
    height: int
    fps: int
    duration: float
    sfx_tag: str


def _validate_kind(kind: str) -> str:
    if kind not in TRANSITION_KINDS:
        raise TransitionError(
            f"unknown transition kind {kind!r}; expected one of {TRANSITION_KINDS}"
        )
    return kind


def _validate_duration_ms(duration_ms: object) -> int:
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        raise TransitionError(f"duration_ms must be a positive int, got {duration_ms!r}")
    if duration_ms <= 0:
        raise TransitionError(f"duration_ms must be positive, got {duration_ms}")
    return duration_ms


def _normalisation_filter(width: int, height: int, fps: int) -> str:
    return (
        f"scale=w={width}:h={height}:force_original_aspect_ratio=disable"
        f":flags=lanczos,setsar=1,fps={fps},format=yuv420p"
    )


def _post_effect(kind: str) -> str:
    if kind == "rgb_split_hit":
        # Chromatic aberration: shift red right and blue left on every frame.
        return "geq=r='r(X+4,Y)':g='g(X,Y)':b='b(X-4,Y)'"
    if kind == "light_leak_warm":
        # Warm tint: lift reds, hold greens, dim blues.
        return "geq=r='r(X,Y)*1.12':g='g(X,Y)*1.04':b='b(X,Y)*0.88'"
    raise TransitionError(f"no post effect defined for {kind!r}")


def _ffmpeg(command: list[str]) -> None:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", *command],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise TransitionError(
            f"ffmpeg failed with exit {completed.returncode}:\n{completed.stderr[-1500:]}"
        )


def apply_transition(
    clip_a: Path,
    clip_b: Path,
    *,
    kind: str,
    output: Path,
    duration_ms: int = 250,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> TransitionResult:
    """Render ``clip_b`` after ``clip_a`` joined by a transition.

    Parameters
    ----------
    clip_a, clip_b:
        Source clips. Any resolution/frame rate/pixel format works; both are
        normalised to ``(width, height, fps, yuv420p)`` first.
    kind:
        One of :data:`TRANSITION_KINDS`.
    output:
        Destination ``.mp4`` path.
    duration_ms:
        Transition length in milliseconds, frame-quantized to ``fps``.
    width, height, fps:
        Normalised output geometry.
    """

    _validate_kind(kind)
    duration_ms = _validate_duration_ms(duration_ms)

    info_a = media.probe(clip_a)
    info_b = media.probe(clip_b)
    duration_a = info_a.duration
    duration_b = info_b.duration
    if duration_a <= 0.0 or duration_b <= 0.0:
        raise TransitionError("both clips must report a positive duration")

    frames = max(1, round(duration_ms * fps / 1000.0))
    td = frames / fps
    duration_ms = round(td * 1000.0)
    offset = duration_a - td
    if offset < 0.0 or td >= duration_b:
        raise TransitionError(
            f"duration_ms={duration_ms} does not fit between the clips "
            f"({duration_a:.3f}s and {duration_b:.3f}s)"
        )

    out_duration = duration_a + duration_b - td
    norm = _normalisation_filter(width, height, fps)
    parts: list[str] = [f"[0:v]{norm}[a]", f"[1:v]{norm}[b]"]

    if kind in CUSTOM_KINDS:
        parts.append(f"[a][b]xfade=transition=fade:duration={td:.6f}:offset={offset:.6f}[base]")
        parts.append(f"[base]{_post_effect(kind)}[outv]")
    else:
        parts.append(
            f"[a][b]xfade=transition={XFADE_BY_KIND[kind]}"
            f":duration={td:.6f}:offset={offset:.6f}[outv]"
        )

    has_a = info_a.audio_codec is not None
    has_b = info_b.audio_codec is not None
    if has_a or has_b:
        audio_parts: list[str] = []
        for index, (has, dur) in enumerate(((has_a, duration_a), (has_b, duration_b))):
            if has:
                audio_parts.append(
                    f"[{index}:a]aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo"
                    f",atrim=0:{dur:.6f}[a{index}]"
                )
            else:
                audio_parts.append(
                    f"anullsrc=r={AUDIO_RATE}:cl=stereo:duration={dur:.6f}[a{index}]"
                )
        audio_parts.append(f"[a0][a1]acrossfade=d={td:.6f}:c1=tri:c2=tri[outa]")
        parts.extend(audio_parts)

    filter_complex = ";".join(parts)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command: list[str] = [
        "-i",
        str(clip_a),
        "-i",
        str(clip_b),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "14",
        "-threads",
        "1",
        # libx264 slice-threaded encoding segfaults on the ``slideup`` xfade
        # filter when explicit BT.709 color metadata is also set (ffmpeg 9.0.2).
        # Single-threaded encode keeps the color tags correct without crashing;
        # transition clips are short so the cost is negligible.
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
    ]
    if has_a or has_b:
        command.extend(
            [
                "-map",
                "[outa]",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                str(AUDIO_RATE),
                "-ac",
                "2",
            ]
        )
    else:
        command.append("-an")
    command.extend(["-t", f"{out_duration:.6f}", "-movflags", "+faststart", str(output)])

    _ffmpeg(command)

    result_info = media.probe(output)
    return TransitionResult(
        output=output,
        kind=kind,
        duration_ms=duration_ms,
        width=result_info.width or width,
        height=result_info.height or height,
        fps=round(result_info.fps or fps),
        duration=result_info.duration,
        sfx_tag=SFX_TAG_BY_KIND[kind],
    )
