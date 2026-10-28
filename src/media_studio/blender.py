"""The 3D path: Blender as a scripted, headless engine.

Every Blender run gets the same environment, because three of these settings are mandatory on this
machine and one of them is a workaround for a packaging mismatch:

* `VK_ICD_FILENAMES` points at the NVIDIA Vulkan ICD, and rendering passes `--gpu-backend vulkan`,
  or the discrete GPU is not used at all.
* `/usr/bin` is placed before `~/.local/bin` on `PATH`, or Blender's embedded Python loses its
  stdlib.
* If Blender was upgraded ahead of the `draco` package it links against, `libdraco.so.N` is missing
  and Blender will not start. A private shim directory is created and put on `LD_LIBRARY_PATH`,
  and every report says the shim was used so the real fix (updating the system package) is not
  forgotten.

The committed artifact is the script, never the `.blend`: scenes are rebuilt from these runs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import media
from .manifest import Project

BLENDER = "blender"
SCRATCH = Path("/tmp/opencode/blender")
SHIM_DIRECTORY = Path("~/.local/state/opencode-media-studio/draco-shim")
VULKAN_ICD = "/usr/share/vulkan/icd.d/nvidia_icd.json"
TIMEOUT_SECONDS = 3600
MISSING_LIBRARY = re.compile(r"error while loading shared libraries: (lib[\w.]+)")
DEFAULT_RESOLUTION = (640, 360)
DEFAULT_FRAMES = 24
SUPPORTED_INPUTS = (
    ".glb",
    ".gltf",
    ".obj",
    ".stl",
    ".fbx",
    ".usd",
    ".usda",
    ".usdc",
    ".usdz",
    ".ply",
)
EXPORT_FORMATS = {
    "glb": ".glb",
    "gltf": ".gltf",
    "obj": ".obj",
    "stl": ".stl",
    "ply": ".ply",
    "fbx": ".fbx",
}


class BlenderError(RuntimeError):
    """Raised when Blender is unavailable or a run fails."""


@dataclass(frozen=True)
class BlenderEnvironment:
    """The environment a Blender run needs, and whether a workaround is active."""

    env: dict[str, str]
    shim: Path | None = None
    notes: tuple[str, ...] = field(default=())

    def describe(self) -> str:
        if not self.notes:
            return "clean"
        return "; ".join(self.notes)


def _missing_library(stderr: str) -> str | None:
    match = MISSING_LIBRARY.search(stderr)
    return match.group(1) if match else None


def environment(*, create_shim: bool = True) -> BlenderEnvironment:
    """Build the environment, creating a library shim only when Blender actually needs one."""

    env = dict(os.environ)
    notes: list[str] = []
    path_entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    if "/usr/bin" in path_entries and path_entries[0] != "/usr/bin":
        path_entries.remove("/usr/bin")
        env["PATH"] = os.pathsep.join(["/usr/bin", *path_entries])
        notes.append("PATH reordered so /usr/bin precedes user shims")
    env["VK_ICD_FILENAMES"] = VULKAN_ICD

    probe = subprocess.run(
        [BLENDER, "--background", "--version"], capture_output=True, text=True, check=False, env=env
    )
    missing = _missing_library(probe.stderr or "")
    if probe.returncode == 0 or not missing:
        return BlenderEnvironment(env=env, notes=tuple(notes))

    if not create_shim:
        raise BlenderError(f"blender cannot start: {missing} is missing")

    # Find a sibling version of the same library and present it under the name Blender asks for.
    shim = SHIM_DIRECTORY.expanduser()
    shim.mkdir(parents=True, exist_ok=True)
    stem = Path(missing).stem.split(".so")[0]
    candidates = sorted(Path("/usr/lib").glob(f"{stem}.so.*"), reverse=True)
    if not candidates:
        raise BlenderError(
            f"blender cannot start: {missing} is missing and no {stem}.so.* exists to shim it"
        )
    link = shim / missing
    if not link.exists():
        link.symlink_to(candidates[0])
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        [str(shim), *[entry for entry in env.get("LD_LIBRARY_PATH", "").split(os.pathsep) if entry]]
    )
    notes.append(
        f"used an ABI shim: {missing} points at {candidates[0].name}; update the system package "
        "to remove it"
    )
    return BlenderEnvironment(env=env, shim=shim, notes=tuple(notes))


def available() -> bool:
    """Whether Blender can run at all right now."""

    try:
        version = version_string()
    except BlenderError:
        return False
    return bool(version)


def version_string() -> str:
    """Blender's version, or an explanation of why it cannot start."""

    built = environment()
    completed = subprocess.run(
        [BLENDER, "--background", "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=built.env,
        timeout=300,
    )
    if completed.returncode != 0:
        missing = _missing_library(completed.stderr or "")
        raise BlenderError(f"blender failed to start: {missing or completed.stderr.strip()[:200]}")
    first = (completed.stdout or "").strip().splitlines()
    return first[0] if first else "blender"


def run_script(
    script: str, *arguments: str, use_gpu: bool = False, timeout: int = TIMEOUT_SECONDS
) -> str:
    """Run an embedded script in background mode and return its stdout."""

    built = environment()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".py", dir=SCRATCH, delete=False) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    command = [BLENDER, "--background"]
    if use_gpu:
        command.extend(["--gpu-backend", "vulkan"])
    # Without this, Blender exits 0 even when the embedded script raised, which hides real
    # failures behind a missing output file.
    command.extend(["--python-exit-code", "1", "--python", str(script_path)])
    if arguments:
        command.extend(["--", *arguments])
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, env=built.env, timeout=timeout
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise BlenderError(f"blender run failed with exit {completed.returncode}:\n{tail}")
    return completed.stdout or ""


