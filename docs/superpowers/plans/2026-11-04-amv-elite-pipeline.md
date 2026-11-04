# AMV Elite Pipeline & 1080p60 Standard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the AMV Elite Pipeline and 1080p60 Standard in `media-studio` and `media-create`, delivering an enforced 1080p60 editing floor, AMV impact frame generation, 2.5D multi-plane parallax compositing, velocity ramping with optical flow, and EffectCraft AMV motion presets.

**Architecture:** The pipeline extends `media-studio` with modular CLI and Python primitives: `src/media_studio/impact.py` for 1-3 frame impact accents on drum/sword hits, `src/media_studio/parallax.py` for multi-plane 2.5D spatial camera movement, and `src/media_studio/ramp.py` for non-linear speed V-curves with FFmpeg `minterpolate` optical flow. Project validation in `manifest.py` and `project.py` enforces a 1080p minimum resolution floor for video deliverables, while `effectcraft.py` is extended with AMV typography and HUD presets (`amv-title-cyber`, `amv-hud-accent`, `amv-drop-shutter`).

**Tech Stack:** Python 3.12, FFmpeg 7.0.2 / FFprobe, EffectCraft CLI 0.19.0, Pillow, NumPy, Pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-11-04-amv-elite-pipeline-design.md`

## Global Constraints

- Minimum Deliverable Resolution: 1920x1080 (1080p) for all non-preview video deliverables.
- Baseline AMV Frame Rate: 60 fps for action and motion interpolation; 30 fps permitted for native 24/30fps footage without interpolation.
- Loudness Target: -14.0 LUFS (±1.0 LUFS tolerance), True Peak ceiling <= -1.0 dBFS on final audio/video exports.
- Output Color & Pixel Formats: ProRes 4444 (`yuva444p12le` / `yuva444p10le`) with alpha for transparent overlays; H.264 / ProRes for final video composites.
- Low-VRAM Hardware Compatibility: All primitives must execute reliably on consumer GPUs (NVIDIA GTX 1650 4GB VRAM) with automatic fallback (e.g. blend mode fallback on optical flow OOM).
- Clean Text Policy: No decorative icons or emojis in any code, docstring, commit message, or written artifact.
- Conventional Commits: Short, plain-text commit messages (`<type>(<scope>): <subject>`).

## Review Focus

1. **Impact Frame Timestamp Bounds**: Input timestamp `--at` outside source clip duration (< 0 or > duration) or frame count `< 1` / `> 10` raises `ImpactError` with bounds diagnostics.
2. **Parallax Subject Alpha Channel**: Subject asset lacking an alpha channel (`yuva444p*` or RGBA) raises `ParallaxError` directing the user to `ms image remove-bg` or `ms video remove-bg`.
3. **Speed-Ramp Window Validation**: Slow window parameters `[start, end]` invalid (start >= end, start < 0, end > clip duration, or factors <= 0) raises `RampError` with exact bounds.
4. **Optical Flow OOM Recovery**: If FFmpeg `minterpolate` optical flow estimation exhausts memory or fails, the engine catches the failure and falls back to blend mode (`mi_mode=blend`) with a diagnostic message.
5. **Non-16:9 Parallax Background Aspect**: Background image/video with non-16:9 aspect ratio is scaled and center-cropped to 1920x1080 without geometric distortion or black pillars.

---

### Task 1: 1080p60 Resolution Baseline & Validation Floor

**Files:**
- Modify: `src/media_studio/manifest.py:536-540`
- Modify: `src/media_studio/project.py:33-55`
- Modify: `~/.agents/skills/media-create/SKILL.md:25-50`
- Test: `tests/test_manifest.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Consumes: `Project`, `Deliverable`, `ValidationReport` from `src/media_studio/manifest.py`.
- Produces: Updated `validate_project(project: Project) -> ValidationReport` enforcing `resolution >= 1920x1080` for video deliverables unless `preview: true`.

