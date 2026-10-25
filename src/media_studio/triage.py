"""Footage triage: index a clip library, then review only what deserves pixels.

Design decision D32. The funnel is deliberately local: technical measurement first, cheap text
cards second, and only then batched contact sheets for a model to look at. Nothing here calls a
model, and every measurement is cached by content hash, so re-running an unchanged concept costs
nothing.

Artifacts, all inside the project:

    footage/index.jsonl      one card per clip, with a reason per reject
    footage/sheets/*.png     labelled batches for review
    footage/decisions.jsonl  the verdict ledger, keyed by clip and concept
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import media
from .manifest import Project

FOOTAGE_DIRECTORY = "footage"
INDEX_NAME = "index.jsonl"
DECISIONS_NAME = "decisions.jsonl"
SHEET_COLUMNS = 5
SHEET_ROWS = 4
SHEET_CELL_WIDTH = 320
SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Thresholds:
    """What counts as unusable footage. Every value is a deliberate, stated choice."""

    min_seconds: float = 1.0
    dark_mean: float = 45.0
    bright_mean: float = 205.0
    blurry_edges: float = 3.0
    static_motion: float = 0.4
    shake_sigma: float = 18.0


@dataclass(frozen=True)
class ClipFacts:
    """Everything measured about one clip, before any judgement."""

    path: Path
    sha256: str
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    mean_luma: float
    low_clip: float
    high_clip: float
    sharpness: float
    motion: float
    shake: float
    phash: str
    scene_cuts: int
    speech_seconds: float
    score: float = 0.0
    reasons: tuple[str, ...] = field(default=())

    @property
    def key(self) -> str:
        """Stable identity: the first twelve characters of the content hash."""

        return self.sha256[:12]

    @property
    def usable(self) -> bool:
        return not self.reasons

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "path": str(self.path),
            "sha256": self.sha256,
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "mean_luma": round(self.mean_luma, 1),
            "low_clip": round(self.low_clip, 3),
            "high_clip": round(self.high_clip, 3),
            "sharpness": round(self.sharpness, 2),
            "motion": round(self.motion, 2),
            "shake": round(self.shake, 3),
            "phash": self.phash,
            "scene_cuts": self.scene_cuts,
            "speech_seconds": round(self.speech_seconds, 2),
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ClipFacts:
        return cls(
            path=Path(str(payload["path"])),
            sha256=str(payload["sha256"]),
            duration=float(payload["duration"]),
            width=payload.get("width") if isinstance(payload.get("width"), int) else None,
            height=payload.get("height") if isinstance(payload.get("height"), int) else None,
            fps=float(payload["fps"]) if payload.get("fps") is not None else None,
            mean_luma=float(payload["mean_luma"]),
            low_clip=float(payload["low_clip"]),
            high_clip=float(payload["high_clip"]),
            sharpness=float(payload["sharpness"]),
            motion=float(payload["motion"]),
            shake=float(payload["shake"]),
            phash=str(payload["phash"]),
            scene_cuts=int(payload["scene_cuts"]),
            speech_seconds=float(payload.get("speech_seconds") or 0.0),
        )


def sha256_of(path: Path) -> str:
    """Content hash, streamed so a long clip does not load into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def measure_clip(path: Path, *, scratch: Path) -> ClipFacts:
    """Measure one clip. This is the expensive part, and the cache exists for it."""

    info = media.probe(path)
    stats = media.frame_stats(path)
    speech = media.speech_segments(path) if info.audio_codec else []
    return ClipFacts(
        path=path,
        sha256=sha256_of(path),
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        mean_luma=stats.mean,
        low_clip=stats.low_clip,
        high_clip=stats.high_clip,
        sharpness=media.edge_strength(path),
        motion=media.motion_level(path),
        shake=media.shake_level(path, scratch=scratch),
        phash=media.perceptual_hash(path),
        scene_cuts=len(media.scene_cuts(path)),
        speech_seconds=sum(end - start for start, end in speech),
    )


def score_clip(facts: ClipFacts, keywords: tuple[str, ...] = ()) -> float:
    """Rank without a model: usable footage scores on detail, movement, and exposure.

    A local score is honest about being a heuristic, and it costs nothing to recompute when the
    thresholds change.
    """

    if facts.reasons:
        return 0.0
    detail = min(facts.sharpness / 40.0, 1.0)
    movement = min(facts.motion / 12.0, 1.0)
    exposure = 1.0 - min(abs(facts.mean_luma - 120.0) / 120.0, 1.0)
    variety = min(facts.scene_cuts / 4.0, 1.0)
    speech = min(facts.speech_seconds / max(facts.duration, 0.1), 1.0)
    weight = 1.0
    if keywords:
        haystack = f"{facts.path.stem}".lower()
        weight = 1.25 if any(word in haystack for word in keywords) else 0.9
    return round(
        (0.35 * detail + 0.3 * movement + 0.2 * exposure + 0.1 * variety + 0.05 * speech) * weight,
        4,
    )


