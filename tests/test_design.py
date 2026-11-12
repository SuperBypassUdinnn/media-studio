"""Tests for the design path and the font layer.

Rendering runs the real toolchain (Inkscape, ImageMagick) and the checks are the same measurements
the report publishes, so a passing test means the geometry and contrast were actually inspected.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from media_studio import design, fonts
from media_studio.cli import main
from media_studio.ledger import stage_asset
from media_studio.manifest import ManifestError, load_project
from media_studio.project import scaffold_project

DEJAVU = Path("/usr/share/fonts/TTF/DejaVuSans.ttf")
MANIFEST = """slug: design-demo
title: Design Demo
deliverables:
  - {{id: post, kind: image, resolution: 1080x1080}}
assets:
  logo: {logo}
  font-heading: {heading}
brand:
  palette:
    primary: "{primary}"
    accent: "#C8A24A"
    ink: "{ink}"
design:
  logo: logo
  fonts:
    heading: font-heading
    body: font-heading
  variants:
    - {{id: post, template: social-post, size: {size},
       headline: "{headline}", subhead: "{subhead}"{variant_extra}}}
"""


def build_project(
    tmp_path: Path, variant_extra: str = "", size: str = "1080x1080", **overrides: str
) -> Path:
    root = scaffold_project("design-demo", root=tmp_path).path
    project = load_project(root)

    # Stage from outside the project: the ledger copies files in.
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    logo = sources / "logo.svg"
    logo.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">'
        '<circle cx="60" cy="60" r="56" fill="#C8A24A"/></svg>',
        encoding="utf-8",
    )
    staged_logo = stage_asset(
        project,
        class_name="design",
        license_text="CC0",
        local_file=logo,
        provider="fixture",
        retrieved_at="2026-10-25T00:00:00+00:00",
    )
    staged_font = stage_asset(
        project,
        class_name="design",
        license_text="OFL-1.1",
        local_file=DEJAVU,
        provider="system",
        retrieved_at="2026-10-25T00:00:00+00:00",
    )

    values = {
        "logo": staged_logo.relative,
        "heading": staged_font.relative,
        "primary": "#123B2E",
        "ink": "#FFFFFF",
        "headline": "Kerja Rapi",
        "subhead": "Subhead",
        "size": size,
        # The extra keys are appended inside the variant flow mapping, so a
        # leading comma separates them from subhead when present.
        "variant_extra": f", {variant_extra}" if variant_extra else "",
    }
    values.update(overrides)
    manifest = MANIFEST.format(**values)
    (root / "project.yaml").write_text(manifest, encoding="utf-8")
    return root


def test_render_exports_every_format_and_passes(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path))

    report = design.render(project)

    assert report.ok, report.problems
    variant = report.variants[0]
    export = variant["export"]
    assert isinstance(export, dict)
    for key in ("svg", "png", "pdf"):
        assert (project.root / str(export[key])).is_file(), key
    assert design._png_size(project.root / str(export["png"])) == (1080, 1080)
    assert report.sheet is not None and report.sheet.is_file()
    assert report.report_path is not None and report.report_path.is_file()
    written = json.loads(report.report_path.read_text(encoding="utf-8"))
    assert written["ok"] is True
    assert written["variants"][0]["size"] == [1080, 1080]


def test_the_rendered_svg_uses_the_brand_tokens_and_the_staged_font(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path))

    design.render(project)

    svg = (project.root / "out" / "images" / "post.svg").read_text(encoding="utf-8")
    assert "#123B2E" in svg  # primary background
    assert "DejaVu Sans" in svg  # the staged font resolved to a family name
    assert "Kerja Rapi" in svg
    assert "{{" not in svg


def test_a_low_contrast_palette_fails_the_check(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path, primary="#101010", ink="#151515"))

    report = design.render(project)

    assert not report.ok
    assert any("contrast" in problem for problem in report.problems)
    assert report.variants[0]["ok"] is False


def test_a_long_headline_overflows_the_safe_area(tmp_path: Path) -> None:
    project = load_project(
        build_project(tmp_path, headline="Judul yang sangat panjang sekali sehingga melewati batas")
    )

    report = design.render(project)

    assert not report.ok
    assert any("safe area" in problem for problem in report.problems)


def test_an_unknown_template_is_refused_before_rendering(tmp_path: Path) -> None:
    root = build_project(tmp_path)
    body = (root / "project.yaml").read_text(encoding="utf-8")
    (root / "project.yaml").write_text(body.replace("social-post", "nope"), encoding="utf-8")
    project = load_project(root)

    problems = design.validate_design(project)

    assert any("no design template 'nope'" in problem for problem in problems)
    with pytest.raises(design.DesignError, match="no design template"):
        design.render(project)


def test_validate_design_reports_manifest_problems(tmp_path: Path) -> None:
    root = build_project(tmp_path)
    body = (root / "project.yaml").read_text(encoding="utf-8")
    body = body.replace('headline: "Kerja Rapi"', "headline: ''")
    body = body.replace('subhead: "Subhead"', "subhead: ''")
    body = body.replace("logo: logo", "logo: missing-asset")
    body = body.replace("heading: font-heading", "heading: undeclared")
    (root / "project.yaml").write_text(body, encoding="utf-8")

    problems = design.validate_design(load_project(root))

    joined = "\n".join(problems)
    assert "needs a headline or a subhead" in joined
    assert "design.logo" in joined
    assert "design.fonts.heading" in joined


def test_check_only_measures_what_was_already_rendered(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path))
    design.render(project)

    report = design.render(project, check_only=True)

    assert report.ok
    assert report.report_path is None  # check-only does not rewrite the report


def test_cli_renders_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = build_project(tmp_path)

    assert main(["design", "render", str(root)]) == 0
    output = capsys.readouterr().out
    assert "post" in output and "verdict: pass" in output

    assert main(["design", "render", str(root), "--check-only"]) == 0
    assert "verdict: pass" in capsys.readouterr().out


def test_trim_and_pad_shrink_then_reframe_the_png(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path, variant_extra="trim: true, pad: 12"))
    report = design.render(project)
    finish = report.variants[0]["finish"]
    assert finish["trim"] is True and finish["pad"] == 12
    w, h = finish["final_size"]
    assert w <= 1080 and h <= 1080  # trimmed content plus 2x12 border never exceeds canvas


def test_finish_key_value_validation_rejects_bad_input(tmp_path: Path) -> None:
    # pad: -4 produces a manifest problem via validate_design
    problems = design.validate_design(
        load_project(build_project(tmp_path, variant_extra="pad: -4"))
    )
    assert any("pad" in problem for problem in problems)


def test_finish_profile_is_recorded(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path, variant_extra="profile: srgb"))
    report = design.render(project)
    assert report.variants[0]["finish"]["profile"] == "srgb"


def test_finish_profile_adobe_rgb_raises_at_load(tmp_path: Path) -> None:
    root = build_project(tmp_path, variant_extra="profile: adobe-rgb")
    with pytest.raises(ManifestError, match="adobe-rgb"):
        load_project(root)


def test_finish_trim_yes_raises_at_load(tmp_path: Path) -> None:
    root = build_project(tmp_path, variant_extra='trim: "yes"')
    with pytest.raises(ManifestError, match="yes"):
        load_project(root)


def test_contrast_ratio_matches_known_values() -> None:
    assert design.contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.1)
    assert design.contrast_ratio("#777777", "#777777") == pytest.approx(1.0, abs=0.01)
    assert design.contrast_ratio("#FFFFFF", "#123B2E") > 7.0


def test_inventory_finds_families_with_indonesian_coverage() -> None:
    families = {font.family for font in fonts.inventory()}

    assert "DejaVu Sans" in families
    dejavu = next(font for font in fonts.inventory() if font.family == "DejaVu Sans")
    assert dejavu.covers_indonesian
    assert dejavu.file.is_file()


def test_search_filters_and_ranks() -> None:
    installed = fonts.inventory()

    matches = fonts.search(installed, "dejavu", limit=5)
    assert matches and all("dejavu" in font.family.lower() for font in matches)

    mono = fonts.search(installed, "mono", monospace=True, limit=3)
    assert mono and all(font.monospace for font in mono)

    assert fonts.search(installed, "dejavu", require_languages=("xx-not-a-language",)) == []


def test_embedding_permission_reads_the_font_flags() -> None:
    allowed, reason = fonts.embedding_permission(DEJAVU)

    assert allowed is True
    assert "fsType" in reason


def test_the_project_fontconfig_makes_staged_fonts_resolvable(tmp_path: Path) -> None:
    """The point of the per-project config: no system install, still resolvable."""

    root = build_project(tmp_path)
    project = load_project(root)
    environment = fonts.font_environment(project)

    assert Path(environment["FONTCONFIG_FILE"]).is_file()
    completed = subprocess.run(
        ["fc-list", "--format", "%{file}\n"], capture_output=True, text=True, env=environment
    )
    staged = project.root / project.assets["font-heading"]

    assert staged.is_file()
    assert completed.returncode == 0
    assert any(str(staged) in line for line in completed.stdout.splitlines())


def test_specimen_sheet_renders_candidates_with_the_projects_words(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path))
    environment = fonts.font_environment(project)
    candidates = fonts.search(fonts.inventory(environment), "dejavu", limit=2)

    sheet = fonts.specimen_sheet(
        candidates,
        "Kerja Rapi",
        project.root / "out" / "images" / "specimen.png",
        environment=environment,
    )

    assert sheet.is_file()
    assert sheet.stat().st_size > 1000


def test_variant_image_cutout_composites_into_svg_and_png(tmp_path: Path) -> None:
    project = load_project(
        build_project(
            tmp_path,
            variant_extra='image: "logo", image_fit: "cover", image_position: "right"',
        )
    )

    report = design.render(project)

    assert report.ok, report.problems
    svg = (project.root / "out" / "images" / "post.svg").read_text(encoding="utf-8")
    root = ElementTree.fromstring(svg)
    foreground = next(
        (element for element in root.iter() if element.get("id") == "foreground"), None
    )
    assert foreground is not None
    assert str(foreground.get("href", "")).startswith("file://") or foreground.get(
        "{http://www.w3.org/1999/xlink}href", ""
    ).startswith("file://")
    assert foreground.get("preserveAspectRatio") == "xMidYMid slice"
    assert int(foreground.get("width", "0")) > 0
    # The composite actually rasterises: the export exists and measured its size.
    export = report.variants[0]["export"]
    assert isinstance(export, dict)
    assert design._png_size(project.root / str(export["png"])) == (1080, 1080)

    project_no_img = load_project(build_project(tmp_path / "no_img"))
    report_no_img = design.render(project_no_img)
    assert report_no_img.ok, report_no_img.problems
    svg_no_img = (project_no_img.root / "out" / "images" / "post.svg").read_text(encoding="utf-8")
    assert 'id="foreground"' not in svg_no_img


def test_safe_area_scales_with_1080p_canvas_no_overflow(tmp_path: Path) -> None:
    project = load_project(build_project(tmp_path, size="1080x1920"))

    report = design.render(project)

    assert report.ok, report.problems
    svg = (project.root / "out" / "images" / "post.svg").read_text(encoding="utf-8")
    root = ElementTree.fromstring(svg)
    safe = next(element for element in root.iter() if element.get("id") == "safe")
    # A 9:16 canvas keeps a proportional 5% margin instead of the square template's
    # fixed pixel box, and the text scales with it.
    assert (
        safe.get("x"),
        safe.get("y"),
        safe.get("width"),
        safe.get("height"),
    ) == ("54", "96", "972", "1728")
    headline = next(element for element in root.iter() if element.get("id") == "headline")
    assert float(headline.get("font-size", "0")) > 96  # scaled up for the taller canvas


def test_variant_size_overrides_reach_the_template(tmp_path: Path) -> None:
    project = load_project(
        build_project(tmp_path, variant_extra="headline_size: 64, subhead_size: 32")
    )

    svg = design.compose(project, project.design.variants[0], environment={})

    assert 'font-size="64"' in svg
    assert 'font-size="32"' in svg


def test_design_render_accepts_deliverable_alias(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_project(tmp_path)

    assert main(["design", "render", str(root), "--deliverable", "post"]) == 0
    assert "post" in capsys.readouterr().out


def test_specimen_sheet_needs_a_candidate(tmp_path: Path) -> None:
    with pytest.raises(fonts.FontError, match="at least one font"):
        fonts.specimen_sheet([], "Kerja", tmp_path / "sheet.png")


def test_cli_fonts_search_and_specimen(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["fonts", "search", "dejavu", "--limit", "2"]) == 0
    assert "DejaVu Sans" in capsys.readouterr().out

    root = build_project(tmp_path)
    assert main(["fonts", "specimen", str(root), "--text", "Kerja Rapi", "--query", "dejavu"]) == 0
    assert "font-specimen.png" in capsys.readouterr().out