- [ ] **Step 1: Write the failing test for 1080p validation**

Add test in `tests/test_manifest.py`:
```python
def test_validate_deliverable_resolution_below_1080p_rejected(tmp_path: Path) -> None:
    manifest_content = """\
slug: test-res
title: Test Resolution
deliverables:
  - id: sub-1080
    kind: video
    resolution: 1280x720
"""
    manifest_path = tmp_path / "project.yaml"
    manifest_path.write_text(manifest_content)
    project = load_project(tmp_path)
    report = validate_project(project)
    assert any("sub-1080: resolution 1280x720 is below the 1920x1080 minimum floor" in err for err in report.errors)


def test_validate_deliverable_preview_below_1080p_permitted(tmp_path: Path) -> None:
    manifest_content = """\
slug: test-preview
title: Test Preview Resolution
deliverables:
  - id: draft-preview
    kind: video
    resolution: 960x540
    preview: true
"""
    manifest_path = tmp_path / "project.yaml"
    manifest_path.write_text(manifest_content)
    project = load_project(tmp_path)
    report = validate_project(project)
    assert not any("below the 1920x1080 minimum floor" in err for err in report.errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manifest.py -k "test_validate_deliverable" -v`
Expected: FAIL with missing error message.

- [ ] **Step 3: Implement 1080p validation and update templates**

1. In `src/media_studio/manifest.py`, in `validate_project()`, when `deliverable.kind == "video"` and `deliverable.resolution`:
   Parse width and height. If `width < 1920 or height < 1080`:
   If not getattr(deliverable, "preview", False) and not deliverable.raw.get("preview", False):
       `errors.append(f"deliverable {deliverable.id}: resolution {deliverable.resolution} is below the 1920x1080 minimum floor (mark preview: true to permit)")`
2. In `src/media_studio/project.py`, update `BRIEF_TEMPLATE` to set default aspect ratio: `16:9 (1920x1080)` and Frame Rate: `60 fps (smooth AMV / action standard)`.
3. In `~/.agents/skills/media-create/SKILL.md`, add the *1080p Floor Gate* to Stage 3 and Stage 5 specifying that non-preview video deliverables must be 1920x1080 at 60fps or 30fps.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manifest.py tests/test_project.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/media_studio/manifest.py src/media_studio/project.py tests/test_manifest.py tests/test_project.py
git commit -m "feat(project): enforce 1080p minimum resolution floor for video deliverables"
```

### Task 2: AMV Impact Frame Engine (`src/media_studio/impact.py`)

**Files:**
- Create: `src/media_studio/impact.py`
- Modify: `src/media_studio/cli.py`
- Test: `tests/test_impact.py`

**Interfaces:**
- Consumes: `probe_media` from `src/media_studio/ingest.py`.
- Produces:
  ```python
  class ImpactError(RuntimeError): ...

  @dataclass(frozen=True)
  class ImpactResult:
      source: Path
      destination: Path
      mode: str
      frames: int
      fps: int
      resolution: str
      at_seconds: float
      size_bytes: int

  def generate_impact_frames(
      source: Path,
      destination: Path | None = None,
      *,
      at: float | str,
      frames: int = 2,
      mode: str = "invert",
      fps: int = 60,
      resolution: str = "1920x1080",
  ) -> ImpactResult: ...
  ```

- [ ] **Step 1: Write the failing tests in `tests/test_impact.py`**

```python
from pathlib import Path
import pytest
from media_studio.impact import (
    ImpactError,
    ImpactResult,
    generate_impact_frames,
    parse_at_timestamp,
    build_filter_chain,
    IMPACT_MODES,
)


def test_impact_modes_contain_all_five():
    assert set(IMPACT_MODES.keys()) == {"flash", "invert", "sketch", "red-sketch", "chromatic"}