def _report(stdout: str, marker: str = "MEDIA_STUDIO_JSON") -> dict[str, object]:
    """Read the JSON a script prints, ignoring Blender's own chatter."""

    for line in stdout.splitlines():
        if line.startswith(marker):
            payload = line[len(marker) :].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError as error:
                raise BlenderError(f"could not read the script report: {error.msg}") from error
    raise BlenderError("the script produced no report")


IMPORT_HELPER = """
import bpy, os

def import_model(path):
    extension = os.path.splitext(path)[1].lower()
    if extension in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif extension == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif extension == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif extension == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif extension in (".usd", ".usda", ".usdc", ".usdz"):
        bpy.ops.wm.usd_import(filepath=path)
    elif extension == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    else:
        raise SystemExit("unsupported input: " + extension)


def export_model(path):
    extension = os.path.splitext(path)[1].lower()
    if extension in (".glb", ".gltf"):
        bpy.ops.export_scene.gltf(filepath=path, export_format=extension.lstrip(".").upper())
    elif extension == ".obj":
        bpy.ops.wm.obj_export(filepath=path)
    elif extension == ".stl":
        bpy.ops.wm.stl_export(filepath=path)
    elif extension == ".ply":
        bpy.ops.wm.ply_export(filepath=path)
    elif extension == ".fbx":
        bpy.ops.export_scene.fbx(filepath=path)
    else:
        raise SystemExit("unsupported output: " + extension)
"""

