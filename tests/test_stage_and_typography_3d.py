"""Tests for 3D Product Stage (Blender 5.3) and Z-Depth 3D Typography (EffectCraft)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from media_studio.stage_3d import Stage3DSpec, build_blender_stage_script
from media_studio.typography_3d import (
    Typography3DSpec,
    build_effectcraft_3d_text_commands,
    composite_z_depth_sandwich,
)


def test_build_blender_stage_script_configures_chiaroscuro_and_speed_ramp() -> None:
    spec = Stage3DSpec(
        model_path=None,
        preset_geometry="luxury-perfume-flacon",
        shot_scale="ECU",
        camera_angle="low-hero",
        camera_move="orbital-reveal",
        velocity_curve="slow-fast-slow",
        focal_length_mm=105.0,
        dof_fstop=1.8,
        key_to_fill_ratio=8.0,
        primary_color="#080B11",
        accent_color="#F59E0B",
        volumetric_fog=True,
        motion_blur=True,
    )
    script = build_blender_stage_script(spec)
    assert "use_motion_blur = True" in script
    assert "lens = 105.0" in script
    assert "aperture_fstop = 1.8" in script
    assert "IOR" in script


def test_effectcraft_3d_text_commands_include_camera_lights_and_sweeps() -> None:
    spec = Typography3DSpec(
        text="AURA NOIR",
        subtext="EXTRAIT DE PARFUM",
        style="text-orbit-3d",
        color="#F8FAFC",
        accent="#F59E0B",
        z_layer="behind-subject",
        focal_point=(0.66, 0.42),
    )
    cmds = build_effectcraft_3d_text_commands(spec, width=1920, height=1080, duration=4.0, fps=60)
    cmd_ids = [c["id"] for c in cmds]
    assert "camera.newCamera" in cmd_ids
    assert "light.newLight" in cmd_ids
    assert "effect.apply" in cmd_ids


def test_composite_z_depth_sandwich_occludes_text_behind_subject_matte(tmp_path: Path) -> None:
    bg = tmp_path / "bg.png"
    txt = tmp_path / "txt.png"
    matte = tmp_path / "matte.png"
    out = tmp_path / "comp.png"

    Image.new("RGBA", (320, 180), (10, 14, 24, 255)).save(bg)
    Image.new("RGBA", (320, 180), (245, 158, 11, 255)).save(txt)
    # Center 80x80 subject matte (white=subject, black=background)
    m_img = Image.new("RGBA", (320, 180), (10, 14, 24, 0))
    for x in range(120, 200):
        for y in range(50, 130):
            m_img.putpixel((x, y), (20, 30, 50, 255))
    m_img.save(matte)

    res = composite_z_depth_sandwich(
        bg, txt, matte, out, z_layer="behind-subject", apply_optical_grade=False
    )
    assert res.is_file()
    out_img = Image.open(res)
    # Center pixel occluded by matte (20, 30, 50); outer shows gold (245, 158, 11)
    assert out_img.getpixel((160, 90))[:3] == (20, 30, 50)
    assert out_img.getpixel((40, 40))[:3] == (245, 158, 11)
