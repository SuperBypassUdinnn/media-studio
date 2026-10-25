"""PBR materials and HDRIs: fetch from a keyless source, then bind them in Blender.

Poly Haven's public API needs no key and states a licence of CC0 for everything it serves, so a
material or an environment can be fetched, staged through the ledger with a sidecar, and bound to a
model in one command.

Binding is deliberately explicit: the base colour, roughness, normal, and ambient-occlusion maps are
wired into a Principled BSDF with the correct colour spaces (only base colour is sRGB; the rest are
non-colour data), and the world is set from the HDRI.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import media
from .blender import BlenderError, _report, run_script
from .ledger import StagedAsset, stage_asset, write_index_and_credits
from .manifest import Project

API_ROOT = "https://api.polyhaven.com/files"
SOURCE_PAGE = "https://polyhaven.com/a"
LICENCE = "CC0"
USER_AGENT = "media-studio/0.1 (local asset ledger)"
# Roles that are not texture maps, and the roles we wire into the shader.
NON_TEXTURE_ROLES = ("blend", "gltf", "mtlx")
BINDABLE = {
    "Diffuse": ("base_color", "sRGB"),
    "Rough": ("roughness", "Non-Color"),
    "nor_gl": ("normal", "Non-Color"),
    "AO": ("ambient_occlusion", "Non-Color"),
}
# One authority for the slot vocabulary and each slot's colour space, used both when serialising
# the plan for Blender and when translating source role names.
COLOUR_SPACES = {slot: space for slot, space in BINDABLE.values()}
SHADER_SLOTS = tuple(COLOUR_SPACES)
FORMAT_PREFERENCE = ("jpg", "png", "exr")
MATERIAL_RESOLUTION = "1k"
HDRI_RESOLUTION = "1k"


class MaterialError(RuntimeError):
    """Raised when a material cannot be fetched or bound."""


@dataclass(frozen=True)
class MaterialSet:
    """One material's maps, already staged inside a project."""

    asset_id: str
    maps: dict[str, Path]
    staged: tuple[StagedAsset, ...] = field(default=())

    @property
    def bound_roles(self) -> list[str]:
        return sorted(role for role in self.maps if role in BINDABLE)

    @property
    def ignored_roles(self) -> list[str]:
        return sorted(role for role in self.maps if role not in BINDABLE)


def asset_name(value: str) -> str:
    """Accept the aggregator's namespaced id (`polyhaven:mossy_rock`) as well as a bare one."""

    name = value.split(":", 1)[1] if ":" in value else value
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise MaterialError(f"not a usable asset id: {value!r} (letters, digits, _ and - only)")
    return name


def shader_maps(maps: dict[str, object]) -> dict[str, Path]:
    """Translate source role names (Diffuse, Rough, nor_gl, AO) into shader slots.

    A key that is already a slot name passes through, so callers can hand over either vocabulary.
    Roles with no slot — Displacement, the packed arm map, DirectX normals — are dropped, and the
    caller reports them as ignored rather than pretending they were used.
    """

    translated: dict[str, Path] = {}
    for role, (slot, _space) in BINDABLE.items():
        for key in (slot, role):
            if key in maps:
                translated[slot] = Path(str(maps[key]))
                break
    return translated


def get_json(url: str, *, opener: object = urllib.request.urlopen) -> dict[str, object]:
    """Fetch and decode JSON, with the project's user agent."""

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=60) as response:  # type: ignore[operator]
            payload = response.read()
    except OSError as error:  # URLError and TimeoutError are OSError subclasses
        raise MaterialError(f"could not read {url}: {error}") from error
    try:
        decoded = json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        raise MaterialError(f"{url} did not answer JSON: {error.msg}") from error
    if not isinstance(decoded, dict):
        raise MaterialError(f"{url} answered {type(decoded).__name__}, expected an object")
    return decoded


def material_maps(
    files: dict[str, object], resolution: str = MATERIAL_RESOLUTION
) -> dict[str, str]:
    """Pick one URL per texture role, preferring jpg over png over exr."""

    maps: dict[str, str] = {}
    for role, by_resolution in files.items():
        if role in NON_TEXTURE_ROLES or not isinstance(by_resolution, dict):
            continue
        entry = by_resolution.get(resolution)
        if not isinstance(entry, dict):
            continue
        for format_name in FORMAT_PREFERENCE:
            candidate = entry.get(format_name)
            if isinstance(candidate, dict) and candidate.get("url"):
                maps[str(role)] = str(candidate["url"])
                break
    if not maps:
        raise MaterialError(f"no textures at {resolution} in this material")
    return maps


def hdri_url(files: dict[str, object], resolution: str = HDRI_RESOLUTION, fmt: str = "hdr") -> str:
    """The HDRI file to download for a resolution."""

    hdri = files.get("hdri")
    if not isinstance(hdri, dict):
        raise MaterialError("this asset has no HDRI files")
    entry = hdri.get(resolution)
    if not isinstance(entry, dict):
        available = ", ".join(sorted(hdri)[:6])
        raise MaterialError(f"no {resolution} HDRI; available: {available}")
    chosen = entry.get(fmt) or next(
        (value for value in entry.values() if isinstance(value, dict) and value.get("url")), None
    )
    if not isinstance(chosen, dict) or not chosen.get("url"):
        raise MaterialError(f"no {fmt} file at {resolution}")
    return str(chosen["url"])


