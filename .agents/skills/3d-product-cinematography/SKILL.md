---
name: 3d-product-cinematography
description: Direct and render 3D product commercials with Blender 5.3, assets-3d PBR materials, studio HDRI environments, Chiaroscuro key/fill/rim lighting, and speed-ramped camera rigs. Use when creating commercial product shots, luxury flacon/bottle scenes, or technical hardware visualizations.
---

# 3D Product Cinematography

## 1. Product Invariant: No Flat 2D Decals
Product scenes must feature authentic 3D geometry, multi-part shaders, and physically based rendering:
- Find or stage 3D models via `assets-3d` MCP (`search_assets`, `get_asset`, `download_asset`) or procedural multi-part geometry (`stage_3d.py`).
- Glass & Liquids: PBR Transmission `1.0`, IOR `1.45–1.52`, roughness `0.02`.
- Metallic Accents: Metallic `1.0`, roughness `0.15–0.25`, specular tint matching `brand.palette.accent`.
- Pedestals: Wet obsidian, textured slate, or brushed anodized aluminum.

## 2. Chiaroscuro Studio Lighting Ratio
- **Key Light**: Focused spot or soft area light at 45-degree angle.
- **Fill Light**: Subdued low-intensity bounce (Key-to-Fill ratio `6:1` to `8:1` for luxury/noir).
- **Rim / Backlight**: High-intensity narrow beam outlining product silhouette against dark backgrounds.
- **Volumetric Depth**: Atmospheric haze scattering rim rays.

## 3. Speed-Ramped 3D Camera Trajectories
- `slow-dolly-in`: 50mm–85mm lens gliding toward hero focal point with f/1.8 Depth of Field.
- `macro-surface-pan`: 105mm macro lens tracing droplets and engraving relief.
- `orbital-reveal`: 120-degree or 360-degree rotational arc with dynamic rim reflection.
- 180-degree shutter motion blur enabled on all moving camera passes.
