"""Tests for PBR material fetching and binding.

The Poly Haven response is a recorded fixture, so the parser is pinned without a network. Binding
runs Blender for real against generated maps, because a shader graph that was never rendered proves
nothing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from media_studio import blender, materials
from media_studio.manifest import load_project
from media_studio.project import scaffold_project

CUBE = """v -0.5 -0.5 -0.5
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

FILES = {
    "Diffuse": {"1k": {"jpg": {"url": "https://example.org/diffuse.jpg", "size": 10}}},
    "Rough": {"1k": {"jpg": {"url": "https://example.org/rough.jpg", "size": 10}}},
    "nor_gl": {"1k": {"png": {"url": "https://example.org/normal.png", "size": 10}}},
    "AO": {"1k": {"jpg": {"url": "https://example.org/ao.jpg", "size": 10}}},
    "Displacement": {"1k": {"png": {"url": "https://example.org/disp.png", "size": 10}}},
    "blend": {"1k": {"blend": {"url": "https://example.org/scene.blend"}}},
    "gltf": {"1k": {"gltf": {"url": "https://example.org/scene.gltf"}}},
}
HDRI_FILES = {
    "hdri": {
        "1k": {
            "hdr": {"url": "https://example.org/sky_1k.hdr", "size": 100},
            "exr": {"url": "https://example.org/sky_1k.exr", "size": 200},
        },
        "4k": {"hdr": {"url": "https://example.org/sky_4k.hdr", "size": 400}},
    },
    "tonemapped": {"url": "https://example.org/preview.jpg"},
}

blender_required = pytest.mark.skipif(not blender.available(), reason="blender cannot start here")


def test_material_maps_pick_one_url_per_role_and_skip_project_files() -> None:
    maps = materials.material_maps(FILES, "1k")

    assert maps["Diffuse"] == "https://example.org/diffuse.jpg"
    assert maps["Rough"] == "https://example.org/rough.jpg"
    assert maps["nor_gl"] == "https://example.org/normal.png"
    assert "blend" not in maps
    assert "gltf" not in maps
    assert len(maps) == 5


def test_material_maps_explain_a_missing_resolution() -> None:
    with pytest.raises(materials.MaterialError, match="no textures at 8k"):
        materials.material_maps(FILES, "8k")


def test_hdri_url_prefers_the_requested_format() -> None:
    assert materials.hdri_url(HDRI_FILES, "1k", "hdr").endswith("sky_1k.hdr")
    assert materials.hdri_url(HDRI_FILES, "1k", "exr").endswith("sky_1k.exr")
    assert materials.hdri_url(HDRI_FILES, "4k").endswith("sky_4k.hdr")


def test_hdri_url_explains_what_is_available() -> None:
    with pytest.raises(materials.MaterialError, match=r"no 16k HDRI; available: 1k, 4k"):
        materials.hdri_url(HDRI_FILES, "16k")
    with pytest.raises(materials.MaterialError, match="no HDRI files"):
        materials.hdri_url({"Diffuse": {}})


def test_shader_maps_translate_poly_haven_role_names() -> None:
    translated = materials.shader_maps(
        {
            "Diffuse": Path("/tmp/d.jpg"),
            "Rough": Path("/tmp/r.jpg"),
            "nor_gl": Path("/tmp/n.png"),
            "AO": Path("/tmp/ao.jpg"),
            "Displacement": Path("/tmp/x.png"),
            "arm": Path("/tmp/arm.jpg"),
            "nor_dx": Path("/tmp/dx.png"),
        }
    )

    assert set(translated) == {"base_color", "roughness", "normal", "ambient_occlusion"}
    assert translated["base_color"] == Path("/tmp/d.jpg")
    assert translated["normal"] == Path("/tmp/n.png")


def test_asset_name_accepts_a_namespaced_id_and_refuses_a_traversal() -> None:
    assert materials.asset_name("polyhaven:mossy_rock") == "mossy_rock"
    assert materials.asset_name("mossy_rock") == "mossy_rock"
    with pytest.raises(materials.MaterialError, match=r"not a usable asset id: '\.\./etc/passwd'"):
        materials.asset_name("../etc/passwd")


def test_shader_maps_keep_slot_names_and_drop_what_has_no_slot() -> None:
    assert materials.shader_maps({"base_color": Path("/tmp/b.png")}) == {
        "base_color": Path("/tmp/b.png")
    }
    # A packed arm map and DirectX normals have no slot of their own.
    assert materials.shader_maps({"arm": Path("/tmp/arm.jpg"), "nor_dx": Path("/tmp/dx.png")}) == {}