def test_parse_at_timestamp_seconds_and_frames():
    assert parse_at_timestamp(2.5, fps=60) == (2.5, 150)
    assert parse_at_timestamp("f120", fps=60) == (2.0, 120)
    assert parse_at_timestamp("3.0", fps=30) == (3.0, 90)


def test_parse_at_timestamp_invalid():
    with pytest.raises(ImpactError, match="negative"):
        parse_at_timestamp(-1.0, fps=60)
    with pytest.raises(ImpactError, match="invalid timestamp format"):
        parse_at_timestamp("invalid", fps=60)


def test_build_filter_chain_modes():
    filters = build_filter_chain(mode="invert", resolution="1920x1080", fps=60)
    assert "negate" in filters
    assert "scale=1920:1080" in filters
    assert "fps=60" in filters

    flash_filter = build_filter_chain(mode="flash", resolution="1920x1080", fps=60)
    assert "lutyuv" in flash_filter

    sketch_filter = build_filter_chain(mode="sketch", resolution="1920x1080", fps=60)
    assert "edgedetect" in sketch_filter


def test_generate_impact_frames_bounds_validation(tmp_path: Path):
    dummy = tmp_path / "dummy.mp4"
    dummy.write_bytes(b"empty")
    with pytest.raises(ImpactError, match="frames must be between 1 and 10"):
        generate_impact_frames(dummy, at=1.0, frames=0)
    with pytest.raises(ImpactError, match="frames must be between 1 and 10"):
        generate_impact_frames(dummy, at=1.0, frames=15)
    with pytest.raises(ImpactError, match="unknown impact mode"):
        generate_impact_frames(dummy, at=1.0, mode="supernova")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_impact.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'media_studio.impact'`.

- [ ] **Step 3: Implement `src/media_studio/impact.py` and register CLI**

1. Create `src/media_studio/impact.py`:
   - Implement `ImpactError`, `ImpactResult`, `IMPACT_MODES` dictionary with filter formulas:
     - `flash`: `scale={w}:{h},fps={fps},lutyuv=y=255:u=128:v=128`
     - `invert`: `scale={w}:{h},fps={fps},negate`
     - `sketch`: `scale={w}:{h},fps={fps},edgedetect=low=0.1:high=0.4,negate`
     - `red-sketch`: `scale={w}:{h},fps={fps},edgedetect=low=0.1:high=0.3,negate,colorchannelmixer=rr=1.5:rg=0:rb=0:br=0:bg=0:bb=0`
     - `chromatic`: Horizontal & vertical RGB split displacement simulating lens impact.
   - Implement `parse_at_timestamp(at: float | str, fps: int) -> tuple[float, int]`.
   - Implement `generate_impact_frames(...)`:
     - Probe input clip via `probe_media(source)`; assert `at_seconds <= info.duration`.
     - Frame start = `target_frame`, Frame end = `target_frame + frames - 1`.
     - FFmpeg invocation: `-ss {start_time} -i {source} -frames:v {frames} -vf {filter_chain} -c:v prores_ks -profile:v 4444 -pix_fmt yuva444p12le {destination}`.
2. In `src/media_studio/cli.py`:
   - Add `impact_p = video_sub.add_parser("impact-frame", help="generate transient impact frames for beat and clash hits")`.
   - Arguments: `source`, `--at` (required), `--frames` (default: 2), `--mode` (choices: sorted(IMPACT_MODES.keys()), default: "invert"), `--fps` (default: 60), `--out` (optional).
   - Implement `cmd_video_impact_frame(args)`.

- [ ] **Step 4: Run unit and CLI tests to verify they pass**

Run: `pytest tests/test_impact.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/media_studio/impact.py src/media_studio/cli.py tests/test_impact.py
git commit -m "feat(impact): add AMV transient impact frame generator"
```

### Task 3: 2.5D Parallax Compositor Engine (`src/media_studio/parallax.py`)

**Files:**
- Create: `src/media_studio/parallax.py`
- Modify: `src/media_studio/video.py`
- Modify: `src/media_studio/cli.py`
- Test: `tests/test_parallax.py`

**Interfaces:**
- Consumes: `_pix_fmt_has_alpha` from `src/media_studio/see.py` or `probe_media` from `src/media_studio/ingest.py`.
- Produces:
  ```python
  class ParallaxError(RuntimeError): ...

  PARALLAX_MOTIONS: tuple[str, ...] = ("push_in", "pull_out", "whip_left", "whip_right", "dramatic_dolly")

  @dataclass(frozen=True)
  class ParallaxResult:
      destination: Path
      duration: float
      fps: int
      resolution: str
      layers_composited: int
      motion: str
      size_bytes: int

  def compose_parallax(
      background: Path,
      subject: Path,
      foreground: Path | None = None,
      destination: Path | None = None,
      *,
      duration: float,
      fps: int = 60,
      resolution: str = "1920x1080",
      motion: str = "push_in",
      vignette: float = 0.3,
  ) -> ParallaxResult: ...
  ```

- [ ] **Step 1: Write the failing tests in `tests/test_parallax.py`**

```python
from pathlib import Path
import pytest
from PIL import Image
from media_studio.parallax import (
    ParallaxError,
    ParallaxResult,
    compose_parallax,
    calculate_layer_transforms,
    PARALLAX_MOTIONS,
)


