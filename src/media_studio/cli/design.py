"""Graphic design variants, declarative SVG, and logo kit (`ms design`) CLI commands."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from media_studio.design import DesignError
from media_studio.design import render as render_design
from media_studio.fonts import FontError, font_environment
from media_studio.logo import LogoError, logo_kit
from media_studio.manifest import ManifestError, load_project
from media_studio.svg_kit import (
    SvgError,
    build_spec,
    check_safe_area,
    compare,
    render_png,
    trace,
    write_svg,
)

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_design_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``design`` command group."""

    design = subparsers.add_parser("design", help="render brand-consistent images")
    design_sub = design.add_subparsers(dest="design_command", required=True)
    draw = design_sub.add_parser("render", help="compose and export every declared variant")
    draw.add_argument("project", help="project path or slug")
    draw.add_argument("--variant", default=None, help="render only this variant")
    draw.add_argument("--check-only", action="store_true", help="check what is already rendered")
    draw.set_defaults(func=cmd_design_render)

    svg = design_sub.add_parser("svg", help="declarative SVG composition")
    svg_sub = svg.add_subparsers(dest="svg_command", required=True)
    svg_build = svg_sub.add_parser(
        "build", help="compose a declarative SVG spec and optionally export it"
    )
    svg_build.add_argument("--spec", type=Path, required=True, help="build spec JSON file")
    svg_build.add_argument("--project", default=None, help="project path or slug (optional)")
    svg_build.add_argument("--base", type=Path, default=None, help="base SVG to append into")
    svg_build.add_argument("--out", type=Path, default=None, help="destination SVG file")
    svg_build.add_argument("--export", default=None, help="comma-separated export kinds (png, pdf)")
    svg_build.add_argument("--check", action="store_true", help="run QC checks and write a report")
    svg_build.add_argument(
        "--sheet", action="store_true", help="write one montage row beside the output"
    )
    svg_build.set_defaults(func=cmd_design_svg_build)

    svg_trace = svg_sub.add_parser("trace", help="trace a raster image into a layered SVG")
    svg_trace.add_argument("source", help="source raster file (PNG or anything magick reads)")
    svg_trace.add_argument("--out", type=Path, required=True, help="destination SVG file")
    svg_trace.add_argument(
        "--engine",
        choices=("auto", "vtracer", "potrace"),
        default="auto",
        help="tracing engine: auto prefers vtracer, falls back to potrace",
    )
    svg_trace.add_argument(
        "--mode",
        choices=("color", "bw"),
        default="color",
        help="trace mode: color layers or a single binary path",
    )
    svg_trace.add_argument(
        "--colors", type=int, default=6, help="colour count for quantised tracing"
    )
    svg_trace.add_argument(
        "--threshold", type=int, default=50, help="bw threshold percent (potrace bw)"
    )
    svg_trace.add_argument(
        "--flatten-bg", default=None, help="flatten alpha over this colour (hex or name)"
    )
    svg_trace.add_argument(
        "--fill-holes", type=int, default=0, help="-morphology Close disk radius (potrace)"
    )
    svg_trace.add_argument(
        "--despeckle", type=int, default=0, help="-morphology Open disk radius (potrace)"
    )
    svg_trace.set_defaults(func=cmd_design_svg_trace)

    svg_compare = svg_sub.add_parser(
        "compare", help="compare a source raster against a rendered SVG"
    )
    svg_compare.add_argument("source", help="source raster file (PNG or anything magick reads)")
    svg_compare.add_argument("svg", help="rendered SVG file to compare against the source")
    svg_compare.add_argument(
        "--out", type=Path, default=None, help="destination sheet PNG (default: beside the source)"
    )
    svg_compare.add_argument(
        "--json", type=Path, default=None, help="write the report dict as JSON to this file"
    )
    svg_compare.add_argument(
        "--min-area", type=int, default=16, help="connected-components area threshold (default 16)"
    )
    svg_compare.add_argument(
        "--fail-above",
        type=float,
        default=None,
        help="exit 1 when pixel_diff_pct is above this percent",
    )
    svg_compare.set_defaults(func=cmd_design_svg_compare)

    logo = design_sub.add_parser(
        "logo", help="derive a logo derivative kit from a staged logo asset"
    )
    logo.add_argument("project", help="project path or slug")
    logo.add_argument(
        "--sizes",
        default="1024,512,256,128,64,32",
        help="comma-separated render sizes (default: 1024,512,256,128,64,32)",
    )
    logo.add_argument(
        "--variants",
        default="color,mono,reversed",
        help="comma-separated variants: color, mono, reversed (default: all)",
    )
    logo.add_argument(
        "--clear-space-pct",
        type=float,
        default=8.0,
        help="minimum clear space as a percent of canvas width (default: 8.0)",
    )
    logo.set_defaults(func=cmd_design_logo)


