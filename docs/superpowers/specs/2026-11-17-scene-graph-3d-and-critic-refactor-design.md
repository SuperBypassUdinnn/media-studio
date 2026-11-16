# Media-Studio v3 Architectural Refactor: Skill-First Contracts, Composable 3D/Motion Scene-Graph, Visual Storyboarding, and Submodule `media-mcp` Dual-Gate Critic

**Date:** 2026-11-17
**Status:** Draft for Review
**Scope:** Architectural overhaul of `media-studio` creative video production, replacing the rigid `ms promo` 5-beat pipeline with a skill-governed, multi-engine 3D/Motion Scene-Graph (`Blender 5.3` + `EffectCraft 0.24.1` + `Google Flow` + `FilmCraft 0.15.0`), visual-first iterative storyboarding (`ms storyboard`), and a mandatory Dual-Gate quality critic (`ms critique`) powered by `submodules/media-mcp` and multimodal vision rubrics.

---

## 1. Problem Statement & Root-Cause Audit

### 1.1 Why the Legacy `ms promo` Pipeline Failed
A first-principles audit and objective inspection via `media-mcp` (`media_motion`, `media_ocr`, `media_manifest`) exposed six structural failures in the legacy `src/media_studio/promo.py` pipeline:

1. **Weak-Model Implementation Debt**: Legacy promo modules were implemented by a model (`nemotron-3.5-super-vl`) with weak spatial and aesthetic reasoning. It optimized exclusively for passing shallow unit tests rather than visual craft, resulting in static slideshows disguised as video.
2. **Rigid 5-Beat Cookie-Cutter Contract (`hook/problem/demo/proof/cta`)**: `plan_promo()` hardcoded every video into the exact same five-beat sequence using 2D HTML cards (`saas-feature-card`, `metric-counter`) and burned-in `.ass` subtitles, regardless of whether the product was a developer CLI or a luxury fragrance.
3. **Underutilization of EffectCraft 0.24.1 (763 Commands, 331 Effects)**: Despite EffectCraft supporting Advanced 3D cameras, 3D lights, 3D primitives, per-character 3D text animators, particle emitters (`ec.sim.particleworld`), optical flares (`ec.generate.lensflare`), and metallic light sweeps (`ec.stylize.cclightsweep`), `media-studio` used less than 2% of its surface area (flat 2D text and solid rectangles).
4. **Absence of True 3D Objects, 3D Environments, and 3D/Kinetic Typography**: Product commercials failed to load real 3D product models (`assets-3d` / Blender `.glb`/`.obj`), PBR glass/metal shaders, studio HDRI environments, and 3D camera rigs (*orbit, macro tracking, dolly-in*). Furthermore, videos relied on bottom-screen subtitles instead of scene-integrated 3D and kinetic typography.
5. **Non-Visual Storyboards & Zero Pre-Production Iteration**: `_write_storyboard()` rendered text boxes and tiny thumbnails from a fixed code template rather than full-frame visual renders per shot, and performed zero iterative refinement at the storyboard stage before committing to full video rendering.
6. **Blind Quality Control (`critique_promo`)**: The legacy critic checked only mechanical metadata (`bt709`, safe-zone bounding boxes, word density) while ignoring `media-mcp` motion energy (`26.3s` of `30.0s` frozen hold passed without error), semantic OCR leaks, and 3D/visual aesthetics.

---

## 2. Architectural Goals & Non-Goals

### 2.1 Goals
1. **Skill-First Repository Architecture (`.agents/skills/` & `.agents/rules/`)**: Establish six modular repository-level skills and architectural rules (inspired by Remotion and EffectCraft skill modularity) before refactoring engine code, ensuring any implementing or operating agent discovers and applies the full capabilities of EffectCraft, Blender 3D, Google Flow, and `media-mcp`.
2. **Retire `ms promo` in Favor of a Composable Scene-Graph (`ms storyboard` -> `ms scene` -> `ms critique`)**: Replace the fixed 5-beat template lock with an open, declarative `scene-graph.json` where each scene independently selects and composites `blender-3d`, `effectcraft`, `flow-gen`, and `filmcraft`.
3. **True 3D Product & Environment Pipeline + 3D/Kinetic Typography**: Mandate 3D geometry, PBR materials, HDRI lighting, and 3D camera choreography for product scenes, paired with EffectCraft 3D/kinetic typography instead of flat burned-in subtitles.
4. **Visual-First Storyboard with Mandatory Self-Iteration**: Render authentic high-resolution visual keyframes per scene (from Blender 3D, EffectCraft `render-frame`, or Google Flow `Nano Banana Pro`), require automated + vision self-iteration at the storyboard stage, and present the visual storyboard for human approval prior to video rendering.
5. **Git Submodule `media-mcp` + Dual-Gate Critic**: Integrate `https://github.com/SuperBypassUdinnn/media-mcp.git` at `submodules/media-mcp` as a mandatory quantitative gate (`media_motion`, `media_ocr`, `media_image_quality`, `media_avsync`) combined with a mandatory 5-axis Multimodal Vision Rubric.
6. **Clean Context Hygiene**: Purge all legacy polluted projects under `~/Projects/*` so historical anti-patterns cannot contaminate future agent sessions.