def reasons_for(facts: ClipFacts, thresholds: Thresholds) -> list[str]:
    """Why this clip should not be used, in the order a person would check."""

    reasons: list[str] = []
    if facts.duration < thresholds.min_seconds:
        reasons.append(f"too short ({facts.duration:.2f}s)")
    if facts.mean_luma < thresholds.dark_mean:
        reasons.append(f"too dark (mean luma {facts.mean_luma:.0f})")
    elif facts.mean_luma > thresholds.bright_mean:
        reasons.append(f"blown out (mean luma {facts.mean_luma:.0f})")
    if facts.sharpness < thresholds.blurry_edges:
        reasons.append(f"soft or featureless (edges {facts.sharpness:.1f})")
    if facts.motion < thresholds.static_motion:
        reasons.append(f"no movement (motion {facts.motion:.2f})")
    if facts.shake > thresholds.shake_sigma:
        reasons.append(f"shaky (sigma {facts.shake:.1f})")
    return reasons


@dataclass(frozen=True)
class TriageIndex:
    """Every clip in a library, with duplicates grouped and rejects explained."""

    facts: tuple[ClipFacts, ...]
    duplicates: dict[str, list[str]]
    measured: int
    reused: int

    @property
    def usable(self) -> tuple[ClipFacts, ...]:
        return tuple(item for item in self.facts if item.usable)

    @property
    def summary(self) -> dict[str, object]:
        rejected = sum(1 for item in self.facts if not item.usable)
        return {
            "clips": len(self.facts),
            "usable": len(self.usable),
            "rejected": rejected,
            "duplicate_groups": len(self.duplicates),
            "measured_now": self.measured,
            "reused_from_cache": self.reused,
        }

    def to_jsonl(self) -> str:
        return "".join(json.dumps(item.to_dict()) + "\n" for item in self.facts)


def read_cache(path: Path) -> dict[str, ClipFacts]:
    """Load previously measured clips, keyed by content hash."""

    if not path.is_file():
        return {}
    cached: dict[str, ClipFacts] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        cached[str(payload["sha256"])] = ClipFacts.from_dict(payload)
    return cached


def clip_paths(
    directory: Path, extensions: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi")
) -> list[Path]:
    """Every media file under a directory, in a stable order."""

    if directory.is_file():
        return [directory]
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions and not path.name.startswith(".")
    )


def build_index(
    paths: list[Path],
    *,
    scratch: Path,
    thresholds: Thresholds | None = None,
    keywords: tuple[str, ...] = (),
    cache: dict[str, ClipFacts] | None = None,
    measure: object = None,
) -> TriageIndex:
    """Measure every clip, reusing the cache by content hash, then explain each reject.

    `measure` exists so a test can count real measurements; the default is `measure_clip`.
    """

    limits = thresholds or Thresholds()
    known = cache if cache is not None else {}
    measurer = measure or measure_clip
    measured = 0
    reused = 0
    facts: list[ClipFacts] = []

    for path in paths:
        digest = sha256_of(path)
        cached = known.get(digest)
        if cached is not None:
            reused += 1
            base = cached
        else:
            base = measurer(path, scratch=scratch)  # type: ignore[operator]
            measured += 1
        reasons = tuple(reasons_for(base, limits))
        facts.append(
            ClipFacts(
                path=base.path,
                sha256=base.sha256,
                duration=base.duration,
                width=base.width,
                height=base.height,
                fps=base.fps,
                mean_luma=base.mean_luma,
                low_clip=base.low_clip,
                high_clip=base.high_clip,
                sharpness=base.sharpness,
                motion=base.motion,
                shake=base.shake,
                phash=base.phash,
                scene_cuts=base.scene_cuts,
                speech_seconds=base.speech_seconds,
                score=score_clip(base, keywords),
                reasons=reasons,
            )
        )

    # A duplicate is only a duplicate when the picture matches; the content hash catches an
    # identical file and the perceptual hash catches the same shot re-encoded.
    by_phash: dict[str, list[str]] = {}
    for item in facts:
        if item.usable:
            by_phash.setdefault(item.phash, []).append(str(item.path))
    duplicates = {digest: sorted(paths_) for digest, paths_ in by_phash.items() if len(paths_) > 1}

    ordered = tuple(sorted(facts, key=lambda item: (-item.score, str(item.path))))
    return TriageIndex(facts=ordered, duplicates=duplicates, measured=measured, reused=reused)


def footage_dir(project: Project) -> Path:
    return project.root / FOOTAGE_DIRECTORY


def write_index(project: Project, index: TriageIndex) -> Path:
    """Persist the cards, which double as the measurement cache."""

    directory = footage_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / INDEX_NAME
    path.write_text(index.to_jsonl(), encoding="utf-8")
    return path


def read_index(project: Project) -> dict[str, ClipFacts]:
    """Read the persisted cards as a cache."""

    return read_cache(footage_dir(project) / INDEX_NAME)


