"""Comprehensive catalog of EffectCraft built-in effects, templates, and AI models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EffectSpec:
    id: str
    name: str
    category: str
    description: str


@dataclass(frozen=True)
class TemplateSpec:
    id: str
    name: str
    category: str
    description: str
    duration: float
    controls: tuple[str, ...]


EFFECTCRAFT_CATEGORIES: dict[str, str] = {
    "stylize": "Visual styling, photorealistic glow, light sweeps, glass refraction, vignettes",
    "color": "3-way color grading wheels, Lumetri, curves, levels, exposure, tint, black & white",
    "blur": "Camera lens blur (physical bokeh), radial blur, fast box blur, unsharp mask",
    "distort": "Displacement mapping, optics compensation, mesh warp, wave warp, ripple",
    "generate": "Cinematic lens flares, gradient ramps, 4-color gradients, laser beams, lightnings",
    "sim": "Particle world, particle systems, star bursts, snow, rain, ball action",
    "transition": "Linear wipe, radial wipe, CC light wipe, venetian blinds, block dissolve",
    "3d": "Cryptomatte extraction, depth of field, fog 3D, depth matte, EXtractoR",
    "key": "Linear color key, color difference key, spill suppressor, matte choker",
    "noise": "Fractal noise, turbulent noise, cinematic 35mm grain",
    "perspective": "3D glasses, bevel alpha, radial shadow, corner pin",
    "time": "Optical flow timewarp, echo, posterize time",
    "channel": "Channel calculations, set matte, invert, shift channels",
    "audio": "Parametric EQ, reverb, compressor, delay",
    "vr": "Immersive video 360 blur, glow, chromatic aberrations",
}

EFFECTCRAFT_TEMPLATES: tuple[TemplateSpec, ...] = (
    TemplateSpec(
        id="logo-reveal",
        name="Logo Reveal",
        category="Brand",
        description="A glowing, spinning mark resolving into brand name with particle burst.",
        duration=5.0,
        controls=("Brand Name", "Logo Color"),
    ),
    TemplateSpec(
        id="title-card",
        name="Title Card",
        category="Titles",
        description="A centred title that scales up over a gradient, with subtitle fade.",
        duration=5.0,
        controls=("Title", "Subtitle"),
    ),
    TemplateSpec(
        id="kinetic-type",
        name="Kinetic Type",
        category="Titles",
        description="Three words popping on in rhythm with spring inertia.",
        duration=4.0,
        controls=("Word 1", "Word 2", "Word 3"),
    ),
    TemplateSpec(
        id="lower-third",
        name="Lower Third",
        category="Titles",
        description="Name and title sliding in on an accent bar.",
        duration=6.0,
        controls=("Name", "Title", "Bar Color"),
    ),
    TemplateSpec(
        id="text-orbit-3d",
        name="3D Text Orbit",
        category="3D",
        description="3D title cards with camera orbiting over a reflective 3D floor.",
        duration=8.0,
        controls=("Title",),
    ),
    TemplateSpec(
        id="vertical-story",
        name="Vertical 9:16 Story",
        category="Social",
        description="1080x1920 story with headline and call to action.",
        duration=8.0,
        controls=("Headline", "Call to Action"),
    ),
    TemplateSpec(
        id="slideshow",
        name="Slideshow",
        category="Photo",
        description="Three slides with a slow push-in and cross-fades.",
        duration=9.0,
        controls=("Caption 1", "Caption 2", "Caption 3"),
    ),
    TemplateSpec(
        id="social-square",
        name="Social Square",
        category="Social",
        description="1080x1080 square social post with headline and accent handle.",
        duration=6.0,
        controls=("Headline", "Handle", "Accent Color"),
    ),
)

KEY_BUILTIN_EFFECTS: tuple[EffectSpec, ...] = (
    EffectSpec(
        "ec.stylize.cclightsweep",
        "CC Light Sweep",
        "stylize",
        "Luxury metallic and glass gleam sweep",
    ),
    EffectSpec("ec.stylize.glow", "Glow", "stylize", "Photorealistic bloom and luminance aura"),
    EffectSpec(
        "ec.stylize.ccvignette", "CC Vignette", "stylize", "Cinematic perimeter edge darkening"
    ),
    EffectSpec("ec.stylize.ccglass", "CC Glass", "stylize", "Refractive liquid glass displacement"),
    EffectSpec(
        "ec.generate.lensflare",
        "Lens Flare",
        "generate",
        "Cinematic anamorphic and prime lens flares",
    ),
    EffectSpec(
        "ec.generate.gradientramp",
        "Gradient Ramp",
        "generate",
        "Smooth two-point linear and radial gradients",
    ),
    EffectSpec(
        "ec.blur.cameralens",
        "Camera Lens Blur",
        "blur",
        "Physically modeled optical bokeh with iris curvature",
    ),
    EffectSpec(
        "ec.blur.fastbox", "Fast Box Blur", "blur", "GPU-accelerated box blur with edge repetition"
    ),
    EffectSpec(
        "ec.color.lumetri",
        "Lumetri Color",
        "color",
        "3-way color wheels, exposure, contrast, and saturation",
    ),
    EffectSpec(
        "ec.color.curves", "Curves", "color", "Per-channel and master RGB spline tone curves"
    ),
    EffectSpec(
        "ec.distort.displacementmap",
        "Displacement Map",
        "distort",
        "Texture and luminance-driven geometry warp",
    ),
    EffectSpec(
        "ec.distort.opticscompensation",
        "Optics Compensation",
        "distort",
        "Wide-angle lens field curvature and fisheye",
    ),
    EffectSpec(
        "ec.sim.particleworld",
        "CC Particle World",
        "sim",
        "3D particle emitter with gravity and turbulence",
    ),
    EffectSpec(
        "ec.sim.ccstarburst", "CC Star Burst", "sim", "3D depth flight through ambient starfield"
    ),
    EffectSpec(
        "ec.transition.cclightwipe",
        "CC Light Wipe",
        "transition",
        "Light beam sweep wipe across incoming layer",
    ),
    EffectSpec(
        "ec.transition.linearwipe", "Linear Wipe", "transition", "Directional feathered linear wipe"
    ),
    EffectSpec(
        "ec.time.timewarp", "Timewarp", "time", "Bidirectional optical-flow motion interpolation"
    ),
    EffectSpec(
        "ec.matte.object",
        "Roto Brush Object Matte",
        "matte",
        "AI-driven foreground segmentation mask",
    ),
)


def list_templates() -> list[dict[str, Any]]:
    """Return all built-in EffectCraft templates as JSON-serializable dictionaries."""
    return [
        {
            "id": t.id,
            "name": t.name,
            "category": t.category,
            "description": t.description,
            "duration": t.duration,
            "controls": list(t.controls),
        }
        for t in EFFECTCRAFT_TEMPLATES
    ]


def list_effects(category: str | None = None) -> list[dict[str, Any]]:
    """Return key built-in EffectCraft effects, optionally filtered by category."""
    items = KEY_BUILTIN_EFFECTS
    if category:
        items = tuple(e for e in items if e.category.lower() == category.lower())
    return [
        {
            "id": e.id,
            "name": e.name,
            "category": e.category,
            "description": e.description,
        }
        for e in items
    ]
