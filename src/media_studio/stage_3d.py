"""3D Product Stage, PBR Shader Binding, and Chiaroscuro Camera Engine (Blender 5.3)."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    typography_3d: dict[str, Any] | None = None
    product_rotation_deg: float = 0.0


def build_blender_stage_script(spec: Stage3DSpec, *, frames: int | None = None) -> str:
    """Generate an authoritative Blender 5.3 Python script configuring the 3D stage."""
    primary_hex = spec.primary_color.lstrip("#")
    accent_hex = spec.accent_color.lstrip("#")

    typo_snippet = ""
    if spec.typography_3d and spec.typography_3d.get("text"):
        typo_text = str(spec.typography_3d["text"])
        typo_sub = str(spec.typography_3d.get("subtext", ""))
        font_style = str(spec.typography_3d.get("font_style", "serif-bold"))
        declared_font = str(spec.typography_3d.get("font", "serif-bold"))

        font_map = {
            "serif-bold": "/usr/share/fonts/noto/NotoSerif-Bold.ttf",
            "serif-monumental": "/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",
            "serif-editorial": "/usr/share/fonts/liberation/LiberationSerif-Bold.ttf",
            "serif-italic": "/usr/share/fonts/liberation/LiberationSerif-Italic.ttf",
            "serif-regular": "/usr/share/fonts/liberation/LiberationSerif-Regular.ttf",
            "Noto Serif": "/usr/share/fonts/noto/NotoSerif-Bold.ttf",
            "DejaVu Serif": "/usr/share/fonts/TTF/DejaVuSerif-Bold.ttf",
            "Liberation Serif": "/usr/share/fonts/liberation/LiberationSerif-Bold.ttf",
        }
        main_font_file = font_map.get(
            declared_font,
            font_map.get(font_style, "/usr/share/fonts/noto/NotoSerif-Bold.ttf"),
        )
        sub_font_file = "/usr/share/fonts/liberation/LiberationSerif-Regular.ttf"

        z_pos = float(spec.typography_3d.get("z_pos", 0.58))
        y_pos = float(
            spec.typography_3d.get(
                "y_pos",
                0.45 if spec.typography_3d.get("z_layer") == "behind-subject" else -0.45,
            )
        )
        scale_val = float(spec.typography_3d.get("scale", 0.28))
        tracking = float(spec.typography_3d.get("tracking", 1.25))

        typo_snippet = f"""
# True 3D Text Geometry (Parented to ProductRoot so it rotates in 3D with the flacon!)
font_main_path = Path("{main_font_file}")
bpy.ops.object.text_add(location=(0, {y_pos}, {z_pos}))
title_3d = bpy.context.active_object
title_3d.name = "Scene3DTitle"
title_3d.data.body = "{typo_text}"
title_3d.data.extrude = 0.16
title_3d.data.bevel_depth = 0.015
title_3d.data.bevel_resolution = 4
title_3d.data.align_x = 'CENTER'
title_3d.data.align_y = 'CENTER'
title_3d.data.space_character = {tracking}
if font_main_path.is_file():
    title_3d.data.font = bpy.data.fonts.load(str(font_main_path))
title_3d.rotation_euler = (math.radians(90.0), 0.0, 0.0)
title_3d.scale = ({scale_val}, {scale_val}, {scale_val})
title_3d.data.materials.append(gold_mat)
title_3d.parent = root_obj
"""
        if typo_sub:
            sub_z = z_pos - scale_val * 0.75
            typo_snippet += f"""
font_sub_path = Path("{sub_font_file}")
bpy.ops.object.text_add(location=(0, {y_pos}, {sub_z}))
sub_3d = bpy.context.active_object
sub_3d.name = "Scene3DSub"
sub_3d.data.body = "{typo_sub}"
sub_3d.data.extrude = 0.08
sub_3d.data.bevel_depth = 0.008
sub_3d.data.align_x = 'CENTER'
sub_3d.data.align_y = 'CENTER'
sub_3d.data.space_character = 1.40
if font_sub_path.is_file():
    sub_3d.data.font = bpy.data.fonts.load(str(font_sub_path))