def cmd_design_render(args: argparse.Namespace) -> int:
    """Render variants and report the checks."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        report = render_design(project, args.variant, check_only=args.check_only)
    except (ManifestError, DesignError, FontError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    for variant in report.variants:
        mark = "ok  " if variant.get("ok") else "FAIL"
        print(f"  {mark} {variant.get('id')}")
        for problem in variant.get("problems") or []:
            print(f"        {problem}")
    if report.sheet is not None:
        print(f"  sheet: {report.sheet.relative_to(project.root)}")
    if report.report_path is not None:
        print(f"  report: {report.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if report.ok else "FAIL"))
    return EXIT_OK if report.ok else EXIT_FAILURE


def cmd_design_svg_build(args: argparse.Namespace) -> int:
    """Build a declarative SVG spec, optionally export and check it."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project) if args.project else None
        spec_text = Path(args.spec).read_text(encoding="utf-8")
        spec = json.loads(spec_text)
        base_text = Path(args.base).read_text(encoding="utf-8") if args.base else None
        svg_text = build_spec(spec, project=project, base=base_text)
    except (ManifestError, SvgError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    out_path = args.out
    if out_path is None:
        out_path = Path(args.spec).with_suffix(".svg")
    out_path = Path(out_path)
    write_svg(svg_text, out_path)

    problems: list[str] = []
    exports: list[str] = []

    if args.export:
        environment = font_environment(project) if project is not None else None
        for kind in [k.strip() for k in str(args.export).split(",") if k.strip()]:
            if kind not in ("png", "pdf"):
                print(f"error: unsupported --export kind {kind!r}", file=sys.stderr)
                return EXIT_FAILURE
            export_path = out_path.with_suffix(f".{kind}")
            width = 0
            height = None
            try:
                match = re.search(r'width="([0-9.]+)"', svg_text)
                width = int(match.group(1)) if match else 0
            except (AttributeError, ValueError):
                width = 0
            render_png(
                svg_path=out_path,
                png_path=export_path,
                width=max(width, 1),
                height=height,
                environment=environment,
            )
            exports.append(str(export_path))

    if args.check:
        from media_studio.design import check_contrast, check_min_font_size, check_overflow

        problems.extend(check_contrast(svg_text))
        problems.extend(check_min_font_size(svg_text))
        problems.extend(check_overflow(out_path, svg_text))
        safe_area = spec.get("safe_area", 0) if isinstance(spec, dict) else 0
        problems.extend(check_safe_area(out_path, safe_area))
        report_dir = out_path.parent / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{out_path.stem}-svg-qc.json"
        report = {
            "svg": str(out_path),
            "exports": exports,
            "problems": problems,
            "ok": not problems,
        }
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.sheet:
        targets = exports or [str(out_path)]
        sheet_path = out_path.parent / f"{out_path.stem}-sheet.png"
        completed = subprocess.run(
            ["magick", "montage", *targets, "-tile", "1x", "-background", "none", str(sheet_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and sheet_path.is_file():
            exports.append(str(sheet_path))
        else:
            problems.append(
                f"magick montage could not build the sheet: {completed.stderr.strip()[:2000]}"
            )

    print(f"  svg: {out_path}")
    for export in exports:
        print(f"  export: {export}")
    if problems:
        for problem in problems:
            print(f"  check: {problem}")
        print("verdict: FAIL")
        return EXIT_FAILURE
    print("verdict: pass")
    return EXIT_OK


def cmd_design_svg_trace(args: argparse.Namespace) -> int:
    """Trace one raster image into a layered SVG."""

    try:
        result = trace(
            Path(args.source),
            Path(args.out),
            engine=args.engine,
            mode=args.mode,
            colors=args.colors,
            threshold=args.threshold,
            flatten_bg=args.flatten_bg,
            fill_holes=args.fill_holes,
            despeckle=args.despeckle,
        )
    except SvgError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"  svg: {result.svg_path}")
    print(f"  input: {result.input_path}")
    print(f"  engine: {result.engine}")
    print(f"  layers: {result.layers}")
    return EXIT_OK


def cmd_design_svg_compare(args: argparse.Namespace) -> int:
    """Compare a source raster against a rendered SVG and report metrics."""

    try:
        report = compare(
            Path(args.source),
            Path(args.svg),
            out=args.out,
            min_area=args.min_area,
        )
    except SvgError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"  pixel_diff_pct: {report.pixel_diff_pct:.3f}")
    print(f"  rmse: {report.rmse:.6f}")
    print(f"  components_missing: {report.components_missing}")
    print(f"  components_extra: {report.components_extra}")
    print(f"  distorted: {report.distorted}")
    print(f"  sheet: {report.sheet}")
    if args.json is not None:
        report_dict = {
            "pixel_diff_pct": report.pixel_diff_pct,
            "rmse": report.rmse,
            "components_missing": report.components_missing,
            "components_extra": report.components_extra,
            "sheet": str(report.sheet) if report.sheet is not None else None,
            "distorted": report.distorted,
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report_dict, indent=2) + "\n", encoding="utf-8")
    if args.fail_above is not None and report.pixel_diff_pct > args.fail_above:
        return EXIT_FAILURE
    return EXIT_OK


def cmd_design_logo(args: argparse.Namespace) -> int:
    """Derive a logo kit and print the sheet path."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        sizes = tuple(int(value) for value in args.sizes.split(",") if value.strip())
        variants = tuple(value.strip() for value in args.variants.split(",") if value.strip())
    except ValueError:
        print("error: --sizes must be comma-separated integers", file=sys.stderr)
        return EXIT_FAILURE
    project = load_fn(Path(args.project))
    try:
        report = logo_kit(
            project,
            sizes=sizes,
            variants=variants,
            clear_space_pct=args.clear_space_pct,
        )
    except LogoError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if report.sheet is not None:
        print(str(report.sheet))
    for note in report.notes:
        print(f"note: {note}")
    if report.problems:
        for problem in report.problems:
            print(f"check: {problem}", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK
