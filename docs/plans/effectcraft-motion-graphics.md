# Plan: EffectCraft headless motion graphics for AMV production

Status: planned 2026-10-28.

Goal: add a `ms motion` command family that uses EffectCraft's headless CLI (and optionally its
MCP server) to produce After Effects-quality animated title sequences, lyric overlays, logo reveals,
kinetic text, and graphic transitions that are then composited into the AMV pipeline.

## Why EffectCraft

Natron handles per-shot VFX compositing (color grade, glow, keying, roto). EffectCraft fills the
motion graphics gap: animated titles with keyframes and easing curves, 3D camera moves, expressions,
and the 300+ effects set that After Effects projects expect (Fractal Noise, CC Particle World,
Shatter, Glow, Curves, Lumetri Color, text animators). Its headless CLI (`effectcraft --render`) and
MCP server let the agent generate an `.ecp` project from a YAML spec and render it without a GUI.

Key properties:
- Open source (AGPL-3.0), GitHub: `storytold/effectcraft`, pure Rust.
- Cross-platform native binary (not a browser app, not Electron).
- Headless render: `effectcraft render project.ecp out.mp4 --format mp4`.
- MCP server: `effectcraft --mcp` → tool-accessible from Claude Code.
- Active development 2025-2026; confirmed headless render working in reviewer tests.
- No VRAM constraint (CPU renderer, GPU-accelerated render optional).

## Architecture

```
project.yaml (motion: section)
       │
       ▼
ms motion render <project>
  ├─ motion/<sequence>.ecp              (generated EffectCraft project)
  └─ out/motion/<sequence>.mov          (alpha-channel rendered sequence)
       │
       ▼
ms video composite (Natron)
  └─ merged with assembled edit         (Natron Over composite)
       │
       ▼
ms video render final + ms video handoff
```

Motion sequences render with an alpha channel (ProRes 4444 or WebM with alpha), so Natron can
composite them over the assembled footage with a Merge node.

## Implementation

### 1. Tool wrapper: `src/media_studio/effectcraft.py`

```python
"""EffectCraft headless motion graphics: generate .ecp project files and render."""

EFFECTCRAFT = shutil.which("effectcraft")  # or path from config


def check_installation() -> None:
    """Raise ToolMissing if effectcraft binary is not on PATH."""


def render(
    project_path: Path, output: Path, *, fps: float = 30.0, transparent: bool = True
) -> Path:
    """Run effectcraft render on an .ecp file; return output path."""


def title_sequence(
    text: str,
    output: Path,
    *,
    duration: float = 3.0,
    fps: float = 30.0,
    width: int = 1280,
    height: int = 720,
    font: str = "Noto Sans",
    font_size: float = 72.0,
    color: str = "#FFFFFF",
    animation: str = "fade_in_up",  # preset animation name
    glow: bool = False,
    glow_intensity: float = 0.5,
) -> Path:
    """Generate an animated title sequence with alpha channel."""


def lyric_overlay(
    lines: list[tuple[float, float, str]],  # (start, end, text)
    output: Path,
    *,
    fps: float = 30.0,
    width: int = 1280,
    height: int = 720,
    style: str = "default",
) -> Path:
    """Generate a lyric subtitle overlay track with alpha channel (for AMV)."""


def logo_reveal(
    logo_path: Path,
    output: Path,
    *,
    duration: float = 2.0,
    animation: str = "scale_up",
) -> Path:
    """Generate a logo reveal animation with alpha channel."""


def build_ecp(spec: dict) -> str:
    """Serialise a motion graphics spec as an EffectCraft .ecp project."""
```

The `.ecp` project format is JSON-based (confirmed from EffectCraft source). `build_ecp` accepts a
minimal spec dict and fills in EffectCraft's composition/layer/keyframe schema.

### 2. CLI extension: `src/media_studio/cli.py`

