"""Tests for media_studio.svg_kit: taper and arc geometry kernel."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from media_studio.svg_kit import (
    SvgError,
    _group_paths_by_fill,
    arc_path,
    build_spec,
    check_safe_area,
    compare,
    render_png,
    taper_path,
    trace,
    write_svg,
)


def _numbers(d: str) -> list[float]:
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", d)]


def test_taper_straight_spine_starts_and_ends_at_half_width_offsets():
    d = taper_path(
        {"from": [100, 100], "angle": 0, "length": 100, "widths": [20, 10], "caps": "flat"}
    )
    xs, ys = _numbers(d)[0::2], _numbers(d)[1::2]
    assert min(xs) == pytest.approx(100, abs=0.5) and max(xs) == pytest.approx(200, abs=0.5)
    assert min(ys) == pytest.approx(90, abs=0.5)  # half of the 20 px base width above the spine
    assert max(ys) == pytest.approx(110, abs=0.5)  # half of the 20 px base width below the spine
    tip = [y for x, y in zip(xs, ys, strict=True) if x >= 199]
    assert min(tip) == pytest.approx(95, abs=0.5) and max(tip) == pytest.approx(105, abs=0.5)
    assert d.rstrip().endswith("Z")


def test_taper_width_arity_mismatch_raises():
    with pytest.raises(SvgError, match="widths"):
        taper_path({"points": [[0, 0], [50, 0], [100, 0]], "widths": [10, 4]})


def test_taper_bend_bulges_toward_the_bend_sign():
    d = taper_path({"from": [0, 0], "angle": 0, "length": 100, "bend": 30, "width": 10})
    xs, ys = _numbers(d)[0::2], _numbers(d)[1::2]
    middle = [y for x, y in zip(xs, ys, strict=True) if 40 <= x <= 60]
    assert min(middle) < -10  # the midpoint bows by the full bend (30 px), plus half width


def test_arc_sagitta_bulges_and_pie_closes():
    d = arc_path({"from": [0, 0], "to": [100, 0], "sag": 20, "close": "pie"})
    assert "A" in d and d.rstrip().endswith("Z")


def test_arc_explicit_angles_are_clockwise_on_screen():
    d = arc_path(
        {"cx": 50, "cy": 50, "rx": 40, "ry": 40, "start_deg": 0, "end_deg": 90, "close": "open"}
    )
    xs, ys = _numbers(d)[0::2], _numbers(d)[1::2]
    assert xs[0] == pytest.approx(90, abs=0.5) and ys[0] == pytest.approx(50, abs=0.5)
    assert xs[-1] == pytest.approx(50, abs=0.5) and ys[-1] == pytest.approx(90, abs=0.5)


def _full_spec() -> dict:
    """One spec exercising every v1 element type from spec section 4.2."""

    return {
        "canvas": {"width": 1080, "height": 1350},
        "safe_area": 64,
        "background": {"type": "solid", "color": "{primary}"},
        "variables": {"tagline": "Belajar Tanpa Batas"},
        "elements": [
            {
                "type": "text",
                "role": "heading",
                "text": "Judul",
                "x": "center",
                "y": 240,
                "size": 96,
                "color": "{ink}",
            },
            {
                "type": "rect",
                "x": 64,
                "y": 420,
                "width": 200,
                "height": 8,
                "fill": "{accent}",
                "radius": 4,
            },
            {"type": "circle", "cx": 100, "cy": 100, "r": 20, "fill": "{primary}"},
            {"type": "ellipse", "cx": 200, "cy": 100, "rx": 30, "ry": 20, "fill": "{accent}"},
            {
                "type": "line",
                "x1": 10,
                "y1": 10,
                "x2": 90,
                "y2": 90,
                "stroke": "{ink}",
                "stroke_width": 2,
            },
            {"type": "polygon", "points": [[0, 0], [50, 0], [25, 50]], "fill": "{primary}"},
            {
                "type": "polyline",
                "points": [[0, 0], [50, 0], [25, 50]],
                "stroke": "{ink}",
                "stroke_width": 2,
            },
            {"type": "path", "d": "M 10 10 L 90 90", "fill": "none", "stroke": "{ink}"},
            {
                "type": "image",
                "href": "assets/design/logo.svg",
                "x": "right",
                "y": "bottom",
                "width": 180,
            },
            {
                "type": "group",
                "children": [{"type": "rect", "width": 10, "height": 10, "fill": "{primary}"}],
            },
            {
                "type": "stack",
                "direction": "vertical",
                "gap": 24,
                "x": 64,
                "y": 420,
                "children": [
                    {"type": "rect", "width": 200, "height": 8, "fill": "{accent}", "radius": 4},
                    {
                        "type": "text",
                        "role": "body",
                        "text": "{tagline}\nparagraf",
                        "size": 32,
                        "color": "{ink}",
                    },
                ],
            },
            {"type": "spacer", "width": 10},
            {
                "type": "taper",
                "from": [880, 1150],
                "angle": 60,
                "length": 220,
                "bend": -60,
                "widths": [48, 8],
                "fill": "{accent}",
            },
            {
                "type": "arc",
                "cx": 540,
                "cy": 675,
                "rx": 100,
                "ry": 100,
                "start_deg": 0,
                "end_deg": 90,
                "close": "pie",
                "fill": "{primary}",
            },
        ],
    }


def test_build_renders_every_v1_primitive_and_substitutes_tokens():
    spec = _full_spec()
    svg = build_spec(spec)
    for tag in (
        "<rect",
        "<circle",
        "<ellipse",
        "<line",
        "<polygon",
        "<polyline",
        "<path",
        "<image",
        "<g",
        "<text",
    ):
        assert tag in svg
    assert 'fill="#C8A24A"' in svg


def test_anchor_right_places_the_element_edge_at_the_safe_margin():
    svg = build_spec(
        {
            "canvas": {"width": 1000, "height": 1000},
            "safe_area": 50,
            "elements": [{"type": "rect", "x": "right", "y": 0, "width": 100, "height": 10}],
        }
    )
    assert 'x="850"' in svg


def test_stack_vertical_gap_offsets_children():
    svg = build_spec(
        {
            "canvas": {"width": 100, "height": 100},
            "elements": [
                {
                    "type": "stack",
                    "direction": "vertical",
                    "gap": 5,
                    "x": 0,
                    "y": 100,
                    "children": [
                        {"type": "rect", "width": 10, "height": 10},
                        {"type": "rect", "width": 10, "height": 10},
                    ],
                }
            ],
        }
    )
    assert 'y="100"' in svg and 'y="115"' in svg


def test_text_lines_become_tspans_with_a_stable_id():
    svg = build_spec(
        {
            "canvas": {"width": 100, "height": 100},
            "elements": [
                {"type": "text", "text": "a\nb", "x": 10, "y": 20, "size": 12, "role": "body"}
            ],
        }
    )
    assert svg.count("<tspan") == 2 and 'id="el-text-1"' in svg


def test_base_append_keeps_the_canvas_and_existing_content():
    base = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect id="keep" width="10" height="10" fill="#000"/></svg>'
    )
    svg = build_spec({"elements": [{"type": "circle", "cx": 5, "cy": 5, "r": 2}]}, base=base)
    assert 'viewBox="0 0 10 10"' in svg and 'id="keep"' in svg and "<circle" in svg


def test_unknown_element_type_raises():
    with pytest.raises(SvgError, match="unknown element"):
        build_spec({"canvas": {"width": 10, "height": 10}, "elements": [{"type": "hexagon"}]})


def test_text_outside_the_safe_area_is_reported(tmp_path):
    svg_path = write_svg(
        build_spec(
            {
                "canvas": {"width": 200, "height": 100},
                "safe_area": 10,
                "elements": [
                    {"type": "text", "text": "x", "x": 100, "y": 99, "size": 40, "role": "body"}
                ],
            }
        ),
        tmp_path / "x.svg",
    )
    problems = check_safe_area(svg_path, 10)
    assert any("safe area" in problem for problem in problems)


def test_stack_places_text_children_at_the_cursor():
    svg = build_spec(
        {
            "canvas": {"width": 200, "height": 200},
            "elements": [
                {
                    "type": "stack",
                    "direction": "vertical",
                    "gap": 6,
                    "x": 20,
                    "y": 30,
                    "children": [
                        {"type": "text", "text": "one", "size": 10, "role": "body"},
                        {"type": "text", "text": "two", "size": 10, "role": "body"},
                    ],
                }
            ],
        }
    )
    ys = [float(m) for m in re.findall(r'<text [^>]*y="([0-9.]+)"', svg)]
    assert ys[0] == pytest.approx(40, abs=0.5)  # first baseline: cursor + one em
    assert ys[1] == pytest.approx(58, abs=0.5)  # + 12 (10 * 1.2 line box) + 6 gap + 10


def _three_colour_png(path: Path) -> Path:
    """A 60x40 PNG: left red, middle blue, right green blocks."""
    subprocess.run(
        [
            "magick",
            "-size",
            "60x40",
            "xc:#ff0000",
            "-fill",
            "#0000ff",
            "-draw",
            "rectangle 20,0 39,39",
            "-fill",
            "#00ff00",
            "-draw",
            "rectangle 40,0 59,39",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return path


def _pinhole_png(path: Path) -> Path:
    """60x40 image with a semi-transparent alpha channel (flat colour at 50% alpha)."""
    subprocess.run(
        [
            "magick",
            "-size",
            "60x40",
            "xc:red",
            "-alpha",
            "set",
            "-channel",
            "A",
            "-evaluate",
            "set",
            "50%",
            "+channel",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return path


def _magick_fx(image: Path, expression: str) -> float:
    """Run `magick identify -format <fx>` on one image, return stdout as float."""
    completed = subprocess.run(
        ["magick", "identify", "-format", expression, str(image)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(completed.stdout.strip())


def _has_vtracer() -> bool:
    try:
        import vtracer  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(shutil.which("potrace") is None, reason="potrace not installed")
def test_trace_color_png_yields_one_group_per_color(tmp_path: Path) -> None:
    source = _three_colour_png(tmp_path / "logo.png")
    result = trace(source, tmp_path / "logo.svg", engine="potrace", mode="color", colors=3)
    svg = result.svg_path.read_text()
    assert result.engine == "potrace" and result.layers == 3
    assert svg.count('<g id="trace-layer-') == 3
    fills = set(re.findall(r'fill="(#[0-9a-fA-F]{6})"', svg))
    assert fills >= {"#ff0000", "#0000ff", "#00ff00"}


@pytest.mark.skipif(shutil.which("potrace") is None, reason="potrace not installed")
def test_trace_preprocessing_flattens_alpha_and_removes_pinholes(tmp_path: Path) -> None:
    source = _pinhole_png(tmp_path / "hole.png")
    result = trace(source, tmp_path / "hole.svg", flatten_bg="#ffffff", fill_holes=3)
    assert result.input_path.is_file()
    alpha = _magick_fx(result.input_path, "%[fx:minima.a]")
    assert alpha == pytest.approx(1.0, abs=0.01)


@pytest.mark.skipif(not _has_vtracer(), reason="vtracer extra not installed")
def test_vtracer_engine_is_used_when_available(tmp_path: Path) -> None:
    source = _three_colour_png(tmp_path / "logo.png")
    assert trace(source, tmp_path / "logo.svg", engine="auto").engine == "vtracer"


def test_explicit_vtracer_engine_without_the_extra_fails_with_the_install_hint(
    tmp_path: Path,
) -> None:
    if _has_vtracer():
        pytest.skip("vtracer installed; the missing-extra path is unreachable here")
    with pytest.raises(SvgError, match=r"\[vector\]"):
        trace(_three_colour_png(tmp_path / "logo.png"), tmp_path / "logo.svg", engine="vtracer")


def _two_blocks_png(path: Path) -> Path:
    """R4 helper: 200x200 white canvas, #1e3a8a 40x40 block at (80,80) and 20x20 at (20,20)."""
    subprocess.run(
        [
            "magick",
            "-size",
            "200x200",
            "xc:white",
            "-fill",
            "#1e3a8a",
            "-draw",
            "rectangle 80,80 119,119",
            "-draw",
            "rectangle 20,20 39,39",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return path


def _render_source(svg_path: Path, tmp_path: Path, width: int, height: int) -> Path:
    """Render one SVG at its canvas size via Inkscape."""
    return render_png(svg_path, tmp_path / "source.png", width=width, height=height)


def test_compare_detects_a_missing_shape_and_writes_the_sheet(tmp_path):
    source = _two_blocks_png(tmp_path / "source.png")
    svg = write_svg(
        build_spec(
            {
                "canvas": {"width": 200, "height": 200},
                "elements": [
                    {"type": "rect", "x": 80, "y": 80, "width": 40, "height": 40, "fill": "#1e3a8a"}
                ],
            }
        ),
        tmp_path / "base.svg",
    )
    report = compare(source, svg)
    assert report.components_missing >= 1 and report.pixel_diff_pct > 0
    assert report.sheet is not None and report.sheet.is_file()


def test_compare_on_identical_pixels_reports_near_zero(tmp_path):
    svg = write_svg(
        build_spec(
            {
                "canvas": {"width": 120, "height": 120},
                "elements": [{"type": "rect", "width": 120, "height": 120, "fill": "#1e3a8a"}],
            }
        ),
        tmp_path / "x.svg",
    )
    source = render_png(svg, tmp_path / "x.png", width=120, height=120)
    report = compare(source, svg, out=tmp_path / "same.png")
    assert report.pixel_diff_pct <= 2.0 and report.components_missing == 0


def test_compare_handles_mismatched_aspect_ratios(tmp_path):
    source = _two_blocks_png(tmp_path / "s.png")
    svg = write_svg(
        build_spec({"canvas": {"width": 100, "height": 100}, "elements": []}),
        tmp_path / "sq.svg",
    )
    report = compare(source, svg)
    assert report.distorted is True and report.pixel_diff_pct >= 0.0


def test_taper_patch_via_base_improves_the_compare_metric(tmp_path):
    source = _two_blocks_png(tmp_path / "source.png")
    base_svg = write_svg(
        build_spec(
            {
                "canvas": {"width": 200, "height": 200},
                "elements": [
                    {"type": "rect", "x": 80, "y": 80, "width": 40, "height": 40, "fill": "#1e3a8a"}
                ],
            }
        ),
        tmp_path / "base.svg",
    )
    before = compare(source, base_svg, out=tmp_path / "before.png")
    patched = build_spec(
        {
            "elements": [
                {
                    "type": "taper",
                    "from": [30, 20],
                    "angle": 90,
                    "length": 20,
                    "width": 20,
                    "caps": "flat",
                    "fill": "#1e3a8a",
                }
            ]
        },
        base=base_svg.read_text(),
    )
    after = compare(
        source,
        write_svg(patched, tmp_path / "patched.svg"),
        out=tmp_path / "after.png",
    )
    assert after.pixel_diff_pct < before.pixel_diff_pct


def test_stack_advances_cursor_past_a_spacer():
    """Finding 1: a spacer inside a stack advances the cursor instead of being a no-op."""
    spec = {
        "canvas": {"width": 200, "height": 200},
        "elements": [
            {
                "type": "stack",
                "direction": "vertical",
                "gap": 4,
                "x": 10,
                "y": 100,
                "children": [
                    {"type": "rect", "width": 20, "height": 10, "fill": "#1e3a8a"},
                    {"type": "spacer", "height": 6},
                    {"type": "rect", "width": 20, "height": 10, "fill": "#1e3a8a"},
                ],
            }
        ],
    }
    svg = build_spec(spec)
    rects = re.findall(r'<rect [^>]*y="([0-9.]+)"', svg)
    assert rects == ["100", "124"], rects


def test_group_paths_by_fill_wraps_consecutive_same_fill_paths():
    """Finding 3: consecutive same-fill paths become trace-layer-N groups in painter order."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<path d="M0,0" fill="#aa0000"/>'
        '<path d="M1,1" fill="#aa0000"/>'
        '<path d="M2,2" fill="#00bb00"/>'
        '<path d="M3,3" fill="#aa0000"/>'
        "</svg>"
    )
    grouped = _group_paths_by_fill(svg)
    assert grouped.count('<g id="trace-layer-') == 3
    assert '<g id="trace-layer-1" fill="#aa0000">' in grouped
    assert '<g id="trace-layer-2" fill="#00bb00">' in grouped
    assert '<g id="trace-layer-3" fill="#aa0000">' in grouped
    # layer 1 holds the two consecutive #aa0000 paths in order
    assert grouped.index('<path d="M0,0"') < grouped.index('<path d="M1,1"')
    assert grouped.index('<path d="M3,3"') > grouped.index("trace-layer-3")
