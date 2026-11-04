"""2.5D multi-plane parallax compositor engine.

Takes a background plate (depth ~0.3), an RGBA subject cutout (depth 1.0, alpha
required), and an optional foreground layer (depth ~1.8), then applies
differential zoom/pan transforms to each plane so a virtual camera move creates
immersive 3D camera depth.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .media import FFMPEG, probe
from .see import _pix_fmt_has_alpha

PARALLAX_MOTIONS: tuple[str, ...] = (
    "push_in",
    "pull_out",
    "whip_left",
    "whip_right",
    "dramatic_dolly",
)

MOTIONS = frozenset(PARALLAX_MOTIONS)

RESOLUTION_PATTERN = re.compile(r"^\d{2,5}x\d{2,5}$")
MIN_WIDTH = 1920
MIN_HEIGHT = 1080


class ParallaxError(RuntimeError):
    """Raised when the parallax compositor cannot continue."""


@dataclass(frozen=True)
class LayerTransform:
    """Per-layer zoom and pan keyframes for one parallax motion."""

    zoom_start: float
    zoom_end: float
    pan_x_start: float
    pan_x_end: float
    pan_y_start: float
    pan_y_end: float


@dataclass(frozen=True)
class ParallaxResult:
    """What one parallax compose produced."""

    destination: Path
    duration: float
    fps: int
    resolution: str
    layers_composited: int
    motion: str
    size_bytes: int


def parse_resolution(resolution: str) -> tuple[int, int]:
    """Parse a WxH resolution string into ``(width, height)``.

    Raises:
        ParallaxError: the string is not WxH.
    """

    if not RESOLUTION_PATTERN.match(resolution):
        raise ParallaxError(f"resolution {resolution!r} must be WxH, for example 1920x1080")
    width_text, _, height_text = resolution.lower().partition("x")
    return int(width_text), int(height_text)


def calculate_layer_transforms(
    *,
    motion: str,
    duration: float,
    fps: int,
    width: int,
    height: int,
) -> tuple[LayerTransform, LayerTransform, LayerTransform | None]:
    """Return background, subject, and foreground transforms for ``motion``.

    Depth ordering: background ~0.3 (slowest), subject 1.0 (medium), foreground
    ~1.8 (fastest). Returns ``(background, subject, foreground)`` where
    foreground may be ``None`` when the motion has no foreground plane.
    """

    if motion not in MOTIONS:
        raise ParallaxError(
            f"unknown motion {motion!r}; expected one of {', '.join(PARALLAX_MOTIONS)}"
        )

    bg = LayerTransform(1.00, 1.06, 0.0, 0.02 * width, 0.0, 0.02 * height)
    subject = LayerTransform(1.00, 1.25, 0.0, 0.04 * width, 0.0, 0.04 * height)
    foreground = LayerTransform(1.00, 1.55, 0.0, 0.07 * width, 0.0, 0.07 * height)

    if motion == "pull_out":
        bg = LayerTransform(1.06, 1.00, 0.02 * width, 0.0, 0.02 * height, 0.0)
        subject = LayerTransform(1.25, 1.00, 0.04 * width, 0.0, 0.04 * height, 0.0)
        foreground = LayerTransform(1.55, 1.00, 0.07 * width, 0.0, 0.07 * height, 0.0)
    elif motion == "whip_left":
        bg = LayerTransform(1.02, 1.00, -0.05 * width, 0.0, -0.05 * height, 0.0)
        subject = LayerTransform(1.10, 1.00, -0.10 * width, 0.0, -0.10 * height, 0.0)
        foreground = LayerTransform(1.20, 1.00, -0.18 * width, 0.0, -0.18 * height, 0.0)
    elif motion == "whip_right":
        bg = LayerTransform(1.02, 1.00, 0.05 * width, 0.0, 0.05 * height, 0.0)
        subject = LayerTransform(1.10, 1.00, 0.10 * width, 0.0, 0.10 * height, 0.0)
        foreground = LayerTransform(1.20, 1.00, 0.18 * width, 0.0, 0.18 * height, 0.0)
    elif motion == "dramatic_dolly":
        bg = LayerTransform(1.00, 1.12, 0.0, 0.04 * width, 0.0, 0.04 * height)
        subject = LayerTransform(1.00, 1.45, 0.0, 0.08 * width, 0.0, 0.08 * height)
        foreground = LayerTransform(1.00, 1.85, 0.0, 0.14 * width, 0.0, 0.14 * height)

    return bg, subject, foreground


def check_alpha_channel(path: Path) -> bool:
    """Whether ``path`` carries an alpha channel.

    Reads the PIL image mode (RGBA / LA / PA) first, falling back to ffprobe's
    pixel format for video sources. Returns False when the file is missing or
    unreadable rather than raising, so callers can gate validation on it.
    """

    if not Path(path).is_file():
        return False
    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.mode in ("RGBA", "LA", "PA"):
                return True
    except Exception:
        pass
    try:
        return _pix_fmt_has_alpha(path)
    except Exception:
        return False


def _scale_and_crop_filter(input_label: str, width: int, height: int) -> str:
    """Scale and center-crop ``input_label`` to ``width``x``height`` without distortion."""

    return (
        f"[{input_label}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}[{input_label}scaled]"
    )


def _zoom_pan_filter(
    input_label: str,
    transform: LayerTransform,
    duration: float,
    fps: int,
    width: int,
    height: int,
) -> str:
    """Build the zoompan filterchain expressing ``transform`` over ``duration``.

    zoompan evaluates its ``z``/``x``/``y`` expressions per frame and exposes a
    running ``zoom`` variable, so a recursive ramp ``zoom + step`` drives a real
    push/pull. Pan is expressed as a function of the running zoom so the pan
    tracks the zoom fraction (0.0 at the start, 1.0 at the end).
    """

    total_frames = max(1, round(duration * fps))
    last_frame = max(1, total_frames - 1)
    z_start, z_end = transform.zoom_start, transform.zoom_end
    px_start, px_end = transform.pan_x_start, transform.pan_x_end
    py_start, py_end = transform.pan_y_start, transform.pan_y_end

    # Expressions must contain no commas (filter argument separators) or spaces.
    if z_end > z_start:
        step = (z_end - z_start) / last_frame
        zoom_expr = f"zoom+{step}"
        span = z_end - 1.0
        if abs(span) > 1e-6:
            pan_x_expr = f"{px_start}+({px_end}-{px_start})*(zoom-1.0)/{span}"
            pan_y_expr = f"{py_start}+({py_end}-{py_start})*(zoom-1.0)/{span}"
        else:
            pan_x_expr = f"{px_end}"
            pan_y_expr = f"{py_end}"
    else:
        step = (z_start - 1.0) / last_frame
        zoom_expr = f"zoom-{step}"
        pan_x_expr = f"{px_end}"
        pan_y_expr = f"{py_end}"

    return (
        f"[{input_label}scaled]zoompan=z={zoom_expr}:x={pan_x_expr}:y={pan_y_expr}:"
        f"s={width}x{height}:fps={fps}:d={total_frames}[{input_label}xformed]"
    )


def _overlay_pair(top_label: str, bottom_label: str, output_label: str) -> str:
    return f"[{bottom_label}][{top_label}]overlay=format=auto:shortest=1[o{output_label}]"


def compose_parallax(
    background: Path,
    subject: Path,
    foreground: Path | None = None,
    destination: Path | None = None,
    *,
    duration: float,
    fps: int = 60,
    resolution: str = "1920x1080",
    motion: str = "push_in",
    vignette: float = 0.3,
) -> ParallaxResult:
    """Composite background, subject, and optional foreground into a parallax shot.

    Raises:
        ParallaxError: a source is missing, the subject lacks alpha, the motion
            is unknown, the resolution is invalid or below the 1920x1080 floor,
            or ffmpeg fails to write the deliverable.
    """

    background = Path(background)
    subject = Path(subject)
    destination = Path(destination) if destination is not None else Path.cwd() / "parallax-out.mov"

    width, height = parse_resolution(resolution)
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise ParallaxError(f"resolution {resolution} is below the 1920x1080 minimum floor")

    if not background.is_file():
        raise ParallaxError(f"background plate not found: {background}")
    if not subject.is_file():
        raise ParallaxError(f"subject cutout not found: {subject}")
    if foreground is not None:
        foreground = Path(foreground)
        if not foreground.is_file():
            raise ParallaxError(f"foreground layer not found: {foreground}")
    if not check_alpha_channel(subject):
        raise ParallaxError(
            "subject layer requires an alpha channel; run ms image remove-bg or "
            "ms video remove-bg first"
        )
    if motion not in MOTIONS:
        raise ParallaxError(
            f"unknown motion {motion!r}; expected one of {', '.join(PARALLAX_MOTIONS)}"
        )
    if duration <= 0:
        raise ParallaxError(f"duration must be positive, got {duration}")

    bg_t, subj_t, fg_t = calculate_layer_transforms(
        motion=motion, duration=duration, fps=fps, width=width, height=height
    )

    filter_parts: list[str] = []
    filter_parts.append(_scale_and_crop_filter("0", width, height))
    filter_parts.append(_scale_and_crop_filter("1", width, height))
    if foreground is not None:
        filter_parts.append(_scale_and_crop_filter("2", width, height))

    filter_parts.append(_zoom_pan_filter("0", bg_t, duration, fps, width, height))
    filter_parts.append(_zoom_pan_filter("1", subj_t, duration, fps, width, height))
    if foreground is not None:
        filter_parts.append(_zoom_pan_filter("2", fg_t, duration, fps, width, height))

    filter_parts.append(_overlay_pair("1xformed", "0xformed", "1"))
    if foreground is not None:
        filter_parts.append(_overlay_pair("2xformed", "o1", "2"))
        composite_label = "o2"
    else:
        composite_label = "o1"

    if vignette and vignette > 0:
        filter_parts.append(f"[{composite_label}]vignette=PI*{vignette}[vignetted]")
        composite_label = "vignetted"

    filter_parts.append(f"[{composite_label}]format=yuva444p12le[out]")
    filtergraph = ";".join(filter_parts)

    command = [
        FFMPEG,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(background),
        "-i",
        str(subject),
    ]
    if foreground is not None:
        command.extend(["-i", str(foreground)])
    command.extend(
        [
            "-filter_complex",
            filtergraph,
            "-map",
            "[out]",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p12le",
            "-t",
            f"{duration:.3f}",
            "-r",
            str(fps),
            str(destination),
        ]
    )

    try:
        subprocess.run(command, capture_output=True, text=True, check=True, timeout=3600)
    except subprocess.CalledProcessError as error:
        tail = (error.stderr or error.stdout or "").strip()[-1200:]
        raise ParallaxError(f"ffmpeg parallax compose failed:\n{tail}") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise ParallaxError(f"ffmpeg could not run: {error}") from error

    if not destination.is_file():
        raise ParallaxError(f"ffmpeg did not write {destination}")

    probe(destination)
    return ParallaxResult(
        destination=destination,
        duration=float(duration),
        fps=int(fps),
        resolution=resolution,
        layers_composited=2 if foreground is None else 3,
        motion=motion,
        size_bytes=destination.stat().st_size,
    )