```
ms motion render <project> [--sequence <id>] [--fps <float>] [--preview]
ms motion title <project> --text <text> --out <path>
    [--duration <float>] [--animation <name>] [--font <family>] [--glow]
ms motion lyrics <project> --lyrics <yaml_path> --out <path>
```

`ms motion` sequences are declared under `motion:` in `project.yaml` or passed inline.

### 3. `project.yaml` extension (motion section)

```yaml
motion:
  sequences:
    - id: opening
      type: title
      text: "夏の記憶"
      duration: 3.0
      animation: fade_in_up
      font: Noto Sans JP
      glow: true
      glow_intensity: 0.6
      insert_at: 0.0      # seconds into the assembled edit
    - id: lyric-track
      type: lyrics
      lyrics: assets/lyrics/song.yaml   # {start, end, text} list
      style: karaoke
    - id: outro
      type: logo_reveal
      logo: assets/images/logo.svg
      duration: 2.0
      insert_at: end      # relative to end of edit
```

### 4. Integration with Natron composite step

`ms video composite` checks `project.yaml` for `motion:` sequences and composites each rendered
`.mov` onto the assembled edit at the declared `insert_at` timecode using a Natron Merge(Over) node
with the sequence's alpha channel as the matte.

### 5. Tests (all offline, no effectcraft required in CI)

- `test_effectcraft.py` (unit):
  - `build_ecp` produces valid JSON that round-trips through `json.loads`.
  - `title_sequence` spec produces the expected layer count and keyframe properties.
  - `check_installation` raises `ToolMissing` when binary absent (mock `shutil.which`).
- `tests/test_effectcraft_render.py` (integration, skip when `effectcraft` absent):
  - Render a 1s title sequence; confirm output resolution, fps, alpha channel present.
  - Render a lyric overlay; confirm text appears at the declared timecodes (sample frame probe).

### 6. Installation

```bash
# From ArtCraft Launcher or direct GitHub release binary
# Check current releases: https://github.com/storytold/effectcraft/releases
# Copy the binary to ~/.local/bin/effectcraft or /usr/local/bin/
```

The implementation checks for `effectcraft` on PATH and raises `ToolMissing` with an installation
hint if absent. No motion-graphics-specific code runs unless explicitly called.

### 7. MCP mode (optional)

When `effectcraft --mcp` is available, `effectcraft.py` can delegate complex multi-layer project
construction to the MCP server, which Claude Code can call directly. This path is optional and
supplements the programmatic `build_ecp` path (which always works offline).

### 8. Documentation and skill update

- Add `ms motion {render,title,lyrics}` to `skills/media-studio-video/SKILL.md`.
- Update `docs/design.md` §5.1 to describe the motion graphics layer.
- Add `scripts/selftest.sh` matrix row: `motion graphics (EffectCraft)`.

## Risks

- **EffectCraft headless stability**: the project is in active development; the headless render path
  is confirmed working but API may change between minor releases. Pin to a specific release tag.
- **Alpha channel format**: ProRes 4444 requires `prores_ks` encoder; WebM VP9 is the CI-safe
  fallback. The wrapper auto-selects based on `ffprobe` codec availability.
- **Font coverage**: EffectCraft uses system fonts; the same `fontconfig`/`fc-match` logic from
  `media_studio/fonts.py` applies.
- **Performance**: EffectCraft's Rust renderer is fast for motion graphics but 3D camera moves on
  complex scenes can be slow on CPU. Preview mode (`--preview`, quarter resolution) is always
  enabled for the edit review step.
- **License**: AGPL-3.0 means any distributed binary that links EffectCraft must be open source.
  Since this project is private and non-distributed (local agent tooling only), AGPL is acceptable.

## Out of scope

- After Effects `.aep` import (not supported by EffectCraft's headless CLI at this time).
- 3D particle simulation (deferred; CPU cost is too high for iteration).
- Real-time preview in Kdenlive (motion sequences are pre-rendered; Kdenlive sees the `.mov`).
