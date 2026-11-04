# AMV Elite Pipeline & 1080p60 Standard Design Specification

## 1. Executive Summary & Goals

This specification defines the architectural and algorithmic evolution of `media-studio` and `media-create` to produce high-impact, professional-grade Anime Music Videos (AMVs) matching modern After Effects production standards.

### 1.1 Context & Findings
Through rigorous comparative analysis against benchmark reference AMVs (e.g., *Heathens* AMV Mix, 4K UHD @ 60.000 fps, 288 scene cuts, cut density up to 0.05s-0.25s during beat drops), our baseline production pipeline was evaluated. While `media-studio` delivers clean, synchronized edits and loudness-compliant audio mixes, five critical gaps separate it from tier-1 AMVs:
1. **Resolution & Motion Fluidity Baseline**: Lack of an enforced 1080p (1920x1080) minimum resolution floor and default 60fps high-frame-rate rendering.
2. **Impact Frames**: Absence of 1-3 frame visceral accent frames (negative color inversion, white exposure flashes, manga sketch line art) on clashing transients.
3. **2.5D Parallax & Spatial Depth**: Need for a formalized, repeatable multi-plane camera pipeline (differential background, subject cutout, and foreground kinetic typography movement with optical motion blur).
4. **Velocity Ramping & Speed Curves**: Lack of non-linear speed mapping (fast-in -> slow-motion apex with optical flow interpolation -> fast snap-out).
5. **Drop Dynamics & Stylized Typography**: Missing structured micro-silence cut-to-black sequences, light slit wipes, and HUD/cyberpunk typography accents.

### 1.2 Core Objectives
- Enforce **1080p (1920x1080)** as the mandatory baseline floor for all editing deliverables, proxies, and timeline manifests, with native **60fps** capability.
- Introduce modular, deterministic CLI and Python primitives in `media-studio` for impact frames, 2.5D parallax compositing, and velocity curves.
- Provide standardized EffectCraft and FFmpeg drop presets.
- Maintain full compatibility with the 9-stage `media-create` orchestration lifecycle and verify all outputs via `ms see`.
- Operate reliably on consumer hardware (NVIDIA GTX 1650 4GB VRAM, headless Linux).

## 2. Subsystem 1: 1080p60 Resolution Baseline

### 2.1 Scope & Constraints
- Minimum Deliverable Resolution: 1920x1080 (1080p).
- Standard AMV Frame Rates: 60 fps (preferred for action and smooth motion blur), 30 fps (permitted for low-motion or constrained renders). Resolutions below 1920x1080 (such as 720p or 540p) are strictly restricted to temporary proxy previews or thumbnails.

### 2.2 Component Modifications
1. **`src/media_studio/project.py`**:
   - `BRIEF_TEMPLATE`: Update default constraint documentation to specify `16:9 (1920x1080)` and `60 fps` as the baseline.
   - Project validation: Deliverables marked `kind: video` in `project.yaml` must define resolution `>= 1920x1080` unless explicitly flagged with `preview: true`.
2. **`src/media_studio/ingest.py`**:
   - Upgrade the `proxy` profile box: `max_width=1920`, `max_height=1080` (DNxHR LB). Sources that are already 1080p are not downscaled.
   - Ensure `hq` (DNxHR HQ) and `hqx` (DNxHR HQX 10-bit) preserve full 1080p and 4K dimensions up to 60fps without loss of frame rate.
3. **`~/.agents/skills/media-create/SKILL.md`**:
   - Add the *1080p Floor Gate*: Mandate that all video deliverables planned by the orchestrator use `resolution: 1920x1080` and `fps: 60` (or `fps: 30` when source clips are natively 24/30fps without interpolation).

---

## 3. Subsystem 2: AMV Impact Frame Engine (`src/media_studio/impact.py`)

### 3.1 Functionality
An impact frame is an ultra-short transient visual accent (1 to 3 frames, corresponding to ~16ms to 50ms at 60fps) inserted precisely on percussive drum hits (kick/snare), blade slashes, or explosive energy bursts.

### 3.2 Visual Modes
The engine supports five distinct visual impact styles:
1. `flash`: Instant full white over-exposure with 1-frame exponential decay (`lutyuv='y=255:u=128:v=128'`).
2. `invert`: Full negative color inversion (`negate`), creating a stark chromatic contrast shock.
3. `sketch`: High-contrast monochrome manga line art (`edgedetect=low=0.1:high=0.4,negate`).
4. `red-sketch`: Stylized dark-background manga line art with vivid crimson red contours (`[0:v]edgedetect=low=0.1:high=0.3,negate,colorchannelmixer=rr=1.5:rg=0:rb=0:br=0:bg=0:bb=0`).
5. `chromatic`: Horizontal and vertical RGB channel displacement (10-20px lateral shift) simulating camera lens impact shock.