### 2.2 Non-Goals
- Adding heavy npm/React runtime dependencies (Remotion remains excluded as a runtime dependency; its modular skill pedagogy and spring math are retained).
- Using low-tier or non-vision models for spatial layout, 3D choreography, or visual critique.

---

## 3. Repository-Level Skill & Rule Contracts (`.agents/`)

To comply with `project-scaffold` standards and eliminate agent hallucination, `media-studio` ships six repository-local skills under `.agents/skills/` and architectural rules under `.agents/rules/`, symlinked into `~/.agents/skills/` and `~/.config/opencode/skills/`:

### 3.1 `.agents/rules/architecture.md`
Defines the non-negotiable engineering and creative invariants of `media-studio`:
- **Model Tier Requirement**: Visual layout, 3D scene choreography, EffectCraft composition authoring, and multimodal critique must execute natively in the main session or via `google/antigravity-gemini-3.8-flash`. Weak subagent models (`nemotron-3.5-super-vl`) are restricted to mechanical non-visual tasks.
- **Asset Priority SOP**:
  - Tier 1: Verified local/library vector SVGs, PNGs, and 3D models (`assets-3d` MCP: Poly Haven, ambientCG, BlenderKit, Kenney, Quaternius). Never hallucinate recognized logos or brand marks with AI.
  - Tier 2: Google Flow (`Nano Banana Pro` @ 0 credits for concept/reference stills; `Veo 3.1 - Lite` @ 10 credits for atmospheric B-roll conditioned on 3D reference renders).
  - Tier 3: Zero silent fallback to keyless generators (Pollinations is permanently disabled for production scenes).
- **No Cookie-Cutter Templates**: Every project's `scene-graph.json` is tailored to its genre, product physics, and creative brief.

### 3.2 `.agents/skills/effectcraft-discovery/SKILL.md`
Guides agents in discovering and invoking EffectCraft 0.24.1's 763 engine commands, 331 built-in GPU/CPU effects across 20 categories, 8 Essential Graphics `.ectemplate` templates, and AI segmentation/tracking models:
- Discovery CLI commands:
  - `effectcraft-cli commands --filter <query>`
  - `effectcraft-cli exec templates.list --json`
  - `ms motion catalog [--category <cat>] [--templates]` (backed by `src/media_studio/effectcraft_catalog.py`)
- Recipes for Advanced 3D compositions (`camera.newCamera`, `light.newLight`, `layer.new3dPrimitive`, `layer.new3dModel`, `effect.apply`), Roto Brush 3.0 (`sam2.1-hiera-base-plus`), and MediaPipe Face Landmarker (`mediapipe-face`).

### 3.3 `.agents/skills/3d-product-cinematography/SKILL.md`
Defines the workflow for 3D product commercials and spatial environments:
1. Search and stage 3D models, PBR materials, and 4K studio HDRIs via `assets-3d` MCP (`search_assets`, `get_asset`, `download_asset`) or generate procedural multi-part 3D geometry in Blender (`glass flacon, metallic collar, pedestal, water droplets`).
2. Bind PBR shader slots (`Base Color`, `Metallic`, `Roughness`, `Transmission/IOR` for glass/liquid, `Normal`, `AO`) and HDRI world lighting via `ms 3d`.
3. Author 3D camera trajectories in Blender Python:
   - `slow-dolly-in`: 50mm–85mm lens pushing toward the hero subject with shallow Depth of Field.
   - `macro-surface-pan`: 100mm macro lens gliding across surface details, droplets, and emblem relief.
   - `orbital-reveal`: 360-degree or 120-degree arc orbit around the product with rim-light keyframing.

