"""Transient AMV impact frame generation.

Generates transient frames (1-10) with high-impact visual filters
(flash, invert, sketch, red-sketch, chromatic) on percussive drum or sword
clash beats. Each frame is rendered as a short ProRes 4444 MOV beside the
source clip, timed to a specific hit point.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ingest import MediaInfo, probe_media


class ImpactError(RuntimeError):
    """Raised when an impact frame cannot be generated."""


@dataclass(frozen=True)
class ImpactResult:
    """The outcome of one impact frame render."""

    source: Path
    destination: Path
    mode: str
    frames: int
    fps: int
    resolution: str
    at_seconds: float
    size_bytes: int


#: Filter formula per impact mode, with ``{w}``, ``{h}`` and ``{fps}`` placeholders.
IMPACT_MODES: dict[str, str] = {
    "flash": "scale={w}:{h},fps={fps},lutyuv=y=255:u=128:v=128",
    "invert": "scale={w}:{h},fps={fps},negate",
    "sketch": "scale={w}:{h},fps={fps},edgedetect=low=0.1:high=0.4,negate",
    "red-sketch": (
        "scale={w}:{h},fps={fps},edgedetect=low=0.1:high=0.3,negate,"
        "colorchannelmixer=rr=1.5:rg=0:rb=0:br=0:bg=0:bb=0"
    ),
    "chromatic": (
        "scale={w}:{h},fps={fps},"
        "split=3[r][g][b];"
        "[r]crop=iw-8:ih:4:0[rp];"
        "[g]crop=iw-8:ih:4:0[gp];"
        "[b]crop=iw-8:ih:4:0[bp];"
        "[rp][gp]overlay=x=4:y=0:shortest=1[rg];"
        "[rg][bp]overlay=x=-4:y=0:shortest=1"
    ),
}

#: Transient frames must stay within this inclusive range.
MIN_FRAMES = 1
MAX_FRAMES = 10


def parse_at_timestamp(at: float | str, fps: int) -> tuple[float, int]:
    """Parse a hit timestamp into ``(seconds, target_frame)``.

    ``at`` may be a number of seconds (``2.5``) or a frame count prefixed with
    ``f`` (``f120`` at 60 fps -> ``2.0`` s). A plain numeric string is treated
    as seconds.

    Raises:
        ImpactError: the timestamp is negative or not a valid number/frame.
    """

    if isinstance(at, str):
        text = at.strip()
        if text.startswith("f"):
            try:
                target_frame = int(text[1:])
            except ValueError as error:
                raise ImpactError("invalid timestamp format") from error
            if target_frame < 0:
                raise ImpactError("negative timestamp")
            seconds = target_frame / fps
            return seconds, target_frame
        try:
            seconds = float(text)
        except ValueError as error:
            raise ImpactError("invalid timestamp format") from error
    else:
        seconds = float(at)

    if seconds < 0:
        raise ImpactError("negative timestamp")
    target_frame = round(seconds * fps)
    return seconds, target_frame


def build_filter_chain(*, mode: str, resolution: str, fps: int) -> str:
    """Build the ffmpeg ``-vf`` filter chain for one impact mode."""

    if mode not in IMPACT_MODES:
        known = ", ".join(sorted(IMPACT_MODES))
        raise ImpactError(f"unknown impact mode {mode!r}; available: {known}")

    width_text, _, height_text = resolution.lower().partition("x")
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as error:
        raise ImpactError(
            f"resolution {resolution!r} must be WxH, for example 1920x1080"
        ) from error

    return IMPACT_MODES[mode].format(w=width, h=height, fps=fps)


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as error:  # pragma: no cover - ffmpeg is a dependency
        raise ImpactError("ffmpeg is not installed") from error
    if result.returncode != 0:
        raise ImpactError(f"ffmpeg failed for {argv[-1]}:\n{result.stderr.strip()[-2000:]}")
    return result


def generate_impact_frames(
    source: Path,
    destination: Path | None = None,
    *,
    at: float | str,
    frames: int = 2,
    mode: str = "invert",
    fps: int = 60,
    resolution: str = "1920x1080",
) -> ImpactResult:
    """Render transient impact frames starting at a beat or clash hit.

    Args:
        source: the source video clip.
        destination: where to write the MOV; defaults to
            ``<stem>.impact.<mode>.mov`` beside the source.
        at: the hit point, seconds (``2.5``) or frame count (``f120``).
        frames: number of transient frames to render (1-10).
        mode: one of the keys of :data:`IMPACT_MODES`.
        fps: output frame rate.
        resolution: ``WxH`` output resolution.

    Raises:
        ImpactError: bounds validation fails, the mode is unknown, the source
            cannot be probed, or ``at`` lies beyond the source duration.
    """

    if not MIN_FRAMES <= frames <= MAX_FRAMES:
        raise ImpactError(f"frames must be between {MIN_FRAMES} and {MAX_FRAMES}")
    if mode not in IMPACT_MODES:
        known = ", ".join(sorted(IMPACT_MODES))
        raise ImpactError(f"unknown impact mode {mode!r}; available: {known}")

    at_seconds, target_frame = parse_at_timestamp(at, fps=fps)

    info: MediaInfo = probe_media(source)
    if at_seconds > info.duration:
        raise ImpactError(f"at {at_seconds:g}s exceeds source duration {info.duration:g}s")

    if destination is None:
        destination = source.with_name(f"{source.stem}.impact.{mode}.mov")
    destination.parent.mkdir(parents=True, exist_ok=True)

    filter_chain = build_filter_chain(mode=mode, resolution=resolution, fps=fps)
    start_time = target_frame / fps

    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(start_time),
        "-i",
        str(source),
        "-frames:v",
        str(frames),
        "-vf",
        filter_chain,
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-pix_fmt",
        "yuva444p12le",
        str(destination),
    ]
    _run(argv)

    if not destination.is_file():
        raise ImpactError(f"ffmpeg reported success but wrote no file: {destination}")

    return ImpactResult(
        source=source,
        destination=destination,
        mode=mode,
        frames=frames,
        fps=fps,
        resolution=resolution,
        at_seconds=at_seconds,
        size_bytes=destination.stat().st_size,
    )