def fetch_material(
    project: Project,
    asset_id: str,
    *,
    resolution: str = MATERIAL_RESOLUTION,
    files: dict[str, object] | None = None,
    opener: object = urllib.request.urlopen,
) -> MaterialSet:
    """Download every map of a material into the project, with a sidecar each."""

    name = asset_name(asset_id)
    payload = files if files is not None else get_json(f"{API_ROOT}/{name}", opener=opener)
    urls = material_maps(payload, resolution)
    staged: list[StagedAsset] = []
    local: dict[str, Path] = {}
    for role, url in sorted(urls.items()):
        asset = stage_asset(
            project,
            class_name="3d",
            license_text=LICENCE,
            url=url,
            source_url=f"{SOURCE_PAGE}/{name}",
            provider="polyhaven",
            attribution="Poly Haven",
            opener=opener,
        )
        staged.append(asset)
        local[role] = asset.path
    if staged:
        # Staging is not done until the derived index and CREDITS reflect it.
        write_index_and_credits(project)
    return MaterialSet(asset_id=name, maps=local, staged=tuple(staged))


def fetch_hdri(
    project: Project,
    asset_id: str,
    *,
    resolution: str = HDRI_RESOLUTION,
    files: dict[str, object] | None = None,
    opener: object = urllib.request.urlopen,
) -> StagedAsset:
    """Download one HDRI into the project, with its sidecar."""

    name = asset_name(asset_id)
    payload = files if files is not None else get_json(f"{API_ROOT}/{name}", opener=opener)
    url = hdri_url(payload, resolution)
    asset = stage_asset(
        project,
        class_name="3d",
        license_text=LICENCE,
        url=url,
        source_url=f"{SOURCE_PAGE}/{name}",
        provider="polyhaven",
        attribution="Poly Haven",
        opener=opener,
    )
    write_index_and_credits(project)
    return asset


BIND_SCRIPT = """
import bpy, json, math, os, sys
from mathutils import Vector

arguments = sys.argv[sys.argv.index("--") + 1:]
source, destination, maps_json, hdri, render_path, width, height = arguments[:7]
maps = json.loads(maps_json)

bpy.ops.wm.read_factory_settings(use_empty=True)
if source.lower().endswith((".glb", ".gltf")):
    bpy.ops.import_scene.gltf(filepath=source)
elif source.lower().endswith(".obj"):
    bpy.ops.wm.obj_import(filepath=source)
elif source.lower().endswith(".stl"):
    bpy.ops.wm.stl_import(filepath=source)
elif source.lower().endswith(".ply"):
    bpy.ops.wm.ply_import(filepath=source)
elif source.lower().endswith(".fbx"):
    bpy.ops.import_scene.fbx(filepath=source)
else:
    raise SystemExit("unsupported input: " + source)

meshes = [item for item in bpy.data.objects if item.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh to bind a material to")

material = bpy.data.materials.new("pbr")
material.use_nodes = True
tree = material.node_tree
nodes, links = tree.nodes, tree.links
nodes.clear()
output = nodes.new("ShaderNodeOutputMaterial")
shader = nodes.new("ShaderNodeBsdfPrincipled")
links.new(shader.outputs["BSDF"], output.inputs["Surface"])

bound = []
unwired = []
targets = {"base_color": "Base Color", "roughness": "Roughness"}

images = {}
for slot, spec in maps.items():
    image = bpy.data.images.load(spec["path"], check_existing=True)
    image.colorspace_settings.name = spec["colorspace"]
    images[slot] = image

wired = {}
for slot in ("base_color", "roughness", "normal"):
    if slot not in images:
        continue
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = images[slot]
    if slot == "normal":
        normal_map = nodes.new("ShaderNodeNormalMap")
        links.new(texture.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], shader.inputs["Normal"])
    else:
        links.new(texture.outputs["Color"], shader.inputs[targets[slot]])
    wired[slot] = texture
    bound.append(slot)

if "ambient_occlusion" in images:
    if "base_color" in wired:
        # Occlusion multiplies the base colour, which is how these maps are meant to be used. The
        # mix node is named rather than indexed: MixRGB's sockets are Factor, Color1, Color2, Color.
        occlusion = nodes.new("ShaderNodeTexImage")
        occlusion.image = images["ambient_occlusion"]
        mix = nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        links.new(wired["base_color"].outputs["Color"], mix.inputs["Color1"])
        links.new(occlusion.outputs["Color"], mix.inputs["Color2"])
        links.new(mix.outputs["Color"], shader.inputs["Base Color"])
        wired["ambient_occlusion"] = occlusion
        bound.append("ambient_occlusion")
    else:
        # Occlusion alone has nothing to modulate, and grey data in a colour slot would tint the
        # surface, so it is reported as unwired instead of being linked anywhere.
        unwired.append("ambient_occlusion")

for item in meshes:
    item.data.materials.clear()
    item.data.materials.append(material)

world_set = False
if hdri:
    world = bpy.data.worlds.new("hdri")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    environment = world.node_tree.nodes.new("ShaderNodeTexEnvironment")
    environment.image = bpy.data.images.load(hdri, check_existing=True)
    world.node_tree.links.new(environment.outputs["Color"], background.inputs["Color"])
    background.inputs[1].default_value = 1.0
    world_set = True

scene = bpy.context.scene
if render_path:
    scene.render.resolution_x, scene.render.resolution_y = int(width), int(height)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = render_path
    corners = []
    for item in meshes:
        for corner in item.bound_box:
            corners.append(item.matrix_world @ Vector(corner))
    centre = sum(corners, Vector((0, 0, 0))) / len(corners) if corners else Vector((0, 0, 0))
    radius = max((point - centre).length for point in corners) if corners else 1.0
    camera_data = bpy.data.cameras.new("camera")
    camera = bpy.data.objects.new("camera", camera_data)
    scene.collection.objects.link(camera)
    # Aim the camera at the mesh centre instead of a fixed angle, so the proof frame shows the
    # whole object whatever its size.
    offset = Vector((0.55, -1.0, 0.45)).normalized() * (radius * 3.9)
    camera.location = centre + offset
    camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    if not world_set:
        sun_data = bpy.data.lights.new("key", type="SUN")
        sun_data.energy = 4.0
        sun = bpy.data.objects.new("key", sun_data)
        scene.collection.objects.link(sun)
        sun.rotation_euler = (math.radians(55), 0, math.radians(35))
    bpy.ops.render.render(write_still=True)

if destination:
    extension = os.path.splitext(destination)[1].lower()
    if extension in (".glb", ".gltf"):
        bpy.ops.export_scene.gltf(filepath=destination, export_format=extension.lstrip(".").upper())
    elif extension == ".obj":
        bpy.ops.wm.obj_export(filepath=destination)

report = {
    "source": source,
    "destination": destination,
    "bound_roles": bound,
    "unwired_slots": unwired,
    "shader_nodes": len(nodes),
    "images": sorted(image.name for image in bpy.data.images if image.name != "Render Result"),
    "world_from_hdri": world_set,
    "rendered": os.path.exists(render_path) if render_path else False,
    "render_path": render_path if render_path else None,
}
print("MEDIA_STUDIO_JSON " + json.dumps(report))
"""


