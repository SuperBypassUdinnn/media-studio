# Media-Studio Architecture & Creative Standards

## 1. Model Tier & Spatial Reasoning Invariant
- Visual layout, 3D scene choreography, EffectCraft composition authoring, and multimodal critique must execute natively in the main session or via `google/antigravity-gemini-3.8-flash`.
- Weak subagent models (`nemotron-3.5-super-vl`) are strictly barred from spatial, 3D, and visual design tasks; they are restricted to mechanical non-visual tasks.

## 2. Asset Priority SOP
- **Tier 1 (Mandatory First Look)**: Verified local/library vector SVGs, PNGs, and 3D models (`assets-3d` MCP: Poly Haven, ambientCG, BlenderKit, Kenney, Quaternius). Never hallucinate recognized logos or brand marks with AI.
- **Tier 2 (Generative Visuals)**: Google Flow (`Nano Banana Pro` @ 0 credits for concept/reference stills; `Veo 3.1 - Lite` @ 10 credits for atmospheric B-roll conditioned on 3D reference renders).
- **Tier 3 (Zero Silent Fallback)**: Silent fallback to keyless third-party generators (Pollinations.ai) is permanently disabled (`allow_keyless=False`).

## 3. World-Class Cinematography & Montage Grammar
- **Shot Scale Progression**: Every scene declares `shot_scale` (`ECU | CU | MS | WS`) and `camera_angle` (`low-hero | eye-level | high-zenith | dutch-tilt`). Adjacent scenes must contrast in scale or angle (zero visual jump-cuts).
- **Speed Ramping & Motion Continuity**: Camera/object moves follow non-linear velocity curves (`slow-fast-slow`, `ease-in-whip-out`) with directional vector continuity across cuts.
- **Tension & Release**: Commercials alternate between Pure Sensory Shots (zero on-screen text, 1.5s–2.5s macro details) and Hero Typography Reveals.
- **3D & Kinetic Typography**: Product commercials default to `subtitles: false`. Key messages are integrated directly into 3D space as Z-depth sandwiched 3D typography (`text-orbit-3d`, per-character animators).
- **60-30-10 Color Theory**:
  - 60% Dominant Tone (`brand.palette.primary`): Deep environment/studio foundation.
  - 30% Secondary Structural Tone (`brand.palette.secondary`): Pedestals, shadows, rim lights.
  - 10% Specular Accent & Ink (`brand.palette.accent` + `brand.palette.ink`): High-contrast focal highlights with WCAG contrast `>= 4.5:1`.
- **Optical Pipeline**: 180-degree shutter motion blur (`motion_blur: true`), optical lens bokeh, subtle halation, and a unified Master Finishing Pass (LUT + 35mm grain).
- **4-Layer Sound Design**: Ambience, Diegetic Foley, Cinematic Transitions, and Sub-Bass Drops with J-Cuts (`-0.35s`) and L-Cuts (`+0.50s`).