### 3.3 CLI Interface
```bash
ms video impact-frame <source> \
  --at <timestamp_or_frame> \
  --frames <count> \
  --mode {flash,invert,sketch,red-sketch,chromatic} \
  --out <output_path>
```
- `<source>`: Path to input video or still image.
- `--at`: Timestamp in seconds (e.g., `7.45`) or frame identifier (e.g., `f224`).
- `--frames`: Number of frames to generate (default: `2`, range: `1` to `5`).
- `--mode`: Visual style selection (default: `invert`).
- `--out`: Destination file path (defaults to `<stem>.impact.<mode>.mov` beside the source).

### 3.4 Data Flow & Implementation Details
1. **Timestamp Resolution**:
   - `frame_index = round(timestamp * fps)` if given in seconds; direct integer parsing if given as `f<N>`.
2. **Extraction & Filtering**:
   - Invokes FFmpeg with frame selection: `select='between(n,FRAME_START,FRAME_END)'`.
   - Chains the visual filter according to `--mode`.
   - Forces output canvas to 1920x1080 at target fps (default 60 fps).
3. **Encoding**:
   - Emits ProRes 4444 (`yuva444p12le`) with alpha or high-bitrate MP4 intermediate.

### 3.5 Error Handling
- Raise `ImpactError` if `--at` falls outside the duration of `<source>`.
- Raise `ImpactError` if `--frames` is less than 1 or exceeds 10.
- Ensure temporary files are securely created in `/tmp/opencode/` and cleaned up.

## 4. Subsystem 3: 2.5D Parallax Compositor Engine (`src/media_studio/parallax.py`)

### 4.1 Concept & Spatial Mechanics
The 2.5D Parallax Engine simulates dynamic multi-plane 3D camera motion without requiring polygon meshes. By decomposing a scene into separate depth planes and applying differential affine transformations, it produces deep spatial immersion and camera inertia.

### 4.2 Multi-Plane Architecture
A parallax composition consists of three spatial tiers:
1. **Deep Background Plane (Depth: 0.2 - 0.5)**:
   - Contains atmospheric plates, glowing particles, speed lines, or distant landscapes.
   - Moves slowly (e.g., zoom 1.05x -> 1.10x, pan -15px).
2. **Subject Midground Plane (Depth: 1.0)**:
   - High-resolution character cutout with transparent alpha (RGBA), produced via `ms image remove-bg --model anime` or `ms video remove-bg`.
   - Moves at baseline camera speed (e.g., zoom 1.00x -> 1.18x, pan +20px).
3. **Foreground / Motion Plane (Depth: 1.5 - 2.0)**:
   - Floating embers, typography titles (`.mov` ProRes 4444 RGBA from EffectCraft), or framing elements.
   - Accelerates faster towards the camera lens, producing strong depth cues.
4. **Optical Finishing**:
   - Dynamic radial vignette focusing luma on the character silhouette.
   - Subtle directional motion blur along the camera velocity vector.

### 4.3 CLI & Manifest Interface

#### Direct CLI Invocation:
```bash
ms video parallax \
  --bg <path_to_background_image_or_video> \
  --subject <path_to_rgba_png_or_mov> \
  --fg <optional_path_to_rgba_title_or_particles> \
  --duration <seconds> \
  --fps 60 \
  --resolution 1920x1080 \
  --motion {push_in,pull_out,whip_left,whip_right,dramatic_dolly} \
  --vignette 0.3 \
  --out <output_file.mov>
```

#### Manifest Integration (`project.yaml`):
A shot in `project.yaml` can directly declare a parallax composite:
```yaml
video:
  shots:
    - shot_type: parallax
      duration: 3.5
      motion: push_in
      background: bg-sparks
      subject: tengen-cutout
      foreground: title-kinetic
      vignette: 0.3
```

### 4.4 Error Handling
- Validate that the `subject` asset contains a true alpha channel (RGBA / `yuva444p*`). Raise `ParallaxError("subject layer requires an alpha channel; run ms image remove-bg or ms video remove-bg first")`.
- Non-16:9 background images must be scaled and center-cropped to 1920x1080 without aspect distortion.

---

## 5. Subsystem 4: Velocity Ramping & Drop Dynamics (`src/media_studio/ramp.py`)

### 5.1 Velocity Curve Theory
High-energy AMVs rely on non-linear speed pacing rather than constant-speed playback. The canonical curve is:
- **Lead-In (Phase 1)**: Rapid acceleration (2.0x - 3.0x speed) building tension.
- **Apex Hold (Phase 2)**: Sudden slow-motion hold (0.2x - 0.25x speed) during an airborne attack or weapon unsheathing.
- **Snap Climax (Phase 3)**: Instant snap-forward (3.0x - 4.0x speed) landing synchronously on the beat impact.

