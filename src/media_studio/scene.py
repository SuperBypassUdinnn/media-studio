"""Sustained scene-change detection: separate story cuts from impact flashes.

Stdlib + subprocess only. No PIL, no heavy imports.

Method: at a candidate scene-change time ``t`` (from ffmpeg's ``gt(scene,T)``
select filter), compare the frame at ``t - CONFIRM_BEFORE_S`` with the frame at
``t + CONFIRM_AFTER_S``. If the two frames differ beyond ``FLASH_DIFF_THRESHOLD``
the change is SUSTAINED (a new picture that holds). If they are similar, the
candidate was a FLASH (the picture returns), so it is filtered out.

A candidate at the very head of the clip (``t <= before_s``) is never SUSTAINED:
the "before" frame would be the clip's own first frame, so there is no preceding
picture inside the clip to cut from. A mined window frequently keeps the outgoing
shot's single-frame impact flash at frame 0, which would otherwise read as an
internal sustained boundary and wrongly flag every cut that starts there.
"""

from __future__ import annotations

import itertools
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCENE_THRESHOLD = 0.3  # ffmpeg `scene` score default
CONFIRM_BEFORE_S = 0.1  # frame taken this far before a candidate
CONFIRM_AFTER_S = 0.25  # frame taken this far after a candidate
FLASH_DIFF_THRESHOLD = 12.0  # mean abs gray diff (0-255) at or below == flash

#: Max candidates classify_scene_changes will process before bailing.
MAX_SCENE_CANDIDATES = 2000


class SceneError(RuntimeError):
    """Raised when scene detection cannot run."""


@dataclass(frozen=True)
class SceneChange:
    """One detected scene boundary with its classification."""

    time_s: float
    sustained: bool
    diff: float


def detect_scene_changes(source: Path, *, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """Return ascending candidate scene-change times (seconds) via ffmpeg select.

    Raises SceneError when the source is not a file or ffmpeg fails.
    """

    source = Path(source)
    if not source.is_file():
        raise SceneError(f"no such file: {source}")

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "info",
                "-i",
                str(source),
                "-vf",
                f"select='gt(scene,{threshold})',showinfo",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise SceneError("ffmpeg is not installed") from error

    if result.returncode != 0:
        raise SceneError(result.stderr[-800:])

    return sorted(float(v) for v in re.findall(r"pts_time:([\d.]+)", result.stderr))


def _gray_frame(source: Path, time_s: float, *, width: int = 64) -> bytes:
    """Decode one grayscale frame at ``time_s`` seconds, width ``width`` px.

    Returns b"" when ffmpeg succeeds but emits no frame (seek at/past EOF).
    Raises SceneError when ffmpeg fails.
    """

    source = Path(source)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{time_s:.3f}",
                "-i",
                str(source),
                "-vframes",
                "1",
                "-vf",
                f"scale={width}:-2,format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise SceneError("ffmpeg is not installed") from error

    if result.returncode != 0:
        raise SceneError(f"ffmpeg frame grab failed at {time_s:.3f}s")
    return result.stdout


def _mean_abs_diff(a: bytes, b: bytes) -> float:
    """Mean absolute difference between two grayscale frame byte strings."""

    n = min(len(a), len(b))
    if n == 0:
        raise SceneError("empty frame")
    return float(sum(abs(x - y) for x, y in zip(a[:n], b[:n], strict=False)) / n)


def is_head_candidate(t: float, *, before_s: float = CONFIRM_BEFORE_S) -> bool:
    """True when ``t`` is so close to the clip head that no earlier frame exists.

    Such a candidate is the clip's entry point (often the outgoing shot's
    single-frame impact flash), never an internal sustained cut.
    """

    return t - before_s <= 0.0


def classify_scene_changes(
    source: Path,
    *,
    threshold: float = SCENE_THRESHOLD,
    before_s: float = CONFIRM_BEFORE_S,
    after_s: float = CONFIRM_AFTER_S,
    diff_threshold: float = FLASH_DIFF_THRESHOLD,
) -> list[SceneChange]:
    """Classify each candidate as sustained or flash."""

    candidates = detect_scene_changes(source, threshold=threshold)
    if len(candidates) > MAX_SCENE_CANDIDATES:
        raise SceneError(f"too many scene candidates ({len(candidates)}); raise the threshold")

    changes: list[SceneChange] = []
    for t in candidates:
        if is_head_candidate(t, before_s=before_s):
            # The candidate sits at the clip head; see the module docstring.
            changes.append(SceneChange(time_s=t, sustained=False, diff=0.0))
            continue
        after = t + after_s
        a = _gray_frame(source, t - before_s)
        b = _gray_frame(source, after)
        if not a or not b:
            changes.append(SceneChange(time_s=t, sustained=True, diff=0.0))
            continue
        diff = _mean_abs_diff(a, b)
        changes.append(SceneChange(time_s=t, sustained=diff > diff_threshold, diff=diff))
    return changes


def sustained_scene_changes(source: Path, **kwargs: object) -> list[float]:
    """Return sustained scene-change times (seconds), ascending."""

    return [c.time_s for c in classify_scene_changes(source, **kwargs) if c.sustained]


def scene_segments(duration_s: float, boundaries: list[float]) -> list[tuple[float, float]]:
    """Split ``[0, duration_s]`` into contiguous segments at ``boundaries``."""

    clipped: list[float] = []
    for b in boundaries:
        if 0.0 < b < duration_s and b not in clipped:
            clipped.append(b)
    clipped.sort()

    segments: list[tuple[float, float]] = []
    start = 0.0
    for b in clipped:
        if b > start:
            segments.append((start, b))
        start = b
    if duration_s > start:
        segments.append((start, duration_s))
    return segments


def motion_score(
    source: Path,
    start_s: float,
    end_s: float,
    *,
    samples: int = 4,
    width: int = 64,
) -> float:
    """Mean consecutive-frame grayscale difference over ``[start_s, end_s]``.

    Returns 0-255; higher means more motion between sampled frames.
    """

    if end_s <= start_s:
        raise SceneError("empty motion window")
    if samples < 2:
        raise SceneError("empty motion window")

    frame_times = [start_s + i * (end_s - start_s) / (samples - 1) for i in range(samples)]
    frames = [_gray_frame(source, t, width=width) for t in frame_times]

    diffs: list[float] = []
    for a, b in itertools.pairwise(frames):
        if not a or not b:
            continue
        diffs.append(_mean_abs_diff(a, b))

    if not diffs:
        raise SceneError("no frames sampled in [start, end]")

    return round(sum(diffs) / len(diffs), 4)
