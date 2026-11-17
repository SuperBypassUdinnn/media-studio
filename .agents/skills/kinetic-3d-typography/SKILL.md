---
name: kinetic-3d-typography
description: Design and composite scene-integrated 3D typography, per-character animators, and light-swept kinetic motion titles using EffectCraft 0.24.1. Use when replacing flat bottom subtitles with cinematic typography, title cards, or Z-depth sandwiched headlines.
---

# Kinetic & 3D Typography

## 1. Subtitle Ban for Commercial Advertisements
Promotional and luxury product commercials default to `subtitles: false`. Rather than small text burned at the bottom of the screen, narrative copy is expressed as high-craft 3D typography integrated directly into the spatial scene.

## 2. Z-Depth Sandwiched Typography (`z_layer: "behind-subject"`)
- Position 3D title text *behind* the foreground 3D product but in front of the background environment.
- Use Blender foreground alpha passes or EffectCraft Roto Brush (`sam2.1-hiera-base-plus`) to allow the 3D product silhouette to organically occlude the 3D typography.

## 3. Kinetic Animation Blueprints
- **Per-Character 3D Stagger**: Animate individual character position Z, rotation Y, and opacity with exponential easing.
- **Letter-Spacing Expansion**: Animate tracking from condensed to expansive as the camera settles.
- **Specular Gleam**: Sweep `ec.stylize.cclightsweep` across metallic/gold letterforms.
- **Optical Halation**: Apply `ec.stylize.glow` with threshold matching peak letter luminance.
