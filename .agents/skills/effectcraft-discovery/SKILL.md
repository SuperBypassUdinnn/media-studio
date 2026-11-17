---
name: effectcraft-discovery
description: Discover and query EffectCraft 0.24.1 engine commands, 331 built-in effects across 20 categories, Essential Graphics templates, and AI segmentation/tracking models. Use when authoring EffectCraft compositions, choosing visual effects, or inspecting engine capabilities.
---

# EffectCraft Discovery & Capability Reference

## 1. Catalog Inspection Commands
- List all 20 effect categories or filter by category:
  `ms motion catalog [--category <cat>] [--json]`
- List built-in Essential Graphics templates:
  `ms motion catalog --templates [--json]`
- Search all 763 engine commands via CLI:
  `effectcraft-cli commands --filter <keyword>`
- Inspect installed AI segmentation/tracking models:
  `effectcraft-cli exec roto.models --json`
  `effectcraft-cli exec face.models --json`

## 2. Key Effect Identifiers by Creative Intent
- **Luxury Light Sweeps & Sheen**: `ec.stylize.cclightsweep` (glides across metallic, glass, or serif title layers).
- **Bloom & Volumetric Glow**: `ec.stylize.glow` and `ec.generate.lensflare`.
- **Cinematic Optical Bokeh**: `ec.blur.cameralens` (physically modeled iris bokeh).
- **3D Particle Atmospheres**: `ec.sim.particleworld` and `ec.sim.ccstarburst`.
- **Stylized Color Grading**: `ec.color.lumetri`, `ec.color.curves`, `ec.color.levels`.
- **Cinematic Transitions**: `ec.transition.cclightwipe`, `ec.transition.linearwipe`.

## 3. Advanced 3D Composition Recipe
```json
[
  {"command": "comp.new", "params": {"name": "HeroComp", "width": 1920, "height": 1080, "duration": 5.0, "fps": 60.0}},
  {"command": "camera.newCamera", "params": {"name": "CinematicCam", "preset": "50mm", "type": "twoNode"}},
  {"command": "light.newLight", "params": {"name": "RimSpot", "type": "spot", "color": "#F59E0B", "intensity": 180.0}},
  {"command": "file.newFromTemplate", "params": {"id": "text-orbit-3d"}},
  {"command": "essential.set", "params": {"layer": "3D Text Orbit", "control": "Title", "value": "AURA NOIR"}}
]
```
