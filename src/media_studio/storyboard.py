"""Visual-First Storyboard Engine and Pre-Production Iteration Loop."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw

from media_studio.manifest import Project
from media_studio.scene_graph import SceneGraph, SceneNode
from media_studio.stage_3d import Stage3DSpec, render_3d_stage_still


def render_scene_keyframe(
    project: Project,
    scene: SceneNode,
    output_png: Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Render a full-resolution 1080p keyframe image for one scene at its visual peak."""
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    palette = getattr(project.brand, "palette", {}) or {}
    primary = palette.get("primary", "#080B11")
    accent = palette.get("accent", "#F59E0B")

    # Render True 3D Product Scene with Integrated 3D Typography in Blender
    stage_spec = Stage3DSpec(
        model_path=None,
        preset_geometry="luxury-perfume-flacon",
        shot_scale=scene.shot_scale,
        camera_angle=scene.camera_angle,
        camera_move=str(scene.camera.get("move", "slow-dolly-in")),
        focal_length_mm=float(scene.camera.get("focal_length_mm", 85.0)),
        dof_fstop=float(scene.camera.get("dof_fstop", 1.8)),
        primary_color=primary,
        accent_color=accent,
        typography_3d=scene.typography_3d,
        product_rotation_deg=float(scene.camera.get("product_rotation_deg", 18.0)),
    )
    render_3d_stage_still(stage_spec, output_png, width=width, height=height)
    return output_png


def build_visual_storyboard(
    project: Project,
    graph: SceneGraph,
    *,
    scenes: Sequence[str] | None = None,
    output: Path | None = None,
) -> Path:
    """Build a visual bento contact sheet (>= 85% area is rendered visual image)."""
    sb_dir = project.root / "out" / "storyboard"
    sb_dir.mkdir(parents=True, exist_ok=True)
    for legacy_f in sb_dir.glob("*.matte.png"):
        legacy_f.unlink(missing_ok=True)
    for legacy_f in sb_dir.glob("*.typo.png"):
        legacy_f.unlink(missing_ok=True)
    out_sheet = output or (sb_dir / "storyboard.png")

    rendered_scenes: list[tuple[SceneNode, Path]] = []
    for sc in graph.scenes:
        if scenes and sc.id not in set(scenes):
            continue
        sc_png = sb_dir / f"{sc.id}.png"
        render_scene_keyframe(project, sc, sc_png)
        rendered_scenes.append((sc, sc_png))

    if not rendered_scenes:
        Image.new("RGB", (1280, 720), (10, 14, 24)).save(out_sheet)
        return out_sheet

    # Visual-First Bento Layout: 3 columns x N rows of 16:9 panels
    n_cols = min(3, len(rendered_scenes))
    n_rows = math_ceil(len(rendered_scenes) / n_cols)
    thumb_w, thumb_h = 640, 360
    meta_h = 72
    pad = 24
    sheet_w = n_cols * thumb_w + (n_cols + 1) * pad
    sheet_h = 80 + n_rows * (thumb_h + meta_h + pad)

    sheet = Image.new("RGB", (sheet_w, sheet_h), (8, 11, 17))
    draw = ImageDraw.Draw(sheet)

    # Header banner
    header_title = (
        f"VISUAL STORYBOARD: {project.slug.upper()} "
        f"({len(rendered_scenes)} SCENES | {graph.total_duration:.1f}s)"
    )
    draw.text((pad, 28), header_title, fill=(245, 158, 11))

    for idx, (sc, sc_png) in enumerate(rendered_scenes):
        r = idx // n_cols
        c = idx % n_cols
        px = pad + c * (thumb_w + pad)
        py = 80 + r * (thumb_h + meta_h + pad)

        try:
            with Image.open(sc_png) as im:
                thumb = im.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                sheet.paste(thumb, (px, py))
        except Exception:
            draw.rectangle([px, py, px + thumb_w, py + thumb_h], fill=(18, 24, 38))

        draw.rectangle([px, py, px + thumb_w, py + thumb_h], outline=(40, 50, 70), width=2)

        fy = py + thumb_h + 8
        header_line = (
            f"SCENE {idx + 1}: {sc.id.upper()} ({sc.duration:.1f}s) | "
            f"{sc.shot_scale} - {sc.camera_angle}"
        )
        draw.text((px + 6, fy), header_line, fill=(245, 158, 11))

        cam_move = sc.camera.get("move", "dolly")
        focal = sc.camera.get("focal_length_mm", 85)
        dof = sc.camera.get("dof_fstop", 1.8)
        draw.text(
            (px + 6, fy + 20),
            f"Camera: {cam_move} ({focal}mm f/{dof})",
            fill=(148, 163, 184),
        )

        typo_text = (
            f'3D Typo: "{sc.typography_3d.get("text", "")}"'
            if sc.typography_3d
            else "Sensory Macro (Pure)"
        )
        draw.text((px + 6, fy + 38), typo_text, fill=(226, 232, 240))

    sheet.save(out_sheet, format="PNG")
    return out_sheet


def math_ceil(v: float) -> int:
    return int(v) + (1 if v > int(v) else 0)