def test_parallax_motions_available():
    assert "push_in" in PARALLAX_MOTIONS
    assert "pull_out" in PARALLAX_MOTIONS
    assert "dramatic_dolly" in PARALLAX_MOTIONS


def test_calculate_layer_transforms_push_in():
    bg_t, subj_t, fg_t = calculate_layer_transforms(
        motion="push_in",
        duration=3.0,
        fps=60,
        width=1920,
        height=1080,
    )
    # Background (depth 0.3) moves slower than Subject (depth 1.0)
    assert bg_t.zoom_start < subj_t.zoom_start or bg_t.zoom_end < subj_t.zoom_end
    assert subj_t.zoom_end > 1.10
    if fg_t:
        assert fg_t.zoom_end > subj_t.zoom_end


def test_compose_parallax_requires_alpha_on_subject(tmp_path: Path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), color=(50, 50, 50)).save(bg)

    subject_no_alpha = tmp_path / "subject_no_alpha.png"
    Image.new("RGB", (1000, 1000), color=(255, 0, 0)).save(subject_no_alpha)

    with pytest.raises(ParallaxError, match="subject layer requires an alpha channel"):
        compose_parallax(
            background=bg,
            subject=subject_no_alpha,
            duration=2.0,
            destination=tmp_path / "out.mov",
        )


