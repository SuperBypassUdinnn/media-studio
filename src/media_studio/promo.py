"""Dual-aspect promotional video orchestrator (`9:16` + `16:9`).

Composes brand reference packs, TTS/cleaned voiceover, generative/fallback
B-roll, EffectCraft promo presets, Playwright HTML motion cards, FFmpeg
transitions, CC0 SFX mixing, subject-tracked reframe, safe-zone captions,
and FFprobe/LUFS/`ms see` quality control.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from media_studio import audio, media, video
from media_studio.captions import CaptionsError, extract_word_timestamps, render_captions
from media_studio.effectcraft import EffectCraftError, render_promo_preset
from media_studio.gen.base import GenError, GenRequest
from media_studio.gen.pipeline import generate_asset
from media_studio.html_motion import HtmlMotionError, render_html_motion
from media_studio.manifest import Project
from media_studio.mlt import Clip, Timeline
from media_studio.reframe import ASPECTS, OUTPUT_SIZE, reframe_video, safe_zone_for
from media_studio.see import SeeError, peek
from media_studio.sfx import SfxCue, SfxError, mix_transition_sfx
from media_studio.transitions import TransitionError, apply_transition
from media_studio.tts import TtsError, WordTiming, synthesize_speech

SCRATCH_ROOT = Path("/tmp/opencode")
BEAT_KINDS = ("hook", "problem", "demo", "proof", "cta")
DEFAULT_ASPECTS = ("9:16", "16:9")
DEFAULT_DELIVERABLES = ("reel-9x16", "master-16x9")

# Pacing thresholds (words per second) and acceptable beat duration window.
TEXT_DENSITY_MAX_WPS = 4.5
BEAT_MIN_SECONDS = 1.0
BEAT_MAX_SECONDS = 12.0
DELIVERABLE_ASPECT_MAP = {
    "reel-9x16": "9:16",
    "master-16x9": "16:9",
    "square-1x1": "1:1",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class PromoError(RuntimeError):
    """Raised when promo planning, rendering, or QC fails."""


@dataclass(frozen=True)
class PromoBeat:
    """One typed narrative beat in a promotional video plan."""

    kind: str
    text: str
    duration: float
    asset_id: str | None = None
    template: str | None = None
    preset: str | None = None
    transition: str = "whip_pan_left"
    sfx: str = "whoosh-fast"
    gen_prompt: str | None = None
    layout_zone: str = "hero-center"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "duration": round(float(self.duration), 3),
            "asset_id": self.asset_id,
            "template": self.template,
            "preset": self.preset,
            "transition": self.transition,
            "sfx": self.sfx,
            "gen_prompt": self.gen_prompt,
            "layout_zone": self.layout_zone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromoBeat:
        return cls(
            kind=str(data["kind"]),
            text=str(data["text"]),
            duration=float(data["duration"]),
            asset_id=data.get("asset_id"),
            template=data.get("template"),
            preset=data.get("preset"),
            transition=str(data.get("transition") or "whip_pan_left"),
            sfx=str(data.get("sfx") or "whoosh-fast"),
            gen_prompt=data.get("gen_prompt"),
            layout_zone=str(data.get("layout_zone") or "hero-center"),
        )


#: Bounding boxes (fractions of canvas, 0..1) for each layout zone. Every zone
#: places a card/preset box and a caption box so they can be checked for overlap
#: against each other and against the platform safe zone.
LAYOUT_ZONE_BOXES: dict[str, dict[str, dict[str, float]]] = {
    "hero-center": {
        "card": {"top": 0.30, "bottom": 0.62, "left": 0.15, "right": 0.85},
        "caption": {"top": 0.78, "bottom": 0.92, "left": 0.10, "right": 0.90},
    },
    "split-stage": {
        "card": {"top": 0.25, "bottom": 0.70, "left": 0.05, "right": 0.48},
        "caption": {"top": 0.30, "bottom": 0.60, "left": 0.52, "right": 0.95},
    },
    "card-focus": {
        "card": {"top": 0.20, "bottom": 0.80, "left": 0.10, "right": 0.90},
        "caption": {"top": 0.82, "bottom": 0.94, "left": 0.10, "right": 0.90},
    },
    "cta-centered": {
        "card": {"top": 0.55, "bottom": 0.72, "left": 0.20, "right": 0.80},
        "caption": {"top": 0.78, "bottom": 0.90, "left": 0.10, "right": 0.90},
    },
}


def _rects_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    """True when two axis-aligned ``{top, bottom, left, right}`` rects overlap."""

    return not (
        a["right"] <= b["left"]
        or a["left"] >= b["right"]
        or a["bottom"] <= b["top"]
        or a["top"] >= b["bottom"]
    )


def check_layout_collisions(beat: PromoBeat, *, aspect: str, width: int, height: int) -> list[str]:
    """Check a beat's layout zone for card/caption and safe-zone collisions.

    Computes the pixel bounding box of the card/preset and the word-highlight
    caption zone for ``beat.layout_zone``, then compares them against each other
    and against ``safe_zone_for(aspect, width, height)``. Returns a list of
    human-readable adjustment messages (empty when there is no collision). The
    messages describe how the card anchor Y or card scale should be adjusted so
    UI cards, titles, and captions never collide with platform UI or captions.
    """

    zone_name = beat.layout_zone or "hero-center"
    boxes = LAYOUT_ZONE_BOXES.get(zone_name, LAYOUT_ZONE_BOXES["hero-center"])
    safe = safe_zone_for(aspect, width, height)

    def to_pixels(box: dict[str, float]) -> dict[str, float]:
        return {
            "top": round(box["top"] * height),
            "bottom": round(box["bottom"] * height),
            "left": round(box["left"] * width),
            "right": round(box["right"] * width),
        }

    card = to_pixels(boxes["card"])
    caption = to_pixels(boxes["caption"])

    issues: list[str] = []

    if _rects_overlap(card, caption):
        issues.append(
            f"layout_zone={zone_name}: card box overlaps caption box; "
            "scale card down or shift card anchor Y to avoid collision"
        )

    bottom_limit = height - safe["bottom"]
    if card["top"] < safe["top"]:
        issues.append(
            f"layout_zone={zone_name}: card intrudes into top safe zone "
            f"({safe['top']}px); shift card anchor Y down"
        )
    if card["bottom"] > bottom_limit:
        issues.append(
            f"layout_zone={zone_name}: card intrudes into bottom safe zone "
            f"({safe['bottom']}px); shift card anchor Y up"
        )

    return issues


def resolve_layout_placement(
    beat: PromoBeat, *, aspect: str, width: int, height: int
) -> dict[str, Any]:
    """Resolve a non-colliding card placement for ``beat``.

    Calculates the card bounding box for ``beat.layout_zone`` and compares it
    against the caption box and the platform safe zone (via
    :func:`safe_zone_for`). When an overlap or safe-zone intrusion is detected,
    automatically adjusts the card ``anchor_y`` (shifting toward the center /
    away from the caption band) and/or shrinks the card ``scale`` so the
    adjusted element fits strictly within the safe area without colliding with
    captions.

    Returns a dict with ``anchor_y``, ``scale``, ``adjusted``,
    ``collision_avoided`` and ``reason`` suitable for passing as
    ``card_anchor_y`` / ``card_scale`` params to the HTML motion templates.
    """

    zone_name = beat.layout_zone or "hero-center"
    boxes = LAYOUT_ZONE_BOXES.get(zone_name, LAYOUT_ZONE_BOXES["hero-center"])
    safe = safe_zone_for(aspect, width, height)

    card_box = dict(boxes["card"])
    caption_box = dict(boxes["caption"])

    # Safe-zone limits expressed as canvas fractions (0..1), matching the card box units.
    top_limit = safe["top"] / height
    bottom_limit = (height - safe["bottom"]) / height

    anchor_y = card_box["top"] + (card_box["bottom"] - card_box["top"]) / 2.0
    scale = 1.0
    adjusted = False
    reasons: list[str] = []

    def overlaps(a: dict[str, float], b: dict[str, float]) -> bool:
        return not (
            a["right"] <= b["left"]
            or a["left"] >= b["right"]
            or a["bottom"] <= b["top"]
            or a["top"] >= b["bottom"]
        )

    # 1. Card/caption overlap: shrink the card until it clears the caption zone.
    if overlaps(card_box, caption_box):
        # Scale down around the card center until no overlap (max 6 shrink steps).
        for _ in range(6):
            trial = scale * 0.85
            cx = (card_box["left"] + card_box["right"]) / 2.0
            cy = anchor_y
            card_w = (card_box["right"] - card_box["left"]) * trial
            card_h = (card_box["bottom"] - card_box["top"]) * trial
            trial_box = {
                "top": cy - card_h / 2.0,
                "bottom": cy + card_h / 2.0,
                "left": cx - card_w / 2.0,
                "right": cx + card_w / 2.0,
            }
            if not overlaps(trial_box, caption_box):
                scale = trial
                adjusted = True
                reasons.append("scaled card down to clear caption zone")
                break
            scale = trial

    # 2. Safe-zone intrusions: shift anchor_y away from platform UI bands.
    card_w = (card_box["right"] - card_box["left"]) * scale
    card_h = (card_box["bottom"] - card_box["top"]) * scale

    # Keep the whole scaled card inside [top_limit, bottom_limit] vertically.
    min_anchor_y = top_limit + card_h / 2.0
    max_anchor_y = bottom_limit - card_h / 2.0

    if anchor_y < min_anchor_y:
        anchor_y = min_anchor_y
        adjusted = True
        reasons.append("shifted card anchor Y down out of top safe zone")
    if anchor_y > max_anchor_y:
        anchor_y = max_anchor_y
        adjusted = True
        reasons.append("shifted card anchor Y up out of bottom safe zone")

    # If still colliding after shifting, fall back to scaling further.
    trial_box = {
        "top": anchor_y - card_h / 2.0,
        "bottom": anchor_y + card_h / 2.0,
        "left": (card_box["left"] + card_box["right"]) / 2.0 - card_w / 2.0,
        "right": (card_box["left"] + card_box["right"]) / 2.0 + card_w / 2.0,
    }
    still_bad = (
        overlaps(trial_box, caption_box)
        or trial_box["top"] < top_limit
        or trial_box["bottom"] > bottom_limit
    )
    if still_bad:
        for _ in range(6):
            scale *= 0.85
            card_h = (card_box["bottom"] - card_box["top"]) * scale
            card_w = (card_box["right"] - card_box["left"]) * scale
            anchor_y = min(max(anchor_y, top_limit + card_h / 2.0), bottom_limit - card_h / 2.0)
            trial_box = {
                "top": anchor_y - card_h / 2.0,
                "bottom": anchor_y + card_h / 2.0,
                "left": (card_box["left"] + card_box["right"]) / 2.0 - card_w / 2.0,
                "right": (card_box["left"] + card_box["right"]) / 2.0 + card_w / 2.0,
            }
            if (
                not overlaps(trial_box, caption_box)
                and trial_box["top"] >= top_limit
                and trial_box["bottom"] <= bottom_limit
            ):
                adjusted = True
                reasons.append("scaled card further to fit safe zone")
                break

    collision_avoided = (
        not overlaps(trial_box, caption_box)
        and trial_box["top"] >= top_limit
        and trial_box["bottom"] <= bottom_limit
    )

    return {
        "zone": zone_name,
        "anchor_y": round(anchor_y, 4),
        "scale": round(scale, 4),
        "adjusted": adjusted,
        "collision_avoided": collision_avoided,
        "reason": "; ".join(reasons) if reasons else "no collision",
    }


@dataclass(frozen=True)
class PromoPlan:
    """Complete beat-by-beat promotional video plan."""

    project_slug: str
    aspects: tuple[str, ...]
    script: str
    beats: tuple[PromoBeat, ...]
    estimated_gen_calls: int
    references: tuple[str, ...]
    total_duration: float
    out_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "aspects": list(self.aspects),
            "script": self.script,
            "beats": [beat.to_dict() for beat in self.beats],
            "estimated_gen_calls": self.estimated_gen_calls,
            "references": list(self.references),
            "total_duration": round(float(self.total_duration), 3),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, out_path: Path | None = None) -> PromoPlan:
        beats = tuple(PromoBeat.from_dict(item) for item in data.get("beats") or ())
        return cls(
            project_slug=str(data["project_slug"]),
            aspects=tuple(str(a) for a in data.get("aspects") or DEFAULT_ASPECTS),
            script=str(data["script"]),
            beats=beats,
            estimated_gen_calls=int(data.get("estimated_gen_calls", 0)),
            references=tuple(str(r) for r in data.get("references") or ()),
            total_duration=float(data.get("total_duration", sum(b.duration for b in beats))),
            out_path=out_path,
        )


@dataclass(frozen=True)
class PromoRenderResult:
    """Outcome of :func:`render_promo`, including storyboard gate state."""

    gated: bool
    storyboard: Path | None
    deliverables: dict[str, Path]
    qc: dict[str, Any]
    notes: tuple[str, ...] = ()
    otio_path: Path | None = None
    mlt_path: Path | None = None


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_beat_cache_key(
    project: Project,
    beat: PromoBeat,
    idx: int,
    *,
    aspect: str,
    fps: int,
) -> str:
    """Content-addressed cache key for one rendered beat.

    Deterministic SHA256 over the beat payload, aspect/fps, target resolution,
    any referenced asset file hash, and brand palette/font tokens.
    """

    mixed = hashlib.sha256()
    mixed.update(json.dumps(beat.to_dict(), sort_keys=True).encode("utf-8"))
    mixed.update(f"|aspect={aspect}|fps={fps}".encode())

    out_w, out_h = OUTPUT_SIZE[aspect]
    mixed.update(f"|resolution={out_w}x{out_h}".encode())

    if beat.asset_id:
        asset_rel = project.assets.get(beat.asset_id)
        if asset_rel:
            asset_path = project.root / asset_rel
            if asset_path.is_file():
                mixed.update(f"|asset={beat.asset_id}:{_sha256_of_file(asset_path)}".encode())

    brand_tokens = {
        "primary": project.brand.palette.get("primary"),
        "accent": project.brand.palette.get("accent"),
        "heading": project.brand.fonts.get("heading"),
        "body": project.brand.fonts.get("body"),
    }
    mixed.update(f"|brand={json.dumps(brand_tokens, sort_keys=True)}".encode())

    return mixed.hexdigest()


def get_cached_beat(cache_dir: Path, key: str) -> Path | None:
    """Return the cached clip path for ``key`` if it exists, else ``None``."""

    candidate = Path(cache_dir) / f"{key}.mp4"
    return candidate if candidate.is_file() else None


def put_cached_beat(cache_dir: Path, key: str, clip: Path) -> Path:
    """Atomically copy a rendered clip into the cache as ``<key>.mp4``.

    A missing source (e.g. a stubbed render in a unit test) is skipped gracefully
    so the caller is not forced to materialise a file just to populate the cache.
    """

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{key}.mp4"
    source = Path(clip)
    if not source.is_file():
        return target
    temporary = cache_dir / f"{key}.tmp"
    shutil.copyfile(source, temporary)
    temporary.replace(target)
    return target


def _rational_time(value: float, rate: int) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "value": float(value), "rate": int(rate)}


def _time_range(start: float, duration: float, rate: int) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational_time(start, rate),
        "duration": _rational_time(duration, rate),
    }


def _external_reference(target_url: str) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "ExternalReference.1", "target_url": target_url}


def export_otio_timeline(
    project: Project,
    plan: PromoPlan,
    deliverable_id: str,
    deliverable_path: Path,
    *,
    fps: int,
) -> Path:
    """Write an OpenTimelineIO 1.0 JSON timeline for the assembled promo."""

    out_dir = project.root / "out" / "promo"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "timeline.otio.json"

    video_children: list[dict[str, Any]] = []
    cursor = 0.0
    for idx, beat in enumerate(plan.beats):
        video_children.append(
            {
                "OTIO_SCHEMA": "Clip.1",
                "name": f"beat-{idx:02d}",
                "source_range": _time_range(cursor, beat.duration, fps),
                "media_reference": _external_reference(str(deliverable_path)),
            }
        )
        if idx < len(plan.beats) - 1:
            out_point = cursor + beat.duration
            video_children.append(
                {
                    "OTIO_SCHEMA": "Transition.1",
                    "name": beat.transition,
                    "transition_type": "Custom",
                    "in_offset": _rational_time(max(0.0, out_point - 0.18), fps),
                    "out_offset": _rational_time(out_point, fps),
                }
            )
        cursor += beat.duration

    tracks = [
        {
            "OTIO_SCHEMA": "Track.1",
            "name": "Video Track",
            "kind": "Video",
            "children": video_children,
        },
        {
            "OTIO_SCHEMA": "Track.1",
            "name": "Voiceover Track",
            "kind": "Audio",
            "children": [
                {
                    "OTIO_SCHEMA": "Clip.1",
                    "name": "voiceover",
                    "source_range": _time_range(0.0, plan.total_duration, fps),
                    "media_reference": _external_reference(str(deliverable_path)),
                }
            ],
        },
        {
            "OTIO_SCHEMA": "Track.1",
            "name": "Music Track",
            "kind": "Audio",
            "children": [
                {
                    "OTIO_SCHEMA": "Clip.1",
                    "name": "music",
                    "source_range": _time_range(0.0, plan.total_duration, fps),
                    "media_reference": _external_reference(str(deliverable_path)),
                }
            ],
        },
    ]

    payload = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": f"{project.slug}-promo",
        "global_start_time": _rational_time(0.0, fps),
        "tracks": {"OTIO_SCHEMA": "Stack.1", "children": tracks},
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def export_mlt_timeline(
    project: Project,
    plan: PromoPlan,
    deliverable_id: str,
    deliverable_path: Path,
    *,
    fps: int,
) -> Path:
    """Write an MLT XML timeline for the assembled promo."""

    out_dir = project.root / "out" / "promo"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "timeline.mlt"

    aspect = _aspect_for_deliverable(project, deliverable_id)
    out_w, out_h = OUTPUT_SIZE[aspect]

    clips: list[Clip] = []
    cursor = 0.0
    for idx, beat in enumerate(plan.beats):
        clips.append(
            Clip(
                resource=deliverable_path,
                duration=beat.duration,
                in_point=cursor,
                transition=beat.transition if idx > 0 else "cut",
            )
        )
        cursor += beat.duration

    timeline = Timeline(
        width=out_w,
        height=out_h,
        fps=float(fps),
        clips=tuple(clips),
        title=f"{project.slug}-promo",
    )
    target.write_text(timeline.to_xml(), encoding="utf-8")
    return target


def save_promo_plan(plan: PromoPlan, path: Path) -> Path:
    """Persist ``plan`` as formatted JSON at ``path``."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan.to_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def load_promo_plan(path: Path) -> PromoPlan:
    """Load a :class:`PromoPlan` from a JSON file."""

    source = Path(path)
    if not source.is_file():
        raise PromoError(f"promo plan not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PromoError(f"invalid promo plan JSON in {source}: {error}") from error
    if not isinstance(payload, dict):
        raise PromoError(f"promo plan in {source} must be a JSON object")
    return PromoPlan.from_dict(payload, out_path=source)


def _split_script_segments(script: str) -> list[str]:
    segments = [part.strip() for part in _SENTENCE_SPLIT.split(script.strip()) if part.strip()]
    if len(segments) >= len(BEAT_KINDS):
        # Collapse extra middle segments into the demo beat so we emit 5 clean beats.
        head = segments[0]
        problem = segments[1]
        cta = segments[-1]
        proof = segments[-2]
        demo = " ".join(segments[2:-2])
        return [head, problem, demo, proof, cta]

    defaults = [
        "Stop scrolling and see what changes today.",
        "Manual workflows slow every launch down.",
        "One deterministic pipeline handles every format.",
        "Built for broadcast speed and measurable quality.",
        "Start your next campaign now.",
    ]
    padded = list(segments)
    while len(padded) < len(BEAT_KINDS):
        padded.append(defaults[len(padded)])
    return padded


def _beat_durations(total_duration: float) -> list[float]:
    weights = (0.12, 0.22, 0.30, 0.20, 0.16)
    raw = [round(total_duration * w, 3) for w in weights]
    diff = round(total_duration - sum(raw), 3)
    raw[-1] = round(raw[-1] + diff, 3)
    return raw


def _collect_references(project: Project) -> tuple[str, ...]:
    refs = project.brand.references
    ordered = [*refs.product, *refs.spokesperson, *refs.style_board]
    seen: set[str] = set()
    unique: list[str] = []
    for item in ordered:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return tuple(unique)


def _visual_asset_candidates(project: Project) -> list[str]:
    video_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    ref_ids = set(_collect_references(project))
    candidates: list[str] = []
    for asset_id, rel in project.assets.items():
        if "logo" in asset_id.lower() or asset_id in ref_ids:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix in video_exts or suffix in image_exts:
            candidates.append(asset_id)
    return candidates


def plan_promo(
    project: Project,
    *,
    script: str,
    aspects: tuple[str, ...] = DEFAULT_ASPECTS,
    out_path: Path | None = None,
    target_duration: float = 30.0,
) -> PromoPlan:
    """Decompose ``script`` into typed promo beats with brand references and quota estimate."""

    cleaned_script = script.strip()
    if not cleaned_script:
        raise PromoError("script must not be empty")
    if target_duration <= 0.0:
        raise PromoError(f"target_duration must be positive, got {target_duration}")
    if not aspects:
        raise PromoError("at least one aspect ratio is required")
    for aspect in aspects:
        if aspect not in ASPECTS:
            raise PromoError(f"unknown aspect {aspect!r}; expected one of {sorted(ASPECTS)}")

    segments = _split_script_segments(cleaned_script)
    durations = _beat_durations(target_duration)
    references = _collect_references(project)
    visual_assets = _visual_asset_candidates(project)

    beat_configs: dict[str, dict[str, Any]] = {
        "hook": {
            "preset": "promo-hook-slam",
            "template": None,
            "transition": "whip_pan_left",
            "sfx": "impact-hit",
            "needs_broll": True,
        },
        "problem": {
            "preset": "promo-kinetic-callout",
            "template": None,
            "transition": "zoom_punch_in",
            "sfx": "whoosh-fast",
            "needs_broll": True,
        },
        "demo": {
            "preset": None,
            "template": "saas-feature-card",
            "transition": "zoom_punch_in",
            "sfx": "ui-pop",
            "needs_broll": False,
        },
        "proof": {
            "preset": None,
            "template": "metric-counter",
            "transition": "flash_white",
            "sfx": "riser-build",
            "needs_broll": False,
        },
        "cta": {
            "preset": "promo-cta-endcard",
            "template": None,
            "transition": "light_leak_warm",
            "sfx": "impact-sub",
            "needs_broll": False,
        },
    }

    beats: list[PromoBeat] = []
    asset_cursor = 0
    estimated_gen_calls = 0

    for kind, text, dur in zip(BEAT_KINDS, segments, durations, strict=True):
        cfg = beat_configs[kind]
        asset_id: str | None = None
        gen_prompt: str | None = None

        if cfg["needs_broll"]:
            if asset_cursor < len(visual_assets):
                asset_id = visual_assets[asset_cursor]
                asset_cursor += 1
            else:
                gen_prompt = f"{project.title} {kind} b-roll: {text}"
                estimated_gen_calls += 1

        beats.append(
            PromoBeat(
                kind=kind,
                text=text,
                duration=dur,
                asset_id=asset_id,
                template=cfg["template"],
                preset=cfg["preset"],
                transition=cfg["transition"],
                sfx=cfg["sfx"],
                gen_prompt=gen_prompt,
            )
        )

    plan = PromoPlan(
        project_slug=project.slug,
        aspects=tuple(aspects),
        script=cleaned_script,
        beats=tuple(beats),
        estimated_gen_calls=estimated_gen_calls,
        references=references,
        total_duration=round(sum(b.duration for b in beats), 3),
        out_path=Path(out_path) if out_path is not None else None,
    )
    if out_path is not None:
        save_promo_plan(plan, Path(out_path))
    return plan


def _write_storyboard(project: Project, plan: PromoPlan, destination: Path) -> Path:
    """Render an informative visual storyboard contact-sheet PNG for human quota approval."""

    destination.parent.mkdir(parents=True, exist_ok=True)

    n_beats = max(1, len(plan.beats))
    tile_w, tile_h = 420, 260
    header_h = 60
    sheet_w = n_beats * tile_w + (n_beats + 1) * 16
    sheet_h = header_h + tile_h + 24

    canvas = Image.new("RGBA", (sheet_w, sheet_h), (8, 12, 22, 255))
    draw = ImageDraw.Draw(canvas)

    accent_hex = project.brand.palette.get("accent", "#38BDF8")

    def hex_to_rgb(h: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
        c = h.lstrip("#")
        try:
            return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
        except Exception:
            return default

    accent_rgb = hex_to_rgb(accent_hex, (56, 189, 248))

    title_text = (
        f"STORYBOARD: {project.slug.upper()} | "
        f"{len(plan.beats)} BEATS ({plan.total_duration:.1f}s) | PROMO QUOTA PREVIEW"
    )
    draw.text((20, 20), title_text, fill=accent_rgb)

    for idx, beat in enumerate(plan.beats):
        x0 = 16 + idx * (tile_w + 16)
        y0 = header_h + 8
        x1 = x0 + tile_w
        y1 = y0 + tile_h

        draw.rounded_rectangle(
            [x0, y0, x1, y1],
            radius=8,
            fill=(18, 24, 38, 255),
            outline=(38, 48, 70, 255),
            width=2,
        )

        beat_title = f"BEAT {idx}: {beat.kind.upper()} ({beat.duration:.1f}s)"
        draw.text((x0 + 16, y0 + 14), beat_title, fill=accent_rgb)

        sub_info = (
            f"{beat.layout_zone} | {beat.preset or beat.template or 'custom'} | {beat.transition}"
        )
        draw.text((x0 + 16, y0 + 36), sub_info, fill=(148, 163, 184, 255))

        box_x0 = x0 + 16
        box_y0 = y0 + 60
        box_x1 = x1 - 16
        box_y1 = box_y0 + 110
        draw.rounded_rectangle(
            [box_x0, box_y0, box_x1, box_y1],
            radius=6,
            fill=(10, 14, 24, 255),
            outline=(30, 41, 59, 255),
            width=1,
        )

        thumbnail_drawn = False
        if beat.asset_id and beat.asset_id in project.assets:
            asset_path = project.root / project.assets[beat.asset_id]
            if asset_path.is_file():
                aim = None
                if asset_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    try:
                        aim = Image.open(asset_path).copy()
                    except Exception:
                        aim = None
                elif asset_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                    try:
                        import io

                        cmd = [
                            media.FFMPEG,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-ss",
                            "0.1",
                            "-i",
                            str(asset_path),
                            "-vframes",
                            "1",
                            "-f",
                            "image2pipe",
                            "-vcodec",
                            "png",
                            "-",
                        ]
                        cp = subprocess.run(cmd, capture_output=True, timeout=5, check=False)
                        if cp.returncode == 0 and cp.stdout:
                            aim = Image.open(io.BytesIO(cp.stdout)).copy()
                    except Exception:
                        aim = None

                if aim is not None:
                    try:
                        aim.thumbnail((box_x1 - box_x0 - 8, box_y1 - box_y0 - 8))
                        tx = box_x0 + ((box_x1 - box_x0) - aim.width) // 2
                        ty = box_y0 + ((box_y1 - box_y0) - aim.height) // 2
                        canvas.paste(aim, (tx, ty), aim if aim.mode == "RGBA" else None)
                        thumbnail_drawn = True
                    except Exception:
                        pass

        if not thumbnail_drawn:
            badge_text = ""
            if beat.asset_id:
                badge_text = f"ASSET: {beat.asset_id}"
            elif beat.template:
                badge_text = f"TEMPLATE: {beat.template}"
            elif beat.gen_prompt:
                badge_text = f"AI GEN: {beat.gen_prompt[:32]}..."
            else:
                badge_text = f"PRESET: {beat.preset}"
            draw.text((box_x0 + 16, box_y0 + 44), badge_text, fill=(226, 232, 240, 255))

        text_y = box_y1 + 10
        lines = textwrap.wrap(beat.text or "", width=45)
        for line in lines[:2]:
            draw.text((x0 + 16, text_y), line, fill=(203, 213, 225, 255))
            text_y += 16

    canvas.save(destination, format="PNG")
    return destination


def _value_from_text(text: str) -> int:
    """Parse the leading integer from ``text`` (e.g. "98% off" -> 98), else 98."""

    match = re.search(r"(\d+)", text or "")
    return int(match.group(1)) if match else 98


def _aspect_for_deliverable(project: Project, deliverable_id: str) -> str:
    for d in project.deliverables:
        if d.id == deliverable_id and d.resolution:
            if d.resolution == "1080x1920":
                return "9:16"
            if d.resolution == "1920x1080":
                return "16:9"
            if d.resolution == "1080x1080":
                return "1:1"
    if deliverable_id in DELIVERABLE_ASPECT_MAP:
        return DELIVERABLE_ASPECT_MAP[deliverable_id]
    if "9x16" in deliverable_id or "reel" in deliverable_id:
        return "9:16"
    if "1x1" in deliverable_id or "square" in deliverable_id:
        return "1:1"
    return "16:9"


def _resolve_reference_paths(project: Project, references: tuple[str, ...]) -> list[str]:
    resolved: list[str] = []
    for ref in references:
        rel = project.assets.get(ref)
        if rel:
            candidate = project.root / rel
            if candidate.is_file():
                resolved.append(str(candidate))
    return resolved


def _render_solid_clip(
    output: Path, *, width: int, height: int, duration: float, color: str, fps: int = 30
) -> Path:
    col = color.lstrip("#")

    def _luma(h: str) -> float:
        try:
            return 0.299 * int(h[0:2], 16) + 0.587 * int(h[2:4], 16) + 0.114 * int(h[4:6], 16)
        except Exception:
            return 0.0

    if _luma(col) < 32.0:
        col = "1e293b"

    vf = (
        f"scale={width}:{height}:flags=lanczos,scale=in_range=auto:out_range=limited,format=yuv420p"
    )
    # Slow cinematic breathing gradient: speed=0.03 (1 rev / 33s) avoids dizzy spinning while
    # keeping the background subtly alive and preventing H.264 flat-freeze artifacts.
    bg_input = (
        f"gradients=s={width}x{height}:r={fps}:d={duration:.4f}:"
        f"c0=0x{col}:c1=0x182234:c2=0x{col}:c3=0x253347:speed=0.03"
    )

    def build(encoder_flags: list[str]) -> list[str]:
        return [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            bg_input,
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration:.4f}",
            "-vf",
            vf,
            *encoder_flags,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(output),
        ]

    video.run_ffmpeg_encode_with_fallback(
        build,
        output=output,
        label=f"solid clip {output.name}",
        fps=fps,
        is_intermediate=True,
    )
    return output


def _normalize_clip(
    source: Path,
    output: Path,
    *,
    width: int,
    height: int,
    duration: float,
    fps: int = 30,
) -> Path:
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},fps={fps},scale=in_range=auto:out_range=limited,format=yuv420p"
    )
    audio_input = f"anullsrc=r=48000:cl=stereo:d={duration:.4f}"
    time_flag = f"{duration:.4f}"

    def build(encoder_flags: list[str]) -> list[str]:
        if source.suffix.lower() in image_exts:
            return [
                media.FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-loop",
                "1",
                "-i",
                str(source),
                "-f",
                "lavfi",
                "-i",
                audio_input,
                "-vf",
                vf,
                "-t",
                time_flag,
                *encoder_flags,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                str(output),
            ]
        return [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            "-f",
            "lavfi",
            "-i",
            audio_input,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
            "-t",
            time_flag,
            *encoder_flags,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output),
        ]

    video.run_ffmpeg_encode_with_fallback(
        build, output=output, label=f"normalize clip {output.name}", fps=fps, is_intermediate=True
    )
    return output


