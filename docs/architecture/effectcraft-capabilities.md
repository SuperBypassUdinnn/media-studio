# EffectCraft Engine Capabilities & Architecture Map

## Overview
**EffectCraft** (`effectcraft-cli 0.24.1`) is a high-performance, GPU-accelerated motion graphics compositor and 2D/3D visual effects engine (equivalent in capability to Adobe After Effects, built in Rust with wgpu/Vulkan backend).

This document serves as the comprehensive intelligence map for OpenCode agents and developers, detailing:
1. Engine Architecture & CLI Execution Model
2. Built-in Effects Catalog (20 Categories, 331 Effects)
3. AI Models & Computer Vision Subsystems (Roto Brush 3.0 SAM 2.1 & MediaPipe Face Tracking)
4. Built-in Essential Graphics Templates (`.ectemplate`)
5. 3D Advanced Compositing Engine (Primitives, Cameras, Lights & Materials)
6. Plug-in & Extension Architecture (WebAssembly & ScriptUI)

---

## 1. Engine Architecture & CLI Execution Model

### CLI Binary & IPC Interface
- Binary path: `~/.local/opt/effectcraft-0.24.1/bin/effectcraft-cli` (symlinked at `~/.local/bin/effectcraft-cli`).
- GUI executable: `~/.local/opt/effectcraft-0.24.1/bin/effectcraft`.
- Project format: `.ecproj` (human-readable JSON or compact single-line JSON) and `.ecprojx` (XML variant).
- Template format: `.ectemplate` (motion graphic template with Essential Graphics parameters).

### Execution Paradigms
1. **Batch Command Runner (`run --commands -`)**:
   Accepts a JSON array of engine command steps over standard input:
   ```json
   [
     {"command": "comp.new", "params": {"name": "Main", "width": 1920, "height": 1080, "duration": 5.0, "fps": 60.0}},
     {"command": "layer.newSolid", "params": {"name": "Background", "color": "#080B11"}},
     {"command": "layer.newText", "params": {"text": "Aura Noir", "font": "Noto Sans", "size": 72, "position": [960, 540]}}
   ]
   ```
2. **Headless Render Queue (`render`)**:
   Renders `.ecproj` headlessly to disk with hardware acceleration:
   ```bash
   effectcraft-cli render project.ecproj -o output.mov --fps 60 --transparent
   ```
   Supported output formats: Apple ProRes 4444 (with alpha), ProRes 422 HQ, H.264, HEVC, AV1, PNG Sequence, OpenEXR.

3. **Live Bridge Mode (Interactive GUI Integration)**:
   When EffectCraft is started with `--control <port>`, MCP tools connect over WebSocket/IPC for live bidirectional control.

---

## 2. Built-in Effects Catalog (20 Categories, 331 Effects)

EffectCraft ships with **331 built-in GPU/CPU effects** categorized across 20 functional domains. Effects are applied via `effect.apply {effect: "<id_or_name>", layers: ["<layer_name>"]}` and configured with `prop.set` or `layer.set`.

### Category Breakdown

