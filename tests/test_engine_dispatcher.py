"""Tests for Scene-Graph JSON Schema validation and multi-engine dispatching."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from media_studio.scene_graph import (
    SceneNode,
    render_scene_clip,
    validate_scene_graph_schema,
)

VALID_ENGINES = ("blender-3d", "effectcraft", "html-motion", "remotion", "flow-gen", "composite")


def _make_project() -> MagicMock:
    project = MagicMock()
    project.brand.palette = {"primary": "#080B11", "accent": "#F59E0B"}
    return project


def test_validate_scene_graph_schema_rejects_unknown_engine(tmp_path: Path) -> None:
    bad_data = {
        "project_slug": "demo",
        "scenes": [{"id": "s1", "duration": 3.0, "engine": "unknown-engine"}],
    }
    errors = validate_scene_graph_schema(bad_data)
    assert len(errors) > 0
    assert any("unknown-engine" in e for e in errors)


def test_validate_scene_graph_schema_accepts_all_valid_engines() -> None:
    for engine in VALID_ENGINES:
        data = {"project_slug": "demo", "scenes": [{"id": "s1", "duration": 3.0, "engine": engine}]}
        errors = validate_scene_graph_schema(data)
        assert errors == [], f"engine {engine!r} produced errors: {errors}"


def test_validate_scene_graph_schema_rejects_missing_and_bad_fields() -> None:
    errors = validate_scene_graph_schema({"project_slug": "demo", "scenes": [{"id": "s1"}]})
    assert any("duration" in e for e in errors)
    assert any("engine" in e for e in errors)

    errors = validate_scene_graph_schema(
        {"project_slug": "demo", "scenes": [{"id": "s1", "duration": -1.0, "engine": "composite"}]}
    )
    assert any("duration" in e for e in errors)

    errors = validate_scene_graph_schema(
        {"scenes": [{"id": "s1", "duration": 1.0, "engine": "composite"}]}
    )
    assert any("project_slug" in e for e in errors)

    errors = validate_scene_graph_schema("not-a-dict")
    assert len(errors) > 0


def test_render_scene_clip_dispatches_to_declared_engine(tmp_path: Path, monkeypatch) -> None:
    dispatched: list[str] = []

    def fake_render_3d(*args, **kwargs):
        dispatched.append("blender-3d")
        raw = Path(args[1])
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=32x32:duration=1",
            "-frames:v",
            "1",
            str(raw),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return args[1]

    def fake_render_ec(*args, **kwargs):
        dispatched.append("effectcraft")
        Path(args[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[2]).touch()
        return args[2]

    def fake_render_html(*args, **kwargs):
        dispatched.append("html-motion")
        Path(args[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[2]).touch()
        return args[2]

    def fake_render_composite(*args, **kwargs):
        dispatched.append("composite")
        Path(args[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[2]).touch()
        return args[2]

    monkeypatch.setattr("media_studio.scene_graph.render_3d_stage_clip", fake_render_3d)
    monkeypatch.setattr("media_studio.scene_graph.render_effectcraft_clip", fake_render_ec)
    monkeypatch.setattr("media_studio.scene_graph.render_html_motion_clip", fake_render_html)
    monkeypatch.setattr("media_studio.scene_graph.render_composite_clip", fake_render_composite)

    project = _make_project()
    for engine in ("blender-3d", "effectcraft", "html-motion", "composite"):
        scene = SceneNode(id="s1", duration=2.0, engine=engine)
        out = tmp_path / f"{engine}.mp4"
        result = render_scene_clip(project, scene, out)
        assert result == out

    assert dispatched == ["blender-3d", "effectcraft", "html-motion", "composite"]
