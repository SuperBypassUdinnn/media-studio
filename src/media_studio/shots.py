"""Mine one long source video into ranked one-scene shot candidates."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .ingest import IngestError, probe_media
from .scene import (
    SceneError,
    motion_score,
    scene_segments,
    sustained_scene_changes,
)

FFMPEG = "ffmpeg"


class ShotsError(RuntimeError):
    """Raised when a source cannot be mined or a candidate cannot be rendered."""


@dataclass(frozen=True)
class ShotCandidate:
    rank: int
    source: str
    in_s: float
    out_s: float
    duration_s: float
    scene_start_s: float
    scene_end_s: float
    motion: float


def _slug(text: str) -> str:
    """Return a filesystem-safe slug for ``text``."""

    return "".join(ch if ch.isalnum() else "-" for ch in text).strip("-") or "shots"


def mine_shots(
    source: Path,
    *,
    threshold: float = 0.3,
    min_duration_s: float = 0.5,
    max_duration_s: float = 2.5,
    samples: int = 4,
) -> list[ShotCandidate]:
    """Mine ``source`` into ranked one-scene shot candidates."""

    source = Path(source)
    if not source.is_file():
        raise ShotsError(f"no such file: {source}")

    try:
        info = probe_media(source)
    except IngestError as error:
        raise ShotsError(str(error)) from error

    try:
        boundaries = sustained_scene_changes(source, threshold=threshold)
    except SceneError as error:
        raise ShotsError(str(error)) from error

    segments = scene_segments(float(info.duration), boundaries)

    kept = [(s, e) for s, e in segments if e - s >= min_duration_s]

    candidates: list[ShotCandidate] = []
    for start, end in kept:
        seg_dur = end - start
        if seg_dur <= max_duration_s:
            windows = [(start, end)]
            motions = [motion_score(source, start, end, samples=samples)]
        else:
            windows = []
            motions = []
            step = max_duration_s / 2.0
            pos = start
            while pos + max_duration_s <= end + 1e-9:
                windows.append((pos, pos + max_duration_s))
                motions.append(motion_score(source, pos, pos + max_duration_s, samples=samples))
                pos += step
        best_index = max(range(len(motions)), key=lambda i: motions[i])
        window_start, window_end = windows[best_index]
        motion = motions[best_index]
        candidates.append(
            ShotCandidate(
                rank=0,
                source=str(source),
                in_s=round(window_start, 3),
                out_s=round(window_end, 3),
                duration_s=round(window_end - window_start, 3),
                scene_start_s=round(start, 3),
                scene_end_s=round(end, 3),
                motion=motion,
            )
        )

    candidates.sort(key=lambda c: (-c.motion, c.in_s))
    ranked = [replace(c, rank=index + 1) for index, c in enumerate(candidates)]

    if not ranked:
        raise ShotsError("no usable scenes")

    return ranked


def write_candidates(candidates: list[ShotCandidate], path: Path) -> None:
    """Serialize candidates to JSON at ``path``."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(c) for c in candidates]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_candidates(path: Path) -> list[ShotCandidate]:
    """Load candidates from a JSON file written by :func:`write_candidates`."""

    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShotsError(f"invalid candidates file {path}: {error}") from error

    try:
        return [
            ShotCandidate(
                rank=int(entry["rank"]),
                source=str(entry["source"]),
                in_s=float(entry["in_s"]),
                out_s=float(entry["out_s"]),
                duration_s=float(entry["duration_s"]),
                scene_start_s=float(entry["scene_start_s"]),
                scene_end_s=float(entry["scene_end_s"]),
                motion=float(entry["motion"]),
            )
            for entry in payload
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ShotsError(f"invalid candidates file {path}: {error}") from error


def render_candidates(
    candidates: list[ShotCandidate],
    out_dir: Path,
    *,
    scale: str = "1920x1080",
) -> list[Path]:
    """Render each candidate to a normalized clip in ``out_dir``."""

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = _slug(Path(candidates[0].source).stem) if candidates else "shots"
    written: list[Path] = []

    for candidate in candidates:
        out = out_dir / f"{slug}-{candidate.rank:02d}.mp4"
        argv = [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{candidate.in_s:.3f}",
            "-to",
            f"{candidate.out_s:.3f}",
            "-i",
            candidate.source,
            "-vf",
            f"scale={scale}",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ]
        try:
            result = subprocess.run(argv, capture_output=True, text=True)
        except FileNotFoundError as error:
            raise ShotsError("ffmpeg is not installed") from error

        tail = result.stderr.strip()[-800:]
        if result.returncode != 0:
            raise ShotsError(f"ffmpeg failed rendering {out}:\n{tail}")
        if not out.is_file():
            raise ShotsError(f"ffmpeg reported success but wrote no file: {out}")

        written.append(out)

    return written