| Category | Count | Key Notable Effects & Creative Use Cases |
|---|---|---|
| **`stylize`** | 30 | `ec.stylize.cclightsweep` (luxury metallic/glass gleam sweep), `ec.stylize.glow` (photorealistic bloom), `ec.stylize.ccvignette` (cinematic edge darkening), `ec.stylize.ccglass` (refractive liquid glass), `ec.stylize.roughenedges` (organic torn edges) |
| **`color`** | 30 | `ec.color.curves`, `ec.color.levels`, `ec.color.lumetri` (3-way color wheels & CDL), `ec.color.hue_sat`, `ec.color.tint`, `ec.color.blackwhite`, `ec.color.exposure` |
| **`blur`** | 18 | `ec.blur.cameralens` (physically modeled bokeh with custom iris shapes), `ec.blur.fastbox`, `ec.blur.radial`, `ec.blur.directional`, `ec.blur.unsharp` |
| **`distort`** | 37 | `ec.distort.displacementmap` (texture-driven surface warp), `ec.distort.meshwarp`, `ec.distort.opticscompensation` (lens curvature distortion), `ec.distort.wavewarp`, `ec.distort.ccbender` |
| **`generate`** | 28 | `ec.generate.lensflare` (cinematic anamorphic & spherical flares), `ec.generate.gradientramp`, `ec.generate.4colorgradient`, `ec.generate.beam`, `ec.generate.grid`, `ec.generate.fill` |
| **`sim` (Simulation)** | 32 | `ec.sim.particleworld` (full 3D particle emitter with physics), `ec.sim.ccstarburst` (depth star field), `ec.sim.ccrain`, `ec.sim.ccsnow`, `ec.sim.ballaction` |
| **`transition`** | 18 | `ec.transition.cclightwipe`, `ec.transition.linearwipe`, `ec.transition.radialwipe`, `ec.transition.venetianblinds`, `ec.transition.blockdissolve` |
| **`3d` (Channel)** | 10 | `ec.3d.cryptomatte` (V-Ray/Blender ID mattes), `ec.3d.depthmatte`, `ec.3d.fog3d`, `ec.3d.extractor`, `ec.3d.depthoffield` |
| **`key` (Keying)** | 23 | `ec.key.linearcolorkey`, `ec.key.colordifference`, `ec.key.spillsuppressor`, `ec.key.mattechoker`, `ec.key.differencematte` |
| **`noise`** | 12 | `ec.noise.fractalnoise` (procedural organic clouds/smoke/energy), `ec.noise.turbulentnoise`, `ec.noise.grain` |
| **`vr` (Immersive)** | 23 | `ec.vr.blur`, `ec.vr.glow`, `ec.vr.chromaticaberrations`, `ec.vr.sharpen` |
| **`perspective`** | 8 | `ec.perspective.3dglasses`, `ec.perspective.bevelalpha`, `ec.perspective.radialshadow` |
| **`channel`** | 12 | `ec.channel.calculations`, `ec.channel.setmatte`, `ec.channel.shiftchannels`, `ec.channel.invert` |
| **`time`** | 11 | `ec.time.timewarp` (optical flow slow motion), `ec.time.echo`, `ec.time.posterizetime` |
| **`audio`** | 14 | `ec.audio.parametriceq`, `ec.audio.reverb`, `ec.audio.compressor`, `ec.audio.delay` |
| **`control`** | 6 | `ec.control.slider`, `ec.control.color`, `ec.control.point`, `ec.control.angle` |
| **`matte`** | 4 | `ec.matte.object` (Roto Brush matte), `ec.matte.simplechoker` |
| **`text`** | 4 | Per-character 3D text animators (tracking, position, scale, opacity) |
| **`utility`** | 6 | `ec.utility.colorprofileconverter`, `ec.utility.hdrcompressor` |
| **`obsolete`** | 5 | Legacy compatibility filters |

---

## 3. AI Models & Computer Vision Subsystems

EffectCraft integrates state-of-the-art vision and machine learning models for rotoscoping and facial motion capture, installed in `~/.config/effectcraft/models/`:

### A. Roto Brush 3.0 (Segment Anything 2.1)
Roto Brush extracts foreground characters and objects without green screen, propagating selections across time with temporal streaming memory:
1. **`sam2.1-hiera-base-plus`** (`323.5 MB`, SHA256: `2012733a0de5d03efd1bba550a2847c4551be9ef2e0d497c83074df66189f780`):
   - **Recommended Production Standard**: Highest tracking precision on difficult footage, thin edges, motion blur, and occlusion.
   - Selected as active model in EffectCraft settings.
2. **`sam2.1-hiera-tiny`** (`155.9 MB`, SHA256: `48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7`):
   - Fast GPU/CPU streaming-memory tracker for rapid turnarounds.
3. **`mobilesam`** (`40.7 MB`, SHA256: `6dbb90523a35330fedd7f1d3dfc66f995213d81b29a5ca8108dbcdd4e37d6c2f`):
   - Distilled 5M TinyViT model for ultra-lightweight CPU rotoscoping.