sub_3d.rotation_euler = (math.radians(90.0), 0.0, 0.0)
sub_3d.scale = ({scale_val * 0.4}, {scale_val * 0.4}, {scale_val * 0.4})
sub_3d.data.materials.append(gold_mat)
sub_3d.parent = root_obj
"""

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

# Synchronized 3D Product & Text Rotation
root_start_rot = math.radians(-10.0 if '{spec.camera_move}' == 'orbital-reveal' else -4.0)
root_end_rot = math.radians(22.0 if '{spec.camera_move}' == 'orbital-reveal' else 14.0)
root_obj.rotation_euler = (0.0, 0.0, root_start_rot)
root_obj.keyframe_insert(data_path="rotation_euler", frame=1)
root_obj.rotation_euler = (0.0, 0.0, root_end_rot)
root_obj.keyframe_insert(data_path="rotation_euler", frame={frames})

for anim_target in (cam_obj, root_obj):
    if anim_target.animation_data and anim_target.animation_data.action:
        fcurves = getattr(anim_target.animation_data.action, "fcurves", None)
        if fcurves:
            for fc in fcurves:
                for kf in fc.keyframe_points:
                    kf.interpolation = 'BEZIER'
                    kf.easing = 'EASE_IN_OUT'
"""

    return f"""\
import bpy
import math
from pathlib import Path

# Reset scene
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
if hasattr(scene, 'eevee') and hasattr(scene.eevee, 'taa_render_samples'):
    scene.eevee.taa_render_samples = 32
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
    bg.inputs['Color'].default_value = (0.012, 0.016, 0.024, 1.0)
    bg.inputs['Strength'].default_value = 0.25

# 2. Materials
# Deep Midnight Obsidian Glass
obs_mat = bpy.data.materials.new("ObsidianGlass")
obs_mat.use_nodes = True
bsdf_obs = obs_mat.node_tree.nodes.get("Principled BSDF")
if bsdf_obs:
    bsdf_obs.inputs['Base Color'].default_value = (0.002, 0.002, 0.003, 1.0)
    bsdf_obs.inputs['Roughness'].default_value = 0.02
    bsdf_obs.inputs['Metallic'].default_value = 0.05
    if 'Specular IOR Level' in bsdf_obs.inputs:
        bsdf_obs.inputs['Specular IOR Level'].default_value = 0.5
    elif 'Specular' in bsdf_obs.inputs:
        bsdf_obs.inputs['Specular'].default_value = 0.5
    if 'IOR' in bsdf_obs.inputs:
        bsdf_obs.inputs['IOR'].default_value = 1.50

# Brushed 24K Gold Metal
gold_mat = bpy.data.materials.new("Gold24K")
gold_mat.use_nodes = True
bsdf_g = gold_mat.node_tree.nodes.get("Principled BSDF")
if bsdf_g:
    bsdf_g.inputs['Base Color'].default_value = (ac_r, ac_g, ac_b, 1.0)
    bsdf_g.inputs['Metallic'].default_value = 1.0
    bsdf_g.inputs['Roughness'].default_value = 0.16

# Engraved Inlay Dark Text Material
engraved_mat = bpy.data.materials.new("EngravedInlay")
engraved_mat.use_nodes = True
bsdf_e = engraved_mat.node_tree.nodes.get("Principled BSDF")
if bsdf_e:
    bsdf_e.inputs['Base Color'].default_value = (0.005, 0.005, 0.008, 1.0)
    bsdf_e.inputs['Roughness'].default_value = 0.2

# Dark Slate Pedestal Material
ped_mat = bpy.data.materials.new("PedestalSlate")
ped_mat.use_nodes = True
bsdf_p = ped_mat.node_tree.nodes.get("Principled BSDF")
if bsdf_p:
    bsdf_p.inputs['Base Color'].default_value = (0.015, 0.02, 0.03, 1.0)
    bsdf_p.inputs['Roughness'].default_value = 0.35

# 3. Product Assembly Root (Synchronizes 3D rotation of bottle + plaque + 3D text!)
root_obj = bpy.data.objects.new("ProductRoot", None)
scene.collection.objects.link(root_obj)
root_obj.location = (0, 0, 0)

# Flacon Body: Dimensions (Width=0.55, Depth=0.30, Height=0.75) centered at z=0.455
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0.455))
bottle = bpy.context.active_object
bottle.name = "HeroBottle"
bottle.scale = (0.55, 0.30, 0.75)
bottle.data.materials.append(obs_mat)
bpy.ops.object.shade_smooth()
bev = bottle.modifiers.new("Bevel", type='BEVEL')
bev.width = 0.035
bev.segments = 5
bottle.parent = root_obj

# Gold Neck Collar
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=0.14, depth=0.14, location=(0, 0, 0.90))
collar = bpy.context.active_object
collar.name = "GoldCollar"
collar.data.materials.append(gold_mat)
bpy.ops.object.shade_smooth()
c_bev = collar.modifiers.new("Bevel", type='BEVEL')
c_bev.width = 0.015
c_bev.segments = 3
collar.parent = root_obj

# Obsidian Magnetic Cap
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=0.18, depth=0.35, location=(0, 0, 1.145))
cap = bpy.context.active_object
cap.name = "CapOnyx"
cap.data.materials.append(obs_mat)
bpy.ops.object.shade_smooth()
cap_bev = cap.modifiers.new("Bevel", type='BEVEL')
cap_bev.width = 0.02
cap_bev.segments = 4
cap.parent = root_obj

# Gold Ring on Cap Base
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=0.184, depth=0.03, location=(0, 0, 0.985))
cap_ring = bpy.context.active_object
cap_ring.data.materials.append(gold_mat)
cap_ring.parent = root_obj

# Gold Cartouche Plaque on Front (Flush on front face at y = -0.152)
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, -0.152, 0.455))
plaque = bpy.context.active_object
plaque.name = "GoldPlaque"
plaque.scale = (0.34, 0.005, 0.46)
plaque.data.materials.append(gold_mat)
p_bev = plaque.modifiers.new("Bevel", type='BEVEL')
p_bev.width = 0.01
p_bev.segments = 3
plaque.parent = root_obj

# Embossed Text on Plaque
font_main = Path("/usr/share/fonts/noto/NotoSerif-Bold.ttf")
font_sub = Path("/usr/share/fonts/liberation/LiberationSerif-Regular.ttf")

bpy.ops.object.text_add(location=(0, -0.156, 0.49))
pq_txt = bpy.context.active_object
pq_txt.data.body = "AURA NOIR"
pq_txt.data.extrude = 0.008
pq_txt.data.align_x = 'CENTER'
pq_txt.data.align_y = 'CENTER'
pq_txt.data.space_character = 1.35
if font_main.is_file():
    pq_txt.data.font = bpy.data.fonts.load(str(font_main))
pq_txt.rotation_euler = (math.radians(90.0), 0.0, 0.0)
pq_txt.scale = (0.045, 0.045, 0.045)
pq_txt.data.materials.append(engraved_mat)
pq_txt.parent = root_obj

bpy.ops.object.text_add(location=(0, -0.156, 0.39))
pq_sub = bpy.context.active_object
pq_sub.data.body = "EXTRAIT DE PARFUM"
pq_sub.data.extrude = 0.005
pq_sub.data.align_x = 'CENTER'
pq_sub.data.align_y = 'CENTER'
pq_sub.data.space_character = 1.45
if font_sub.is_file():
    pq_sub.data.font = bpy.data.fonts.load(str(font_sub))
pq_sub.rotation_euler = (math.radians(90.0), 0.0, 0.0)
pq_sub.scale = (0.022, 0.022, 0.022)
pq_sub.data.materials.append(engraved_mat)
pq_sub.parent = root_obj

{typo_snippet}

# Pedestal
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=1.6, depth=0.10, location=(0, 0, -0.05))
ped = bpy.context.active_object
ped.name = "Pedestal"
ped.data.materials.append(ped_mat)
bpy.ops.object.shade_smooth()
ped_bev = ped.modifiers.new("Bevel", type='BEVEL')
ped_bev.width = 0.02
ped_bev.segments = 3

# Static 3D Product Angle for Stills
root_obj.rotation_euler = (0.0, 0.0, math.radians({spec.product_rotation_deg}))

# 4. Camera & Dynamic Aim Target
target = bpy.data.objects.new("LookTarget", None)
scene.collection.objects.link(target)
target.location = (0, 0, 0.58)

if '{spec.shot_scale}' == 'ECU':
    target.location = (0.0, 0.0, 0.85)
    cam_loc = (0.0, -2.0, 1.05)
elif '{spec.shot_scale}' == 'CU':
    target.location = (0.0, 0.0, 0.70)
    cam_loc = (0.0, -3.0, 0.95)
elif '{spec.shot_scale}' == 'WS':
    target.location = (0.0, 0.0, 0.55)
    cam_loc = (0.0, -5.8, 1.1)
else:  # MS
    target.location = (0.0, 0.0, 0.65)
    cam_loc = (0.0, -3.6, 0.85)

if '{spec.camera_angle}' == 'low-hero':
    cam_loc = (cam_loc[0], cam_loc[1] * 0.95, cam_loc[2] * 0.70)
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

# 5. Chiaroscuro Studio Lighting (Side Grazing Strips + Flush Plaque Spot)
# Left Strip Light
strip_l = bpy.data.lights.new("StripL", type='AREA')
strip_l.energy = 220.0
strip_l.size = 0.15
strip_l.size_y = 2.4
strip_l.color = (1.0, 0.95, 0.90)
strip_l_obj = bpy.data.objects.new("StripL", strip_l)
strip_l_obj.location = (-2.4, -0.2, 0.85)
strip_l_obj.rotation_euler = (math.radians(80.0), 0.0, math.radians(-75.0))
scene.collection.objects.link(strip_l_obj)

# Right Strip Light (Warm Amber)
strip_r = bpy.data.lights.new("StripR", type='AREA')
strip_r.energy = 280.0
strip_r.size = 0.15
strip_r.size_y = 2.4
strip_r.color = (1.0, 0.82, 0.35)
strip_r_obj = bpy.data.objects.new("StripR", strip_r)
strip_r_obj.location = (2.4, -0.2, 0.85)
strip_r_obj.rotation_euler = (math.radians(80.0), 0.0, math.radians(75.0))
scene.collection.objects.link(strip_r_obj)

# Plaque Soft Focused Key Spot
plaque_spot = bpy.data.lights.new("PlaqueSpot", type='SPOT')
plaque_spot.energy = 320.0
plaque_spot.spot_size = math.radians(22.0)
plaque_spot.color = (1.0, 0.94, 0.86)
plaque_spot_obj = bpy.data.objects.new("PlaqueSpot", plaque_spot)
plaque_spot_obj.location = (0.5, -2.4, 1.1)
scene.collection.objects.link(plaque_spot_obj)
plaque_spot_track = plaque_spot_obj.constraints.new(type='TRACK_TO')
plaque_spot_track.target = target
plaque_spot_track.track_axis = 'TRACK_NEGATIVE_Z'
plaque_spot_track.up_axis = 'UP_Y'

# 3D Text Extrusion Depth Accent Light
text_light = bpy.data.lights.new("TextExtrudeLight", type='SPOT')
text_light.energy = 450.0
text_light.spot_size = math.radians(65.0)
text_light.color = (1.0, 0.88, 0.50)
text_light_obj = bpy.data.objects.new("TextExtrudeLight", text_light)
text_light_obj.location = (2.2, 0.6, 1.8)
scene.collection.objects.link(text_light_obj)
text_light_track = text_light_obj.constraints.new(type='TRACK_TO')
text_light_track.target = target
text_light_track.track_axis = 'TRACK_NEGATIVE_Z'
text_light_track.up_axis = 'UP_Y'

# High Rim Spot
rim = bpy.data.lights.new("RimSpot", type='SPOT')
rim.energy = 750.0
rim.spot_size = math.radians(65.0)
rim.color = (0.98, 0.78, 0.25)
rim_obj = bpy.data.objects.new("RimSpot", rim)
rim_obj.location = (0.0, 1.8, 1.9)
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