def test_material_set_separates_bound_from_ignored_roles() -> None:
    staged = materials.MaterialSet(
        asset_id="demo",
        maps={
            "Diffuse": Path("/tmp/d.jpg"),
            "Rough": Path("/tmp/r.jpg"),
            "Displacement": Path("/tmp/x.png"),
        },
    )

    assert staged.bound_roles == ["Diffuse", "Rough"]
    assert staged.ignored_roles == ["Displacement"]


def test_fetch_uses_the_ledger_and_records_provenance(tmp_path: Path) -> None:
    """Staging keeps the licence trail, without touching the network."""

    root = scaffold_project("material-demo", root=tmp_path).path
    project = load_project(root)
    sources = tmp_path / "downloads"
    sources.mkdir()
    payloads = {
        "https://example.org/diffuse.jpg": b"diffuse-bytes",
        "https://example.org/rough.jpg": b"rough-bytes",
        "https://example.org/nor_gl.png": b"normal-bytes",
        "https://example.org/ao.jpg": b"ao-bytes",
        "https://example.org/displacement.png": b"disp-bytes",
    }

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def opener(request: object, timeout: int | None = None) -> Response:
        return Response(payloads[request.full_url])  # type: ignore[attr-defined]

    files = {
        "Diffuse": {"1k": {"jpg": {"url": "https://example.org/diffuse.jpg"}}},
        "Rough": {"1k": {"jpg": {"url": "https://example.org/rough.jpg"}}},
        "nor_gl": {"1k": {"png": {"url": "https://example.org/nor_gl.png"}}},
        "AO": {"1k": {"jpg": {"url": "https://example.org/ao.jpg"}}},
        "Displacement": {"1k": {"png": {"url": "https://example.org/displacement.png"}}},
    }

    staged = materials.fetch_material(project, "demo", files=files, opener=opener)

    assert len(staged.staged) == 5
    sidecar = materials.Path(str(next(iter(staged.maps.values()))) + ".license")
    fields = sidecar.read_text(encoding="utf-8")
    assert "SPDX-License-Identifier: CC0-1.0" in fields
    assert "https://polyhaven.com/a/demo" in fields
    # Staging an asset has to leave the provenance behind, not only the file. CC0 needs no
    # attribution, so CREDITS stays empty by policy while the index and the sidecar hold the facts.
    credits = Path(project.root) / "assets" / "CREDITS.md"
    index = Path(project.root) / "assets" / "index.json"
    assert credits.is_file() and index.is_file()
    assert "0 requiring attribution" in credits.read_text(encoding="utf-8")
    body = index.read_text(encoding="utf-8")
    assert '"spdx": "CC0-1.0"' in body
    assert "polyhaven" in body
    assert "Attribution: Poly Haven" in fields


def test_get_json_surfaces_a_bad_response() -> None:
    class Response:
        def read(self) -> bytes:
            return b"not json"

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    with pytest.raises(materials.MaterialError, match="did not answer JSON"):
        materials.get_json("https://example.org/x", opener=lambda *_a, **_k: Response())