INSPECT_SCRIPT = (
    IMPORT_HELPER
    + """
import bmesh, json, sys

arguments = sys.argv[sys.argv.index("--") + 1:]
source = arguments[0]
bpy.ops.wm.read_factory_settings(use_empty=True)
import_model(source)

meshes = [item for item in bpy.data.objects if item.type == "MESH"]
triangles = 0
vertices = 0
non_manifold = 0
loose = 0
flat_shaded = 0
for item in meshes:
    mesh = item.data
    bmesh_data = bmesh.new()
    bmesh_data.from_mesh(mesh)
    bmesh_data.transform(item.matrix_world)
    triangles += sum(len(face.verts) - 2 for face in bmesh_data.faces)
    vertices += len(bmesh_data.verts)
    non_manifold += sum(1 for edge in bmesh_data.edges if not edge.is_manifold)
    loose += sum(1 for vert in bmesh_data.verts if not vert.link_edges)
    flat_shaded += 1 if mesh.polygons and not mesh.polygons[0].use_smooth else 0
    bmesh_data.free()

corners = []
for item in meshes:
    for corner in item.bound_box:
        corners.append(item.matrix_world @ __import__("mathutils").Vector(corner))
minimum = [min(point[index] for point in corners) for index in range(3)] if corners else [0, 0, 0]
maximum = [max(point[index] for point in corners) for index in range(3)] if corners else [0, 0, 0]

report = {
    "source": source,
    "objects": len(bpy.data.objects),
    "mesh_objects": len(meshes),
    "triangles": triangles,
    "vertices": vertices,
    "non_manifold_edges": non_manifold,
    "loose_vertices": loose,
    "flat_shaded_meshes": flat_shaded,
    "materials": [material.name for material in bpy.data.materials],
    "images": [image.name for image in bpy.data.images if image.name != "Render Result"],
    "unit_scale": bpy.context.scene.unit_settings.scale_length,
    "bounds_min": [round(value, 4) for value in minimum],
    "bounds_max": [round(value, 4) for value in maximum],
    "size": [round(maximum[index] - minimum[index], 4) for index in range(3)],
}
print("MEDIA_STUDIO_JSON " + json.dumps(report))
"""
)

NORMALIZE_SCRIPT = (
    IMPORT_HELPER
    + """
import bmesh, json, sys

arguments = sys.argv[sys.argv.index("--") + 1:]
source, destination = arguments[0], arguments[1]
decimate = float(arguments[2]) if len(arguments) > 2 and arguments[2] else 0.0

bpy.ops.wm.read_factory_settings(use_empty=True)
import_model(source)

for item in [entry for entry in bpy.data.objects if entry.type == "MESH"]:
    item.select_set(True)
    bpy.context.view_layer.objects.active = item
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    # `normals_make_consistent` is the repair that matters; Blender 5 no longer ships the
    # separate "normalize normals" operator.
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.object.mode_set(mode="OBJECT")
    if decimate and decimate < 1.0:
        modifier = item.modifiers.new(name="decimate", type="DECIMATE")
        modifier.ratio = decimate
        bpy.ops.object.modifier_apply(modifier=modifier.name)

for item in bpy.data.objects:
    item.select_set(True)

repaired = [item for item in bpy.data.objects if item.type == "MESH"]
triangles = 0
non_manifold = 0
loose = 0
for item in repaired:
    bmesh_data = bmesh.new()
    bmesh_data.from_mesh(item.data)
    triangles += sum(len(face.verts) - 2 for face in bmesh_data.faces)
    non_manifold += sum(1 for edge in bmesh_data.edges if not edge.is_manifold)
    loose += sum(1 for vert in bmesh_data.verts if not vert.link_edges)
    bmesh_data.free()

export_model(destination)
print("MEDIA_STUDIO_JSON " + json.dumps({
    "source": source,
    "destination": destination,
    "decimate": decimate,
    "triangles": triangles,
    "non_manifold_edges": non_manifold,
    "loose_vertices": loose,
}))
"""
)