def test_compose_parallax_scales_non_16_9_background(tmp_path: Path):
    bg_square = tmp_path / "bg_square.png"
    Image.new("RGB", (1080, 1080), color=(30, 40, 50)).save(bg_square)

    subject_rgba = tmp_path / "subject.png"
    Image.new("RGBA", (1000, 1000), color=(255, 0, 0, 180)).save(subject_rgba)

    out = tmp_path / "out.mov"
    res = compose_parallax(
        background=bg_square,
        subject=subject_rgba,
        duration=1.0,
        destination=out,
        fps=30,
    )
    assert res.destination.is_file()
    assert res.resolution == "1920x1080"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parallax.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'media_studio.parallax'`.

- [ ] **Step 3: Implement `src/media_studio/parallax.py` and manifest integration**

1. Create `src/media_studio/parallax.py`:
   - Implement `ParallaxError`, `ParallaxResult`, and `LayerTransform` helper dataclass.
   - Implement `calculate_layer_transforms(...)` mapping `push_in`, `pull_out`, `whip_left`, `whip_right`, `dramatic_dolly` to zoom and pan expressions.
   - Implement `check_alpha_channel(path: Path) -> bool` reading PIL image mode (`RGBA`, `LA`) or ffprobe pixel format (`yuva444p*`, `yuva420p*`).
   - Implement `compose_parallax(...)`:
     - Validate `background.is_file()` and `subject.is_file()`.
     - Assert `check_alpha_channel(subject)` is True; else raise `ParallaxError("subject layer requires an alpha channel; run ms image remove-bg or ms video remove-bg first")`.
     - Construct FFmpeg filtergraph:
       - Input 0: Background scaled & center-cropped to 1920x1080, transformed with slow push/pan.
       - Input 1: Subject RGBA scaled, transformed with medium push/pan.
       - Input 2 (optional): Foreground RGBA scaled, transformed with fast push/pan.
       - Overlay chaining: `[0:v][1:v]overlay=...[m1];[m1][2:v]overlay=...[m2]`.
       - Optional vignette: `vignette=PI*vignette_amount`.
       - Output encoding: ProRes 4444 (`yuva444p12le`) or high-bitrate MP4.
2. In `src/media_studio/video.py`:
   - In timeline rendering, detect `shot.get("shot_type") == "parallax"`.
   - Dispatch to `compose_parallax()` with the declared assets and duration.
3. In `src/media_studio/cli.py`:
   - Add `parallax_p = video_sub.add_parser("parallax", help="compose 2.5D multi-plane parallax camera shots")`.
   - Add arguments: `--bg`, `--subject`, `--fg`, `--duration`, `--fps`, `--resolution`, `--motion`, `--vignette`, `--out`.
   - Implement `cmd_video_parallax(args)`.

- [ ] **Step 4: Run unit and CLI tests to verify they pass**

Run: `pytest tests/test_parallax.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/media_studio/parallax.py src/media_studio/video.py src/media_studio/cli.py tests/test_parallax.py
git commit -m "feat(parallax): implement 2.5D multi-plane spatial camera compositor"
```

### Task 4: Velocity Ramping & Drop Dynamics Engine (`src/media_studio/ramp.py`)

**Files:**
- Create: `src/media_studio/ramp.py`
- Modify: `src/media_studio/cli.py`
- Test: `tests/test_ramp.py`

**Interfaces:**
- Consumes: `probe_media` from `src/media_studio/ingest.py`.
- Produces:
  ```python
  class RampError(RuntimeError): ...

  RAMP_CURVES: tuple[str, ...] = ("fast_slow_fast", "slow_snap", "flash_freeze")

  @dataclass(frozen=True)
  class SpeedSegment:
      start_time: float
      end_time: float
      speed_factor: float

  @dataclass(frozen=True)
  class RampResult:
      source: Path
      destination: Path
      curve: str
      duration: float
      fps: int
      optical_flow: bool
      fallback_used: bool
      size_bytes: int

  def calculate_ramp_segments(
      clip_duration: float,
      slow_window: tuple[float, float],
      curve: str = "fast_slow_fast",
      slow_factor: float = 0.25,
      fast_factor: float = 2.5,
  ) -> list[SpeedSegment]: ...

  def apply_speed_ramp(
      source: Path,
      destination: Path | None = None,
      *,
      curve: str = "fast_slow_fast",
      slow_window: tuple[float, float],
      slow_factor: float = 0.25,
      fast_factor: float = 2.5,
      optical_flow: bool = True,
      fps: int = 60,
  ) -> RampResult: ...
  ```

- [ ] **Step 1: Write the failing tests in `tests/test_ramp.py`**

```python
from pathlib import Path
import pytest
from media_studio.ramp import (
    RampError,
    RampResult,
    SpeedSegment,
    calculate_ramp_segments,
    apply_speed_ramp,
    RAMP_CURVES,
)


def test_ramp_curves_available():
    assert "fast_slow_fast" in RAMP_CURVES
    assert "slow_snap" in RAMP_CURVES
    assert "flash_freeze" in RAMP_CURVES