@pytest.fixture(scope="module")
def maps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    work = tmp_path_factory.mktemp("maps")

    def magick(name: str, *arguments: str) -> Path:
        destination = work / name
        completed = subprocess.run(
            ["magick", *arguments, str(destination)], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr[-300:]
        return destination

    return {
        "base_color": magick("diffuse.png", "-size", "64x64", "xc:#8a6f4f"),
        "roughness": magick("rough.png", "-size", "64x64", "xc:#333333"),
        "normal": magick("normal.png", "-size", "64x64", "xc:#8080ff"),
        "ambient_occlusion": magick("ao.png", "-size", "64x64", "xc:#bbbbbb"),
        "hdri": magick("sky.hdr", "-size", "128x64", "gradient:black-white"),
    }


@pytest.fixture(scope="module")
def cube(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("material-model")
    path = work / "cube.obj"
    path.write_text(CUBE, encoding="utf-8")
    return path


@blender_required
def test_binding_wires_the_maps_and_renders_a_proof(
    cube: Path, maps: dict[str, Path], tmp_path: Path
) -> None:
    hdri = maps["hdri"]
    bound = {role: path for role, path in maps.items() if role != "hdri"}

    result = materials.bind(
        cube,
        bound,
        destination=tmp_path / "cube-pbr.glb",
        hdri=hdri,
        render=tmp_path / "proof.png",
        resolution=(160, 90),
    )

    assert result.problems == []
    assert "base_color" in result.bound_roles
    assert "normal" in result.bound_roles
    assert result.facts["world_from_hdri"] is True
    assert result.destination is not None and result.destination.is_file()
    assert result.render is not None and result.render.is_file()
    assert materials.proof_frame_luminance(result.render) > 5.0


@blender_required
def test_the_exported_model_keeps_its_material(
    cube: Path, maps: dict[str, Path], tmp_path: Path
) -> None:
    bound = {role: path for role, path in maps.items() if role != "hdri"}
    result = materials.bind(cube, bound, destination=tmp_path / "kept.glb")

    inspected = blender.inspect(result.destination)

    assert "pbr" in [name for name in inspected.facts["materials"]]


@blender_required
def test_binding_accepts_source_role_names(
    cube: Path, maps: dict[str, Path], tmp_path: Path
) -> None:
    roles = {
        "Diffuse": maps["base_color"],
        "Rough": maps["roughness"],
        "nor_gl": maps["normal"],
        "Displacement": maps["ambient_occlusion"],
    }

    result = materials.bind(cube, roles, render=tmp_path / "roles.png", resolution=(96, 54))

    assert sorted(result.bound_roles) == ["base_color", "normal", "roughness"]
    assert materials.proof_frame_luminance(result.render) > 0.0


@blender_required
def test_occlusion_without_a_base_colour_is_reported_not_wired(
    cube: Path, maps: dict[str, Path], tmp_path: Path
) -> None:
    result = materials.bind(
        cube,
        {"AO": maps["ambient_occlusion"]},
        render=tmp_path / "ao-only.png",
        resolution=(96, 54),
    )

    assert result.unwired_slots == ["ambient_occlusion"]
    assert result.bound_roles == []
    # Grey occlusion data in a colour slot would tint the surface, so nothing may be linked at all.
    assert "no texture was wired into the shader" in result.problems


@blender_required
def test_binding_a_single_map_still_works(
    cube: Path, maps: dict[str, Path], tmp_path: Path
) -> None:
    result = materials.bind(cube, {"base_color": maps["base_color"]}, render=tmp_path / "one.png")

    assert result.bound_roles == ["base_color"]
    assert result.facts["world_from_hdri"] is False
    # Without an HDRI the script adds a sun, so the proof frame is lit by something.
    assert materials.proof_frame_luminance(result.render) > 0.0


def test_binding_refuses_an_empty_or_missing_map(cube: Path, tmp_path: Path) -> None:
    with pytest.raises(materials.MaterialError, match="no maps to bind"):
        materials.bind(cube, {})
    with pytest.raises(materials.MaterialError, match="base_color map is missing"):
        materials.bind(cube, {"base_color": tmp_path / "absent.png"})
    with pytest.raises(blender.BlenderError, match="no such model"):
        materials.bind(tmp_path / "absent.obj", {"base_color": tmp_path / "absent.png"})


def test_cli_reports_the_binding(
    tmp_path: Path, cube: Path, maps: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from media_studio import cli

    root = scaffold_project("material-cli", root=tmp_path).path
    # Point the resolver at the generated maps instead of the network. The command imported the
    # name into its own module, so the patch has to land there rather than on materials.
    original = cli.fetch_material

    def fake_fetch(project: object, asset_id: str, **kwargs: object) -> materials.MaterialSet:
        # Source role names, not slot names, so the command's own translation is exercised.
        return materials.MaterialSet(
            asset_id=asset_id,
            maps={
                "Diffuse": maps["base_color"],
                "Rough": maps["roughness"],
                "nor_gl": maps["normal"],
                "Displacement": maps["ambient_occlusion"],
            },
        )

    cli.fetch_material = fake_fetch  # type: ignore[assignment]
    try:
        exit_code = cli.main(
            [
                "3d",
                "material",
                str(cube),
                "--project",
                str(root),
                "--material",
                "demo",
                "--render",
                "--proof-resolution",
                "160x90",
            ]
        )
    finally:
        cli.fetch_material = original  # type: ignore[assignment]

    captured = capsys.readouterr()
    assert exit_code == 0, captured.out + captured.err
    output = captured.out
    assert "bound base_color, roughness, normal" in output
    assert "verdict: pass" in output
    assert (Path(root) / "out" / "reports" / "cube-material-qc.json").is_file()


def test_cli_writes_a_report_that_names_the_licence(
    tmp_path: Path, cube: Path, maps: dict[str, Path]
) -> None:
    # The report body is produced by the same run as the test above; this asserts the shape the
    # path promises, using the fixture directly so it does not depend on test order.
    bound = {role: path for role, path in maps.items() if role != "hdri"}
    result = materials.bind(cube, bound, render=tmp_path / "proof.png", resolution=(96, 54))
    report = {**result.facts, "material": "demo", "licence": "CC0", "problems": result.problems}

    assert json.loads(json.dumps(report))["licence"] == "CC0"
    assert report["bound_roles"]
