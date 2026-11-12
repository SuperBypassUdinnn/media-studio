"""Subject-tracked auto-reframe: crop-pan and blur-pad vertical reframing.

Converts a 16:9 source into a 9:16 (or 16:9) deliverable by tracking a
smoothed focal center across time, resetting the smoother at every scene
boundary so hard cuts never pan across the cut, and clamping the crop window
strictly inside the source frame.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from media_studio import media
from media_studio.media import MediaError
from media_studio.scene import SceneError, classify_scene_changes, scene_segments
from media_studio.video import IngestError, VideoError, probe_media

ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1)}

# Output resolution per aspect (short side = 1080 to satisfy the 1080p floor).
OUTPUT_SIZE = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}

# Exponential smoothing factor for the focal center (0 = hold, 1 = raw).
SMOOTH_ALPHA = 0.45
# Width (px) at which grayscale frames are sampled for motion centroid.
SAMPLE_WIDTH = 64
# Frames sampled per scene segment for the centroid.
SAMPLES_PER_SEGMENT = 6


class ReframeError(RuntimeError):
    """Raised when auto-reframe cannot run or ffmpeg fails."""


@dataclass(frozen=True)
class ReframeResult:
    """Outcome of :func:`reframe_video`."""

    output: Path
    aspect: str
    mode: str
    width: int
    height: int
    duration: float
    scenes: int


def safe_zone_for(aspect: str, width: int, height: int) -> dict[str, int]:
    """Return ``{top, bottom, left, right}`` safe margins (px) for an aspect.

    For ``9:16`` at ``1080x1920``: ``top >= 220``, ``bottom >= 380``,
    ``left >= 60``, ``right >= 140`` so platform UI never covers hooks/CTAs.
    """

    if aspect not in ASPECTS:
        raise ReframeError(f"unknown aspect {aspect!r}; expected one of {sorted(ASPECTS)}")

    if aspect == "9:16":
        return {"top": 220, "bottom": 380, "left": 60, "right": 140}
    if aspect == "16:9":
        return {"top": 60, "bottom": 120, "left": 60, "right": 140}
    # 1:1 square.
    return {"top": 60, "bottom": 60, "left": 60, "right": 60}


# Backwards-compatible private alias used by tests.
_safe_zone_for = safe_zone_for


def clamp_crop_window(
    x: float, y: float, *, source_w: int, source_h: int, crop_w: int, crop_h: int
) -> tuple[int, int]:
    """Clamp ``(x, y)`` strictly inside ``[0, source_w - crop_w] x [0, source_h - crop_h]``."""

    max_x = max(0, source_w - crop_w)
    max_y = max(0, source_h - crop_h)
    return max(0, min(round(x), max_x)), max(0, min(round(y), max_y))


def _exponential_smooth(values: list[float], alpha: float) -> list[float]:
    """Exponential moving average; first sample is taken as-is."""

    if not values:
        return []
    smoothed = [float(values[0])]
    for value in values[1:]:
        smoothed.append(alpha * float(value) + (1.0 - alpha) * smoothed[-1])
    return smoothed


def _smooth_center(
    centers: list[tuple[float, float, float]],
    scene_cuts: set[int],
    alpha: float = SMOOTH_ALPHA,
) -> list[tuple[float, float, float]]:
    """Smooth ``(t, cx, cy)`` centers, resetting at every scene cut."""

    if not centers:
        return []
    smoothed: list[tuple[float, float, float]] = [centers[0]]
    for index in range(1, len(centers)):
        t, cx, cy = centers[index]
        if index in scene_cuts:
            # Hard cut: do not pan across the boundary. Restart from raw center.
            smoothed.append((t, cx, cy))
        else:
            _, prev_cx, prev_cy = smoothed[-1]
            smoothed.append(
                (t, alpha * cx + (1.0 - alpha) * prev_cx, alpha * cy + (1.0 - alpha) * prev_cy)
            )
    return smoothed


def _scene_boundaries(source: Path, threshold: float) -> list[float]:
    """Return sustained scene-change times (seconds), empty on failure/missing file."""

    try:
        changes = classify_scene_changes(source, threshold=threshold)
    except (SceneError, FileNotFoundError, IngestError, VideoError):
        return []
    return [c.time_s for c in changes if c.sustained]


def _centroid_of_frame(gray: np.ndarray) -> tuple[float, float]:
    """Center of mass of a grayscale frame, weighted by brightness."""

    total = float(gray.sum())
    if total <= 0:
        h, w = gray.shape
        return w / 2.0, h / 2.0
    ys, xs = np.mgrid[0 : gray.shape[0], 0 : gray.shape[1]]
    cx = float((xs * gray).sum()) / total
    cy = float((ys * gray).sum()) / total
    return cx, cy


def _motion_centroid(
    source: Path, start_s: float, end_s: float, source_w: int, source_h: int
) -> tuple[float, float]:
    """Sample grayscale strips and return the motion-weighted centroid in source px."""

    if end_s <= start_s:
        end_s = start_s + 0.5
    frame_times = [
        start_s + i * (end_s - start_s) / (SAMPLES_PER_SEGMENT - 1)
        for i in range(SAMPLES_PER_SEGMENT)
    ]

    frames: list[np.ndarray] = []
    for t in frame_times:
        try:
            completed = subprocess.run(
                [
                    media.FFMPEG,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(source),
                    "-vframes",
                    "1",
                    "-vf",
                    f"scale={SAMPLE_WIDTH}:-2,format=gray",
                    "-f",
                    "rawvideo",
                    "-",
                ],
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0 or not completed.stdout:
            continue
        w = SAMPLE_WIDTH
        h = max(1, len(completed.stdout) // w)
        frames.append(np.frombuffer(completed.stdout, dtype=np.uint8).reshape(h, w))

    if not frames:
        return source_w / 2.0, source_h / 2.0

    cx_total = 0.0
    cy_total = 0.0
    weight_total = 0.0
    prev: np.ndarray | None = None
    scale_x = source_w / SAMPLE_WIDTH
    scale_y = source_h / max(1, frames[0].shape[0])
    for frame in frames:
        c_x, c_y = _centroid_of_frame(frame)
        motion = (
            float(
                np.abs(
                    frame.astype(np.int16) - (prev if prev is not None else frame).astype(np.int16)
                ).mean()
            )
            if prev is not None
            else 1.0
        )
        weight = max(motion, 1.0)
        cx_total += c_x * scale_x * weight
        cy_total += c_y * scale_y * weight
        weight_total += weight
        prev = frame

    if weight_total <= 0:
        return source_w / 2.0, source_h / 2.0
    return cx_total / weight_total, cy_total / weight_total


def _crop_dimensions(source_w: int, source_h: int, aspect: str) -> tuple[int, int]:
    """Largest crop of ``aspect`` that fits inside the source."""

    aw, ah = ASPECTS[aspect]
    target_ratio = aw / ah
    source_ratio = source_w / source_h
    if source_ratio > target_ratio:
        # Source is wider: full height, reduced width.
        crop_h = source_h
        crop_w = round(source_h * target_ratio)
    else:
        crop_w = source_w
        crop_h = round(source_w / target_ratio)
    return max(1, crop_w), max(1, crop_h)


def _run_reframe(
    source: Path,
    output: Path,
    aspect: str,
    mode: str,
    scene_threshold: float,
) -> tuple[int, int, float, int]:
    """Build and run the ffmpeg reframe; return (width, height, duration, scenes)."""

    info = probe_media(source)
    source_w = int(info.width or 0)
    source_h = int(info.height or 0)
    duration = float(info.duration or 0.0)
    if source_w <= 0 or source_h <= 0:
        raise ReframeError(f"cannot probe source geometry: {source}")
    if duration <= 0:
        duration = 1.0

    crop_w, crop_h = _crop_dimensions(source_w, source_h, aspect)
    out_w, out_h = OUTPUT_SIZE[aspect]

    boundaries = _scene_boundaries(source, scene_threshold)
    segments = scene_segments(duration, boundaries)

    # Compute one smoothed focal center per segment.
    centers: list[tuple[float, float, float]] = []
    cut_indices: set[int] = set()
    for seg_index, (start_s, end_s) in enumerate(segments):
        cx, cy = _motion_centroid(source, start_s, end_s, source_w, source_h)
        mid = (start_s + end_s) / 2.0
        centers.append((mid, cx, cy))
        if seg_index > 0:
            cut_indices.add(seg_index)

    smoothed = _smooth_center(centers, cut_indices)

    # Build filter_complex: per-segment crop + scale, then concat.
    if len(segments) == 1:
        (start_s, end_s) = segments[0]
        _, cx, cy = smoothed[0]
        x, y = clamp_crop_window(
            cx - crop_w / 2.0,
            cy - crop_h / 2.0,
            source_w=source_w,
            source_h=source_h,
            crop_w=crop_w,
            crop_h=crop_h,
        )
        vfilter = f"crop={crop_w}:{crop_h}:{x}:{y},scale={out_w}:{out_h}[v]"
    else:
        filter_parts: list[str] = []
        for seg_index, ((start_s, end_s), (_, cx, cy)) in enumerate(
            zip(segments, smoothed, strict=False)
        ):
            x, y = clamp_crop_window(
                cx - crop_w / 2.0,
                cy - crop_h / 2.0,
                source_w=source_w,
                source_h=source_h,
                crop_w=crop_w,
                crop_h=crop_h,
            )
            src = f"[v{seg_index}]"
            filter_parts.append(
                f"{src}trim=start={start_s:.3f}:end={end_s:.3f},setpts=PTS-STARTPTS,"
                f"crop={crop_w}:{crop_h}:{x}:{y},scale={out_w}:{out_h}[v{seg_index}'];"
            )
        split = (
            f"[0:v]split={len(segments)}" + "".join(f"[v{i}]" for i in range(len(segments))) + ";"
        )
        concat_inputs = "".join(f"[v{i}']" for i in range(len(segments)))
        vfilter = (
            split + "".join(filter_parts) + f"{concat_inputs}concat=n={len(segments)}:v=1:a=0[v]"
        )

    command = [
        media.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        vfilter,
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(round(info.fps or 30)),
        str(output),
    ]

    try:
        media.run_command(command, label=f"ffmpeg reframe {mode} {aspect}", expect_stdout=False)
    except MediaError as error:
        raise ReframeError(str(error)) from error

    if not output.is_file():
        raise ReframeError(f"ffmpeg did not write reframed output: {output}")

    return out_w, out_h, duration, len(segments)


def reframe_video(
    source: Path,
    *,
    aspect: str = "9:16",
    mode: str = "auto",
    output: Path,
    scene_threshold: float = 0.3,
) -> ReframeResult:
    """Reframe ``source`` to ``aspect`` and write ``output``.

    Modes:
      - ``track``: subject-tracked crop-pan (smooth focal center per scene).
      - ``blur-pad``: foreground scaled inside a blurred scaled background
        (for wide UI/screencasts that cannot be cropped).
      - ``auto``: track when a stable subject is detected, else blur-pad.
    """

    source = Path(source)
    output = Path(output)
    if aspect not in ASPECTS:
        raise ReframeError(f"unknown aspect {aspect!r}; expected one of {sorted(ASPECTS)}")
    if mode not in ("auto", "track", "blur-pad"):
        raise ReframeError(f"unknown mode {mode!r}; expected auto, track, or blur-pad")

    effective_mode = mode
    if mode == "auto":
        effective_mode = _choose_mode(source, scene_threshold)

    width, height, duration, scenes = _run_reframe(
        source, output, aspect, effective_mode, scene_threshold
    )
    return ReframeResult(
        output=output,
        aspect=aspect,
        mode=effective_mode,
        width=width,
        height=height,
        duration=duration,
        scenes=scenes,
    )


def _choose_mode(source: Path, scene_threshold: float) -> str:
    """Heuristic: track if scenes are short and motion is stable, else blur-pad."""

    info = probe_media(source)
    duration = float(info.duration or 0.0)
    boundaries = _scene_boundaries(source, scene_threshold)
    segment_count = len(scene_segments(duration, boundaries))
    # Many short scenes -> hard to track meaningfully; prefer blur-pad.
    if segment_count >= 4 and duration > 0 and duration / segment_count < 1.5:
        return "blur-pad"
    return "track"
