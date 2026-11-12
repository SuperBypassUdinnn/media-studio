"""Reading and validating ``project.yaml``.

The manifest is the source of truth for a project: deliverables, timeline intent, brand
tokens, and budget. Everything else in the toolkit reads it through this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .sidecar import UNKNOWN_SPDX, read_sidecar

MANIFEST_NAME = "project.yaml"
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
COLOUR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
RESOLUTION_PATTERN = re.compile(r"^\d{2,5}x\d{2,5}$")
DELIVERABLE_KINDS = ("video", "image", "audio", "3d")
# Unknown keys are rejected so a typo fails loudly instead of being ignored.
MANIFEST_KEYS = frozenset(
    {
        "slug",
        "title",
        "status",
        "deliverables",
        "timeline",
        "brand",
        "assets",
        "effects",
        "video",
        "design",
        "budget",
        "motion",
        "captions",
    }
)
DESIGN_KEYS = frozenset({"logo", "fonts", "variants"})
VARIANT_KEYS = frozenset(
    {
        "id",
        "template",
        "size",
        "headline",
        "subhead",
        "trim",
        "pad",
        "profile",
        "image",
        "image_fit",
        "image_position",
        "headline_size",
        "subhead_size",
    }
)
VIDEO_KEYS = frozenset({"shots", "voice", "music", "titles", "fade_in", "fade_out", "crossfade"})
SHOT_KEYS = frozenset({"asset", "in", "duration", "transition", "shot_type"})
TITLE_KEYS = frozenset({"text", "start", "duration", "size", "colour", "position"})
CUE_KEYS = frozenset(
    {"asset", "in", "gain", "fade_in", "fade_out", "duck", "duration", "clamp_to_video"}
)
DELIVERABLE_KEYS = frozenset(
    {"id", "kind", "container", "resolution", "fps", "duration", "loudness_lufs", "preview"}
)
BRAND_KEYS = frozenset({"palette", "fonts", "references", "voice", "safe_zones"})
BUDGET_KEYS = frozenset({"max_asset_bytes", "max_project_bytes", "require_cc0_music"})
# Integrated loudness outside this window is almost always a manifest mistake.
LOUDNESS_RANGE = (-40.0, -5.0)
# How a variant's foreground image fills its box, and where that box sits on the canvas.
IMAGE_FITS = ("contain", "cover")
IMAGE_POSITIONS = ("left", "center", "right", "full")


class ManifestError(RuntimeError):
    """Raised when the manifest cannot be read at all."""


@dataclass(frozen=True)
class Deliverable:
    """One output the project promises."""

    id: str
    kind: str
    container: str | None = None
    resolution: str | None = None
    fps: float | None = None
    duration: float | None = None
    loudness_lufs: float | None = None
    preview: bool = False


@dataclass(frozen=True)
class Shot:
    """One cut on the video track. `transition` is `cut` or `crossfade`."""

    asset: str
    in_point: float = 0.0
    duration: float | None = None
    transition: str = "cut"
    shot_type: str | None = None


@dataclass(frozen=True)
class AudioCue:
    """A music bed or a voice track, referenced by asset id."""

    asset: str
    in_point: float = 0.0
    gain: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    duck: bool = True
    duration: float | None = None
    clamp_to_video: bool = True


def audio_span(
    cue: AudioCue, video_seconds: float, source_seconds: float | None = None
) -> float | None:
    """How many seconds of a cue should play, or ``None`` when the source sets the length.

    A cue entered 30 s into a 45 s bed and asked for 20 s must stop at the end of the edit
    rather than run past the picture, and a source with only 15 s left cannot fill a 20 s
    span. Both limits are applied here so MLT and FilmCraft clamp identically.
    """

    span = cue.duration
    if cue.clamp_to_video:
        span = video_seconds if span is None else min(span, video_seconds)
    if span is None:
        return None
    span = max(span, 0.0)
    if source_seconds is not None:
        remaining = source_seconds - cue.in_point
        span = min(span, max(remaining, 0.0))
    return span


@dataclass(frozen=True)
class TitleText:
    """A line of text shown over the picture."""

    text: str
    start: float
    duration: float
    size: int = 64
    colour: str = "#ffffffff"
    position: str = "+0+0"


@dataclass(frozen=True)
class VideoPlan:
    """How the video deliverable is assembled."""

    shots: tuple[Shot, ...]
    voice: AudioCue | None = None
    music: AudioCue | None = None
    fade_in: float = 0.0
    fade_out: float = 0.0
    crossfade: float = 0.5
    titles: tuple[TitleText, ...] = ()


@dataclass(frozen=True)
class VariantSpec:
    """One rendered design: a template, a canvas, and the words on it."""

    id: str
    template: str
    width: int
    height: int
    headline: str = ""
    subhead: str = ""
    trim: bool = False
    pad: int = 0
    profile: str | None = None
    image: str | None = None
    image_fit: str = "contain"
    image_position: str = "right"
    headline_size: int | None = None
    subhead_size: int | None = None


@dataclass(frozen=True)
class DesignSpec:
    """How the image deliverables are composed."""

    variants: tuple[VariantSpec, ...]
    logo: str | None = None
    fonts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReferencePack:
    """Product, spokesperson and style-board asset references for a brand kit."""

    product: tuple[str, ...] = field(default_factory=tuple)
    spokesperson: tuple[str, ...] = field(default_factory=tuple)
    style_board: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BrandVoice:
    """Voice-over engine, voice id and rate for a project brand."""

    engine: str
    voice_id: str
    rate: str


@dataclass(frozen=True)
class Brand:
    """Colour and type tokens every design path reads."""

    palette: dict[str, str] = field(default_factory=dict)
    fonts: dict[str, str] = field(default_factory=dict)
    references: ReferencePack = field(default_factory=ReferencePack)
    voice: BrandVoice | None = None
    safe_zones: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class Budget:
    """Size limits and licence preferences."""

    max_asset_bytes: int = 200 * 1024 * 1024
    max_project_bytes: int = 20 * 1024**3
    require_cc0_music: bool = False


@dataclass(frozen=True)
class Project:
    """A loaded, structurally valid project."""

    root: Path
    slug: str
    title: str
    status: str
    deliverables: tuple[Deliverable, ...]
    brand: Brand
    budget: Budget
    assets: dict[str, str]
    timeline: tuple[dict[str, Any], ...]
    video: VideoPlan | None
    design: DesignSpec | None
    effects: tuple[dict[str, Any], ...]
    raw: dict[str, Any]

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def loudness_lufs(self) -> float:
        """Project loudness target: the first deliverable that declares one, else -14 LUFS."""

        for deliverable in self.deliverables:
            if deliverable.loudness_lufs is not None:
                return deliverable.loudness_lufs
        return -14.0


@dataclass(frozen=True)
class ValidationReport:
    """Problems and warnings found while validating a project."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def resolve_project(location: str | Path | None = None) -> Path:
    """Resolve a project directory from a path, a slug, or nothing (the current directory)."""

    if location is None:
        candidate = Path.cwd()
    else:
        candidate = Path(location).expanduser()
        if not candidate.exists():
            candidate = Path.home() / "Projects" / str(location)
    if candidate.is_file() and candidate.name == MANIFEST_NAME:
        candidate = candidate.parent
    if not (candidate / MANIFEST_NAME).is_file():
        raise ManifestError(f"no {MANIFEST_NAME} under {candidate}; run /media-new first")
    return candidate.resolve()


