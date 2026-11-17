"""Tests for the logo derivative kit and ``ms design logo``.

Rendering runs the real toolchain (Inkscape, ImageMagick) and the checks are the same measurements
the report publishes, so a passing test means the geometry was actually inspected.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from media_studio.cli import main
from media_studio.ledger import stage_asset
from media_studio.logo import logo_kit
from media_studio.manifest import load_project
from media_studio.project import scaffold_project

SVG_LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">'
    '  <circle cx="100" cy="100" r="60" fill="#C8A24A"/>'
    '  <text x="100" y="170" font-size="16" text-anchor="middle" fill="#101010">MK</text>'
    "</svg>"
)

MANIFEST = """slug: logo-demo
title: Logo Demo
assets:
  logo: {logo}
design:
  logo: logo
  variants:
    - {{id: post, template: social-post, size: 1080x1080, headline: "Logo", subhead: ""}}
"""


def _logo_project(tmp_path: Path, *, full_bleed: bool = False, raster: bool = False) -> Path:
    root = scaffold_project("logo-demo", root=tmp_path).path
    project = load_project(root)

    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)

    if raster:
        png = sources / "logo.png"
        subprocess.run(
            [
                "magick",
                "-size",
                "200x200",
                "xc:none",
                "-fill",
                "#C8A24A",
                "-draw",
                "circle 100,100 100,40",
                str(png),
            ],
            check=True,
        )
        source = png
    else:
        logo = sources / "logo.svg"
        svg = SVG_LOGO.replace('cy="100"', f'cy="{10 if full_bleed else 100}"')
        svg = svg.replace('r="60"', f'r="{90 if full_bleed else 60}"')
        logo.write_text(svg, encoding="utf-8")
        source = logo

    staged_logo = stage_asset(
        project,
        class_name="design",
        license_text="CC0",
        local_file=source,
        provider="fixture",
        retrieved_at="2026-10-25T00:00:00+00:00",
    )
    manifest = MANIFEST.format(logo=staged_logo.relative)
    (root / "project.yaml").write_text(manifest, encoding="utf-8")
    return root


def test_logo_kit_derives_every_variant_and_size(tmp_path: Path) -> None:
    project = load_project(_logo_project(tmp_path))
    report = logo_kit(project, sizes=(256, 64, 32))
    assert len(report.entries) == 9
    assert all(entry.png.is_file() for entry in report.entries)
    assert report.sheet is not None and report.sheet.is_file()
    assert not report.problems


def test_mono_and_reversed_recolour_the_solid_fills(tmp_path: Path) -> None:
    project = load_project(_logo_project(tmp_path))
    logo_kit(project, sizes=(64,))
    mono = (project.root / "out/images/logo/mono.svg").read_text()
    reversed_svg = (project.root / "out/images/logo/reversed.svg").read_text()
    assert 'fill="#101010"' in mono and 'fill="#FFFFFF"' in reversed_svg


def test_a_full_bleed_logo_fails_the_clear_space_check(tmp_path: Path) -> None:
    project = load_project(_logo_project(tmp_path, full_bleed=True))
    report = logo_kit(project, sizes=(64,))
    assert any("clear space" in problem for problem in report.problems)


@pytest.mark.skipif(shutil.which("potrace") is None, reason="potrace not installed")
def test_a_raster_logo_is_traced_before_derivation(tmp_path: Path) -> None:
    project = load_project(_logo_project(tmp_path, raster=True))
    report = logo_kit(project, sizes=(64,))
    assert (project.root / "out/images/logo/source.svg").is_file()
    assert report.report_path.is_file()


def test_design_logo_cli_prints_sheet_and_exits_ok(tmp_path: Path) -> None:
    project = load_project(_logo_project(tmp_path))
    exit_code = main(["design", "logo", str(project.root)])
    assert exit_code == 0
    captured = project.root / "out" / "reports" / "logo-demo-logo.json"
    assert captured.is_file()
    json.loads(captured.read_text(encoding="utf-8"))


@pytest.mark.skipif(shutil.which("potrace") is None, reason="potrace not installed")
def test_design_logo_cli_raster_exits_ok_with_note(tmp_path: Path, capsys) -> None:
    project = load_project(_logo_project(tmp_path, raster=True))
    exit_code = main(["design", "logo", str(project.root)])
    out = capsys.readouterr().out
    assert any(line.startswith("note:") for line in out.splitlines()), out
    captured = project.root / "out" / "reports" / "logo-demo-logo.json"
    report = json.loads(captured.read_text(encoding="utf-8"))
    assert report["notes"]
    assert not any("raster" in problem for problem in report["problems"])
    assert exit_code == (0 if not report["problems"] else 1)


def test_design_logo_cli_full_bleed_exits_fail(tmp_path: Path) -> None:
    project = load_project(_logo_project(tmp_path, full_bleed=True))
    exit_code = main(["design", "logo", str(project.root)])
    assert exit_code == 1
