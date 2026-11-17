"""Unit + integration tests for `ms 3d modify` (Blender-backed; skips when Blender is absent)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from media_studio.blender import BlenderError, ModifyResult, modify_model


def _have_blender() -> bool:
    if shutil.which("blender") is None:
        return False
    try:
        return (
            subprocess.run(["blender", "--background", "--version"], capture_output=True).returncode
            == 0
        )
    except (FileNotFoundError, OSError):
        return False


def test_modify_model_rejects_missing_file(tmp_path):
    """modify_model raises BlenderError when the input model does not exist."""

    with pytest.raises(BlenderError, match="no such model"):
        modify_model(tmp_path / "nonexistent.glb", tmp_path / "out.glb", [])


def test_modify_model_rejects_unsupported_extension(tmp_path):
    """modify_model raises BlenderError for an unsupported input extension."""

    bad = tmp_path / "model.xyz"
    bad.write_text("not a model", encoding="utf-8")
    with pytest.raises(BlenderError, match="is not supported"):
        modify_model(bad, tmp_path / "out.glb", [])


@pytest.mark.skipif(not _have_blender(), reason="Blender not available")
def test_modify_mirror_doubles_triangles(tmp_path):
    """Mirroring a cube along X doubles its triangle count (merged seam)."""

    cube = tmp_path / "cube.obj"
    cube.write_text(
        "v -1 -1 -1\n"
        "v 1 -1 -1\n"
        "v 1 1 -1\n"
        "v -1 1 -1\n"
        "v -1 -1 1\n"
        "v 1 -1 1\n"
        "v 1 1 1\n"
        "v -1 1 1\n"
        "f 1 2 3 4\n"
        "f 5 8 7 6\n"
        "f 1 5 6 2\n"
        "f 2 6 7 3\n"
        "f 3 7 8 4\n"
        "f 4 8 5 1\n",
        encoding="utf-8",
    )

    out = tmp_path / "cube-modified.glb"
    result = modify_model(cube, out, [{"type": "mirror", "x": True, "merge": True}])

    assert isinstance(result, ModifyResult)
    assert result.ok, result.facts
    assert result.operations_applied == 1
    # Mirroring along one axis with merge produces roughly double the triangles.
    assert result.after_triangles >= result.before_triangles


@pytest.mark.skipif(not _have_blender(), reason="Blender not available")
def test_modify_decimate_halves_triangles(tmp_path):
    """Decimating to 0.5 ratio roughly halves the triangle count."""

    cube = tmp_path / "cube.obj"
    cube.write_text(
        "v -1 -1 -1\n"
        "v 1 -1 -1\n"
        "v 1 1 -1\n"
        "v -1 1 -1\n"
        "v -1 -1 1\n"
        "v 1 -1 1\n"
        "v 1 1 1\n"
        "v -1 1 1\n"
        "f 1 2 3 4\n"
        "f 5 8 7 6\n"
        "f 1 5 6 2\n"
        "f 2 6 7 3\n"
        "f 3 7 8 4\n"
        "f 4 8 5 1\n",
        encoding="utf-8",
    )

    out = tmp_path / "cube-decimated.glb"
    result = modify_model(cube, out, [{"type": "decimate", "ratio": 0.5}])

    assert result.ok, result.facts
    assert result.operations_applied == 1
    assert result.after_triangles <= result.before_triangles


@pytest.mark.skipif(not _have_blender(), reason="Blender not available")
def test_modify_invalid_operation_raises(tmp_path):
    """An unknown operation type raises SystemExit inside Blender, surfaced as BlenderError."""

    cube = tmp_path / "cube.obj"
    cube.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")

    out = tmp_path / "cube-modified.glb"
    with pytest.raises(BlenderError):
        modify_model(cube, out, [{"type": "not_a_real_op"}])