@dataclass(frozen=True)
class BindResult:
    """What a material binding produced."""

    destination: Path | None
    render: Path | None
    facts: dict[str, object]

    @property
    def bound_roles(self) -> list[str]:
        return list(self.facts.get("bound_roles") or [])

    @property
    def unwired_slots(self) -> list[str]:
        """Slots a map was supplied for but the shader could not use, such as occlusion alone."""

        return list(self.facts.get("unwired_slots") or [])

    @property
    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.bound_roles:
            found.append("no texture was wired into the shader")
        if self.render is not None and not self.render.is_file():
            found.append(f"the proof render was not written to {self.render}")
        return found


def bind(
    model: Path,
    maps: dict[str, Path],
    *,
    destination: Path | None = None,
    hdri: Path | None = None,
    render: Path | None = None,
    resolution: tuple[int, int] = (640, 360),
    timeout: int = 3600,
) -> BindResult:
    """Wire maps into a Principled BSDF, set the world from an HDRI, export and/or render."""

    source = Path(model).expanduser()
    if not source.is_file():
        raise BlenderError(f"no such model: {source}")
    if not maps:
        raise MaterialError("no maps to bind")

    for role, path in maps.items():
        if not Path(path).is_file():
            raise MaterialError(f"{role} map is missing: {path}")

    slots = shader_maps(maps)
    if not slots:
        raise MaterialError(
            "none of these maps has a shader slot: " + ", ".join(sorted(str(role) for role in maps))
        )

    serialised = json.dumps(
        {
            slot: {"path": str(path), "colorspace": COLOUR_SPACES[slot]}
            for slot, path in slots.items()
        }
    )
    if render is not None:
        Path(render).parent.mkdir(parents=True, exist_ok=True)
    if destination is not None:
        Path(destination).parent.mkdir(parents=True, exist_ok=True)

    facts = _report(
        run_script(
            BIND_SCRIPT,
            str(source),
            str(destination) if destination else "",
            serialised,
            str(hdri) if hdri else "",
            str(render) if render else "",
            str(resolution[0]),
            str(resolution[1]),
            timeout=timeout,
        )
    )
    return BindResult(destination=destination, render=render, facts=facts)


def proof_frame_luminance(path: Path) -> float:
    """Mean luminance of a proof render, so a test can tell it is not an empty frame."""

    return media.mean_luminance_at(path, 0.0)
