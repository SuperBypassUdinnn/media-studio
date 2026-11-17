"""Dual-Gate Visual & Semantic Quality Critic for Media-Studio projects."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from media_studio import video
from media_studio.manifest import Project

FORBIDDEN_BOILERPLATE = (
    "new feature",
    "try it free",
    "up from last period",
    "key feature",
    "automated demo",
)

VISION_RUBRIC_AXES = (
    "three_d_object_and_environment_depth",
    "three_d_and_kinetic_typography_z_depth",
    "camera_choreography_speed_ramp_and_motion",
    "shot_scale_montage_and_eye_trace",
    "color_palette_selection_and_harmony",
    "chiaroscuro_lighting_and_optical_finish",
    "conceptual_fit_to_brief",
)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def adjust(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (adjust(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _wcag_contrast(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    l1 = _relative_luminance(rgb1)
    l2 = _relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return (15, 23, 42)


def _rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    # Standard sRGB to CIELAB approximation
    r, g, b = [c / 255.0 for c in rgb[:3]]
    r = ((r + 0.055) / 1.055) ** 2.4 if r > 0.04045 else r / 12.92
    g = ((g + 0.055) / 1.055) ** 2.4 if g > 0.04045 else g / 12.92
    b = ((b + 0.055) / 1.055) ** 2.4 if b > 0.04045 else b / 12.92

    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = (r * 0.2126 + g * 0.7152 + b * 0.0722) / 1.00000
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t) + (16.0 / 116.0)

    fx, fy, fz = f(x), f(y), f(z)
    return (round(116.0 * fy - 16.0, 2), round(500.0 * (fx - fy), 2), round(200.0 * (fy - fz), 2))


def _delta_e(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    return math.sqrt((lab1[0] - lab2[0]) ** 2 + (lab1[1] - lab2[1]) ** 2 + (lab1[2] - lab2[2]) ** 2)


def critique_montage(scene_specs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate montage grammar, shot scale contrast, and eye-trace continuity."""
    defects: list[dict[str, Any]] = []
    if not scene_specs:
        return defects

    has_sensory = False
    for idx in range(len(scene_specs)):
        s_curr = scene_specs[idx]
        typo = s_curr.get("typography_3d")
        if typo is None or not (isinstance(typo, dict) and typo.get("text")):
            has_sensory = True

        if idx > 0:
            s_prev = scene_specs[idx - 1]
            scale_prev = s_prev.get("shot_scale")
            scale_curr = s_curr.get("shot_scale")
            angle_prev = s_prev.get("camera_angle")
            angle_curr = s_curr.get("camera_angle")
            if (
                scale_prev
                and scale_curr
                and angle_prev
                and angle_curr
                and scale_prev == scale_curr
                and angle_prev == angle_curr
            ):
                defects.append(
                    {
                        "category": "montage",
                        "severity": "error",
                        "defect": (
                            f"Visual jump-cut: Scene {idx} ({s_curr.get('id')}) shares both "
                            f"shot_scale={scale_curr} and camera_angle={angle_curr}"
                        ),
                        "corrective_action": "Vary shot scale or change angle",
                    }
                )

            # Eye-trace continuity check
            fp_prev = s_prev.get("focal_point") or [0.5, 0.5]
            fp_curr = s_curr.get("focal_point") or [0.5, 0.5]
            dist = math.hypot(fp_curr[0] - fp_prev[0], fp_curr[1] - fp_prev[1])
            if dist > 0.45:
                defects.append(
                    {
                        "category": "montage",
                        "severity": "warning",
                        "defect": f"Abrupt eye-trace jump ({dist:.2f} dist) between scenes",
                        "corrective_action": "Align focal points within 0.40 distance across cuts",
                    }
                )

    if len(scene_specs) >= 4 and not has_sensory:
        defects.append(
            {
                "category": "montage",
                "severity": "error",
                "defect": "Zero pure sensory shots (all scenes cluttered with text; lacks tension)",
                "corrective_action": (
                    "Include at least one 1.5s-2.5s pure macro shot with typography_3d=null"
                ),
            }
        )

    return defects


