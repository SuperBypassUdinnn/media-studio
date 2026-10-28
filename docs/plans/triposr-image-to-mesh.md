# Plan: TripoSR image-to-3D for lightweight model generation

Status: planned 2026-10-28. **Prerequisite:** storage-doctor check (disk ~87% used; TripoSR weights ~3-5GB).

Goal: add a `ms 3d image-to-mesh` command that runs TripoSR locally (MIT license, offline after
install, no API key) to convert a single reference image into a textured 3D mesh (OBJ/GLB), then
pipes it through the existing `ms 3d convert` → `ms 3d material` → `ms 3d turntable` pipeline.

## Why TripoSR

The user's 3D preference: "mampu membuat model dari text atau gambar yang tidak terlalu detail
namun konsisten dengan input yang diberikan" — able to create models from text or images that are
not overly detailed but consistent with the input. TRELLIS.2 (~8GB VRAM) and SF3D (~7GB VRAM) are
ruled out by the GTX 1650 Max-Q 4GB VRAM constraint. TripoSR runs on 4GB with a `--chunk-size`
workaround and produces draft-quality but usable base meshes.

## How text-to-image-to-3D works

TripoSR itself is image-to-3D. For text input, the pipeline is:

```
text prompt → image generator (user-supplied or local SDXL fallback) → PNG → TripoSR → OBJ/GLB
```

The image generation step is user-supplied (not part of this project; no paid APIs). TripoSR takes
the image and outputs a base mesh which `media_studio/blender.py` then refines (`ms 3d modify`,
`ms 3d material`, `ms 3d turntable`).

## Implementation

### 1. Python wrapper: `src/media_studio/triposr.py`

```python
"""TripoSR image-to-3D: headless conversion of a reference image into a textured mesh."""

TRIPOSR = shutil.which("triposr")  # or configured path; otherwise python -m triposr.cli


def check_installation() -> None:
    """Raise ToolMissing if triposr is not available."""


def image_to_mesh(
    image_path: Path,
    output_path: Path,
    *,
    chunk_size: int = 4096,  # VRAM safety for 4GB GPUs
    device: str = "cuda",  # "cuda" or "cpu" (slow)
    mc_resolution: int = 256,
    remove_bg: bool = True,
) -> Path:
    """Run TripoSR on image_path and write textured OBJ/GLB to output_path."""


def vram_safe_chunk_size() -> int:
    """Probe available VRAM via nvidia-smi and return a safe --chunk-size."""
```

For a GTX 1650 Max-Q 4GB, `--chunk-size 4096` is the documented safe value (confirmed by the
TripoVRAM calculator). `--device cpu` is the fallback if CUDA OOMs.

TripoSR is installed via `pip install triposr` (MIT). The weights download automatically on first
run (~3-5GB) — hence the storage-doctor gate.

### 2. CLI extension: `src/media_studio/cli.py`

```
ms 3d image-to-mesh <image> [--out <obj|glb>]
    [--chunk-size <int>] [--device cuda|cpu] [--mc-resolution <int>]
    [--remove-bg]
```

`image` is a PNG/JPG reference (front/side view recommended; TripoSR assumes a single view).

### 3. Integration with the 3D pipeline

`ms 3d image-to-mesh` writes to `out/3d/<slug>-base.<ext>`. A convenience wrapper:

```
ms 3d from-image <image> [--project <project_slug>]
```

runs TripoSR then chains:
1. `ms 3d convert` (decimate if needed, fix normals)
2. `ms 3d material` (PBR binding from Poly Haven — already supported)
3. `ms 3d turntable` (render preview — already supported)

### 4. Tests (skip when triposr unavailable)

- `test_triposr.py` (unit):
  - `check_installation` raises `ToolMissing` when triposr absent (mock `shutil.which`).
  - `vramsafe_chunk_size` returns <= 8192 for a 4GB VRAM machine (mock `subprocess` nvidia-smi output).
- `tests/test_triposr_render.py` (integration, requires triposr + model weights):
  - Convert a small synthetic reference image (rendered from Blender or a placeholder PNG).
  - Confirm output OBJ/GLB parses and contains vertices/faces.

### 5. Storage gate (mandatory before install)

```bash
# Check available space before downloading TripoSR weights (~3-5GB)
storage-doctor --check /home/superbypassudin/.clone/Github/media-studio
# Or inline:
df -h ~/.local/share/triposr  # weights live here by default
```

If disk is below 10GB free, abort with a hint to free space via `storage-doctor --approve`.

### 6. Documentation and skill update

- Add `ms 3d image-to-mesh` and `ms 3d from-image` to `skills/media-studio-3d/SKILL.md`.
- Update `docs/design.md` §5.2 to describe the image-to-mesh sub-path.
- Add `scripts/selftest.sh` matrix row: `3D image-to-mesh (TripoSR)` (skip when triposr absent).

## Risks

- **Draft quality**: TripoSR output is usable as a base mesh but not fine art. `ms 3d modify`
  and `ms 3d material` clean it up; fine sculpting is out of scope.
- **Single-view limitation**: TripoSR infers from one image. Multi-view consistency requires
  the user to supply a good front/side reference image (user-supplied asset).
- **Weights download**: ~3-5GB on first run. Disk at ~87% used makes this a real constraint —
  the storage-doctor gate prevents silent failure.
- **VRAM**: default TripoSR settings OOM a 4GB card. `--chunk-size 4096` is mandatory; CPU
  fallback is slow (~minutes per mesh) but works.
- **License**: MIT — compatible with the project's free/CC0-first policy. Model weights are
  bundled under TripoSR's own MIT terms.

## Out of scope

- Text-to-3D directly (no image generator bundled; user supplies the image).
- Multi-view / photogrammetry input.
- Rigging, animation, or sculpt refinement beyond `ms 3d modify`.
