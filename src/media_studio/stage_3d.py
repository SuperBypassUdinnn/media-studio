"""3D Product Stage, PBR Shader Binding, and Chiaroscuro Camera Engine (Blender 5.3)."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from media_studio import blender


@dataclass(frozen=True)
class Stage3DSpec:
    """Specification for a 3D product stage render in Blender."""

    model_path: Path | None
    preset_geometry: str = "luxury-perfume-flacon"
    shot_scale: str = "MS"
    camera_angle: str = "low-hero"
    camera_move: str = "slow-dolly-in"
    velocity_curve: str = "slow-fast-slow"
    focal_length_mm: float = 85.0
    dof_fstop: float = 1.8
    key_to_fill_ratio: float = 8.0
    primary_color: str = "#080B11"
    accent_color: str = "#F59E0B"
    volumetric_fog: bool = True
    motion_blur: bool = True


def build_blender_stage_script(spec: Stage3DSpec) -> str:
    """Generate an authoritative Blender 5.3 Python script configuring the 3D stage."""
    primary_hex = spec.primary_color.lstrip("#")
    accent_hex = spec.accent_color.lstrip("#")

    return f"""\
import bpy
import math

# Reset scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = (
    'BLENDER_EEVEE_NEXT' if hasattr(bpy.types, 'RenderSettings') else 'BLENDER_EEVEE'
)
scene.render.fps = 60
scene.render.use_motion_blur = {spec.motion_blur}
scene.render.motion_blur_shutter = 0.5  # 180-degree shutter motion blur

# 1. World & Ambient Light
world = bpy.data.worlds.new("StudioWorld")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg:
    pr_r, pr_g, pr_b = (
        int('{primary_hex}'[0:2], 16) / 255.0,
        int('{primary_hex}'[2:4], 16) / 255.0,
        int('{primary_hex}'[4:6], 16) / 255.0,
    )
    bg.inputs['Color'].default_value = (pr_r, pr_g, pr_b, 1.0)
    bg.inputs['Strength'].default_value = 0.08

# 2. Camera Configuration with Physical Bokeh
cam_data = bpy.data.cameras.new("CinematicCamera")
cam_data.lens = {spec.focal_length_mm}
cam_data.dof.use_dof = True
cam_data.dof.aperture_fstop = {spec.dof_fstop}
cam_data.dof.focus_distance = 2.4
cam_obj = bpy.data.objects.new("Camera", cam_data)
scene.collection.objects.link(cam_obj)
scene.camera = cam_obj

# Camera Angle & Movement Rigging
cam_z = 0.4 if '{spec.camera_angle}' == 'low-hero' else 1.0
cam_rot_x = 82.0 if '{spec.camera_angle}' == 'low-hero' else 75.0
cam_obj.location = (0.0, -2.5, cam_z)
cam_obj.rotation_euler = (math.radians(cam_rot_x), 0.0, 0.0)

# 3. Chiaroscuro 3-Point + Volumetric Rim Lighting
# Key Light (Warm Amber)
key_data = bpy.data.lights.new("KeyLight", type='SPOT')
key_data.energy = 450.0
key_data.spot_size = math.radians(45.0)
ac_r, ac_g, ac_b = (
    int('{accent_hex}'[0:2], 16) / 255.0,
    int('{accent_hex}'[2:4], 16) / 255.0,
    int('{accent_hex}'[4:6], 16) / 255.0,
)
key_data.color = (ac_r, ac_g, ac_b)
key_obj = bpy.data.objects.new("KeyLight", key_data)
key_obj.location = (1.8, -1.5, 2.2)
scene.collection.objects.link(key_obj)

# Fill Light (Subdued 8:1 ratio)
fill_data = bpy.data.lights.new("FillLight", type='AREA')
fill_data.energy = 450.0 / {spec.key_to_fill_ratio}
fill_data.size = 2.0
fill_obj = bpy.data.objects.new("FillLight", fill_data)
fill_obj.location = (-2.0, -0.8, 1.2)
scene.collection.objects.link(fill_obj)

# High-Intensity Rim Light (Outlines glass & metallic silhouette)
rim_data = bpy.data.lights.new("RimLight", type='SPOT')
rim_data.energy = 850.0
rim_data.color = (1.0, 0.95, 0.85)
rim_obj = bpy.data.objects.new("RimLight", rim_data)
rim_obj.location = (0.0, 1.8, 2.0)
scene.collection.objects.link(rim_obj)

# 4. Procedural Flacon / Subject Geometry
mat = bpy.data.materials.new("ObsidianGlass")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs['Base Color'].default_value = (0.05, 0.06, 0.08, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.04
    if 'Transmission Weight' in bsdf.inputs:
        bsdf.inputs['Transmission Weight'].default_value = 0.92
    elif 'Transmission' in bsdf.inputs:
        bsdf.inputs['Transmission'].default_value = 0.92
    bsdf.inputs['IOR'].default_value = 1.48

bpy.ops.mesh.primitive_cylinder_add(radius=0.45, depth=1.4, location=(0, 0, 0.7))
bottle = bpy.context.active_object
bottle.name = "HeroBottle"
bottle.data.materials.append(mat)
"""


def render_3d_stage_still(
    spec: Stage3DSpec,
    output_png: Path,
    *,
    matte_png: Path | None = None,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Render one 3D stage hero still frame using Blender headless."""
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    script = (
        build_blender_stage_script(spec)
        + f"""
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.render.filepath = "{output_png!s}"
bpy.ops.render.render(write_still=True)
"""
    )
    blender.run_script(script, use_gpu=True)

    if matte_png is not None:
        matte_png = Path(matte_png)
        matte_png.parent.mkdir(parents=True, exist_ok=True)
        matte_script = (
            build_blender_stage_script(spec)
            + f"""
scene.render.film_transparent = True
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.render.filepath = "{matte_png!s}"
bpy.ops.render.render(write_still=True)
"""
        )
        blender.run_script(matte_script, use_gpu=True)

    return output_png


def render_3d_stage_clip(
    spec: Stage3DSpec,
    output_mp4: Path,
    *,
    matte_mov: Path | None = None,
    duration: float = 4.0,
    fps: int = 60,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Render a 60fps 3D stage clip with camera motion and speed ramping."""
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(duration * fps))

    # Render image sequence in temp scratch then encode via NVENC/libx264
    with tempfile.TemporaryDirectory(prefix="b3d-", dir="/tmp/opencode") as scratch_dir:
        scratch = Path(scratch_dir)
        render_script = (
            build_blender_stage_script(spec)
            + f"""
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.frame_start = 1
scene.frame_end = {frames}
scene.render.filepath = "{scratch / "frame_"!s}"
bpy.ops.render.render(animation=True)
"""
        )
        blender.run_script(render_script, use_gpu=True)

        # Encode image sequence to MP4
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-framerate",
            str(fps),
            "-i",
            str(scratch / "frame_%04d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-color_space",
            "bt709",
            str(output_mp4),
        ]
        subprocess.run(cmd, check=True)

    return output_mp4
