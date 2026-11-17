---
name: visual-storyboarding
description: Build, iterate, and critique full-resolution visual storyboards prior to video rendering. Use when planning scene layouts, pre-visualizing 3D/Flow/EffectCraft keyframes, or gating human approval.
---

# Visual Storyboarding & Pre-Production Iteration

## 1. Visual-First Storyboard Standard
Every storyboard panel must display authentic rendered visual keyframes at native resolution (1080p):
- `blender-3d`: 1-frame render of 3D scene with PBR materials, HDRI, and studio lighting.
- `effectcraft`: 1-frame render (`effectcraft-cli render-frame`) capturing peak 3D typography and effects.
- `flow-gen`: 0-credit concept still via Google Flow `Nano Banana Pro` conditioned on 3D reference frames.
- `composite`: Composite RGBA typography still overlaid on base 3D/Flow still.
- At least 85% of each panel's surface area in `storyboard.png` must be visual image content.

## 2. Mandatory Self-Iteration Loop
1. Render all scene keyframes: `ms storyboard build <project>`.
2. Inspect every frame visually via `read` and run `ms critique <project> --stage storyboard`.
3. If any frame shows poor lighting, low contrast, clipped text, or flat composition, adjust `scene-graph.json` and re-render only the defective scene: `ms storyboard build <project> --scene <scene_id>`.
4. Only present `storyboard.png` to the user when `ms critique` reports `ok: true`.