def _composite_overlay(base: Path, overlay: Path, output: Path, *, width: int, height: int) -> Path:
    filter_complex = (
        f"[1:v]scale={width}:{height}:flags=lanczos,format=rgba[ov];"
        f"[0:v][ov]overlay=0:0:shortest=1,scale=in_range=auto:out_range=limited,format=yuv420p[outv]"
    )

    def build(encoder_flags: list[str]) -> list[str]:
        return [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(base),
            "-i",
            str(overlay),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "0:a?",
            *encoder_flags,
            "-c:a",
            "copy",
            str(output),
        ]

    video.run_ffmpeg_encode_with_fallback(
        build, output=output, label=f"composite overlay {output.name}", is_intermediate=True
    )
    return output


def _pad_audio(source: Path, output: Path, *, total_duration: float) -> Path:
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            f"aformat=sample_rates=48000:channel_layouts=stereo,apad=whole_dur={total_duration:.6f},atrim=0:{total_duration:.6f}",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        label=f"pad audio {output.name}",
        expect_stdout=False,
    )
    return output


def _mux_video_and_audio(video_in: Path, audio_in: Path, output: Path) -> Path:
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_in),
            "-i",
            str(audio_in),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output),
        ],
        label=f"mux {output.name}",
        expect_stdout=False,
    )
    return output