def test_calculate_ramp_segments_math():
    segments = calculate_ramp_segments(
        clip_duration=6.0,
        slow_window=(2.0, 4.0),
        curve="fast_slow_fast",
        slow_factor=0.25,
        fast_factor=2.0,
    )
    assert len(segments) == 3
    # Phase 1: 0.0 to 2.0 at 2.0x (takes 1.0s)
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 2.0
    assert segments[0].speed_factor == 2.0
    # Phase 2: 2.0 to 4.0 at 0.25x (takes 8.0s)
    assert segments[1].speed_factor == 0.25
    # Phase 3: 4.0 to 6.0 at 2.0x (takes 1.0s)
    assert segments[2].speed_factor == 2.0


def test_calculate_ramp_segments_invalid_window():
    with pytest.raises(RampError, match="slow_window start must be strictly less than end"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(3.0, 2.0))

    with pytest.raises(RampError, match="outside clip duration"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(1.0, 6.0))

    with pytest.raises(RampError, match="negative"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(-0.5, 2.0))


def test_apply_speed_ramp_validation(tmp_path: Path):
    dummy = tmp_path / "dummy.mp4"
    dummy.write_bytes(b"empty")

    with pytest.raises(RampError, match="slow_factor must be positive"):
        apply_speed_ramp(dummy, slow_window=(1.0, 2.0), slow_factor=0.0)

    with pytest.raises(RampError, match="unknown ramp curve"):
        apply_speed_ramp(dummy, slow_window=(1.0, 2.0), curve="warp_speed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ramp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'media_studio.ramp'`.

- [ ] **Step 3: Implement `src/media_studio/ramp.py` and register CLI**

1. Create `src/media_studio/ramp.py`:
   - Implement `RampError`, `SpeedSegment`, `RampResult`, and `RAMP_CURVES`.
   - Implement `calculate_ramp_segments(clip_duration, slow_window, curve, slow_factor, fast_factor) -> list[SpeedSegment]`:
     - Validate bounds (`0 <= start < end <= clip_duration`, `slow_factor > 0`, `fast_factor > 0`).
     - Calculate segments for `fast_slow_fast`, `slow_snap` (normal -> slow -> snap), and `flash_freeze` (snap -> freeze/0.1x -> snap).
   - Implement `apply_speed_ramp(...)`:
     - Probe input clip via `probe_media(source)`.
     - Construct FFmpeg complex filter:
       - Split video into segments, adjust timestamps via `setpts=(PTS-STARTPTS)/SPEED`.
       - Concat segments: `concat=n={len(segments)}:v=1:a=0`.
       - If `optical_flow` is enabled: chain `minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1`.
       - If FFmpeg fails with optical flow (e.g. exit code != 0 or OOM), re-run with `minterpolate=fps={fps}:mi_mode=blend` and set `fallback_used = True`.
       - Force output fps `{fps}` (default 60 fps) and resolution `1920x1080`.
2. In `src/media_studio/cli.py`:
   - Add `ramp_p = video_sub.add_parser("speed-ramp", help="apply non-linear velocity curves with optical flow")`.
   - Arguments: `source`, `--curve` (choices: RAMP_CURVES, default: "fast_slow_fast"), `--slow-window` (nargs=2, type=float, required=True), `--slow-factor` (default: 0.25), `--fast-factor` (default: 2.5), `--optical-flow` / `--no-optical-flow`, `--fps` (default: 60), `--out`.
   - Implement `cmd_video_speed_ramp(args)`.

- [ ] **Step 4: Run unit and CLI tests to verify they pass**

Run: `pytest tests/test_ramp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/media_studio/ramp.py src/media_studio/cli.py tests/test_ramp.py
git commit -m "feat(ramp): implement velocity ramping engine with optical flow interpolation"
```

### Task 5: EffectCraft AMV Motion & Typography Presets & E2E Validation

**Files:**
- Modify: `src/media_studio/effectcraft.py`
- Modify: `src/media_studio/cli.py`
- Test: `tests/test_amv_presets.py`
- Documentation: `docs/notes/amv-elite-e2e-verification.md`

**Interfaces:**
- Consumes: `build_ecp`, `render` from `src/media_studio/effectcraft.py`, `generate_impact_frames` from `src/media_studio/impact.py`, `compose_parallax` from `src/media_studio/parallax.py`, `apply_speed_ramp` from `src/media_studio/ramp.py`.
- Produces:
  ```python
  AMV_MOTION_PRESETS: dict[str, dict[str, Any]] = {
      "amv-title-cyber": { ... },
      "amv-hud-accent": { ... },
      "amv-drop-shutter": { ... },
  }

  def render_amv_preset(
      preset_name: str,
      output: Path,
      *,
      text: str = "",
      duration: float = 3.0,
      fps: float = 60.0,
      width: int = 1920,
      height: int = 1080,
  ) -> MotionResult: ...
  ```

- [ ] **Step 1: Write the failing tests in `tests/test_amv_presets.py`**

```python
from pathlib import Path
import pytest
from media_studio.effectcraft import (
    AMV_MOTION_PRESETS,
    render_amv_preset,
    EffectCraftError,
)


def test_amv_presets_defined():
    assert "amv-title-cyber" in AMV_MOTION_PRESETS
    assert "amv-hud-accent" in AMV_MOTION_PRESETS
    assert "amv-drop-shutter" in AMV_MOTION_PRESETS


def test_amv_preset_spec_generation():
    cyber_preset = AMV_MOTION_PRESETS["amv-title-cyber"]
    assert cyber_preset["fps"] == 60.0
    assert cyber_preset["width"] == 1920
    assert cyber_preset["height"] == 1080
    assert any(layer.get("params", {}).get("glow") for layer in cyber_preset["layers"])


def test_unknown_amv_preset_raises(tmp_path: Path):
    with pytest.raises(EffectCraftError, match="unknown AMV motion preset"):
        render_amv_preset(
            preset_name="unknown-preset",
            output=tmp_path / "out.mov",
            text="TEST",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_amv_presets.py -v`
Expected: FAIL with `ImportError: cannot import name 'AMV_MOTION_PRESETS' from 'media_studio.effectcraft'`.

- [ ] **Step 3: Implement AMV presets and wire CLI**

1. In `src/media_studio/effectcraft.py`:
   - Define `AMV_MOTION_PRESETS`:
     - `amv-title-cyber`: 1920x1080 @ 60fps, animated tracking, cyan/magenta glow (`#00FFFF`), semi-transparent backdrop.
     - `amv-hud-accent`: 1920x1080 @ 60fps, Swiss grid line elements, corner crosshairs `[ + ]`, tick-mark accents.
     - `amv-drop-shutter`: 1920x1080 @ 60fps, vertical slit mask animation opening on beat drop.
   - Implement `render_amv_preset(...)`:
     - Looks up preset, injects `text`, `duration`, `fps`, `width`, `height`.
     - Writes `.ecp` and renders via `render()`, producing ProRes 4444 RGBA with alpha.
2. In `src/media_studio/cli.py`:
   - Update `motion_title` subparser:
     - Add `--preset` with choices `sorted(AMV_MOTION_PRESETS.keys())`.
     - Update default `--font-size` and dimensions (default width=1920, height=1080, fps=60).
     - In `cmd_motion_title`, if `--preset` is passed, call `render_amv_preset()`.
3. In `tests/test_amv_presets.py`:
   - Add end-to-end integration test chaining:
     - Generate an impact frame (`flash` or `invert`).
     - Render an AMV title preset (`amv-title-cyber`).
     - Composite parallax shot with subject cutout + background + AMV title.
     - Verify with `ms see` producing clean 12-tile contact sheet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_amv_presets.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/media_studio/effectcraft.py src/media_studio/cli.py tests/test_amv_presets.py
git commit -m "feat(motion): add EffectCraft AMV typography and HUD presets"
```
