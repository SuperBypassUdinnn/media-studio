"""Tests for the Dual-Gate Visual Critic (6 quantitative gates + 7-axis Vision Rubric)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from media_studio.critique import critique_montage, critique_palette, validate_vision_rubric
from media_studio.manifest import load_project


def test_critique_montage_rejects_adjacent_same_scale_and_all_text_clutter() -> None:
    scenes = [
        {
            "id": "s1",
            "duration": 4.0,
            "shot_scale": "MS",
            "camera_angle": "eye-level",
            "focal_point": [0.33, 0.5],
            "typography_3d": {"text": "ONE"},
        },
        {
            "id": "s2",
            "duration": 4.0,
            "shot_scale": "MS",
            "camera_angle": "eye-level",
            "focal_point": [0.90, 0.9],
            "typography_3d": {"text": "TWO"},
        },
        {
            "id": "s3",
            "duration": 4.0,
            "shot_scale": "CU",
            "camera_angle": "low-hero",
            "focal_point": [0.5, 0.5],
            "typography_3d": {"text": "THREE"},
        },
        {
            "id": "s4",
            "duration": 4.0,
            "shot_scale": "WS",
            "camera_angle": "high-zenith",
            "focal_point": [0.5, 0.5],
            "typography_3d": {"text": "FOUR"},
        },
    ]
    defects = critique_montage(scenes)
    categories = {d["defect"] for d in defects}
    assert any("jump-cut" in d.lower() for d in categories)
    assert any("eye-trace" in d.lower() for d in categories)
    assert any("sensory" in d.lower() for d in categories)


def test_critique_palette_rejects_off_palette_white_card_and_low_wcag(tmp_path: Path) -> None:
    proj_dir = tmp_path / "lux"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(
        "slug: lux\ntitle: Lux\nstatus: draft\ndeliverables: []\nbrand:\n  palette:\n"
        "    primary: '#080B11'\n    accent: '#F59E0B'\n    secondary: '#1E293B'\n"
        "    ink: '#F8FAFC'\nassets: {}\n",
        encoding="utf-8",
    )
    project = load_project(proj_dir)
    bad_frame = tmp_path / "white_card.png"
    Image.new("RGB", (320, 180), (255, 255, 255)).save(bad_frame)
    defects = critique_palette(project, [bad_frame])
    assert any(d["severity"] == "error" and "palette" in d["category"] for d in defects)


def test_validate_vision_rubric_blocks_missing_or_low_score(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    assert validate_vision_rubric(missing, stage="storyboard")

    low_report = tmp_path / "vision-critique.json"
    low_report.write_text(
        json.dumps(
            {
                "stage": "storyboard",
                "evaluated_by": "google/antigravity-gemini-3.8-flash",
                "keyframes_inspected": ["s1.png"],
                "scores": {
                    "three_d_object_and_environment_depth": 3,
                    "three_d_and_kinetic_typography_z_depth": 5,
                    "camera_choreography_speed_ramp_and_motion": 5,
                    "shot_scale_montage_and_eye_trace": 5,
                    "color_palette_selection_and_harmony": 5,
                    "chiaroscuro_lighting_and_optical_finish": 5,
                    "conceptual_fit_to_brief": 5,
                },
                "defects": [],
                "passed": True,
            }
        ),
        encoding="utf-8",
    )
    defects = validate_vision_rubric(low_report, stage="storyboard")
    assert any("three_d_object_and_environment_depth" in d["defect"] for d in defects)