TURNTABLE_SCRIPT = (
    IMPORT_HELPER
    + """
import hashlib, json, math, os, sys, time
from mathutils import Vector

arguments = sys.argv[sys.argv.index("--") + 1:]
source, out_dir, frames, width, height = (
    arguments[0], arguments[1], int(arguments[2]), int(arguments[3]), int(arguments[4])
)

bpy.ops.wm.read_factory_settings(use_empty=True)
import_model(source)
scene = bpy.context.scene
scene.render.resolution_x = width
scene.render.resolution_y = height
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False

meshes = [item for item in bpy.data.objects if item.type == "MESH"]
corners = []
for item in meshes:
    for corner in item.bound_box:
        corners.append(item.matrix_world @ Vector(corner))
centre = Vector((0, 0, 0))
radius = 2.0
if corners:
    centre = sum(corners, Vector((0, 0, 0))) / len(corners)
    radius = max((point - centre).length for point in corners) or 1.0
for item in meshes:
    item.location -= centre

pivot = bpy.data.objects.new("pivot", None)
scene.collection.objects.link(pivot)
for item in meshes:
    item.parent = pivot

camera_data = bpy.data.cameras.new("camera")
camera = bpy.data.objects.new("camera", camera_data)
scene.collection.objects.link(camera)
camera.location = (0, -radius * 3.0, radius * 1.1)
camera.rotation_euler = (math.radians(78), 0, 0)
scene.camera = camera

sun_data = bpy.data.lights.new("key", type="SUN")
sun_data.energy = 4.0
sun = bpy.data.objects.new("key", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(55), 0, math.radians(35))

world = bpy.data.worlds.new("world")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.35, 0.37, 0.4, 1.0)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6

os.makedirs(out_dir, exist_ok=True)
frame_reports = []
scene.frame_start = 1
scene.frame_end = frames
pivot.rotation_euler = (0, 0, 0)
pivot.keyframe_insert("rotation_euler", frame=1)
pivot.rotation_euler = (0, 0, math.tau)
pivot.keyframe_insert("rotation_euler", frame=frames)

for frame in range(1, frames + 1):
    scene.frame_set(frame)
    path = os.path.join(out_dir, "frame-%04d.png" % frame)
    scene.render.filepath = path
    started = time.time()
    bpy.ops.render.render(write_still=True)
    seconds = time.time() - started
    digest = ""
    size = 0
    if os.path.exists(path + ".png"):
        path = path + ".png"
    if os.path.exists(path):
        with open(path, "rb") as handle:
            data = handle.read()
        digest = hashlib.sha256(data).hexdigest()[:16]
        size = len(data)
    frame_reports.append({
        "frame": frame,
        "file": os.path.basename(path),
        "sha256": digest,
        "bytes": size,
        "seconds": round(seconds, 3),
    })

report = {
    "source": source,
    "frames": frames,
    "resolution": [width, height],
    "radius": round(radius, 4),
    "geometry": {
        "objects": len(meshes),
        "triangles": sum(
            len(item.data.loop_triangles) if item.data.loop_triangles else len(item.data.polygons)
            for item in meshes
        ),
    },
    "frame_reports": frame_reports,
}
print("MEDIA_STUDIO_JSON " + json.dumps(report))
"""
)