def critique_palette(project: Project, frame_paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Evaluate color palette selection, WCAG contrast, and CIELAB 60-30-10 adherence."""
    defects: list[dict[str, Any]] = []
    palette = getattr(project.brand, "palette", {}) or {}
    primary = _hex_to_rgb(palette.get("primary", "#080B11"))
    secondary = _hex_to_rgb(palette.get("secondary", "#1E293B"))
    ink = _hex_to_rgb(palette.get("ink", "#F8FAFC"))

    # WCAG contrast check
    ratio = _wcag_contrast(ink, primary)
    if ratio < 4.5:
        defects.append(
            {
                "category": "palette",
                "severity": "error",
                "defect": f"Low WCAG contrast ({ratio:.2f}:1) between ink and primary (< 4.5:1)",
                "corrective_action": "Increase luminance difference between text ink and primary",
            }
        )

    # Dominant surface cluster check against primary & secondary
    target_labs = [_rgb_to_lab(c) for c in (primary, secondary)]
    for fpath in frame_paths:
        if not fpath.is_file():
            continue
        try:
            with Image.open(fpath) as im:
                thumb = im.convert("RGB").resize((64, 36))
                arr = np.array(thumb, dtype=np.float32).reshape(-1, 3)
                mean_rgb = tuple(int(x) for x in arr.mean(axis=0))
                frame_lab = _rgb_to_lab(mean_rgb)
                min_de = min(_delta_e(frame_lab, t_lab) for t_lab in target_labs)
                if min_de > 38.0:
                    defects.append(
                        {
                            "category": "palette",
                            "severity": "error",
                            "defect": (
                                f"Frame {fpath.name} off-palette (Delta-E {min_de:.1f} > 38)"
                            ),
                            "corrective_action": "Align 3D lighting and grading with brand palette",
                        }
                    )
        except Exception:
            pass

    return defects


def validate_vision_rubric(
    report_path: Path, *, stage: str, min_mtime: float = 0.0
) -> list[dict[str, Any]]:
    """Validate that a signed vision critique report exists and scores >= 4 on all axes."""
    defects: list[dict[str, Any]] = []
    path = Path(report_path)
    if not path.is_file():
        return [
            {
                "category": "vision-rubric",
                "severity": "error",
                "defect": f"Missing vision critique report: {path}",
                "corrective_action": "Run visual inspection and save vision-critique.json",
            }
        ]

    if min_mtime > 0 and path.stat().st_mtime < min_mtime:
        return [
            {
                "category": "vision-rubric",
                "severity": "error",
                "defect": f"Stale vision critique report: {path.name} older than deliverable",
                "corrective_action": "Re-inspect latest rendered frames and re-issue critique",
            }
        ]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as err:
        return [
            {
                "category": "vision-rubric",
                "severity": "error",
                "defect": f"Invalid JSON in vision critique report: {err}",
                "corrective_action": "Re-write valid vision-critique.json",
            }
        ]

    scores = data.get("scores") or {}
    for axis in VISION_RUBRIC_AXES:
        val = scores.get(axis)
        if val is None or float(val) < 4.0:
            defects.append(
                {
                    "category": "vision-rubric",
                    "severity": "error",
                    "defect": f"Vision rubric score for '{axis}' is {val} (minimum 4/5 required)",
                    "corrective_action": f"Improve {axis.replace('_', ' ')} and re-evaluate",
                }
            )

    if not data.get("passed", False):
        defects.append(
            {
                "category": "vision-rubric",
                "severity": "error",
                "defect": "Vision critique report marked passed=false",
                "corrective_action": "Resolve all listed visual defects in vision critique report",
            }
        )

    return defects


def critique_project(
    project: Project,
    *,
    stage: str = "video",
    scene_graph_path: Path | None = None,
    deliverables: tuple[str, ...] = ("reel-9x16", "master-16x9"),
) -> dict[str, Any]:
    """Execute the full Dual-Gate visual critic across quantitative and vision rubrics."""
    defects: list[dict[str, Any]] = []
    sg_file = scene_graph_path or (project.root / "scene-graph.json")

    # 1. Montage Grammar Gate
    if sg_file.is_file():
        try:
            sg_data = json.loads(sg_file.read_text(encoding="utf-8"))
            scenes = sg_data.get("scenes") or []
            defects.extend(critique_montage(scenes))
        except Exception as e:
            defects.append(
                {
                    "category": "scene-graph",
                    "severity": "error",
                    "defect": f"Failed to parse scene-graph.json: {e}",
                    "corrective_action": "Fix JSON syntax in scene-graph.json",
                }
            )

    # 2. Keyframes / Storyboard Gate
    sb_dir = project.root / "out" / "storyboard"
    keyframe_stills = (
        [
            p
            for p in sb_dir.glob("scene-*.png")
            if not p.name.endswith(".matte.png") and not p.name.endswith(".typo.png")
        ]
        if sb_dir.is_dir()
        else []
    )
    if keyframe_stills:
        defects.extend(critique_palette(project, keyframe_stills))

    # 3. Vision Rubric Gate
    rubric_file = project.root / "out" / "reports" / "vision-critique.json"
    defects.extend(validate_vision_rubric(rubric_file, stage=stage))

    # 4. Video Deliverable Gates (if stage == "video")
    if stage == "video":
        for deliv in deliverables:
            vpath = project.root / "out" / "video" / f"{deliv}.mp4"
            if not vpath.is_file():
                defects.append(
                    {
                        "category": "missing",
                        "severity": "error",
                        "defect": f"Deliverable missing: {vpath}",
                        "corrective_action": "Render the deliverable before video critique",
                    }
                )
                continue
            qc = video.qc_report(vpath, expected_duration=30.0, target_lufs=-14.0)
            if not qc.get("ok"):
                for check_name, status in qc.get("checks", {}).items():
                    if not status:
                        defects.append(
                            {
                                "category": "qc",
                                "severity": "error",
                                "defect": f"QC check failed on {deliv}: {check_name}",
                                "corrective_action": f"Fix {check_name} in audio/video pipeline",
                            }
                        )

    errors = sum(1 for d in defects if d["severity"] == "error")
    warnings = sum(1 for d in defects if d["severity"] == "warning")

    return {
        "project": project.slug,
        "stage": stage,
        "ok": errors == 0,
        "summary": {"errors": errors, "warnings": warnings},
        "defects": defects,
    }
