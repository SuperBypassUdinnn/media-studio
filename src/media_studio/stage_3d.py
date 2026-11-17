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


def build_blender_stage_script(spec: Stage3DSpec, *, frames: int | None = None) -> str:
    """Generate an authoritative Blender 5.3 Python script configuring the 3D stage."""
    primary_hex = spec.primary_color.lstrip("#")
    accent_hex = spec.accent_color.lstrip("#")

    anim_snippet = ""
    if frames is not None and frames > 1:
        anim_snippet = f"""
# Camera Dynamic Motion Keyframes
if '{spec.camera_move}' == 'slow-dolly-in':
    start_cam = (cam_loc[0] * 1.15, cam_loc[1] * 1.15, cam_loc[2] * 1.08)
    end_cam = (cam_loc[0] * 0.90, cam_loc[1] * 0.90, cam_loc[2] * 0.95)
elif '{spec.camera_move}' == 'orbital-reveal':
    r = math.hypot(cam_loc[0], cam_loc[1])
    theta = math.atan2(cam_loc[0], cam_loc[1])
    start_cam = (r * math.sin(theta - 0.22), r * math.cos(theta - 0.22), cam_loc[2])
    end_cam = (r * math.sin(theta + 0.22), r * math.cos(theta + 0.22), cam_loc[2])
elif '{spec.camera_move}' == 'macro-surface-pan':
    start_cam = (cam_loc[0] - 0.22, cam_loc[1] * 1.05, cam_loc[2] - 0.06)
    end_cam = (cam_loc[0] + 0.22, cam_loc[1] * 0.95, cam_loc[2] + 0.06)
else:
    start_cam = (cam_loc[0] - 0.08, cam_loc[1] * 1.06, cam_loc[2])
    end_cam = (cam_loc[0] + 0.08, cam_loc[1] * 0.96, cam_loc[2])

cam_obj.location = start_cam
cam_obj.keyframe_insert(data_path="location", frame=1)
cam_obj.location = end_cam
cam_obj.keyframe_insert(data_path="location", frame={frames})

if cam_obj.animation_data and cam_obj.animation_data.action:
    fcurves = getattr(cam_obj.animation_data.action, "fcurves", None)
    if fcurves:
        for fc in fcurves:
            for kf in fc.keyframe_points:
                kf.interpolation = 'BEZIER'
                kf.easing = 'EASE_IN_OUT'
"""

    return f"""\
import bpy
import math

# Reset scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
if hasattr(scene, 'eevee') and hasattr(scene.eevee, 'taa_render_samples'):
    scene.eevee.taa_render_samples = 16
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
    ac_r, ac_g, ac_b = (
        int('{accent_hex}'[0:2], 16) / 255.0,
        int('{accent_hex}'[2:4], 16) / 255.0,
        int('{accent_hex}'[4:6], 16) / 255.0,
    )
    bg.inputs['Color'].default_value = (pr_r, pr_g, pr_b, 1.0)
    bg.inputs['Strength'].default_value = 0.25

# 2. Materials
glass_mat = bpy.data.materials.new("ObsidianGlass")
glass_mat.use_nodes = True
bsdf = glass_mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs['Base Color'].default_value = (0.06, 0.08, 0.12, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.04
    if 'Transmission Weight' in bsdf.inputs:
        bsdf.inputs['Transmission Weight'].default_value = 0.85
    elif 'Transmission' in bsdf.inputs:
        bsdf.inputs['Transmission'].default_value = 0.85
    bsdf.inputs['IOR'].default_value = 1.50

gold_mat = bpy.data.materials.new("GoldMetal")
gold_mat.use_nodes = True
g_bsdf = gold_mat.node_tree.nodes.get("Principled BSDF")
if g_bsdf:
    g_bsdf.inputs['Base Color'].default_value = (ac_r, ac_g, ac_b, 1.0)
    g_bsdf.inputs['Metallic'].default_value = 1.0
    g_bsdf.inputs['Roughness'].default_value = 0.18

ped_mat = bpy.data.materials.new("Pedestal")
ped_mat.use_nodes = True
p_bsdf = ped_mat.node_tree.nodes.get("Principled BSDF")
if p_bsdf:
    p_bsdf.inputs['Base Color'].default_value = (0.08, 0.10, 0.14, 1.0)
    p_bsdf.inputs['Roughness'].default_value = 0.18

# 3. Geometry: Pedestal, Bottle, Gold Collar, Cap, Plate
bpy.ops.mesh.primitive_cylinder_add(radius=1.8, depth=0.15, location=(0, 0, -0.075))
ped = bpy.context.active_object
ped.name = "Pedestal"
ped.data.materials.append(ped_mat)
bpy.ops.object.shade_smooth()

bpy.ops.mesh.primitive_cylinder_add(radius=0.35, depth=0.9, location=(0, 0, 0.45))
bottle = bpy.context.active_object
bottle.name = "HeroBottle"
bottle.data.materials.append(glass_mat)
bpy.ops.object.shade_smooth()

bpy.ops.mesh.primitive_cylinder_add(radius=0.16, depth=0.18, location=(0, 0, 0.99))
collar = bpy.context.active_object
collar.data.materials.append(gold_mat)
bpy.ops.object.shade_smooth()

bpy.ops.mesh.primitive_cube_add(size=0.32, location=(0, 0, 1.22))
cap = bpy.context.active_object
cap.data.materials.append(glass_mat)
bpy.ops.object.shade_smooth()

bpy.ops.mesh.primitive_cube_add(size=0.35, location=(0, -0.36, 0.45))
plate = bpy.context.active_object
plate.scale = (0.7, 0.02, 1.1)
plate.data.materials.append(gold_mat)

# 4. Camera & Dynamic Aim Target
target = bpy.data.objects.new("LookTarget", None)
scene.collection.objects.link(target)

if '{spec.shot_scale}' == 'ECU':
    target.location = (0.0, -0.36, 0.65)
    cam_loc = (0.0, -2.6, 0.75)
elif '{spec.shot_scale}' == 'CU':
    target.location = (0.0, 0.0, 1.05)
    cam_loc = (0.0, -3.2, 1.25)
elif '{spec.shot_scale}' == 'WS':
    target.location = (0.0, 0.0, 0.55)
    cam_loc = (0.0, -7.5, 1.4)
else:  # MS
    target.location = (0.0, 0.0, 0.68)
    cam_loc = (0.0, -4.5, 0.85)

if '{spec.camera_angle}' == 'low-hero':
    cam_loc = (cam_loc[0], cam_loc[1] * 0.95, cam_loc[2] * 0.75)
elif '{spec.camera_angle}' == 'high-zenith':
    cam_loc = (cam_loc[0], cam_loc[1] * 0.75, cam_loc[2] * 1.8)

cam_data = bpy.data.cameras.new("CinematicCamera")
cam_data.lens = {spec.focal_length_mm}
cam_data.dof.use_dof = True
cam_data.dof.aperture_fstop = {spec.dof_fstop}
cam_data.dof.focus_distance = 2.4
cam_obj = bpy.data.objects.new("Camera", cam_data)
scene.collection.objects.link(cam_obj)
scene.camera = cam_obj
cam_obj.location = cam_loc

track = cam_obj.constraints.new(type='TRACK_TO')
track.target = target
track.track_axis = 'TRACK_NEGATIVE_Z'
track.up_axis = 'UP_Y'
{anim_snippet}

# 5. Chiaroscuro 3-Point Studio Lighting
key_data = bpy.data.lights.new("KeyLight", type='SPOT')
key_data.energy = 5500.0
key_data.spot_size = math.radians(52.0)
key_data.color = (ac_r, ac_g, ac_b)
key_obj = bpy.data.objects.new("KeyLight", key_data)
key_obj.location = (1.8, -2.2, 1.8)
scene.collection.objects.link(key_obj)

fill_data = bpy.data.lights.new("FillLight", type='AREA')
fill_data.energy = 5500.0 / {spec.key_to_fill_ratio}
fill_data.size = 2.5
fill_obj = bpy.data.objects.new("FillLight", fill_data)
fill_obj.location = (-2.2, -1.2, 1.2)
scene.collection.objects.link(fill_obj)

rim_data = bpy.data.lights.new("RimLight", type='SPOT')
rim_data.energy = 9500.0
rim_data.spot_size = math.radians(65.0)
rim_data.color = (1.0, 0.95, 0.85)
rim_obj = bpy.data.objects.new("RimLight", rim_data)
rim_obj.location = (0.0, 2.0, 2.2)
scene.collection.objects.link(rim_obj)
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
if 'Pedestal' in [o.name for o in scene.objects]:
    scene.objects['Pedestal'].hide_render = True
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
            build_blender_stage_script(spec, frames=frames)
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
            "-colorspace",
            "bt709",
            str(output_mp4),
        ]
        subprocess.run(cmd, check=True)

    return output_mp4