MODIFY_SCRIPT = (
    IMPORT_HELPER
    + """
import bmesh, json, math, sys
from mathutils import Vector, Euler

arguments = sys.argv[sys.argv.index("--") + 1:]
source, destination, operations_json = arguments[0], arguments[1], arguments[2]
operations = json.loads(operations_json)

bpy.ops.wm.read_factory_settings(use_empty=True)
import_model(source)

# Capture before stats before any modification.
before_meshes = [item for item in bpy.data.objects if item.type == "MESH"]
before_triangles = 0
before_vertices = 0
for item in before_meshes:
    bmesh_data = bmesh.new()
    bmesh_data.from_mesh(item.data)
    before_triangles += sum(len(face.verts) - 2 for face in bmesh_data.faces)
    before_vertices += len(bmesh_data.verts)
    bmesh_data.free()

# Apply each operation in declaration order; the user is responsible for ordering.
for op in operations:
    op_type = op.get("type")
    target_name = op.get("object")
    target = (
        bpy.data.objects[target_name]
        if target_name and target_name in bpy.data.objects
        else (before_meshes[0] if before_meshes else None)
    )
    if target is None or target.type != "MESH":
        raise SystemExit(f"no mesh target for operation {op_type!r}")

    if op_type == "mirror":
        mod = target.modifiers.new(name="mirror", type="MIRROR")
        mod.use_axis = (
            bool(op.get("x", True)),
            bool(op.get("y", False)),
            bool(op.get("z", False)),
        )
        bpy.ops.object.modifier_apply(modifier=mod.name)

    elif op_type == "smooth":
        iterations = int(op.get("iterations", 1))
        factor = float(op.get("factor", 0.5))
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.vertices_smooth(repeat=iterations, factor=factor)
        bpy.ops.object.mode_set(mode="OBJECT")

    elif op_type == "subdivide":
        levels = max(1, min(3, int(op.get("levels", 1))))
        mod = target.modifiers.new(name="subdivide", type="SUBSURF")
        mod.levels = levels
        mod.render_levels = levels
        bpy.ops.object.modifier_apply(modifier=mod.name)

    elif op_type == "deform_bend":
        angle = math.radians(float(op.get("angle", 45.0)))
        axis = op.get("axis", "Z")
        mod = target.modifiers.new(name="bend", type="SIMPLE_DEFORM")
        mod.deform_method = "BEND"
        mod.deform_axis = axis
        mod.origin = target
        mod.angle = angle
        bpy.ops.object.modifier_apply(modifier=mod.name)

    elif op_type == "deform_taper":
        mod = target.modifiers.new(name="taper", type="SIMPLE_DEFORM")
        mod.deform_method = "TAPER"
        mod.deform_axis = op.get("axis", "Z")
        mod.origin = target
        mod.start = float(op.get("start", 1.0))
        mod.end = float(op.get("end", 0.0))
        bpy.ops.object.modifier_apply(modifier=mod.name)

    elif op_type == "boolean_cut":
        shape = op.get("shape", "box")
        size = float(op.get("size", 1.0))
        if shape == "box":
            bpy.ops.mesh.primitive_cube_add(size=size)
        elif shape == "cylinder":
            bpy.ops.mesh.primitive_cylinder_add(radius=size, depth=size * 2.0)
        elif shape == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(radius=size)
        else:
            raise SystemExit(f"unknown boolean shape {shape!r}")
        cutter = bpy.context.active_object
        if op.get("location"):
            loc = op["location"]
            cutter.location = (
                float(loc.get("x", 0)),
                float(loc.get("y", 0)),
                float(loc.get("z", 0)),
            )
        mod = target.modifiers.new(name="boolean", type="BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.data.objects.remove(cutter, do_unlink=True)

    elif op_type == "uv_project":
        method = op.get("method", "smart")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        if method == "cube":
            bpy.ops.uv.cube_projection()
        else:
            bpy.ops.uv.smart_project()
        bpy.ops.object.mode_set(mode="OBJECT")

    elif op_type == "decimate":
        ratio = float(op.get("ratio", 0.5))
        mod = target.modifiers.new(name="decimate", type="DECIMATE")
        mod.ratio = ratio
        bpy.ops.object.modifier_apply(modifier=mod.name)

    elif op_type == "transform":
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        loc = op.get("location", {})
        rot = op.get("rotation", {})
        scale = op.get("scale", {})
        target.location = (
            float(loc.get("x", 0)) + target.location.x,
            float(loc.get("y", 0)) + target.location.y,
            float(loc.get("z", 0)) + target.location.z,
        )
        target.rotation_euler = Euler((
            math.radians(float(rot.get("x", 0))),
            math.radians(float(rot.get("y", 0))),
            math.radians(float(rot.get("z", 0))),
        ), 'XYZ')
        target.scale = (
            float(scale.get("x", 1)) * target.scale.x,
            float(scale.get("y", 1)) * target.scale.y,
            float(scale.get("z", 1)) * target.scale.z,
        )
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    elif op_type == "vertex_color":
        name = op.get("name", "Col")
        r = float(op.get("r", 0.8))
        g = float(op.get("g", 0.8))
        b = float(op.get("b", 0.8))
        if target.data.vertex_colors.get(name):
            color_layer = target.data.vertex_colors[name]
        else:
            color_layer = target.data.vertex_colors.new(name=name)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        for loop in color_layer.data:
            loop.color = (r, g, b, 1.0)
        bpy.ops.object.mode_set(mode="OBJECT")

    else:
        raise SystemExit(f"unknown modification type {op_type!r}")

# After stats.
after_meshes = [item for item in bpy.data.objects if item.type == "MESH"]
after_triangles = 0
after_vertices = 0
for item in after_meshes:
    bmesh_data = bmesh.new()
    bmesh_data.from_mesh(item.data)
    after_triangles += sum(len(face.verts) - 2 for face in bmesh_data.faces)
    after_vertices += len(bmesh_data.verts)
    bmesh_data.free()

export_model(destination)
print("MEDIA_STUDIO_JSON " + json.dumps({
    "source": source,
    "destination": destination,
    "operations": [op.get("type") for op in operations],
    "operations_applied": len(operations),
    "before_triangles": before_triangles,
    "before_vertices": before_vertices,
    "after_triangles": after_triangles,
    "after_vertices": after_vertices,
}))
"""
)