def _mix_vo_and_music(
    vo_wav: Path, ducked_music: Path, output: Path, *, total_duration: float
) -> Path:
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(vo_wav),
            "-i",
            str(ducked_music),
            "-filter_complex",
            (
                f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total_duration:.6f}[vo];"
                f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total_duration:.6f}[bgm];"
                "[vo][bgm]amix=inputs=2:duration=first:weights=1 0.45:normalize=0[outa]"
            ),
            "-map",
            "[outa]",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        label=f"mix vo and music {output.name}",
        expect_stdout=False,
    )
    return output


def _resolve_music_path(project: Project) -> Path | None:
    music_id: str | None = None
    if project.video is not None and project.video.music is not None:
        music_id = project.video.music.asset
    elif "music" in project.assets:
        music_id = "music"
    elif "bgm" in project.assets:
        music_id = "bgm"
    if not music_id:
        return None
    rel = project.assets.get(music_id)
    if not rel:
        return None
    candidate = project.root / rel
    return candidate if candidate.is_file() else None


def render_promo(
    project: Project,
    plan: PromoPlan,
    *,
    auto: bool = False,
    max_gen_calls: int = 6,
    force: bool = False,
    deliverables: tuple[str, ...] = DEFAULT_DELIVERABLES,
    tts_engine: str = "auto",
    fps: int = 30,
    beats: tuple[int, ...] | list[int] | None = None,
) -> PromoRenderResult:
    """Render a promotional video plan, enforcing the Storyboard/Quota Gate unless ``auto=True``.

    ``fps`` sets the deliverable frame rate (default 30; use 60 for high-motion
    promo output) and is threaded through every beat render, transition,
    caption burn, and the final encode.

    ``beats`` selects which beat indices to (re)render. When ``None`` every beat
    renders fresh (the default). When a non-empty list/tuple is given, only those
    indices bypass the cache and re-render; every other beat reuses a cached clip
    when available and is stored to the cache when newly rendered.
    """

    if not plan.beats:
        raise PromoError("promo plan has no beats")

    storyboard_path = project.root / "out" / "promo" / "storyboard.png"
    notes: list[str] = []

    if not auto:
        _write_storyboard(project, plan, storyboard_path)
        notes.append(
            f"gated: storyboard written to {storyboard_path} "
            f"(estimated_gen_calls={plan.estimated_gen_calls}; pass auto=True to render)"
        )
        return PromoRenderResult(
            gated=True,
            storyboard=storyboard_path,
            deliverables={},
            qc={},
            notes=tuple(notes),
        )

    out_dir = project.root / "out" / "video"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_paths = {deliv_id: out_dir / f"{deliv_id}.mp4" for deliv_id in deliverables}
    if not force:
        for path in target_paths.values():
            if path.exists():
                raise PromoError(
                    f"deliverable already exists: {path} (pass force=True to overwrite)"
                )

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"promo-{os.getpid()}-", dir=str(SCRATCH_ROOT)))
    try:
        # 1. Voiceover (clean existing VO asset if declared, else synthesize via TTS).
        vo_wav = scratch / "voiceover.wav"
        words_json = scratch / "words.json"
        vo_asset_rel = project.assets.get("voiceover") or project.assets.get("vo")
        if vo_asset_rel and (project.root / vo_asset_rel).is_file():
            vo_src = project.root / vo_asset_rel
            audio.clean_voiceover(vo_src, output=vo_wav, target_lufs=-16.0)
            try:
                extracted = extract_word_timestamps(vo_src)
                words_json.write_text(
                    json.dumps(
                        [{"word": w.word, "start": w.start, "end": w.end} for w in extracted]
                    ),
                    encoding="utf-8",
                )
            except CaptionsError as error:
                notes.append(f"captions skipped for pre-recorded VO: {error}")
                words_json.write_text("[]", encoding="utf-8")
        else:
            voice_id = project.brand.voice.voice_id if project.brand.voice else "id-ID-ArdiNeural"
            voice_rate = project.brand.voice.rate if project.brand.voice else ""
            rate = voice_rate or "+0%"
            engine = (
                project.brand.voice.engine
                if tts_engine == "auto" and project.brand.voice
                else tts_engine
            )
            try:
                tts_res = synthesize_speech(
                    plan.script,
                    output=vo_wav,
                    voice=voice_id,
                    engine=engine,
                    rate=rate,
                    words_out=words_json,
                    target_lufs=-16.0,
                    scratch=scratch,
                )
                words_json = tts_res.words_path
                # Neural TTS: light cleanup only -- no afftdn denoise, no heavy
                # compression -- so synthetic speech stays artifact-free.
                audio.clean_voiceover(vo_wav, output=vo_wav, target_lufs=-16.0, profile="tts")
            except TtsError as error:
                raise PromoError(f"voiceover synthesis failed: {error}") from error

        # 2. Acquire/generate B-roll + render motion overlays per beat.
        primary_col = project.brand.palette.get("primary", "#0F172A")
        accent_col = project.brand.palette.get("accent", "#38BDF8")
        ref_paths = _resolve_reference_paths(project, plan.references)
        gen_calls_used = 0

        min_beat_dur = min(b.duration for b in plan.beats)
        trans_td = min(0.18, max(0.06, min_beat_dur * 0.35))
        trans_ms = max(60, round(trans_td * 1000))
        # Quantize td to the deliverable fps to match transition frame quantization.
        quant_td = max(1, round(trans_ms * fps / 1000.0)) / fps

        master_dur = max(plan.total_duration, 1.0)

        # 3. Mix voiceover + optional ducked music at -14 LUFS (once; duration shared).
        padded_vo = scratch / "vo-padded.wav"
        _pad_audio(vo_wav, padded_vo, total_duration=master_dur)
        base_bed = padded_vo
        music_path = _resolve_music_path(project)
        if music_path is not None:
            trimmed_music = scratch / "music-trimmed.wav"
            _pad_audio(music_path, trimmed_music, total_duration=master_dur)
            ducked_music = scratch / "music-ducked.wav"
            audio.duck_music(trimmed_music, padded_vo, ducked_music)
            vo_plus_music = scratch / "vo-plus-music.wav"
            _mix_vo_and_music(padded_vo, ducked_music, vo_plus_music, total_duration=master_dur)
            base_bed = vo_plus_music

        # 4. Word timings for captions.
        raw_words = json.loads(words_json.read_text(encoding="utf-8"))
        word_timings = [
            WordTiming(
                word=str(item["word"]),
                start=float(item["start"]),
                end=float(item["end"]),
            )
            for item in raw_words
            if isinstance(item, dict) and item.get("word")
        ]

        # 5. Per-deliverable: build the master at native aspect, then mux/caption/encode.
        rendered_paths: dict[str, Path] = {}
        qc_results: dict[str, Any] = {}

        for deliv_id, final_mp4 in target_paths.items():
            aspect = _aspect_for_deliverable(project, deliv_id)
            out_w, out_h = OUTPUT_SIZE[aspect]

            # Build beat clips at the deliverable's native aspect. Footage / B-roll
            # beats (asset_id / gen_prompt) are normalized at 16:9 then reframed to
            # (out_w, out_h); HTML cards and CTA titles render natively at (out_w, out_h).
            beat_clips: list[Path] = []
            sfx_cues: list[SfxCue] = []
            elapsed = 0.0

            for idx, beat in enumerate(plan.beats):
                clip_dur = beat.duration + (quant_td if idx > 0 else 0.0)
                collisions = check_layout_collisions(beat, aspect=aspect, width=out_w, height=out_h)
                placement = resolve_layout_placement(beat, aspect=aspect, width=out_w, height=out_h)
                if collisions:
                    notes.append(f"{deliv_id} beat {idx} ({beat.kind}): " + "; ".join(collisions))
                base_clip = scratch / f"{deliv_id}-beat-{idx:02d}-base.mp4"

                # Selective beat regeneration: beats in ``beats`` are force-rendered
                # (bypassing cache) while all other beats reuse a cached clip when one
                # exists and store newly rendered clips to the cache.
                force_render = beats is not None and idx in set(beats)
                use_cache = not force_render
                cache_key = compute_beat_cache_key(project, beat, idx, aspect=aspect, fps=fps)
                cache_dir = project.root / "out" / "promo" / "cache"
                cached_clip = get_cached_beat(cache_dir, cache_key) if use_cache else None

                if cached_clip is not None:
                    final_beat_clip = cached_clip
                else:
                    asset_rel = project.assets.get(beat.asset_id) if beat.asset_id else None
                    if asset_rel and (project.root / asset_rel).is_file():
                        normalized = scratch / f"{deliv_id}-beat-{idx:02d}-norm.mp4"
                        _normalize_clip(
                            project.root / asset_rel,
                            normalized,
                            width=1920,
                            height=1080,
                            duration=clip_dur,
                            fps=fps,
                        )
                        reframed = scratch / f"{deliv_id}-beat-{idx:02d}-reframed.mp4"
                        reframe_video(
                            normalized, aspect=aspect, mode="auto", output=reframed, fps=fps
                        )
                        base_clip = reframed
                    elif beat.gen_prompt and gen_calls_used < max_gen_calls:
                        gen_out = (
                            project.root / "assets" / "video" / f"gen-{project.slug}-{idx:02d}.mp4"
                        )
                        try:
                            gen_res = generate_asset(
                                GenRequest(
                                    kind="video",
                                    prompt=beat.gen_prompt,
                                    duration=clip_dur,
                                    fps=fps,
                                    resolution="1920x1080",
                                    references=ref_paths,
                                ),
                                project=project,
                                output=gen_out,
                                max_gen_calls=max(1, max_gen_calls - gen_calls_used),
                                allow_keyless=False,
                            )
                            gen_calls_used += 1
                            normalized = scratch / f"{deliv_id}-beat-{idx:02d}-norm.mp4"
                            _normalize_clip(
                                gen_res.output,
                                normalized,
                                width=1920,
                                height=1080,
                                duration=clip_dur,
                                fps=fps,
                            )
                            reframed = scratch / f"{deliv_id}-beat-{idx:02d}-reframed.mp4"
                            reframe_video(
                                normalized, aspect=aspect, mode="auto", output=reframed, fps=fps
                            )
                            base_clip = reframed
                        except GenError as error:
                            notes.append(f"gen fallback on beat {idx}: {error}")
                            _render_solid_clip(
                                base_clip,
                                width=out_w,
                                height=out_h,
                                duration=clip_dur,
                                color=primary_col,
                            )
                    elif beat.template:
                        html_out = scratch / f"{deliv_id}-beat-{idx:02d}-html.mov"
                        _render_solid_clip(
                            base_clip,
                            width=out_w,
                            height=out_h,
                            duration=clip_dur,
                            color=primary_col,
                            fps=fps,
                        )
                        try:
                            value = _value_from_text(beat.text)
                            # Cleanly format params so headline, subhead, and feature never overlap
                            headline_txt = beat.text if len(beat.text) <= 45 else "Key Feature"
                            subhead_txt = beat.text if len(beat.text) > 45 else project.title
                            feature_txt = f"Automated {beat.kind} pipeline"
                            html_res = render_html_motion(
                                beat.template,
                                output=html_out,
                                params={
                                    "headline": headline_txt,
                                    "subhead": subhead_txt,
                                    "feature": feature_txt,
                                    "label": beat.kind.upper(),
                                    "value": value,
                                    "suffix": "%",
                                    "ink": "#0F172A",
                                    "card_anchor_y": placement["anchor_y"],
                                    "card_scale": placement["scale"],
                                    "layout_zone": placement["zone"],
                                },
                                project=project,
                                width=out_w,
                                height=out_h,
                                fps=fps,
                                duration=clip_dur,
                                transparent=True,
                            )
                            if html_res.output.is_file():
                                if html_res.transparent:
                                    composited = (
                                        scratch / f"{deliv_id}-beat-{idx:02d}-html-comp.mp4"
                                    )
                                    _composite_overlay(
                                        base_clip,
                                        html_res.output,
                                        composited,
                                        width=out_w,
                                        height=out_h,
                                    )
                                    base_clip = composited
                                else:
                                    _normalize_clip(
                                        html_res.output,
                                        base_clip,
                                        width=out_w,
                                        height=out_h,
                                        duration=clip_dur,
                                        fps=fps,
                                    )
                        except HtmlMotionError as error:
                            notes.append(f"html_motion fallback on beat {idx}: {error}")
                    else:
                        _render_solid_clip(
                            base_clip,
                            width=out_w,
                            height=out_h,
                            duration=clip_dur,
                            color=primary_col,
                            fps=fps,
                        )

                    final_beat_clip = base_clip
                    if beat.preset:
                        preset_out = scratch / f"{deliv_id}-beat-{idx:02d}-preset.mov"
                        try:
                            ec_res = render_promo_preset(
                                beat.preset,
                                preset_out,
                                text=beat.text,
                                subtext=project.title,
                                project=project,
                                width=out_w,
                                height=out_h,
                                fps=fps,
                                duration=clip_dur,
                            )
                            if ec_res.ok and ec_res.output.is_file():
                                composited = scratch / f"{deliv_id}-beat-{idx:02d}-comp.mp4"
                                _composite_overlay(
                                    base_clip, ec_res.output, composited, width=out_w, height=out_h
                                )
                                final_beat_clip = composited
                        except EffectCraftError as error:
                            notes.append(f"effectcraft fallback on beat {idx}: {error}")

                    put_cached_beat(cache_dir, cache_key, final_beat_clip)

                beat_clips.append(final_beat_clip)
                if idx > 0:
                    sfx_cues.append(SfxCue(path_or_id=beat.sfx, at=max(0.0, elapsed), gain_db=-4.0))

                # Intra-beat motion SFX: sync sound effects to visual progress/clicks
                if beat.template == "metric-counter":
                    # Ticks strictly during active visual counting (first 75% of duration)
                    active_count_dur = clip_dur * 0.75
                    tick_step = 0.4
                    t_curr = 0.4
                    while t_curr < active_count_dur - 0.1:
                        sfx_cues.append(
                            SfxCue(
                                path_or_id="ui-pop",
                                at=max(0.0, elapsed + t_curr),
                                gain_db=-12.0,
                            )
                        )
                        t_curr += tick_step
                    # Climax confirmation pop exactly when visual counter reaches target value
                    sfx_cues.append(
                        SfxCue(
                            path_or_id="ui-pop",
                            at=max(0.0, elapsed + active_count_dur),
                            gain_db=-4.0,
                        )
                    )
                elif beat.template == "saas-feature-card":
                    # Click pop at t = 78% of clip duration when cursor clicks button
                    sfx_cues.append(
                        SfxCue(
                            path_or_id="ui-pop",
                            at=max(0.0, elapsed + clip_dur * 0.78),
                            gain_db=-4.0,
                        )
                    )

                elapsed += beat.duration

            # Join clips sequentially with transitions at the deliverable aspect.
            master_video = beat_clips[0]
            for j in range(1, len(beat_clips)):
                joined = scratch / f"{deliv_id}-joined-{j:02d}.mp4"
                try:
                    apply_transition(
                        master_video,
                        beat_clips[j],
                        kind=plan.beats[j].transition,
                        output=joined,
                        duration_ms=trans_ms,
                        width=out_w,
                        height=out_h,
                        fps=fps,
                    )
                    master_video = joined
                except TransitionError as error:
                    raise PromoError(
                        f"transition failed at beat {j} for {deliv_id}: {error}"
                    ) from error

            # SFX mix per deliverable (SFX cues reference elapsed within this master).
            deliv_mixed = scratch / f"{deliv_id}-mixed-audio.wav"
            try:
                valid_cues = [cue for cue in sfx_cues if 0.0 <= cue.at <= master_dur]
                mix_transition_sfx(
                    base_bed,
                    valid_cues,
                    output=deliv_mixed,
                    total_duration=master_dur,
                    target_lufs=-14.0,
                )
            except SfxError as error:
                raise PromoError(f"SFX mix failed: {error}") from error

            muxed = scratch / f"{deliv_id}-muxed.mp4"
            _mux_video_and_audio(master_video, deliv_mixed, muxed)

            captioned = muxed
            if word_timings:
                captioned_out = scratch / f"{deliv_id}-captioned.mp4"
                render_captions(
                    muxed,
                    words=word_timings,
                    style="promo-pop",
                    aspect=aspect,
                    accent_color=accent_col,
                    output=captioned_out,
                    engine="ass",
                    fps=fps,
                )
                captioned = captioned_out

            video.encode_with_loudness(
                captioned, final_mp4, target_lufs=project.loudness_lufs, fps=fps
            )
            rendered_paths[deliv_id] = final_mp4
            qc_results[deliv_id] = video.qc_report(
                final_mp4,
                expected_duration=plan.total_duration,
                target_lufs=project.loudness_lufs,
                engine="promo",
            )

        # 6. Canonical timeline interchange export (based on the primary deliverable).
        primary_deliv = deliverables[0]
        primary_path = rendered_paths[primary_deliv]
        otio_path = export_otio_timeline(project, plan, primary_deliv, primary_path, fps=fps)
        mlt_path = export_mlt_timeline(project, plan, primary_deliv, primary_path, fps=fps)

        return PromoRenderResult(
            gated=False,
            storyboard=storyboard_path if storyboard_path.is_file() else None,
            deliverables=rendered_paths,
            qc=qc_results,
            notes=tuple(notes),
            otio_path=otio_path,
            mlt_path=mlt_path,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run_media_mcp_checks(path: Path) -> dict[str, Any]:
    """Best-effort hook for media-mcp AV-sync verification when available."""

    default_root = str(Path.home() / ".clone" / "Github" / "SuperBypassUdinnn" / "media-mcp")
    media_mcp_root = Path(os.environ.get("MEDIA_MCP_ROOT", default_root))
    python_bin = media_mcp_root / "media" / ".venv" / "bin" / "python"
    server_py = media_mcp_root / "media" / "server.py"
    if not python_bin.is_file() or not server_py.is_file():
        return {"available": False, "status": "unavailable", "reason": "media-mcp not installed"}

    probe_script = (
        "import asyncio, json, sys\n"
        "from media import server\n"
        "async def run():\n"
        "    await server.media_beats(sys.argv[1])\n"
        "    res = await server.media_avsync(sys.argv[1])\n"
        "    print(res)\n"
        "asyncio.run(run())\n"
    )
    try:
        completed = subprocess.run(
            [str(python_bin), "-c", probe_script, str(path)],
            cwd=str(media_mcp_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": True, "status": "error", "error": str(error)}

    if completed.returncode != 0:
        return {
            "available": True,
            "status": "error",
            "error": (completed.stderr or completed.stdout or "").strip()[-400:],
        }
    return {
        "available": True,
        "status": "ok",
        "avsync": completed.stdout.strip()[-800:],
    }


def qc_promo(
    project: Project,
    *,
    deliverables: tuple[str, ...] = DEFAULT_DELIVERABLES,
    use_media_mcp: bool = True,
) -> dict[str, Any]:
    """Run FFprobe/LUFS QC and ``ms see`` contact-sheet verification on promo deliverables."""

    if not deliverables:
        raise PromoError("deliverables must not be empty")

    expected_by_id = {d.id: d.duration for d in project.deliverables}
    results: dict[str, Any] = {}
    all_ok = True

    for deliv_id in deliverables:
        path = project.root / "out" / "video" / f"{deliv_id}.mp4"
        if not path.is_file():
            raise PromoError(f"missing deliverable: {path}")

        report = video.qc_report(
            path,
            expected_duration=expected_by_id.get(deliv_id),
            target_lufs=project.loudness_lufs,
            engine="promo",
        )
        aspect = _aspect_for_deliverable(project, deliv_id)
        exp_w, exp_h = OUTPUT_SIZE[aspect]
        dim_ok = report["width"] == exp_w and report["height"] == exp_h
        fps_ok = float(report["fps"] or 0.0) >= 30.0
        report["checks"]["dimensions_match_aspect"] = dim_ok
        report["checks"]["fps_at_least_30"] = fps_ok
        report["ok"] = bool(all(report["checks"].values()))

        sheet_path = project.root / "out" / "promo" / f"{deliv_id}-sheet.png"
        try:
            peek_res = peek(path, sheet_path, columns=4, rows=3, width=320)
            report["contact_sheet"] = str(peek_res.sheet) if peek_res.sheet else None
        except SeeError as error:
            raise PromoError(f"ms see failed on {path}: {error}") from error

        if use_media_mcp:
            report["media_mcp"] = _run_media_mcp_checks(path)

        if not report["ok"]:
            all_ok = False
        results[deliv_id] = report

    return {"project": project.slug, "ok": all_ok, "deliverables": results}


def _probe_video_color(path: Path) -> dict[str, str | None]:
    """Report ``color_range``, ``color_space`` and ``pix_fmt`` of the first video stream."""

    completed = media.run_command(
        [
            media.FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_range,color_space,pix_fmt",
            "-of",
            "json",
            str(path),
        ],
        label=f"ffprobe color {path.name}",
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    streams = payload.get("streams") or []
    return streams[0] if streams else {}


def _layout_defects(
    deliverable_id: str,
    beat: PromoBeat,
    idx: int,
    timestamp: float,
    *,
    aspect: str,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Return layout/safe-zone defect dicts for one beat on one deliverable."""

    zone_name = beat.layout_zone or "hero-center"
    boxes = LAYOUT_ZONE_BOXES.get(zone_name, LAYOUT_ZONE_BOXES["hero-center"])
    safe = safe_zone_for(aspect, width, height)

    card = {
        "top": round(boxes["card"]["top"] * height),
        "bottom": round(boxes["card"]["bottom"] * height),
        "left": round(boxes["card"]["left"] * width),
        "right": round(boxes["card"]["right"] * width),
    }
    caption = {
        "top": round(boxes["caption"]["top"] * height),
        "bottom": round(boxes["caption"]["bottom"] * height),
        "left": round(boxes["caption"]["left"] * width),
        "right": round(boxes["caption"]["right"] * width),
    }

    defects: list[dict[str, Any]] = []

    if _rects_overlap(card, caption):
        overlap_w = max(
            0, min(card["right"], caption["right"]) - max(card["left"], caption["left"])
        )
        overlap_h = max(
            0, min(card["bottom"], caption["bottom"]) - max(card["top"], caption["top"])
        )
        overlap_px = overlap_w * overlap_h
        defects.append(
            {
                "deliverable": deliverable_id,
                "beat_index": idx,
                "timestamp": timestamp,
                "severity": "error",
                "category": "layout",
                "defect": f"Card bounding box intersects caption band by {overlap_px} pixels",
                "corrective_action": (
                    "Shift card_anchor_y up or reduce card_scale to clear safe zone"
                ),
            }
        )

    bottom_limit = height - safe["bottom"]
    if card["top"] < safe["top"]:
        defects.append(
            {
                "deliverable": deliverable_id,
                "beat_index": idx,
                "timestamp": timestamp,
                "severity": "error",
                "category": "layout",
                "defect": f"Card intrudes into top safe zone ({safe['top']}px)",
                "corrective_action": "Shift card anchor Y down into the safe zone",
            }
        )
    if card["bottom"] > bottom_limit:
        defects.append(
            {
                "deliverable": deliverable_id,
                "beat_index": idx,
                "timestamp": timestamp,
                "severity": "error",
                "category": "layout",
                "defect": f"Card intrudes into bottom safe zone ({safe['bottom']}px)",
                "corrective_action": "Shift card anchor Y up into the safe zone",
            }
        )

    return defects


def _pacing_defects(
    deliverable_id: str,
    beat: PromoBeat,
    idx: int,
    timestamp: float,
) -> list[dict[str, Any]]:
    """Return pacing/text-density defect dicts for one beat."""

    defects: list[dict[str, Any]] = []
    duration = beat.duration
    words = len(beat.text.split())
    wps = words / duration if duration > 0 else float("inf")

    if wps > TEXT_DENSITY_MAX_WPS:
        defects.append(
            {
                "deliverable": deliverable_id,
                "beat_index": idx,
                "timestamp": timestamp,
                "severity": "warning",
                "category": "pacing",
                "defect": f"High text density ({wps:.1f} wps)",
                "corrective_action": "Extend beat duration or trim text",
            }
        )
    if duration < BEAT_MIN_SECONDS or duration > BEAT_MAX_SECONDS:
        defects.append(
            {
                "deliverable": deliverable_id,
                "beat_index": idx,
                "timestamp": timestamp,
                "severity": "warning",
                "category": "pacing",
                "defect": (
                    f"Pacing anomaly: {duration}s beat outside {BEAT_MIN_SECONDS}s-"
                    f"{BEAT_MAX_SECONDS}s"
                ),
                "corrective_action": "Adjust beat duration to fall within 1.0s-12.0s",
            }
        )

    return defects


def critique_promo(
    project: Project,
    *,
    plan: PromoPlan | None = None,
    deliverables: tuple[str, ...] = DEFAULT_DELIVERABLES,
) -> dict[str, Any]:
    """Critique rendered promo deliverables for visual, color, pacing and A/V defects.

    Evaluates every beat of ``plan`` against each deliverable's aspect and
    resolution (safe-zone compliance and card/caption collisions), inspects each
    deliverable with ffprobe for BT.709 limited-range color metadata
    (``color_range=tv``, ``color_space=bt709``, ``pix_fmt=yuv420p``), checks
    pacing/text density per beat, and runs the optional media-mcp A/V sync hook
    per deliverable. Returns a structured defect report: ``project``, ``ok``
    (no error-severity defects), ``defects`` and ``summary`` counts.

    When ``plan`` is not given it is loaded from
    ``<project.root>/out/promo/promo-plan.json`` if present; beat-level checks
    are then skipped entirely when no plan is available.
    """

    if not deliverables:
        raise PromoError("deliverables must not be empty")

    defects: list[dict[str, Any]] = []

    if plan is None:
        plan_path = project.root / "out" / "promo" / "promo-plan.json"
        if plan_path.is_file():
            plan = load_promo_plan(plan_path)

    for deliv_id in deliverables:
        path = project.root / "out" / "video" / f"{deliv_id}.mp4"
        aspect = _aspect_for_deliverable(project, deliv_id)
        width, height = OUTPUT_SIZE[aspect]

        if not path.is_file():
            defects.append(
                {
                    "deliverable": deliv_id,
                    "beat_index": -1,
                    "timestamp": 0.0,
                    "severity": "error",
                    "category": "missing",
                    "defect": f"Deliverable not found: {path}",
                    "corrective_action": "Render the missing deliverable before critique",
                }
            )
            continue

        color = _probe_video_color(path)
        color_range = color.get("color_range")
        color_space = color.get("color_space")
        pix_fmt = color.get("pix_fmt")
        compliant = color_range == "tv" and color_space == "bt709" and pix_fmt == "yuv420p"
        if not compliant:
            defects.append(
                {
                    "deliverable": deliv_id,
                    "beat_index": -1,
                    "timestamp": 0.0,
                    "severity": "error",
                    "category": "color",
                    "defect": (
                        f"{deliv_id}: non-compliant color metadata "
                        f"(range={color_range}, space={color_space}, pix_fmt={pix_fmt}); "
                        "expected tv/bt709/yuv420p"
                    ),
                    "corrective_action": (
                        "Re-encode with -color_range tv -color_space bt709 "
                        "-color_trc bt709 -pix_fmt yuv420p"
                    ),
                }
            )

        mcp = _run_media_mcp_checks(path)
        if mcp.get("status") == "error":
            tail = str(mcp.get("error", ""))[:200]
            defects.append(
                {
                    "deliverable": deliv_id,
                    "beat_index": -1,
                    "timestamp": 0.0,
                    "severity": "warning",
                    "category": "av-sync",
                    "defect": f"A/V sync check failed: {tail}" if tail else "A/V sync check failed",
                    "corrective_action": (
                        "Verify transition cuts align with music onsets and re-render if needed"
                    ),
                }
            )

        if plan is not None:
            cursor = 0.0
            for idx, beat in enumerate(plan.beats):
                defects.extend(
                    _layout_defects(
                        deliv_id, beat, idx, cursor, aspect=aspect, width=width, height=height
                    )
                )
                defects.extend(_pacing_defects(deliv_id, beat, idx, cursor))
                cursor += beat.duration

    errors = sum(1 for defect in defects if defect["severity"] == "error")
    warnings = sum(1 for defect in defects if defect["severity"] == "warning")
    ok = errors == 0

    return {
        "project": project.slug,
        "ok": ok,
        "defects": defects,
        "summary": {"errors": errors, "warnings": warnings, "critique_passed": ok},
    }