def concept_key(concept: str) -> str:
    """A short, stable key for a concept, so decisions can be reused."""

    normalized = SLUG.sub("-", concept.lower()).strip("-")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{normalized[:40] or 'concept'}-{digest}"


@dataclass(frozen=True)
class Decision:
    """A verdict a reviewer reached about one clip for one concept."""

    key: str
    concept: str
    verdict: str  # in | hold | out
    reason: str = ""
    score: float = 0.0
    decided_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "concept": self.concept,
            "verdict": self.verdict,
            "reason": self.reason,
            "score": self.score,
            "decided_at": self.decided_at,
        }


def read_decisions(project: Project) -> list[Decision]:
    """Every recorded verdict, oldest first."""

    path = footage_dir(project) / DECISIONS_NAME
    if not path.is_file():
        return []
    decisions: list[Decision] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        decisions.append(
            Decision(
                key=str(payload["key"]),
                concept=str(payload["concept"]),
                verdict=str(payload["verdict"]),
                reason=str(payload.get("reason") or ""),
                score=float(payload.get("score") or 0.0),
                decided_at=str(payload.get("decided_at") or ""),
            )
        )
    return decisions


def record_decision(
    project: Project,
    *,
    key: str,
    concept: str,
    verdict: str,
    reason: str = "",
    score: float = 0.0,
) -> Decision:
    """Append a verdict. A later verdict for the same clip and concept replaces the earlier one."""

    if verdict not in ("in", "hold", "out"):
        raise ValueError(f"verdict must be in, hold, or out, got {verdict!r}")
    directory = footage_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    decision = Decision(
        key=key,
        concept=concept,
        verdict=verdict,
        reason=reason,
        score=score,
        decided_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )
    kept = [
        item
        for item in read_decisions(project)
        if not (item.key == key and item.concept == concept)
    ]
    path = directory / DECISIONS_NAME
    path.write_text(
        "".join(json.dumps(item.to_dict()) + "\n" for item in [*kept, decision]),
        encoding="utf-8",
    )
    return decision


def undecided(index: TriageIndex, decisions: list[Decision], concept: str) -> list[ClipFacts]:
    """Usable clips with no verdict yet for this concept: the only ones worth reviewing."""

    decided = {item.key for item in decisions if item.concept == concept}
    return [item for item in index.usable if item.key not in decided]


def clip_strip(path: Path, destination: Path, *, frames: int = 4, width: int = 480) -> Path:
    """A horizontal strip of one clip, for the second pass on a candidate."""

    info = media.probe(path)
    interval = max(info.duration / (frames + 1), 0.05)
    destination.parent.mkdir(parents=True, exist_ok=True)
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-nostats",
            "-y",
            "-i",
            str(path),
            "-vf",
            f"fps=1/{interval:.3f},scale={width}:-1,tile={frames}x1",
            "-frames:v",
            "1",
            str(destination),
        ],
        label=f"build a strip for {path.name}",
        expect_stdout=False,
    )
    return destination


def build_sheets(
    facts: list[ClipFacts],
    directory: Path,
    *,
    columns: int = SHEET_COLUMNS,
    rows: int = SHEET_ROWS,
    cell_width: int = SHEET_CELL_WIDTH,
) -> list[Path]:
    """Batch candidates into labelled contact sheets, one frame per clip.

    Each thumbnail is named `<index>-<key>.jpg` so the montage label carries the key a reviewer
    can answer with; a later cell-to-key mapping is unnecessary.
    """

    if not facts:
        return []
    per_sheet = columns * rows
    directory.mkdir(parents=True, exist_ok=True)
    scratch = directory / ".cells"
    scratch.mkdir(exist_ok=True)
    sheets: list[Path] = []

    for sheet_number, start in enumerate(range(0, len(facts), per_sheet), start=1):
        batch = facts[start : start + per_sheet]
        cells: list[Path] = []
        for offset, item in enumerate(batch):
            cell = scratch / f"{start + offset + 1:03d}-{item.key}.jpg"
            at = max(item.duration / 2, 0.05)
            media.run_command(
                [
                    media.FFMPEG,
                    "-hide_banner",
                    "-nostats",
                    "-y",
                    "-ss",
                    f"{at:.3f}",
                    "-i",
                    str(item.path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={cell_width}:-2",
                    str(cell),
                ],
                label=f"extract a cell for {item.path.name}",
                expect_stdout=False,
            )
            cells.append(cell)
        sheet = directory / f"sheet-{sheet_number:02d}.jpg"
        media.run_command(
            [
                "magick",
                "montage",
                *[str(cell) for cell in cells],
                "-label",
                "%f",
                "-tile",
                f"{columns}x{rows}",
                "-geometry",
                "+6+6",
                "-background",
                "#111111",
                "-fill",
                "white",
                "-pointsize",
                "14",
                str(sheet),
            ],
            label=f"build contact sheet {sheet.name}",
            expect_stdout=False,
        )
        sheets.append(sheet)
    return sheets