@dataclass(frozen=True)
class ModelReport:
    """What a model is, measured by Blender."""

    source: Path
    facts: dict[str, object]

    @property
    def triangles(self) -> int:
        return int(self.facts.get("triangles") or 0)

    @property
    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.facts.get("mesh_objects"):
            found.append("no mesh objects in the file")
        if int(self.facts.get("non_manifold_edges") or 0) > 0:
            found.append(f"{self.facts['non_manifold_edges']} non-manifold edges")
        if int(self.facts.get("loose_vertices") or 0) > 0:
            found.append(f"{self.facts['loose_vertices']} loose vertices")
        return found


def inspect(source: Path) -> ModelReport:
    """Import a model and report its geometry, without touching it."""

    path = Path(source).expanduser()
    if not path.is_file():
        raise BlenderError(f"no such model: {path}")
    if path.suffix.lower() not in SUPPORTED_INPUTS:
        raise BlenderError(
            f"{path.suffix} is not supported; expected one of {', '.join(SUPPORTED_INPUTS)}"
        )
    facts = _report(run_script(INSPECT_SCRIPT, str(path)))
    return ModelReport(source=path, facts=facts)


@dataclass(frozen=True)
class Conversion:
    """A repaired model, with the geometry of the repaired mesh.

    The numbers come from the Blender session that did the repair, not from re-importing the
    exported file: a glTF round trip splits vertices per attribute, so inspecting the output would
    report non-manifold edges that are an artefact of the format rather than of the geometry.
    """

    destination: Path
    facts: dict[str, object]

    @property
    def triangles(self) -> int:
        return int(self.facts.get("triangles") or 0)

    @property
    def problems(self) -> list[str]:
        found: list[str] = []
        if int(self.facts.get("non_manifold_edges") or 0) > 0:
            found.append(f"{self.facts['non_manifold_edges']} non-manifold edges")
        if int(self.facts.get("loose_vertices") or 0) > 0:
            found.append(f"{self.facts['loose_vertices']} loose vertices")
        return found