### 3.4 `.agents/skills/kinetic-3d-typography/SKILL.md`
Replaces monotonous bottom-screen subtitles with high-impact 3D and kinetic typography:
- **Rule**: Commercial and luxury promotional videos default to `subtitles: false`. Narrative hooks, claims, and brand signatures are expressed as scene-integrated 3D/kinetic typography layers.
- Provides composition blueprints for:
  - `text-orbit-3d` (3D title cards with an orbiting camera over a reflective floor).
  - Per-character 3D text animators (staggered Z-depth fly-in, Y-axis rotation, tracking expansion, exponential easing).
  - Specular light sweeps (`ec.stylize.cclightsweep`) and photorealistic bloom (`ec.stylize.glow`) across metallic/serif headlines.
  - Depth-composited typography (placing 3D text behind the 3D product bottle via Roto/alpha matte and Z-depth layering).

### 3.5 `.agents/skills/visual-storyboarding/SKILL.md`
Codifies the visual-first pre-production and iteration loop:
- Every scene in `scene-graph.json` must render a full-resolution visual frame (`1920x1080` or `1080x1920`) before any video encoding begins.
- Mandates the Storyboard Self-Iteration Loop (`ms storyboard build` -> `ms critique --stage storyboard` -> visual `read` inspection -> targeted scene revision) prior to presenting `storyboard.png` at the Human Approval Gate.

### 3.6 `.agents/skills/multimodal-critic-gate/SKILL.md` & `.agents/skills/feature-dev-workflow/SKILL.md`
Documents the Dual-Gate verification protocol (`submodules/media-mcp` deterministic metrics + Color Palette Harmony + 6-axis Multimodal Vision Rubric) and repository development standards (TDD, CodeGraph sync, Ruff linting, sub-600-line module boundaries). Includes the **60-30-10 Color Theory Standard**:
- **60% Dominant Tone** (`brand.palette.primary`): Deep environment/studio foundation (e.g., obsidian `#080B11` for nocturnal luxury, warm ivory for botanical skincare, deep slate for developer tooling).
- **30% Secondary Structural Tone** (`brand.palette.secondary`): Pedestal, glass shadow, and ambient rim lighting.
- **10% Specular Accent & Ink** (`brand.palette.accent` + `brand.palette.ink`): High-contrast focal highlights (metallic gold, electric cyan) with mandatory WCAG contrast ratio `>= 4.5:1` against the dominant tone.

---

## 4. Composable Scene-Graph & Multi-Engine Architecture (`ms scene`)

### 4.1 Retirement of Legacy `ms promo`
- `src/media_studio/promo.py` and `src/media_studio/cli/promo.py` (with their hardcoded 5-beat `BEAT_KINDS = ("hook", "problem", "demo", "proof", "cta")` and static HTML card assumptions) are deprecated and replaced by:
  - `src/media_studio/scene_graph.py` & `src/media_studio/cli/scene.py` (`ms scene`)
  - `src/media_studio/storyboard.py` & `src/media_studio/cli/storyboard.py` (`ms storyboard`)
  - `src/media_studio/critique.py` & `src/media_studio/cli/critique.py` (`ms critique`)

### 4.2 Declarative `scene-graph.json` Schema
Instead of forcing 5 fixed beats, a project defines an arbitrary list of scenes (`scenes[]`) in `<project>/scene-graph.json`. Each scene is a self-contained production node:

```json
{
  "project_slug": "aura-noir-3d",
  "aspects": ["9:16", "16:9"],
  "fps": 60,
  "subtitles": false,
  "audio": {
    "voiceover_script": "In the stillness of obsidian night, true presence awakens...",
    "music_asset": "bgm",
    "ducking_db": -10.0
  },
  "scenes": [
    {
      "id": "scene-01-silhouette",
      "duration": 4.0,
      "engine": "composite",
      "camera": {
        "move": "slow-dolly-in",
        "focal_length_mm": 65,
        "dof_fstop": 2.0,
        "start_pos": [0.0, -2.8, 1.1],
        "end_pos": [0.0, -2.1, 1.05]
      },
      "blender_3d": {
        "model_asset": "perfume-flacon-3d",
        "environment_hdri": "studio-dark-amber",
        "pedestal": "obsidian-wet-stone",
        "lights": [
          {"kind": "spot", "color": "#F59E0B", "power": 450, "pos": [1.5, -1.2, 2.4]},
          {"kind": "area", "color": "#FEF3C7", "power": 180, "pos": [-1.8, -0.5, 1.8]}
        ]
      },
      "typography_3d": {
        "template": "per-char-3d-reveal",
        "text": "SILENCE SPEAKS",
        "font": "Noto Serif",
        "color": "#F8FAFC",
        "accent": "#F59E0B",
        "effects": ["ec.stylize.cclightsweep", "ec.stylize.glow"],
        "z_layer": "foreground"
      },
      "transition_out": {"kind": "ec.transition.cclightwipe", "duration": 0.4},
      "sfx": [{"id": "impact-sub", "offset": 0.0, "gain_db": -3.0}]
    }
  ]
}
```