def load_project(location: str | Path | None = None) -> Project:
    """Read and validate the manifest, raising on anything structurally broken."""

    root = resolve_project(location)
    try:
        raw = yaml.safe_load((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ManifestError(f"{root / MANIFEST_NAME} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestError(f"{root / MANIFEST_NAME} must contain a mapping at the top level")

    slug = str(raw.get("slug") or root.name)
    if not SLUG_PATTERN.match(slug):
        raise ManifestError(f"invalid slug {slug!r} in {MANIFEST_NAME}")

    return Project(
        root=root,
        slug=slug,
        title=str(raw.get("title") or slug),
        status=str(raw.get("status") or "draft"),
        deliverables=_deliverables(raw.get("deliverables")),
        brand=_brand(raw.get("brand")),
        budget=_budget(raw.get("budget")),
        assets=_assets(raw.get("assets")),
        timeline=tuple(raw.get("timeline") or ()),
        video=_video(raw.get("video")),
        design=_design(raw.get("design")),
        effects=_effects(raw.get("effects")),
        raw=raw,
    )


def _deliverables(value: Any) -> tuple[Deliverable, ...]:
    if value in (None, {}):
        return ()
    if not isinstance(value, list):
        raise ManifestError("deliverables must be a list")
    deliverables: list[Deliverable] = []
    for entry in value:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ManifestError(f"every deliverable needs an id, got {entry!r}")
        deliverables.append(
            Deliverable(
                id=str(entry["id"]),
                kind=str(entry.get("kind") or "video"),
                container=entry.get("container"),
                resolution=entry.get("resolution"),
                fps=_optional_float(entry.get("fps"), "fps"),
                duration=_optional_float(entry.get("duration"), "duration"),
                loudness_lufs=_optional_float(entry.get("loudness_lufs"), "loudness_lufs"),
                preview=bool(entry.get("preview", False)),
            )
        )
    return tuple(deliverables)


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ManifestError(f"{field_name} must be a number, got {value!r}") from error


def _brand(value: Any) -> Brand:
    if not isinstance(value, dict):
        return Brand()
    palette = value.get("palette") or {}
    fonts = value.get("fonts") or {}
    if not isinstance(palette, dict) or not isinstance(fonts, dict):
        raise ManifestError("brand.palette and brand.fonts must be mappings")
    return Brand(
        palette={str(key): str(colour) for key, colour in palette.items()},
        fonts={str(key): str(name) for key, name in fonts.items()},
        references=_reference_pack(value.get("references")),
        voice=_brand_voice(value.get("voice")),
        safe_zones=_safe_zones(value.get("safe_zones")),
    )


def _reference_pack(value: Any) -> ReferencePack:
    if value in (None, {}):
        return ReferencePack()
    if not isinstance(value, dict):
        raise ManifestError("brand.references must be a mapping of role to asset ids")

    def _ids(key: str) -> tuple[str, ...]:
        if key not in value:
            return ()
        raw = value[key]
        if raw is None:
            return ()
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ManifestError(f"brand.references.{key} must be a list of asset ids")
        return tuple(raw)

    return ReferencePack(
        product=_ids("product"),
        spokesperson=_ids("spokesperson"),
        style_board=_ids("style_board"),
    )


def _brand_voice(value: Any) -> BrandVoice | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ManifestError("brand.voice must be a mapping of engine, voice_id and rate")
    engine = str(value.get("engine") or "")
    voice_id = str(value.get("voice_id") or "")
    rate = str(value.get("rate") or "")
    if not engine or not voice_id:
        raise ManifestError("brand.voice.engine and brand.voice.voice_id are required")
    return BrandVoice(engine=engine, voice_id=voice_id, rate=rate)


def _safe_zones(value: Any) -> dict[str, dict[str, int]]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ManifestError("brand.safe_zones must be a mapping of zone to margins")
    zones: dict[str, dict[str, int]] = {}
    for name, margins in value.items():
        if not isinstance(margins, dict):
            raise ManifestError(f"brand.safe_zones.{name} must be a mapping of margins")
        zones[str(name)] = {
            str(key): _optional_int(val, f"brand.safe_zones.{name}.{key}")
            for key, val in margins.items()
        }
    return zones


def _budget(value: Any) -> Budget:
    if not isinstance(value, dict):
        return Budget()
    defaults = Budget()
    return Budget(
        max_asset_bytes=_optional_int(
            value.get("max_asset_bytes", defaults.max_asset_bytes), "budget.max_asset_bytes"
        ),
        max_project_bytes=_optional_int(
            value.get("max_project_bytes", defaults.max_project_bytes), "budget.max_project_bytes"
        ),
        require_cc0_music=bool(value.get("require_cc0_music", defaults.require_cc0_music)),
    )


def _optional_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ManifestError(f"{field_name} must be a whole number, got {value!r}") from error


def _design(value: Any) -> DesignSpec | None:
    """The design section: a logo, font roles, and the variants to render."""

    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise ManifestError("design must be a mapping with variants")

    variants: list[VariantSpec] = []
    for entry in value.get("variants") or []:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("template"):
            raise ManifestError(f"every design variant needs an id and a template, got {entry!r}")
        size = str(entry.get("size") or "")
        width, height = _size(size, str(entry["id"]))
        variants.append(
            VariantSpec(
                id=str(entry["id"]),
                template=str(entry["template"]),
                width=width,
                height=height,
                headline=str(entry.get("headline") or ""),
                subhead=str(entry.get("subhead") or ""),
                trim=_variant_bool(entry.get("trim", False), "trim", str(entry["id"])),
                pad=_variant_int(entry.get("pad", 0), "pad", str(entry["id"])),
                profile=_variant_profile(entry.get("profile"), "profile", str(entry["id"])),
                image=str(entry["image"]) if entry.get("image") else None,
                image_fit=_variant_choice(
                    entry.get("image_fit", "contain"),
                    "image_fit",
                    IMAGE_FITS,
                    str(entry["id"]),
                ),
                image_position=_variant_choice(
                    entry.get("image_position", "right"),
                    "image_position",
                    IMAGE_POSITIONS,
                    str(entry["id"]),
                ),
                headline_size=_variant_optional_size(
                    entry.get("headline_size"), "headline_size", str(entry["id"])
                ),
                subhead_size=_variant_optional_size(
                    entry.get("subhead_size"), "subhead_size", str(entry["id"])
                ),
            )
        )
    if not variants:
        raise ManifestError("design.variants must list at least one variant")

    fonts = value.get("fonts") or {}
    if not isinstance(fonts, dict):
        raise ManifestError("design.fonts must map a role to an asset id")
    logo = value.get("logo")
    return DesignSpec(
        variants=tuple(variants),
        logo=str(logo) if logo else None,
        fonts={str(role): str(asset) for role, asset in fonts.items()},
    )


def parse_resolution(value: str) -> tuple[int, int]:
    """Parse a WxH resolution string like 1920x1080 into (width, height)."""
    if not RESOLUTION_PATTERN.match(value):
        raise ManifestError(f"size {value!r} must be WxH, for example 1920x1080")
    width, _, height = value.lower().partition("x")
    return int(width), int(height)


def _size(value: str, variant_id: str) -> tuple[int, int]:
    try:
        return parse_resolution(value)
    except ManifestError as error:
        raise ManifestError(f"design variant {variant_id!r}: {error}") from error


def _variant_bool(value: Any, field_name: str, variant_id: str) -> bool:
    if not isinstance(value, bool):
        raise ManifestError(
            f"design variant {variant_id!r}: {field_name} must be a boolean, got {value!r}"
        )
    return value


def _variant_int(value: Any, field_name: str, variant_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(
            f"design variant {variant_id!r}: {field_name} must be a whole number, got {value!r}"
        )
    return value


def _variant_profile(value: Any, field_name: str, variant_id: str) -> str | None:
    if value is not None and value != "srgb":
        raise ManifestError(
            f"design variant {variant_id!r}: {field_name} must be 'srgb' or unset, got {value!r}"
        )
    return value


def _variant_choice(value: Any, field_name: str, allowed: tuple[str, ...], variant_id: str) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise ManifestError(
            f"design variant {variant_id!r}: {field_name} must be one of "
            f"{', '.join(allowed)}, got {value!r}"
        )
    return text


def _variant_optional_size(value: Any, field_name: str, variant_id: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ManifestError(
            f"design variant {variant_id!r}: {field_name} must be a positive whole number, "
            f"got {value!r}"
        )
    return value


def _effects(value: Any) -> tuple[dict[str, Any], ...]:
    """Top-level preset references. Validated against the registry by the caller."""

    if value in (None, {}):
        return ()
    if not isinstance(value, list):
        raise ManifestError("effects must be a list of preset references")
    entries: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ManifestError(f"every effects entry needs a preset id, got {entry!r}")
        entries.append(dict(entry))
    return tuple(entries)


def _video(value: Any) -> VideoPlan | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise ManifestError("video must be a mapping with shots, and optionally voice and music")

    shots: list[Shot] = []
    for entry in value.get("shots") or []:
        if not isinstance(entry, dict) or not entry.get("asset"):
            raise ManifestError(f"every video.shots entry needs an asset id, got {entry!r}")
        transition = str(entry.get("transition") or "cut").lower()
        if transition not in ("cut", "crossfade"):
            raise ManifestError(
                f"video.shots[{entry['asset']}].transition must be cut or crossfade, "
                f"got {transition!r}"
            )
        shots.append(
            Shot(
                asset=str(entry["asset"]),
                in_point=_optional_float(entry.get("in"), "in") or 0.0,
                duration=_optional_float(entry.get("duration"), "duration"),
                transition=transition,
                shot_type=str(entry["shot_type"]) if entry.get("shot_type") else None,
            )
        )
    if not shots:
        raise ManifestError("video.shots must list at least one asset id")

    cues: dict[str, AudioCue | None] = {}
    for name in ("voice", "music"):
        raw_cue = value.get(name)
        if raw_cue in (None, {}):
            cues[name] = None
            continue
        if not isinstance(raw_cue, dict) or not raw_cue.get("asset"):
            raise ManifestError(f"video.{name} needs an asset id")
        cues[name] = AudioCue(
            asset=str(raw_cue["asset"]),
            in_point=_optional_float(raw_cue.get("in"), "in") or 0.0,
            gain=_optional_float(raw_cue.get("gain"), "gain") or 1.0,
            fade_in=_optional_float(raw_cue.get("fade_in"), "fade_in") or 0.0,
            fade_out=_optional_float(raw_cue.get("fade_out"), "fade_out") or 0.0,
            duck=bool(raw_cue.get("duck", True)),
            duration=_optional_float(raw_cue.get("duration"), "duration"),
            clamp_to_video=bool(raw_cue.get("clamp_to_video", True)),
        )
    fade_in = _optional_float(value.get("fade_in"), "fade_in") or 0.0
    fade_out = _optional_float(value.get("fade_out"), "fade_out") or 0.0
    for name, fade in (("fade_in", fade_in), ("fade_out", fade_out)):
        if fade < 0:
            raise ManifestError(f"video.{name} cannot be negative")
    titles: list[TitleText] = []
    for entry in value.get("titles") or []:
        if not isinstance(entry, dict) or not str(entry.get("text") or "").strip():
            raise ManifestError(f"every video.titles entry needs text, got {entry!r}")
        titles.append(
            TitleText(
                text=str(entry["text"]),
                start=_optional_float(entry.get("start"), "start") or 0.0,
                duration=_optional_float(entry.get("duration"), "duration") or 2.0,
                size=int(entry.get("size") or 64),
                colour=str(entry.get("colour") or "#ffffffff"),
                position=str(entry.get("position") or "+0+0"),
            )
        )

    crossfade = _optional_float(value.get("crossfade"), "crossfade")
    crossfade = 0.5 if crossfade is None else crossfade
    if crossfade < 0:
        raise ManifestError("video.crossfade cannot be negative")
    return VideoPlan(
        shots=tuple(shots),
        voice=cues["voice"],
        music=cues["music"],
        fade_in=fade_in,
        fade_out=fade_out,
        crossfade=crossfade,
        titles=tuple(titles),
    )


def _assets(value: Any) -> dict[str, str]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise ManifestError("assets must be a mapping of id to path")
    return {str(key): str(path) for key, path in value.items()}


def _unknown_fields(project: Project) -> list[str]:
    """Top-level and per-section keys that the manifest does not define."""

    problems: list[str] = []
    for key in sorted(set(project.raw) - MANIFEST_KEYS):
        problems.append(f"unknown manifest field: {key}")
    deliverables = project.raw.get("deliverables")
    if isinstance(deliverables, list):
        for entry in deliverables:
            if isinstance(entry, dict):
                for key in sorted(set(entry) - DELIVERABLE_KEYS):
                    problems.append(f"unknown deliverable field: {key}")
    sections = (
        ("brand", BRAND_KEYS),
        ("budget", BUDGET_KEYS),
        ("video", VIDEO_KEYS),
        ("design", DESIGN_KEYS),
    )
    for name, allowed in sections:
        section = project.raw.get(name)
        if isinstance(section, dict):
            for key in sorted(set(section) - allowed):
                problems.append(f"unknown {name} field: {key}")

    video = project.raw.get("video")
    if isinstance(video, dict):
        for entry in video.get("shots") or []:
            if isinstance(entry, dict):
                for key in sorted(set(entry) - SHOT_KEYS):
                    problems.append(f"unknown video.shots field: {key}")
        for name in ("voice", "music"):
            cue = video.get(name)
            if isinstance(cue, dict):
                for key in sorted(set(cue) - CUE_KEYS):
                    problems.append(f"unknown video.{name} field: {key}")
        for entry in video.get("titles") or []:
            if isinstance(entry, dict):
                for key in sorted(set(entry) - TITLE_KEYS):
                    problems.append(f"unknown video.titles field: {key}")
    design = project.raw.get("design")
    if isinstance(design, dict):
        for entry in design.get("variants") or []:
            if isinstance(entry, dict):
                for key in sorted(set(entry) - VARIANT_KEYS):
                    problems.append(f"unknown design.variants field: {key}")
    return problems


def validate_project(project: Project) -> ValidationReport:
    """Check the manifest, and the assets it references, without touching the network."""

    report = ValidationReport()
    errors = report.errors
    warnings = report.warnings

    seen: set[str] = set()
    for deliverable in project.deliverables:
        if deliverable.id in seen:
            errors.append(f"duplicate deliverable id: {deliverable.id}")
        seen.add(deliverable.id)
        if deliverable.kind not in DELIVERABLE_KINDS:
            errors.append(
                f"deliverable {deliverable.id}: kind {deliverable.kind!r} is not one of "
                f"{', '.join(DELIVERABLE_KINDS)}"
            )
        if deliverable.resolution and not RESOLUTION_PATTERN.match(deliverable.resolution):
            errors.append(
                f"deliverable {deliverable.id}: resolution {deliverable.resolution!r} must be WxH"
            )
        if (
            deliverable.kind == "video"
            and deliverable.resolution
            and RESOLUTION_PATTERN.match(deliverable.resolution)
            and not getattr(deliverable, "preview", False)
        ):
            width, height = parse_resolution(deliverable.resolution)
            if width < 1920 or height < 1080:
                errors.append(
                    f"deliverable {deliverable.id}: resolution {deliverable.resolution} is below "
                    "the 1920x1080 minimum floor (mark preview: true to permit)"
                )
        if deliverable.duration is not None and deliverable.duration <= 0:
            errors.append(f"deliverable {deliverable.id}: duration must be positive")
        if deliverable.loudness_lufs is not None:
            low, high = LOUDNESS_RANGE
            if not low <= deliverable.loudness_lufs <= high:
                errors.append(
                    f"deliverable {deliverable.id}: loudness_lufs {deliverable.loudness_lufs} "
                    f"is outside the plausible range {low} to {high}"
                )

    for name, colour in project.brand.palette.items():
        if not COLOUR_PATTERN.match(colour):
            errors.append(f"brand.palette.{name}: {colour!r} must be a #rrggbb colour")

    if project.budget.max_asset_bytes <= 0:
        errors.append("budget.max_asset_bytes must be positive")
    if project.budget.max_project_bytes < project.budget.max_asset_bytes:
        errors.append("budget.max_project_bytes must be at least budget.max_asset_bytes")

    errors.extend(_unknown_fields(project))

    for asset_id, relative in project.assets.items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            errors.append(f"assets.{asset_id}: {relative!r} must be a path inside the project")
            continue
        declared = project.root / relative
        if not declared.exists():
            warnings.append(f"assets.{asset_id}: {relative} is not staged yet")
            continue
        fields = read_sidecar(declared)
        if not fields:
            errors.append(
                f"assets.{asset_id}: {relative} has no .license sidecar; a deliverable may not "
                "reference an asset the ledger cannot account for"
            )
        elif fields.get("SPDX-License-Identifier", UNKNOWN_SPDX) == UNKNOWN_SPDX:
            errors.append(
                f"assets.{asset_id}: {relative} is quarantined with an unknown licence; confirm "
                "the licence and stage it again"
            )

    if project.video is not None:
        for title in project.video.titles:
            if title.duration <= 0:
                errors.append(f"video.titles[{title.text!r}].duration must be positive")
        referenced = [shot.asset for shot in project.video.shots]
        for name, cue in (("voice", project.video.voice), ("music", project.video.music)):
            if cue is not None:
                referenced.append(cue.asset)
            if cue is not None and cue.gain <= 0:
                errors.append(f"video.{name}.gain must be positive")
            if cue is not None and cue.duration is not None and cue.duration <= 0:
                errors.append(f"video.{name}.duration must be positive")
        for asset_id in referenced:
            if asset_id not in project.assets:
                errors.append(
                    f"video references asset {asset_id!r}, which is not declared under assets:"
                )
        for shot in project.video.shots:
            if shot.duration is not None and shot.duration <= 0:
                errors.append(f"video.shots[{shot.asset}].duration must be positive")

    if project.design is not None:
        for variant in project.design.variants:
            if variant.image is not None and variant.image not in project.assets:
                errors.append(
                    f"design.variants[{variant.id}]: image asset {variant.image!r} is not "
                    "declared under assets:"
                )

    for role in ("product", "spokesperson", "style_board"):
        for asset_id in getattr(project.brand.references, role):
            if asset_id not in project.assets:
                errors.append(
                    f"brand.references.{role}: asset {asset_id!r} is not declared under assets:"
                )

    for name, margins in project.brand.safe_zones.items():
        for key, value in margins.items():
            if value < 0:
                errors.append(f"brand.safe_zones.{name}.{key}: margin {value} must be non-negative")

    if not project.deliverables:
        warnings.append("no deliverables declared; renders have nothing to produce")

    return report
