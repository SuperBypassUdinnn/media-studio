"""End-to-end test for heterogeneous multi-engine timeline assembly.

A scene graph mixing blender-3d, effectcraft, and html-motion scenes is
rendered through the dynamic dispatcher, concatenated into both deliverables,
mixed with the SFX audio bed, and verified end-to-end by the ms dual-gate
critique (render, concatenation, audio mix, critique PASS).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from media_studio.critique import VISION_RUBRIC_AXES, critique_project
from media_studio.manifest import load_project
from media_studio.scene_graph import (
    SceneNode,
    load_scene_graph,
    render_scene_graph,
)
from media_studio.video import qc_report

TOTAL_DURATION = 10.0

SCENES = [
    {
        "id": "scene-01-macro",
        "duration": 3.0,
        "engine": "blender-3d",
        "shot_scale": "ECU",
        "camera_angle": "low-hero",
        "focal_point": [0.35, 0.48],
        "typography_3d": None,
        "sound_events": [{"asset_id": "impact-sub", "start_offset_s": 0.4, "gain_db": -6.0}],
    },
    {
        "id": "scene-02-hero",
        "duration": 3.5,
        "engine": "effectcraft",
        "shot_scale": "MS",
        "camera_angle": "eye-level",
        "focal_point": [0.50, 0.50],
        "typography_3d": {"text": "AURA NOIR", "z_layer": "behind-subject"},
        "sound_events": [{"asset_id": "impact-sub", "start_offset_s": 0.4, "gain_db": -6.0}],
    },
    {
        "id": "scene-03-title",
        "duration": 3.5,
        "engine": "html-motion",
        "shot_scale": "CU",
        "camera_angle": "high-zenith",
        "focal_point": [0.65, 0.52],
        "typography_3d": None,
        "sound_events": [{"asset_id": "impact-sub", "start_offset_s": 0.4, "gain_db": -6.0}],
    },
]

PROJECT_YAML = """\
slug: noir
title: Aura Noir
status: draft
deliverables:
- id: reel-9x16
  kind: video
  container: mp4
  resolution: 1080x1920
  fps: 60
  duration: 10.0
  loudness_lufs: -14.0
- id: master-16x9
  kind: video
  container: mp4
  resolution: 1920x1080
  fps: 60
  duration: 10.0
  loudness_lufs: -14.0
brand:
  palette:
    primary: '#080B11'
    accent: '#F59E0B'
    secondary: '#1E293B'
    ink: '#F8FAFC'
assets: {}
"""


def _write_test_clip(output: Path, duration: float) -> None:
    """Write a non-black, non-frozen H.264 test clip of ``duration`` seconds."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=30",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ],
        check=True,
        capture_output=True,
    )


def test_heterogeneous_scene_graph_assembly_and_critique(tmp_path: Path, monkeypatch) -> None:
    dispatched: list[str] = []

    def _fake_blender(project, scene, output_mp4, **_kw):
        dispatched.append("blender-3d")
        _write_test_clip(output_mp4, scene.duration)
        return output_mp4

    def _fake_effectcraft(project, scene, output_mp4, **_kw):
        dispatched.append("effectcraft")
        _write_test_clip(output_mp4, scene.duration)
        return output_mp4

    def _fake_html_motion(project, scene, output_mp4, **_kw):
        dispatched.append("html-motion")
        _write_test_clip(output_mp4, scene.duration)
        return output_mp4

    monkeypatch.setattr("media_studio.scene_graph.render_blender_3d_clip", _fake_blender)
    monkeypatch.setattr("media_studio.scene_graph.render_effectcraft_clip", _fake_effectcraft)
    monkeypatch.setattr("media_studio.scene_graph.render_html_motion_clip", _fake_html_motion)

    proj_dir = tmp_path / "noir"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    sg_path = proj_dir / "scene-graph.json"
    sg_path.write_text(
        json.dumps(
            {
                "project_slug": "noir",
                "aspects": ["16:9", "9:16"],
                "fps": 60,
                "scenes": SCENES,
            }
        ),
        encoding="utf-8",
    )

    project = load_project(proj_dir)
    graph = load_scene_graph(sg_path)

    # --- Render: dispatcher + concatenation + audio mix + encode ----------------
    results = render_scene_graph(project, graph)

    assert dispatched == ["blender-3d", "effectcraft", "html-motion"] * 2, dispatched
    assert set(results) == {"reel-9x16", "master-16x9"}
    reel = project.root / "out" / "video" / "reel-9x16.mp4"
    master = project.root / "out" / "video" / "master-16x9.mp4"
    assert reel.is_file()
    assert master.is_file()

    reel_qc = qc_report(reel, expected_duration=TOTAL_DURATION, target_lufs=-14.0)
    assert reel_qc["ok"], reel_qc
    assert reel_qc["checks"]["has_video"]
    assert reel_qc["checks"]["has_audio"]
    assert reel_qc["checks"]["duration_matches"]

    master_qc = qc_report(master, expected_duration=TOTAL_DURATION, target_lufs=-14.0)
    assert master_qc["ok"], master_qc

    # --- Critique: vision rubric bound to the exact render artifact ------------
    digest = hashlib.sha256(reel.read_bytes()).hexdigest()
    report_dir = project.root / "out" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "stage": "video",
        "target_sha256": digest,
        "evaluated_by": "test-suite",
        "keyframes_inspected": [scene["id"] for scene in SCENES],
        "scores": {axis: 5 for axis in VISION_RUBRIC_AXES},
        "defects": [],
        "passed": True,
    }
    (report_dir / "vision-critique.json").write_text(json.dumps(report), encoding="utf-8")

    result = critique_project(project, stage="video", deliverables=("reel-9x16",))
    assert result["ok"] is True, result["defects"]
    assert result["summary"]["errors"] == 0
    assert result["defects"] == []

    # SceneNode dataclass path stays consistent with the rendered graph.
    for node in graph.scenes:
        assert isinstance(node, SceneNode)
    assert graph.total_duration == TOTAL_DURATION
