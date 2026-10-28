# Plan: Natron headless compositing for AMV production

Status: planned 2026-10-28.

Goal: add a `ms video composite` command that passes a rendered shot (or full timeline) through
Natron's headless renderer (`NatronRenderer`) to apply node-based VFX (color grade, glow,
chromakey, roto, tracking) at After Effects production quality without touching the VRAM budget.

## Why Natron

The baseline MLT/melt engine handles assembly, ducking, loudness, and Kdenlive handoff, but it has
no node compositor: filters are static, applied per-clip, and limited to what MLT ships. AMV
production requires:

- Per-shot color grading with curves and LUTs (OpenColorIO).
- Optical glow, chromatic aberration, vignette, and lens effects.
- Chroma keying and roto for transparent overlays.
- Point/planar tracking for motion-linked text or graphic elements.
- Merge operators (Screen, Add, Multiply, Over) for multi-layer composites.
- Time Warp / retime for rhythm-locked edits.

Natron (GPLv2, v2.5.0 Nov 2022 / v2.5.1 pre2 Sept 2024) delivers all of the above headlessly
through `NatronRenderer`, which runs via SSH without a display and accepts a `.ntp` project file
produced programmatically from its Python scripting API.

CPU-based: no VRAM consumed (compatible with GTX 1650 4GB).

## Architecture

```
ms video render (MLT/melt)
  └─ out/video/<deliverable>.mp4           (assembled edit, -14 LUFS)
       │
       ▼
ms video composite (NatronRenderer)
  ├─ natron/<deliverable>.ntp              (generated Natron project)
  └─ out/video/<deliverable>-comp.mp4     (composited output, same loudness)
       │
       ▼
ms video handoff (Kdenlive)
  └─ out/kdenlive/<deliverable>.kdenlive  (optional; reference comp output)
```

For per-shot compositing, `ms video composite` can also process individual shot clips before the
assembly step, writing composited clips back into the project's asset pool.

## Implementation

### 1. Tool wrapper: `src/media_studio/natron.py`

```python
"""Natron headless compositing: generate .ntp project files and render via NatronRenderer."""

NATRON_RENDERER = shutil.which("NatronRenderer")  # /usr/bin or path from config
NATRON = shutil.which("Natron")  # GUI for verification only


def check_installation() -> None:
    """Raise ToolMissing if NatronRenderer is not on PATH."""


def render(project_path: Path, output: Path, *, extra_args: list[str] | None = None) -> Path:
    """Run NatronRenderer on a .ntp file and return the output path."""


def simple_grade(
    source: Path,
    output: Path,
    *,
    lut: Path | None = None,
    gain: float = 1.0,
    gamma: float = 1.0,
    saturation: float = 1.0,
    glow_size: float = 0.0,
    glow_mix: float = 0.5,
    vignette: float = 0.0,
) -> Path:
    """Build a minimal Natron project that applies grade + glow + vignette and render it."""


def composite_layers(
    background: Path,
    foreground: Path,
    output: Path,
    *,
    merge_op: str = "over",
    mask: Path | None = None,
) -> Path:
    """Merge foreground over background using the specified Merge operator."""


def build_ntp(nodes: list[dict]) -> str:
    """Serialise a Natron node graph as a .ntp XML/JSON document."""
```

Node graph construction uses the Natron Python API schema: every node is a dict with
`pluginID`, `inputs`, and `params` (each a `{name, value}` pair). The `.ntp` serialisation
matches what Natron's File > Save Project writes, so the same project opens in the GUI for
inspection.

### 2. CLI extension: `src/media_studio/cli.py`

```
ms video composite <project> [--deliverable <id>] [--preset <preset>]
    [--lut <path>]
    [--gain <float>]  [--gamma <float>]  [--saturation <float>]
    [--glow-size <float>]  [--glow-mix <float>]
    [--vignette <float>]
```

Reads `project.yaml` (specifically the `compositing:` section described in §4 below), applies the
grade/glow recipe, renders to `out/video/<deliverable>-comp.mp4`, and writes a report JSON under
`out/reports/`.

### 3. `project.yaml` extension (compositing section)

```yaml
compositing:
  grade:
    lut: assets/luts/film_emulation.cube     # CC0 or project-owned LUT
    gain: 1.05
    gamma: 0.96
    saturation: 1.1
  effects:
    - glow:
        size: 3.0
        mix: 0.35
        threshold: 0.7
    - vignette:
        strength: 0.25
  per_shot:
    - asset: shot-1
      grade:
        gain: 1.2
        saturation: 1.4
```

`compositing:` is optional; `ms video render` ignores it (renders clean assembly). `ms video
composite` applies it after `ms video render` completes or can be run standalone on any video file.

### 4. QC extension: `src/media_studio/media.py`

Reuse the existing `qc_report` function: after compositing, run it again on the composited output
to confirm duration, loudness (should be preserved — comp step does not alter audio), and no black
frames from a broken composite.

### 5. Tests (all offline, no NatronRenderer required in CI)

- `test_natron.py` (unit):
  - `build_ntp` produces valid XML that parses back.
  - `simple_grade` with all-default params produces a pass-through graph (no colour shift).
  - `composite_layers` with `merge_op="over"` and identical inputs produces an identity composite.
  - `check_installation` raises `ToolMissing` when `NatronRenderer` is not present (mock `shutil.which`).
- `tests/test_natron_render.py` (integration, skip when `NatronRenderer` is absent):
  - Render a 1s synthetic clip through `simple_grade`; confirm output duration and mean luma within
    tolerance of input.
  - Apply a known glow and assert peak luminance > input peak luminance.

### 6. Installation

Natron is in the Arch/CachyOS community repos:

```bash
# Check if available in current pacman repos
pacman -Ss natron 2>/dev/null
# Or from flatpak
flatpak install flathub fr.natron.Natron
```

The implementation checks for `NatronRenderer` on `PATH` and raises `ToolMissing` with an
installation hint if absent. No compositing-specific code runs unless explicitly called.

### 7. Documentation and skill update

- Add `ms video composite` to `skills/media-studio-video/SKILL.md` under its own section.
- Update `docs/design.md` §5.1 to describe the compositing layer.
- Add `scripts/selftest.sh` matrix row: `video compositing (Natron)`.

## Risks

- **Natron .ntp format stability**: the project file schema is stable across v2.x but not
  formally versioned. Tested against v2.5.0 and v2.5.1. A major version bump would require schema
  verification.
- **CPU render speed**: Natron compositing is CPU-bound. A 1080p shot at 30fps costs roughly 2-5
  min/min of footage on this machine. AMV projects should budget render time accordingly.
- **NatronRenderer PATH**: the flatpak version sandboxes the binary; prefer the pacman package so
  `NatronRenderer` is on system PATH.
- **Audio passthrough**: `NatronRenderer` is video-only. The audio track from the assembled edit
  must be muxed back after compositing (FFmpeg `-c:a copy`). The CLI wrapper handles this.

## Out of scope

- Roundtripping Natron project files back to `project.yaml`.
- Natron GUI automation (GUI is for human review only).
- Motion tracking (tracking node is available in Natron but requires manual setup or a separate
  Python tracker adapter — deferred to a future plan).
