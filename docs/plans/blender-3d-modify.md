# Plan: Blender `ms 3d modify` for model modification

Status: planned 2026-10-28.

Goal: add a `ms 3d modify` command that applies procedural mesh modifications (deform, mirror,
boolean, UV project, smooth, subdivision, vertex group edits) to an existing 3D model headlessly
via Blender Geometry Nodes and Python scripting. No new tools to install — uses the same
`media_studio/blender.py` infrastructure already in the baseline.

## Why

The baseline 3D path (`ms 3d inspect`, `ms 3d convert`, `ms 3d material`, `ms 3d turntable`) handles
import, repair, texture, and render. It does not expose **purposeful mesh modification**: a user who
downloads a CC0 dragon model and wants to (a) mirror the wings, (b) apply a deformation to the
tail, or (c) cut a boolean hole for a prop attachment has to open Blender manually. `ms 3d modify`
automates these via Blender's Python API and Geometry Nodes, which run headlessly inside the
existing Blender CLI infrastructure.

Hardware constraint: zero VRAM overhead during modification (only the EEVEE render step uses GPU).

## Supported modifications

The first increment implements the following, each as a named `operation` in the `project.yaml`
`model.modifications:` list:

| operation | What it does |
|-----------|-------------|
| `mirror` | Mirror the mesh along X, Y, or Z axis with optional merge at seam |
| `smooth` | Apply Laplacian smooth (n iterations, factor) |
| `subdivide` | Catmull-Clark subdivision (levels 1-3, max on 4GB GPU) |
| `deform_bend` | Bend the mesh along an axis by angle (degrees) |
| `deform_taper` | Taper along an axis (start/end factor) |
| `boolean_cut` | Subtract a primitive (box/cylinder/sphere) from the mesh |
| `uv_project` | Reproject UVs (smart UV project or cube projection) |
| `decimate` | Decimate to a target face ratio (extends the existing `ms 3d convert` decimation) |
| `transform` | Translate, rotate, scale (relative or absolute) |
| `vertex_color` | Paint a solid vertex colour or gradient onto a named attribute |

Complex modifications (rigging, sculpt, retopology) are out of scope for this increment.

## Implementation

### 1. Blender script: `src/media_studio/blender_scripts/modify.py`

```python
"""Headless Blender modifier pipeline. Called by blender.py:modify_model."""

import bpy, sys, json


def apply_modification(op: dict, obj: bpy.types.Object) -> None:
    """Dispatch to the correct modifier or operator based on op["type"]."""
    if op["type"] == "mirror":
        mod = obj.modifiers.new("Mirror", "MIRROR")
        mod.use_axis = (op.get("x", True), op.get("y", False), op.get("z", False))
        mod.use_merge = op.get("merge", True)
        bpy.ops.object.modifier_apply(modifier=mod.name)
    elif op["type"] == "smooth":
        ...
    # … one branch per operation


def main():
    args = json.loads(sys.argv[sys.argv.index("--") + 1])
    bpy.ops.wm.open_mainfile(filepath=args["input"])
    for op in args["operations"]:
        obj = bpy.data.objects[args.get("object", bpy.context.active_object.name)]
        apply_modification(op, obj)
    bpy.ops.wm.save_mainfile(filepath=args["output"])
    # Also export to the target format
    ...
```

### 2. Python wrapper: `src/media_studio/blender.py` extension

Add a `modify_model(input_path, output_path, operations, ...)` function that:
1. Validates each operation dict against the supported-operation schema.
2. Calls `run_script(BLENDER_SCRIPTS / "modify.py", args_json)` through the existing Blender CLI
   infrastructure (Vulkan, PATH guard, etc.).
3. Returns a `ModifyResult(output_path, operations_applied, before_stats, after_stats)`.

### 3. CLI extension

```
ms 3d modify <project> --model <asset_id>
    [--ops <json_or_yaml_file>]
    [--mirror x|y|z]
    [--smooth <iterations>]
    [--subdivide <levels>]
    [--decimate <ratio>]
    [--out <filename>]
```

Short-form flags cover the most common single-op use. `--ops` accepts a YAML/JSON file for a
multi-step modification pipeline.

### 4. `project.yaml` extension (model modifications)

```yaml
assets:
  dragon: video/dragon.glb

model:
  asset: dragon
  modifications:
    - type: mirror
      x: true
      merge: true
    - type: smooth
      iterations: 3
      factor: 0.5
    - type: uv_project
      method: smart
    - type: decimate
      ratio: 0.7
```

`ms 3d modify` reads `model.modifications`, applies them in order, writes the result to
`out/3d/<asset_id>-modified.<ext>`, and then passes the output through `ms 3d inspect` to report
before/after face/vertex count.

### 5. Tests

- `test_blender_modify.py` (unit; skip when Blender absent):
  - Mirror operation on a 1k-poly cube doubles face count and produces a merged seam.
  - Smooth reduces mean edge length variance.
  - Decimate to 0.5 ratio halves face count within ±5%.
  - Invalid operation name raises `ValueError` before calling Blender.
- Integration: run `ms 3d modify` on a CC0 test model from `tests/fixtures/` and compare
  before/after stats.

### 6. Documentation and skill update

- Add `ms 3d modify` to `skills/media-studio-3d/SKILL.md`.
- Update `docs/design.md` §5.2 to describe the modification sub-path.
- Add `scripts/selftest.sh` matrix row: `3D model modification`.

## Risks

- **Modifier dependency order**: some modifier combinations (e.g., smooth after subdivide) produce
  different results depending on application order. The implementation applies them in declaration
  order and documents that the user is responsible for ordering.
- **Geometry Nodes vs. modifiers**: Blender 4.x prefers Geometry Nodes for procedural work.
  Where possible, the script uses GN-based modifiers, falling back to legacy modifiers for
  operations not yet ported to GN.
- **Object multi-mesh files**: GLB/FBX may contain multiple mesh objects. `--object` flag selects
  the target; default is the active object after import.

## Out of scope

- Rigging and skinning.
- Sculpt-mode operations.
- Material-driven deformation.
- Retopology.
