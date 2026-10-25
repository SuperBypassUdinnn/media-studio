"""Tests for the 3D path.

Blender-dependent tests skip when Blender cannot start at all, but they run here, including the
headless Vulkan render, because a skipped render proves nothing about the path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_studio import blender
from media_studio.cli import main

CUBE = """# unit cube
v -0.5 -0.5 -0.5
v  0.5 -0.5 -0.5
v  0.5  0.5 -0.5
v -0.5  0.5 -0.5
v -0.5 -0.5  0.5
v  0.5 -0.5  0.5
v  0.5  0.5  0.5
v -0.5  0.5  0.5
f 1 2 3 4
f 5 8 7 6
f 1 5 6 2
f 2 6 7 3
f 3 7 8 4
f 4 8 5 1
"""

blender_required = pytest.mark.skipif(not blender.available(), reason="blender cannot start here")


@pytest.fixture(scope="module")
def cube(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("models")
    path = work / "cube.obj"
    path.write_text(CUBE, encoding="utf-8")
    return path


def test_environment_prioritises_system_binaries() -> None:
    """Blender's embedded Python loses its stdlib if a user shim comes first."""

    built = blender.environment()

    assert built.env["PATH"].split(":")[0] == "/usr/bin"
    assert built.env["VK_ICD_FILENAMES"] == blender.VULKAN_ICD


def test_environment_reports_a_shim_when_it_needs_one() -> None:
    built = blender.environment()

    if built.shim is not None:
        assert "ABI shim" in built.describe()
    assert built.describe()  # either "clean" or an explanation, never silence


@blender_required
def test_inspect_measures_a_known_cube(cube: Path) -> None:
    report = blender.inspect(cube)

    assert report.facts["mesh_objects"] == 1
    assert report.facts["vertices"] == 8
    assert report.triangles == 12
    assert report.facts["size"] == [1.0, 1.0, 1.0]
    assert report.problems == []


def test_inspect_refuses_an_unknown_format(tmp_path: Path) -> None:
    stray = tmp_path / "model.xyz"
    stray.write_text("not a model", encoding="utf-8")

    with pytest.raises(blender.BlenderError, match="is not supported"):
        blender.inspect(stray)


def test_inspect_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(blender.BlenderError, match="no such model"):
        blender.inspect(tmp_path / "absent.obj")


@blender_required
def test_convert_repairs_and_re_exports(cube: Path, tmp_path: Path) -> None:
    conversion = blender.convert(cube, tmp_path / "cube.glb")

    assert conversion.destination.is_file()
    assert conversion.triangles == 12
    assert conversion.problems == []
    # The exported file is readable again, which is the point of a conversion.
    assert blender.inspect(conversion.destination).triangles == 12


@blender_required
def test_convert_can_simplify(cube: Path, tmp_path: Path) -> None:
    conversion = blender.convert(cube, tmp_path / "simple.stl", decimate=0.5)

    assert conversion.destination.is_file()
    assert conversion.triangles < 12


@blender_required
def test_turntable_renders_distinct_frames_and_passes(tmp_path: Path, cube: Path) -> None:
    result = blender.turntable(cube, tmp_path / "spin", frames=3, resolution=(128, 72))

    report = result.facts
    assert report["ok"] is True, report["checks"]
    assert report["checks"]["frames_rendered"] is True
    assert report["checks"]["distinct_frames"] is True
    assert report["checks"]["has_contact_sheet"] is True
    assert len(result.frames) == 3
    assert all(entry.get("sha256") for entry in result.frames)
    assert all(float(entry.get("seconds") or 0) > 0 for entry in result.frames)
    assert all("mean_luma" in entry for entry in result.frames)
    assert result.sheet.is_file()
    written = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert written["engine"] == "BLENDER_EEVEE"
    assert written["gpu_backend"] == "vulkan"
    assert written["environment"]


@blender_required
def test_turntable_matches_its_own_baseline(tmp_path: Path, cube: Path) -> None:
    """A repeated render is expected to be the same picture, within a stated tolerance."""

    first = blender.turntable(cube, tmp_path / "first", frames=2, resolution=(96, 54))

    second = blender.turntable(
        cube, tmp_path / "second", frames=2, resolution=(96, 54), baseline=first.report_path
    )

    comparison = second.facts["comparison"]
    assert comparison["matched"] is True, comparison
    assert comparison["compared_frames"] == 2
    assert comparison["differing_frames"] == []


@blender_required
def test_turntable_reports_a_baseline_mismatch(tmp_path: Path, cube: Path) -> None:
    first = blender.turntable(cube, tmp_path / "one", frames=2, resolution=(96, 54))
    baseline = json.loads(first.report_path.read_text(encoding="utf-8"))
    for entry in baseline["frame_reports"]:
        entry["mean_luma"] = float(entry["mean_luma"]) + 40.0
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(baseline), encoding="utf-8")

    second = blender.turntable(
        cube, tmp_path / "two", frames=2, resolution=(96, 54), baseline=tampered
    )

    comparison = second.facts["comparison"]
    assert comparison["matched"] is False
    assert comparison["differing_frames"]
    assert second.facts["checks"]["baseline_matched"] is False
    assert second.facts["ok"] is False


def test_duplicate_frame_detection() -> None:
    facts = {
        "frame_reports": [
            {"frame": 1, "sha256": "aaa"},
            {"frame": 2, "sha256": "bbb"},
            {"frame": 3, "sha256": "aaa"},
        ]
    }

    assert blender._duplicate_frames(facts) == [3]
    assert blender._duplicate_frames({"frame_reports": []}) == []


@blender_required
def test_cli_inspect_and_turntable(
    cube: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["3d", "inspect", str(cube)]) == 0
    output = capsys.readouterr().out
    assert "12 triangles" in output
    assert "verdict: clean" in output

    assert main(["3d", "inspect", str(cube), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["triangles"] == 12

    assert main(["3d", "turntable", str(cube), "--frames", "2", "--resolution", "96x54"]) == 0
    printed = capsys.readouterr().out
    assert "verdict: pass" in printed