def convert(source: Path, destination: Path, *, decimate: float = 0.0) -> Conversion:
    """Import, repair, and re-export a model.

    Repair means: apply transforms, recalculate outward normals, and weld duplicate vertices.
    """

    path = Path(source).expanduser()
    target = Path(destination).expanduser()
    if not path.is_file():
        raise BlenderError(f"no such model: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    facts = _report(run_script(NORMALIZE_SCRIPT, str(path), str(target), f"{decimate:.4f}"))
    if not target.is_file():
        raise BlenderError(f"blender did not write {target}")
    return Conversion(destination=target, facts=facts)


@dataclass(frozen=True)
class TurntableResult:
    """A rendered turntable and its measurements."""

    directory: Path
    report_path: Path
    sheet: Path
    facts: dict[str, object]

    @property
    def frames(self) -> list[dict[str, object]]:
        reports = self.facts.get("frame_reports")
        return list(reports) if isinstance(reports, list) else []


def turntable(
    model: Path,
    destination: Path,
    *,
    frames: int = DEFAULT_FRAMES,
    resolution: tuple[int, int] = DEFAULT_RESOLUTION,
    baseline: Path | None = None,
    keep_frames: bool = True,
) -> TurntableResult:
    """Render a turntable headlessly on the GPU, then measure every frame."""

    path = Path(model).expanduser()
    if not path.is_file():
        raise BlenderError(f"no such model: {path}")
    out_dir = Path(destination).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height = resolution

    facts = _report(
        run_script(
            TURNTABLE_SCRIPT,
            str(path),
            str(out_dir),
            str(frames),
            str(width),
            str(height),
            use_gpu=True,
        )
    )

    # Luminance is measured afterwards, with the same helper the video path uses, so the two paths
    # report the same quantity.
    for entry in facts.get("frame_reports", []):
        if not isinstance(entry, dict):
            continue
        frame_path = out_dir / str(entry.get("file") or "")
        if frame_path.is_file():
            entry["mean_luma"] = round(media.mean_luminance_at(frame_path, 0.0), 2)

    sheet = out_dir / "contact-sheet.png"
    _sheet(out_dir, sheet)

    duplicate_frames = _duplicate_frames(facts)
    comparison: dict[str, object] = {"baseline": None}
    if baseline is not None and Path(baseline).is_file():
        comparison = _compare_to_baseline(facts, Path(baseline))

    report = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": str(path),
        "frames": frames,
        "resolution": [width, height],
        "engine": "BLENDER_EEVEE",
        "gpu_backend": "vulkan",
        "environment": environment().describe(),
        "geometry": facts.get("geometry"),
        "frame_reports": facts.get("frame_reports"),
        "checks": {
            "frames_rendered": len(facts.get("frame_reports") or []) == frames,
            "distinct_frames": not duplicate_frames,
            "has_contact_sheet": sheet.is_file(),
            "baseline_matched": bool(comparison.get("matched", True)),
        },
        "duplicate_frames": duplicate_frames,
        "comparison": comparison,
    }
    report["ok"] = all(report["checks"].values())

    report_path = out_dir / "turntable-qc.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not keep_frames:
        for entry in facts.get("frame_reports", []):
            if isinstance(entry, dict):
                (out_dir / str(entry.get("file") or "")).unlink(missing_ok=True)
    return TurntableResult(directory=out_dir, report_path=report_path, sheet=sheet, facts=report)


@dataclass(frozen=True)
class ModifyResult:
    """The result of one model-modification run."""

    output: Path
    """Modified model file."""

    operations_applied: int
    """How many operations ran."""

    before_triangles: int
    after_triangles: int
    before_vertices: int
    after_vertices: int

    facts: dict[str, object]

    @property
    def ok(self) -> bool:
        return self.output.is_file() and self.after_triangles > 0