### 4.3 Four Composable Scene Engines
1. **`blender-3d`**:
   - Executes a generated Blender 5.3 Python scene script (`blender.run_script(..., use_gpu=True)`) that loads the 3D product asset (`.glb`/`.obj`) and 3D environment pedestal, binds PBR glass/liquid/metal materials and HDRI lighting, animates the 3D camera (`slow-dolly-in`, `macro-surface-pan`, `orbital-reveal`), and renders either a single hero still (`ms storyboard`) or a 60fps image sequence / ProRes clip (`ms scene render`).
2. **`effectcraft`**:
   - Builds an `.ecproj` composition using EffectCraft 0.24.1's native 3D and motion capabilities: `camera.newCamera`, `light.newLight`, `layer.new3dPrimitive`, `file.newFromTemplate` (`text-orbit-3d`, `kinetic-type`, `logo-reveal`), per-character 3D text animators, and GPU effects (`ec.stylize.cclightsweep`, `ec.stylize.glow`, `ec.blur.cameralens`, `ec.sim.particleworld`).
3. **`flow-gen`**:
   - Calls `GoogleFlowProvider` (`Nano Banana Pro` @ 0 credits for stills; `Veo 3.1 - Lite` @ 10 credits or `Omni 1.2 Flash` @ 24 credits for video), passing the rendered 3D product keyframe in `references[]` so atmospheric shots preserve exact product geometry.
4. **`composite`**:
   - Renders the base 3D scene (`blender-3d`) or generative environment (`flow-gen`) and composites an alpha-channel ProRes 4444 3D typography / particle layer from `effectcraft` over (or behind, using depth/roto mattes from `sam2.1-hiera-base-plus`) the subject.

### 4.4 Content-Addressed Scene Cache & Canonical Timeline Export
- Every scene node's hash is computed from its `SceneSpec` JSON, referenced 3D/audio asset SHA-256 digests, aspect ratio, resolution, and FPS, stored at `<project>/out/cache/scenes/<sha256>.mp4`.
- Modifying a single scene (`ms scene render <project> --scene scene-03`) re-renders only that scene and re-assembles the master timeline in seconds.
- Final assembly compiles into a native **FilmCraft `.fcproj`** project, exports **OpenTimelineIO (`out/timeline/edit.otio.json`)** and **MLT (`out/timeline/edit.mlt`)**, and encodes 1080p60 deliverables (`h264_nvenc` dGPU-first with transparent `libx264` fallback, VBV 12 Mbps, `bt709` `tv` range, `-14.0 LUFS`).

---

## 5. Visual Storyboard Engine & Pre-Production Iteration Loop (`ms storyboard`)

### 5.1 Authentic Visual Keyframes (Zero Text-Box Placeholders)
`ms storyboard build <project> [--scene <id>]` renders a true 1080p keyframe image for each scene at its visual peak (`t = 0.5 * duration`) into `<project>/out/storyboard/<scene_id>.png`:
- For `blender-3d`: Renders the exact 3D camera frame with PBR materials, studio lights, and HDRI reflections.
- For `effectcraft`: Invokes `effectcraft-cli render-frame --time <peak_sec>` to capture the active 3D typography, light sweep, and particle state.
- For `flow-gen`: Generates a 0-credit keyframe still via Google Flow `Nano Banana Pro` conditioned on the 3D product reference.
- For `composite`: Composites the rendered `effectcraft` RGBA frame onto the `blender-3d` / `flow-gen` base frame.

### 5.2 Visual-First Bento Contact Sheet (`out/storyboard/storyboard.png`)
- Assembles all scene keyframes into a high-resolution visual board (`1920px` wide) where **at least 85% of each panel's area is the rendered visual frame**, accompanied by a clean metadata footer (`Scene ID | Duration | Camera & Lens | Engine | 3D Typography`).

