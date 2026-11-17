"""Tests for engine-accurate storyboard keyframe rendering."""

from pathlib import Path

from media_studio.manifest import load_project
from media_studio.scene_graph import SceneNode
from media_studio.storyboard import render_scene_keyframe


def _make_project(tmp_path: Path) -> object:
    proj_dir = tmp_path / "noir"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(
        "slug: noir\ntitle: Aura Noir\nstatus: draft\n"
        "deliverables:\n- id: master-16x9\n  kind: video\n  container: mp4\n"
        "  resolution: 1920x1080\n  fps: 60\n  duration: 8.0\n  loudness_lufs: -14.0\n"
        "brand:\n  palette:\n    primary: '#080B11'\n    accent: '#F59E0B'\nassets: {}\n",
        encoding="utf-8",
    )
    return load_project(proj_dir)


def _make_scene(engine: str) -> SceneNode:
    return SceneNode(
        id="scene-hero",
        duration=4.0,
        engine=engine,
        shot_scale="MS",
        camera_angle="low-hero",
        camera={"move": "slow-dolly-in", "focal_length_mm": 85.0, "dof_fstop": 1.8},
        typography_3d={"text": "AURA NOIR"},
    )


def test_render_scene_keyframe_calls_effectcraft_for_motion_scene(
    tmp_path: Path, monkeypatch
) -> None:
    # Verify render_scene_keyframe delegates to effectcraft render-frame when
    # scene.engine == "effectcraft"
    calls: list[str] = []

    def _fake_effectcraft_render_frame(_project, _scene, output_png, **_kw):
        calls.append("effectcraft")
        Path(output_png).parent.mkdir(parents=True, exist_ok=True)
        Path(output_png).write_bytes(b"fake-effectcraft-frame")
        return Path(output_png)

    monkeypatch.setattr(
        "media_studio.storyboard._render_effectcraft_frame", _fake_effectcraft_render_frame
    )

    project = _make_project(tmp_path)
    scene = _make_scene("effectcraft")
    out_png = tmp_path / "out" / "keyframe.png"

    result = render_scene_keyframe(project, scene, out_png)

    assert result == out_png
    assert calls == ["effectcraft"]


def test_render_scene_keyframe_calls_html_motion_for_html_motion_scene(
    tmp_path: Path, monkeypatch
) -> None:
    # Verify render_scene_keyframe renders a single HTML/Playwright frame when
    # scene.engine == "html-motion" or "remotion"
    calls: list[str] = []

    def _fake_html_motion_frame(_project, _scene, output_png, **_kw):
        calls.append("html-motion")
        Path(output_png).parent.mkdir(parents=True, exist_ok=True)
        Path(output_png).write_bytes(b"fake-html-frame")
        return Path(output_png)

    monkeypatch.setattr(
        "media_studio.storyboard._render_html_motion_frame", _fake_html_motion_frame
    )

    project = _make_project(tmp_path)
    scene = _make_scene("html-motion")
    out_png = tmp_path / "out" / "keyframe.png"

    result = render_scene_keyframe(project, scene, out_png)

    assert result == out_png
    assert calls == ["html-motion"]


def test_render_scene_keyframe_calls_blender_for_blender_3d_scene(
    tmp_path: Path, monkeypatch
) -> None:
    # Verify render_scene_keyframe calls render_3d_stage_still for blender-3d
    calls: list[str] = []

    def _fake_stage_still(spec, output_png, **_kw):
        calls.append("blender-3d")
        Path(output_png).parent.mkdir(parents=True, exist_ok=True)
        Path(output_png).write_bytes(b"fake-blender-still")
        return Path(output_png)

    monkeypatch.setattr("media_studio.storyboard.render_3d_stage_still", _fake_stage_still)

    project = _make_project(tmp_path)
    scene = _make_scene("blender-3d")
    out_png = tmp_path / "out" / "keyframe.png"

    result = render_scene_keyframe(project, scene, out_png)

    assert result == out_png
    assert calls == ["blender-3d"]


def test_render_scene_keyframe_calls_blender_for_composite_scene(
    tmp_path: Path, monkeypatch
) -> None:
    # Verify render_scene_keyframe falls through to blender-3d for composite
    calls: list[str] = []

    def _fake_stage_still(spec, output_png, **_kw):
        calls.append("composite")
        Path(output_png).parent.mkdir(parents=True, exist_ok=True)
        Path(output_png).write_bytes(b"fake-composite-still")
        return Path(output_png)

    monkeypatch.setattr("media_studio.storyboard.render_3d_stage_still", _fake_stage_still)

    project = _make_project(tmp_path)
    scene = _make_scene("composite")
    out_png = tmp_path / "out" / "keyframe.png"

    result = render_scene_keyframe(project, scene, out_png)

    assert result == out_png
    assert calls == ["composite"]
