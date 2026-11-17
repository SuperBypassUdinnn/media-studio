"""Tests for Composable Scene-Graph (`ms scene`) and Visual Storyboard Engine (`ms storyboard`)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from media_studio.manifest import load_project
from media_studio.scene_graph import load_scene_graph
from media_studio.storyboard import build_visual_storyboard


def test_build_visual_storyboard_renders_high_res_bento_panels(tmp_path: Path, monkeypatch) -> None:
    proj_dir = tmp_path / "noir"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(
        "slug: noir\ntitle: Aura Noir\nstatus: draft\ndeliverables:\n"
        "- id: master-16x9\n  kind: video\n  container: mp4\n  resolution: 1920x1080\n"
        "  fps: 60\n  duration: 8.0\n  loudness_lufs: -14.0\nbrand:\n  palette:\n"
        "    primary: '#080B11'\n    accent: '#F59E0B'\n    secondary: '#1E293B'\n"
        "    ink: '#F8FAFC'\nassets: {}\n",
        encoding="utf-8",
    )
    sg_path = proj_dir / "scene-graph.json"
    sg_path.write_text(
        json.dumps(
            {
                "project_slug": "noir",
                "aspects": ["16:9"],
                "fps": 60,
                "subtitles": False,
                "scenes": [
                    {
                        "id": "scene-01-macro",
                        "duration": 2.5,
                        "engine": "blender-3d",
                        "shot_scale": "ECU",
                        "camera_angle": "low-hero",
                        "focal_point": [0.35, 0.48],
                        "typography_3d": None,
                    },
                    {
                        "id": "scene-02-hero",
                        "duration": 5.5,
                        "engine": "composite",
                        "shot_scale": "MS",
                        "camera_angle": "eye-level",
                        "focal_point": [0.40, 0.50],
                        "typography_3d": {"text": "AURA NOIR", "z_layer": "behind-subject"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    project = load_project(proj_dir)
    graph = load_scene_graph(sg_path)

    def _fake_scene_still(_project, _scene, out_png, **_kw):
        img = Image.new("RGB", (1920, 1080), (14, 18, 28))
        img.save(out_png)
        return out_png

    monkeypatch.setattr("media_studio.storyboard.render_scene_keyframe", _fake_scene_still)
    sheet = build_visual_storyboard(project, graph)
    assert sheet.is_file()
    assert (proj_dir / "out" / "storyboard" / "scene-01-macro.png").is_file()
    assert (proj_dir / "out" / "storyboard" / "scene-02-hero.png").is_file()
    with Image.open(sheet) as im:
        assert im.width >= 1280