### 5.3 Mandatory Storyboard Self-Iteration Gate
Before presenting `storyboard.png` to the user:
1. The agent runs `ms critique <project> --stage storyboard`.
2. Each `out/storyboard/<scene_id>.png` frame is checked by `submodules/media-mcp` (`media_image_quality` for sharpness, exposure, contrast; `media_ocr` for 3D text legibility and zero clipping) AND inspected via multimodal vision (`read`).
3. If any scene frame exhibits poor lighting, missing 3D product silhouette, clipped 3D text, or weak composition, the agent adjusts that scene's camera/lighting/typography in `scene-graph.json` and re-runs `ms storyboard build <project> --scene <scene_id>`.
4. Only when `ms critique <project> --stage storyboard` reports `ok: true` does the agent present `storyboard.png` to the user for approval.

---

## 6. Git Submodule `media-mcp` & Mandatory Dual-Gate Critic (`ms critique`)

### 6.1 Submodule Integration (`submodules/media-mcp`)
- Repository `https://github.com/SuperBypassUdinnn/media-mcp.git` is registered in `.gitmodules` and checked out at `submodules/media-mcp`.
- `src/media_studio/critique.py` imports `media-mcp` analytics directly (`media.media`, `media.artifacts`, `rhythm`), removing the legacy "best-effort subprocess hook" that silently passed when errors or unsynced cuts occurred.

### 6.2 Gate 1: Hard Quantitative Gates (`submodules/media-mcp` + Palette Analyzer)
`ms critique <project> [--stage storyboard|video]` enforces five deterministic blocking gates (`severity: "error"` blocks `ok: true` and returns exit code `1`):

| Gate | Underlying Sensor | Blocking Threshold (`severity: "error"`) | Actionable Corrective Guidance |
|---|---|---|---|
| **1. Motion Energy & Anti-Hold** | `media_motion` (`analysis_fps=10`) | Any scene with static hold (`max_diff < 2.0`) exceeding `35%` of scene duration, OR total video hold ratio exceeding `20%`. | Add continuous 3D camera motion (`slow-dolly-in`, `orbital-reveal`), particle emitter, or active 3D text animation to the static scene. |
| **2. Contextual & Anti-Boilerplate OCR** | `media_ocr` (per-scene midpoint) | Presence of any forbidden template leak (`"New feature"`, `"Try it free"`, `"Up from last period"`, `"Key Feature"`, `"Automated demo"`), OR expected `typography_3d.text` missing/truncated (`< 60%` token match). | Remove hardcoded template strings; adjust 3D text camera distance, font size, or line wrapping in `typography_3d`. |
| **3. Frame Image Quality & Exposure** | `media_image_quality` & `media_frames` | Any scene keyframe rated `tier == "poor"`, mean luminance `< 18` (crushed black) or `> 240` (blown highlight), or RMS contrast `< 12`. | Adjust 3D key/rim light power, HDRI strength, or EffectCraft exposure/glow parameters. |
| **4. Color Palette Selection, Contrast, & 60-30-10 Adherence** | `critique_palette` (WCAG + CIELAB $\Delta E$ K-means cluster analyzer on `media_frames`) | (a) Declared `brand.palette` fails WCAG luminance contrast (`ink`/`accent` vs `primary` `< 4.5:1`) or uses uncurated template defaults (`#38BDF8` sky-blue on non-tech genres); (b) Rendered scene keyframe dominant color clusters deviate from `brand.palette` (`min CIELAB Delta-E > 38.0`, e.g., unstyled `#FFFFFF` cards on a dark luxury palette); or (c) Accent color exceeds `25%` frame area (violating the 60-30-10 balance). | Recalibrate `brand.palette` to match product archetype; align 3D light colors, PBR materials, and `ec.color.lumetri` / `ec.color.curves` grading with the declared palette. |
| **5. Audio-Visual Beat & Loudness** | `media_beats`, `media_avsync`, FFprobe EBU R128 | Integrated loudness outside `-14.0 LUFS +- 1.0`, true peak `> -1.0 dBTP`, black/frozen segments `> 0`, or `color_space != "bt709"` / `color_range != "tv"`. | Re-normalize audio mix, snap scene durations to beat grid onsets, and enforce BT.709 limited range. |