### 5.2 Optical Flow Frame Interpolation
When slowing 24fps or 30fps anime footage down to 0.2x speed, native frame duplication causes noticeable stutter.
The engine implements FFmpeg `minterpolate` with bidirectional motion compensation:
`minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1`
This synthesizes intermediate motion vectors at full 60fps, yielding buttery-smooth slow-motion curves.

### 5.3 Drop Structure Automation
The engine codifies the 5-stage drop anatomy:
1. `build`: Fast action sequence.
2. `pause`: Complete micro-silence cut to black (100ms - 150ms) immediately preceding the drop.
3. `shutter`: Vertical light slit wipe (50ms).
4. `shockwave`: Radial ripple distortion expanding outward.
5. `drop_burst`: Instantaneous clashing strike with impact frame overlay.

### 5.4 CLI Interface
```bash
ms video speed-ramp <source> \
  --curve {fast_slow_fast,slow_snap,flash_freeze} \
  --slow-window <start_seconds> <end_seconds> \
  --slow-factor 0.25 \
  --fast-factor 2.5 \
  --optical-flow \
  --fps 60 \
  --out <output_file>
```

### 5.5 Error Handling
- If optical flow vector estimation triggers an OOM on the 4GB GPU, catch the failure and gracefully fallback to high-quality blend mode (`mi_mode=blend`) with an informational warning logged.
- Validate that the slow window is strictly within the source clip duration.

## 6. Subsystem 5: EffectCraft AMV Motion & Typography Presets

### 6.1 Presets Specification
New motion graphic templates targeting the headless EffectCraft engine (`effectcraft-cli 0.19.0`):
1. **`amv-title-cyber`**:
   - Staggered serif / sans-serif anime title typography.
   - Animated letter-spacing expansion (`tracking: 0 -> 25`).
   - Neon luma-glow with subtle chromatic split on borders.
2. **`amv-hud-accent`**:
   - Thin Swiss design grid with corner crosshairs (`[ + ]`).
   - Measurement tick-marks and technical status markers.
   - Transparent ProRes 4444 overlay for combat HUD aesthetics.
3. **`amv-drop-shutter`**:
   - 3-frame vertical slit light reveal for drop transition sequences.

### 6.2 Invocation Flow
Executable via `ms motion title` or `ms motion render`:
```bash
ms motion title <project> \
  --preset amv-title-cyber \
  --text "DEMON SLAYER // HINOKAMI" \
  --duration 3.5 \
  --out assets/motion/title-cyber.mov
```

---

## 7. Verification & Testing Strategy

### 7.1 Automated Testing Matrix
1. **Unit Tests**:
   - `tests/test_impact.py`: Test frame index calculations, filter strings for all 5 modes, duration assertions, and out-of-bounds timestamp rejections.
   - `tests/test_parallax.py`: Test affine transformation math, layer stacking, aspect ratio preservation, and missing-alpha error triggers.
   - `tests/test_ramp.py`: Test piecewise speed map calculation, exact output duration matching, and optical flow fallback.
   - `tests/test_project.py`: Assert 1080p minimum resolution floor validation.
2. **Integration Verification via `ms see`**:
   - Every generated impact frame, parallax shot, and speed-ramped clip must pass `ms see <path>`.
   - The orchestrating agent must inspect the contact sheet PNG via the `read` tool, verifying that 12/12 tiles are active and free of black frames, clipping, or corrupted transparency.
3. **Loudness Compliance**:
   - All final composite renders must pass `ms video qc` with integrated loudness `-14.0 LUFS ± 1.0` and true peak ceiling `<= -1.0 dBFS`.

---

## 8. Phased Implementation Roadmap

- **Phase 1: 1080p60 Baseline & Impact Frame Engine**
  - Update `src/media_studio/project.py`, `src/media_studio/ingest.py`, and `~/.agents/skills/media-create/SKILL.md`.
  - Implement `src/media_studio/impact.py`, CLI subcommand `ms video impact-frame`, and `tests/test_impact.py`.
- **Phase 2: 2.5D Parallax Compositor Engine**
  - Implement `src/media_studio/parallax.py`, CLI subcommand `ms video parallax`, `project.yaml` shot integration, and `tests/test_parallax.py`.
- **Phase 3: Velocity Ramping & Drop Dynamics**
  - Implement `src/media_studio/ramp.py`, CLI subcommand `ms video speed-ramp`, optical flow integration, and `tests/test_ramp.py`.
- **Phase 4: EffectCraft AMV Motion & Typography Presets**
  - Create EffectCraft AMV templates and test end-to-end integration with the parallax pipeline.