def modify_model(
    input_path: Path,
    output_path: Path,
    operations: list[dict],
    *,
    object_name: str | None = None,
) -> ModifyResult:
    """Apply procedural mesh modifications headlessly through Blender.

    ``operations`` is a list of dicts, each with a ``type`` key naming one of:
    ``mirror``, ``smooth``, ``subdivide``, ``deform_bend``, ``deform_taper``,
    ``boolean_cut``, ``uv_project``, ``decimate``, ``transform``, ``vertex_color``.
    Operations run in declaration order; the caller is responsible for ordering.

    Returns a :class:`ModifyResult`.
    """

    path = Path(input_path).expanduser()
    if not path.is_file():
        raise BlenderError(f"no such model: {path}")
    if path.suffix.lower() not in SUPPORTED_INPUTS:
        raise BlenderError(
            f"{path.suffix} is not supported; expected one of {', '.join(SUPPORTED_INPUTS)}"
        )
    target = Path(output_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    args = [str(path), str(target), json.dumps(operations)]
    if object_name:
        args.append(object_name)

    facts = _report(run_script(MODIFY_SCRIPT, *args))
    if not target.is_file():
        raise BlenderError(f"blender did not write {target}")
    return ModifyResult(
        output=target,
        operations_applied=int(facts.get("operations_applied") or 0),
        before_triangles=int(facts.get("before_triangles") or 0),
        after_triangles=int(facts.get("after_triangles") or 0),
        before_vertices=int(facts.get("before_vertices") or 0),
        after_vertices=int(facts.get("after_vertices") or 0),
        facts=facts,
    )


def _duplicate_frames(facts: dict[str, object]) -> list[int]:
    """Frames whose pixels repeat an earlier frame, which a turntable should never do."""

    seen: dict[str, int] = {}
    duplicates: list[int] = []
    for entry in facts.get("frame_reports", []):
        if not isinstance(entry, dict):
            continue
        digest = str(entry.get("sha256") or "")
        if not digest:
            continue
        if digest in seen:
            duplicates.append(int(entry.get("frame") or 0))
        else:
            seen[digest] = int(entry.get("frame") or 0)
    return duplicates


LUMA_TOLERANCE = 1.0


def _compare_to_baseline(
    facts: dict[str, object], baseline: Path, *, tolerance: float = LUMA_TOLERANCE
) -> dict[str, object]:
    """Compare this run against a recorded one, frame by frame.

    Mean luminance with a tolerance, not frame hashes: EEVEE's temporal antialiasing makes two
    runs of the same scene differ by a few bits per frame, so hash equality would report a false
    regression on every rerun. Hashes are still recorded, because they catch a *frozen* frame
    inside a single run.
    """

    try:
        recorded = json.loads(baseline.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return {"baseline": str(baseline), "matched": False, "reason": f"unreadable: {error.msg}"}

    recorded_frames = {
        int(entry.get("frame") or 0): float(entry.get("mean_luma") or 0.0)
        for entry in recorded.get("frame_reports") or []
        if isinstance(entry, dict)
    }
    differences: list[dict[str, object]] = []
    identical_hashes = 0
    for entry in facts.get("frame_reports", []):
        if not isinstance(entry, dict):
            continue
        frame = int(entry.get("frame") or 0)
        if frame not in recorded_frames:
            continue
        delta = abs(float(entry.get("mean_luma") or 0.0) - recorded_frames[frame])
        if delta > tolerance:
            differences.append({"frame": frame, "delta_luma": round(delta, 2)})
        recorded_hash = next(
            (
                str(item.get("sha256") or "")
                for item in recorded.get("frame_reports") or []
                if isinstance(item, dict) and int(item.get("frame") or 0) == frame
            ),
            "",
        )
        if recorded_hash and recorded_hash == str(entry.get("sha256") or ""):
            identical_hashes += 1
    matched = bool(recorded_frames) and not differences
    return {
        "baseline": str(baseline),
        "compared_frames": len(recorded_frames),
        "tolerance_luma": tolerance,
        "differing_frames": differences[:10],
        "identical_frame_hashes": identical_hashes,
        "matched": matched,
        "note": "luminance with tolerance; hashes are informational because EEVEE jitters",
    }


def _sheet(directory: Path, destination: Path) -> Path:
    """A contact sheet of the rendered frames."""

    frames = sorted(directory.glob("frame-*.png"))
    if not frames:
        raise BlenderError(f"no rendered frames in {directory}")
    completed = subprocess.run(
        [
            "magick",
            "montage",
            *[str(frame) for frame in frames],
            "-tile",
            "6x",
            "-geometry",
            "240x135+4+4",
            "-background",
            "#111111",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        raise BlenderError(f"could not build a contact sheet: {completed.stderr.strip()[:200]}")
    return destination


def project_output(project: Project, name: str) -> Path:
    """Where 3D artifacts land inside a project."""

    directory = project.root / "out" / "3d"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name


def write_report(project: Project, report: dict[str, object], name: str) -> Path:
    """Write a 3D QC report next to the other reports."""

    directory = project.root / "out" / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path