### 6.3 Gate 2: Mandatory Multimodal Vision Rubric (6 Axes)
Quantitative metrics alone cannot judge whether a 3D perfume bottle looks luxurious, whether the color palette evokes the right mood, or whether 3D typography is artistically integrated. Therefore, `ms critique` requires a signed **Vision Critique Report (`<project>/out/reports/vision-critique.json`)** evaluated by a capable vision model (`google/antigravity-gemini-3.8-flash` or main session native vision) after inspecting the extracted scene keyframes via `read`:

```json
{
  "stage": "storyboard | video",
  "evaluated_by": "google/antigravity-gemini-3.8-flash",
  "keyframes_inspected": ["out/storyboard/scene-01.png", "..."],
  "scores": {
    "three_d_object_and_environment_depth": 5,
    "three_d_and_kinetic_typography_craft": 5,
    "camera_choreography_and_motion_energy": 4,
    "color_palette_selection_and_harmony": 5,
    "lighting_and_atmospheric_continuity": 5,
    "conceptual_fit_to_brief": 5
  },
  "defects": [],
  "passed": true
}
```
- **Blocking Rule**: Every rubric axis is scored `1..5`. If `vision-critique.json` is absent, stale (older than the rendered file), has any axis score `< 4` (including `color_palette_selection_and_harmony`), or lists any unresolved visual defect, `ms critique` returns `ok: false` (exit code `1`) with defect category `"vision-rubric"`.

---

## 7. Implementation Sequence, Context Cleanup, & Verification Plan

### 7.1 Execution Order
1. **Phase 1 — Repository Skill & Rule Contracts (`.agents/`)**:
   - Author `.agents/rules/architecture.md` and the six skills under `.agents/skills/` (`effectcraft-discovery`, `3d-product-cinematography`, `kinetic-3d-typography`, `visual-storyboarding`, `multimodal-critic-gate`, `feature-dev-workflow`).
   - Symlink the new skills into `~/.agents/skills/` and `~/.config/opencode/skills/`.
2. **Phase 2 — Workspace Hygiene (`~/Projects` Cleanup) & Root Codebase Audit**:
   - Delete all legacy directories inside `~/Projects/*` (`amv-demonslayer-3d`, `aura-noir-perfume`, `lumina-motion-v2`, `scr2026-*`) so zero polluted artifacts remain.
   - Audit `src/media_studio/` against the Phase 1 skill contracts; retire `src/media_studio/promo.py` and `src/media_studio/cli/promo.py` (retaining a thin deprecation shim or migrating callers to `ms scene` / `ms storyboard` / `ms critique`).
3. **Phase 3 — Git Submodule `media-mcp` & Dual-Gate Critic (`src/media_studio/critique.py`)**:
   - Add `submodules/media-mcp` and wire the 5 quantitative gates (including Color Palette Selection, WCAG Contrast, & CIELAB 60-30-10 Cluster Adherence) + Gate 2 6-Axis Vision Rubric validation into `ms critique`.
4. **Phase 4 — 3D Scene Engine, 3D Typography Presets, & Scene-Graph Orchestrator**:
   - Expand `src/media_studio/blender.py` with procedural/asset 3D product stage builders (glass/liquid/metal PBR shaders, studio pedestal, water droplets, HDRI lighting, and `slow-dolly-in` / `macro-surface-pan` / `orbital-reveal` camera rigs).
   - Expand `src/media_studio/effectcraft.py` with true 3D & kinetic typography builders (`text-orbit-3d`, per-character 3D animators, `camera.newCamera`, `light.newLight`, `ec.stylize.cclightsweep`, `ec.stylize.glow`, `ec.sim.particleworld`).
   - Implement `src/media_studio/scene_graph.py` (`ms scene`) and `src/media_studio/storyboard.py` (`ms storyboard`).
5. **Phase 5 — Benchmark Proof (3D Luxury Perfume Commercial)**:
   - Build a clean 3D luxury perfume commercial project from scratch using the new workflow, stop at the iterated Visual Storyboard Gate to present `storyboard.png` to the user, and upon approval render the full 60fps dual-aspect video and pass Dual-Gate `ms critique`.

### 7.2 Testing & Quality Requirements
- All unit tests under `tests/` run 100% offline (`pytest -m "not slow"`).
- Every modified/created Python file passes `ruff check` and `ruff format --check` with zero warnings and stays under the 600-line module ceiling.
- CodeGraph index (`codegraph sync`) is updated after every implementation phase.