**Engine Commands**:
- `roto.models`: List all model statuses.
- `roto.model.select {"id": "sam2.1-hiera-base-plus"}`: Set active rotoscoping model.
- `roto.stroke {"layer": ..., "kind": "fg", "points": [[x,y], ...]}`: Apply foreground/background strokes.
- `roto.propagate {"direction": "both", "wait": true}`: Propagate mask across video clips.
- `roto.freeze`: Cache and lock rendered segmentation mattes.

### B. Face Tracking (MediaPipe 478-Point Face Mesh)
- **`face_landmarker.task`** (`3.7 MB`, SHA256: `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`):
  - 478-point 3D facial landmark detection and tracking.
  - Handles profile angles, partial occlusions, and rapid head turns.
  - Command: `face.model.select {"id": "mediapipe-face"}`.

---

## 4. Built-in Essential Graphics Templates (`.ectemplate`)

EffectCraft provides modular, parameter-driven motion graphic templates accessible via `file.newFromTemplate {"id": "<template_id>"}`:

1. **`logo-reveal`** (Duration: 5.0s, 1080p):
   - Spinning, glowing placeholder mark resolving into brand name with particle burst.
   - Essential Graphics Controls: `Brand Name`, `Logo Color`.
2. **`title-card`** (Duration: 5.0s, 1080p):
   - Centered title scaling up over radial gradient with elegant subtitle fade.
   - Essential Graphics Controls: `Title`, `Subtitle`.
3. **`kinetic-type`** (Duration: 4.0s, 1080p):
   - Three rhythmic popping words with spring inertia.
   - Essential Graphics Controls: `Word 1`, `Word 2`, `Word 3`.
4. **`lower-third`** (Duration: 6.0s, 1080p):
   - Sleek sliding bar and typography lockup.
   - Essential Graphics Controls: `Name`, `Title`, `Bar Color`.
5. **`text-orbit-3d`** (Duration: 8.0s, 1080p):
   - True 3D title cards with camera orbiting over a reflective ground plane.
   - Essential Graphics Controls: `Title`.
6. **`vertical-story`** (Duration: 8.0s, 1080x1920):
   - 9:16 vertical story format with headline and call to action.
7. **`slideshow`** (Duration: 9.0s, 1080p):
   - 3-slide Ken Burns slow push-in with cross-dissolve.
8. **`social-square`** (Duration: 6.0s, 1080x1080):
   - Square Instagram post with dynamic accent ring.

---

## 5. 3D Advanced Compositing Engine

EffectCraft supports true 3D spatial compositing, enabling cinematic camera motion, realistic lights, and geometry:

### 3D Primitives (`layer.new3dPrimitive`)
- Supported shapes: `cube`, `sphere`, `plane`, `torus`, `cone`, `cylinder`.
- Physically Based Rendering (PBR) parameters: `baseColor`, `metallic`, `roughness`, `emissive`, `castsShadows`, `acceptsShadows`, `acceptsLights`.
- Automatic engine switch: Adding a primitive automatically switches the composition to **Advanced 3D**.

### 3D Cameras (`camera.newCamera`)
- Focal lengths: `15mm`, `20mm`, `24mm`, `28mm`, `35mm`, `50mm`, `80mm`, `135mm`, `200mm`.
- Camera nodes: `oneNode` (direct fly-through) and `twoNode` (pointing at Point of Interest).
- Depth of Field: `focusDistance`, `aperture`, `blurLevel`.

### 3D Lights (`light.newLight`)
- Light types: `point`, `spot`, `ambient`, `parallel`.
- Volumetrics and shadows: `intensity`, `coneAngle`, `coneFeather`, `castsShadows`, `shadowDarkness`, `shadowDiffusion`.

---

## 6. Plug-in & Extension Architecture

- **WebAssembly Plug-ins**: Effect plugins compiled to WebAssembly (ABI v1 and v2) placed in `~/.config/effectcraft/Plug-ins/`.
- **ScriptUI Panels**: Extendable JSX panels placed in `~/.config/effectcraft/Scripts/ScriptUI Panels/`.
- **Startup/Shutdown Hooks**: Automatically executes scripts placed in `Scripts/Startup/`.
