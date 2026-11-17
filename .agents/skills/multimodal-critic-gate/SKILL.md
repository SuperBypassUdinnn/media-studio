---
name: multimodal-critic-gate
description: Execute the mandatory Dual-Gate quality critic powered by submodules/media-mcp analytics and a 7-axis multimodal vision rubric. Use when verifying storyboards, rendered video deliverables, color palettes, motion energy, and A/V sync.
---

# Multimodal Critic Gate

## 1. Dual-Gate Architecture

### Gate 1: Deterministic Quantitative Gates (`submodules/media-mcp`)
All gates report `severity: "error"` and block release (`ok: false`, exit code 1) upon failure:
1. **Motion Energy & Anti-Hold**: `media_motion` static hold (`max_diff < 2.0`) `<= 35%` of scene duration and `<= 20%` of total video duration.
2. **Anti-Boilerplate OCR**: `media_ocr` proves absence of template leaks (`"New feature"`, `"Try it free"`, `"Up from last period"`) and verifies 3D title text integrity.
3. **Image Quality & Exposure**: `media_image_quality` rejects `poor` tiers and enforces mean luma `18..240` and RMS contrast `>= 12`.
4. **Color Palette & 60-30-10 Harmony**: Enforces WCAG `>= 4.5:1`, CIELAB $\Delta E \le 38.0$ against `brand.palette`, and accent area `<= 25%`.
5. **A/V Beat, Envelope & Loudness**: Loudness at `-14.0 LUFS +- 1.0`, zero black/frozen segments, sound envelope decay `< -45 dBFS`, and cuts snapped to `media_beats` onsets.
6. **Montage Grammar & Eye-Trace**: Rejects adjacent identical `shot_scale` + `camera_angle` (jump-cuts), enforces pure sensory breathing shots, and bounds eye-trace jump `<= 0.45`.

### Gate 2: Mandatory 7-Axis Multimodal Vision Rubric
Requires `<project>/out/reports/vision-critique.json` evaluated by `google/antigravity-gemini-3.8-flash` or native main session vision with all 7 axes scored `>= 4 / 5`:
1. `three_d_object_and_environment_depth`
2. `three_d_and_kinetic_typography_z_depth`
3. `camera_choreography_speed_ramp_and_motion`
4. `shot_scale_montage_and_eye_trace`
5. `color_palette_selection_and_harmony`
6. `chiaroscuro_lighting_and_optical_finish`
7. `conceptual_fit_to_brief`
