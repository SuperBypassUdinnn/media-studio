"""Command line interface for media-studio."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import __version__, media
from .blender import (
    EXPORT_FORMATS,
    BlenderError,
    project_output,
    turntable,
    write_report,
)
from .blender import (
    convert as convert_model,
)
from .blender import (
    inspect as inspect_model,
)
from .design import DesignError, validate_design
from .design import render as render_design
from .effectcraft import EffectCraftError, lyric_overlay, motion_project, title_sequence
from .fonts import FontError, font_environment, inventory, specimen_sheet
from .fonts import search as search_fonts
from .ledger import (
    ASSET_CLASSES,
    LedgerError,
    StagedAsset,
    build_index,
    stage_asset,
    write_index_and_credits,
)
from .manifest import ManifestError, Project, load_project, validate_project
from .materials import (
    MaterialError,
    bind,
    fetch_hdri,
    fetch_material,
    proof_frame_luminance,
)
from .media import MediaError
from .natron import NatronError, composite_project
from .presets import (
    KINDS as PRESET_KINDS,
)
from .presets import (
    PresetError,
    load_recipes,
    validate_effects,
    validate_recipe,
)
from .presets import (
    search as search_presets,
)
from .project import ProjectError, default_project_root, scaffold_project
from .search import SEARCH_KINDS, SearchError, load_outcome, run_search, save_outcome
from .sources import module_named
from .sources.base import Candidate, SourceError, http_fetch
from .tools import CheckResult, DoctorReport, collect_toolchain, deep_checks, path_order_problem
from .triage import (
    build_index as build_triage_index,
)
from .triage import (
    build_sheets,
    clip_paths,
    concept_key,
    read_decisions,
    read_index,
    record_decision,
    undecided,
    write_index,
)
from .video import VideoError, qc_report
from .video import handoff as video_handoff
from .video import render as render_video

# A search class is not always the staging class: a stock photo belongs in the design set.
STAGING_CLASS_FOR_KIND = {"image": "design", "video": "video", "audio": "audio", "music": "music"}

EXIT_OK = 0
EXIT_FAILURE = 1


def build_parser() -> argparse.ArgumentParser:
    """Build the ``ms`` argument parser."""

    parser = argparse.ArgumentParser(
        prog="ms",
        description="Headless media production toolkit: video, audio, 3D, design, and assets.",
    )
    parser.add_argument("--version", action="version", version=f"media-studio {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="report the local media toolchain")
    doctor.add_argument("--deep", action="store_true", help="also probe GPU files and torch venvs")
    doctor.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    doctor.set_defaults(func=cmd_doctor)

    project = subparsers.add_parser("project", help="manage media projects")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    init = project_sub.add_parser("init", help="scaffold a project from the template")
    init.add_argument("slug", help="project slug, for example scr2026-promo")
    init.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"parent directory (default: {default_project_root()})",
    )
    init.add_argument("--title", default=None, help="human title for the project")
    init.set_defaults(func=cmd_project_init)

    validate = project_sub.add_parser("validate", help="check a manifest and the assets it names")
    validate.add_argument("project", nargs="?", help="project path or slug (default: here)")
    validate.set_defaults(func=cmd_project_validate)

    assets = subparsers.add_parser("assets", help="stage assets and maintain the ledger")
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)

    stage = assets_sub.add_parser("stage", help="copy or download one asset with its sidecar")
    stage.add_argument("project", help="project path or slug")
    stage.add_argument("--class", dest="class_name", required=True, choices=ASSET_CLASSES)
    source = stage.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="local file to copy in")
    source.add_argument("--url", help="URL to download")
    stage.add_argument("--license", dest="license_text", required=True, help="licence wording")
    stage.add_argument("--attribution", default="", help="credit line, when required")
    stage.add_argument("--source-url", default="", help="page the asset came from")
    stage.add_argument("--provider", default="", help="provider name")
    stage.add_argument(
        "--sha256",
        dest="expected_sha256",
        default=None,
        help="known hash of the asset; skips the transfer when it is already staged",
    )
    stage.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="permit a licence that forbids commercial use (personal work only)",
    )
    stage.set_defaults(func=cmd_assets_stage)

    index = assets_sub.add_parser("index", help="regenerate assets/index.json and CREDITS.md")
    index.add_argument("project", help="project path or slug")
    index.set_defaults(func=cmd_assets_index)

    listing = assets_sub.add_parser("list", help="show staged assets and their licences")
    listing.add_argument("project", help="project path or slug")
    listing.set_defaults(func=cmd_assets_list)

    search = assets_sub.add_parser("search", help="search keyless sources for licensed candidates")
    search.add_argument("query", help="what to look for, for example 'forest canopy'")
    search.add_argument("--class", dest="class_name", required=True, choices=SEARCH_KINDS)
    search.add_argument("--limit", type=int, default=8, help="results per source (default 8)")
    search.add_argument(
        "--source", action="append", default=[], metavar="NAME", help="only this source"
    )
    search.add_argument(
        "--save",
        type=Path,
        default=None,
        help="where to write the result JSON (default: ~/.cache/media-studio/searches/)",
    )
    search.add_argument("--json", action="store_true", help="emit the result JSON on stdout")
    search.add_argument(
        "--include-unlicensed",
        action="store_true",
        help="also list candidates whose licence is unknown",
    )
    search.set_defaults(func=cmd_assets_search)

    pull = assets_sub.add_parser("pull", help="stage one candidate from a saved search")
    pull.add_argument("project", help="project path or slug")
    pull.add_argument("--search", type=Path, required=True, help="result JSON from `assets search`")
    pull.add_argument("--index", type=int, required=True, help="1-based candidate number")
    pull.add_argument(
        "--class",
        dest="class_name",
        default=None,
        choices=ASSET_CLASSES,
        help="staging class (default: derived from the search class)",
    )
    pull.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="permit a licence that forbids commercial use (personal work only)",
    )
    pull.add_argument(
        "--dry-run", action="store_true", help="show what would be staged, download nothing"
    )
    pull.set_defaults(func=cmd_assets_pull)

    video = subparsers.add_parser("video", help="render and verify video deliverables")
    video_sub = video.add_subparsers(dest="video_command", required=True)

    render = video_sub.add_parser("render", help="render one video deliverable end to end")
    render.add_argument("project", help="project path or slug")
    render.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    render.add_argument("--preview", action="store_true", help="small and fast, for a look")
    render.add_argument(
        "--keep-intermediate",
        action="store_true",
        help="keep the lossless render under /tmp/opencode for inspection",
    )
    render.set_defaults(func=cmd_video_render)

    handoff = video_sub.add_parser(
        "handoff", help="write a Kdenlive-compatible project beside the render input"
    )
    handoff.add_argument("project", help="project path or slug")
    handoff.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    handoff.set_defaults(func=cmd_video_handoff)

    qc = video_sub.add_parser("qc", help="measure an existing file and write a report")
    qc.add_argument("project", help="project path or slug")
    qc.add_argument("--file", type=Path, required=True, help="file to measure")
    qc.set_defaults(func=cmd_video_qc)

    composite = video_sub.add_parser(
        "composite", help="composite a rendered deliverable through Natron headless"
    )
    composite.add_argument("project", help="project path or slug")
    composite.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    composite.add_argument("--lut", type=Path, default=None, help="OCIO LUT file for the grade")
    composite.add_argument("--gain", type=float, default=1.0, help="grade gain (default 1.0)")
    composite.add_argument("--gamma", type=float, default=1.0, help="grade gamma (default 1.0)")
    composite.add_argument(
        "--saturation", type=float, default=1.0, help="grade saturation (default 1.0)"
    )
    composite.add_argument(
        "--glow-size", type=float, default=0.0, help="glow size, 0 disables (default 0)"
    )
    composite.add_argument("--glow-mix", type=float, default=0.5, help="glow mix (default 0.5)")
    composite.add_argument(
        "--vignette", type=float, default=0.0, help="vignette strength, 0 disables (default 0)"
    )
    composite.set_defaults(func=cmd_video_composite)

    three_d = subparsers.add_parser("3d", help="inspect, convert, and render models")
    three_d_sub = three_d.add_subparsers(dest="three_d_command", required=True)
    inspect = three_d_sub.add_parser("inspect", help="report a model's geometry")
    inspect.add_argument("model", type=Path)
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_3d_inspect)
    convert = three_d_sub.add_parser("convert", help="repair and re-export a model")
    convert.add_argument("model", type=Path)
    convert.add_argument("--to", required=True, choices=sorted(EXPORT_FORMATS))
    convert.add_argument("--out", type=Path, default=None, help="output directory")
    convert.add_argument("--decimate", type=float, default=0.0, help="ratio below 1.0 to simplify")
    convert.set_defaults(func=cmd_3d_convert)
    material = three_d_sub.add_parser("material", help="fetch and bind a PBR material")
    material.add_argument("model", type=Path)
    material.add_argument("--project", required=True, help="project to stage the maps into")
    material.add_argument("--material", required=True, help="asset id, for example mossy_rock")
    material.add_argument("--hdri", default=None, help="HDRI asset id for the world lighting")
    material.add_argument("--resolution", default="1k", help="texture resolution, default 1k")
    material.add_argument("--out", type=Path, default=None, help="where to write the bound model")
    material.add_argument("--render", action="store_true", help="also render a proof frame")
    material.add_argument("--proof-resolution", default="640x360")
    material.set_defaults(func=cmd_3d_material)

    spin = three_d_sub.add_parser("turntable", help="render a turntable headlessly")
    spin.add_argument("model", type=Path)
    spin.add_argument("--project", default=None, help="project to write outputs and reports into")
    spin.add_argument("--frames", type=int, default=24)
    spin.add_argument("--resolution", default="640x360", help="WxH, default 640x360")
    spin.add_argument("--baseline", type=Path, default=None, help="a previous turntable-qc.json")
    spin.set_defaults(func=cmd_3d_turntable)

    design = subparsers.add_parser("design", help="render brand-consistent images")
    design_sub = design.add_subparsers(dest="design_command", required=True)
    draw = design_sub.add_parser("render", help="compose and export every declared variant")
    draw.add_argument("project", help="project path or slug")
    draw.add_argument("--variant", default=None, help="render only this variant")
    draw.add_argument("--check-only", action="store_true", help="check what is already rendered")
    draw.set_defaults(func=cmd_design_render)

    fonts = subparsers.add_parser("fonts", help="find and preview fonts")
    fonts_sub = fonts.add_subparsers(dest="fonts_command", required=True)
    find = fonts_sub.add_parser("search", help="search installed families by intent")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=8)
    find.add_argument("--mono", action="store_true", help="only monospaced families")
    find.set_defaults(func=cmd_fonts_search)
    preview = fonts_sub.add_parser("specimen", help="render candidates with the project's words")
    preview.add_argument("project", help="project path or slug")
    preview.add_argument("--text", required=True, help="the headline to set in each candidate")
    preview.add_argument("--query", default="", help="narrow the candidates by name")
    preview.add_argument("--limit", type=int, default=6)
    preview.set_defaults(func=cmd_fonts_specimen)

    presets = subparsers.add_parser("presets", help="search the preset registry")
    presets_sub = presets.add_subparsers(dest="presets_command", required=True)

    listing = presets_sub.add_parser("list", help="every recipe, optionally by kind")
    listing.add_argument("--kind", choices=PRESET_KINDS, default=None)
    listing.set_defaults(func=cmd_presets_list)

    find = presets_sub.add_parser("search", help="search recipes by intent")
    find.add_argument("query")
    find.set_defaults(func=cmd_presets_search)

    show = presets_sub.add_parser("show", help="one recipe with its parameters")
    show.add_argument("preset_id")
    show.set_defaults(func=cmd_presets_show)

    triage = subparsers.add_parser("triage", help="index footage and record review decisions")
    triage_sub = triage.add_subparsers(dest="triage_command", required=True)

    index = triage_sub.add_parser("index", help="measure a clip library and explain every reject")
    index.add_argument("source", type=Path, help="a directory of clips, or one clip")
    index.add_argument("--project", default=None, help="project path or slug, to persist the cards")
    index.add_argument("--concept", default="", help="concept words, used to weight the ranking")
    index.add_argument("--json", action="store_true", help="emit the cards as JSON")
    index.set_defaults(func=cmd_triage_index)

    sheets = triage_sub.add_parser("sheets", help="batch the unreviewed clips into contact sheets")
    sheets.add_argument("project", help="project path or slug")
    sheets.add_argument("--concept", required=True, help="the concept being reviewed")
    sheets.add_argument("--limit", type=int, default=20, help="clips per run (default 20)")
    sheets.set_defaults(func=cmd_triage_sheets)

    decide = triage_sub.add_parser("decide", help="record a verdict for one clip")
    decide.add_argument("project", help="project path or slug")
    decide.add_argument("--key", required=True, help="clip key, from the index or a sheet label")
    decide.add_argument("--concept", required=True)
    decide.add_argument("--verdict", required=True, choices=("in", "hold", "out"))
    decide.add_argument("--reason", default="")
    decide.set_defaults(func=cmd_triage_decide)

    status = triage_sub.add_parser("status", help="how much of the library is reviewed")
    status.add_argument("project", help="project path or slug")
    status.add_argument("--concept", required=True)
    status.set_defaults(func=cmd_triage_status)

    motion = subparsers.add_parser("motion", help="render motion graphics through EffectCraft")
    motion_sub = motion.add_subparsers(dest="motion_command", required=True)

    motion_render = motion_sub.add_parser("render", help="render all declared motion sequences")
    motion_render.add_argument("project", help="project path or slug")
    motion_render.set_defaults(func=cmd_motion_render)

    motion_title = motion_sub.add_parser("title", help="render one animated title sequence")
    motion_title.add_argument("project", help="project path or slug")
    motion_title.add_argument("--text", required=True, help="title text")
    motion_title.add_argument("--out", type=Path, required=True, help="output file path")
    motion_title.add_argument("--duration", type=float, default=3.0, help="duration in seconds")
    motion_title.add_argument("--animation", default="fade_in_up", help="animation preset name")
    motion_title.add_argument("--font", default="Noto Sans", help="font family")
    motion_title.add_argument("--font-size", type=float, default=72.0, help="font size in points")
    motion_title.add_argument("--color", default="#FFFFFF", help="text color")
    motion_title.add_argument("--glow", action="store_true", help="enable glow effect")
    motion_title.add_argument("--glow-intensity", type=float, default=0.5, help="glow strength")
    motion_title.set_defaults(func=cmd_motion_title)

    motion_lyrics = motion_sub.add_parser("lyrics", help="render a lyric overlay track")
    motion_lyrics.add_argument("project", help="project path or slug")
    motion_lyrics.add_argument("--out", type=Path, required=True, help="output file path")
    motion_lyrics.add_argument(
        "--lines", required=True, help="lyrics as start,end,text|start,end,text"
    )
    motion_lyrics.add_argument("--style", default="default", help="text style name")
    motion_lyrics.set_defaults(func=cmd_motion_lyrics)

    return parser


def _resolution(value: str) -> tuple[int, int]:
    width, _, height = value.lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError as error:
        raise BlenderError(f"resolution {value!r} must be WxH, for example 640x360") from error


def cmd_3d_inspect(args: argparse.Namespace) -> int:
    """Report what a model contains, measured by Blender."""

    try:
        report = inspect_model(args.model)
    except BlenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        json.dump(report.facts, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK
    facts = report.facts
    print(f"{args.model.name}")
    print(f"  objects:   {facts.get('objects')} ({facts.get('mesh_objects')} mesh)")
    print(f"  geometry:  {facts.get('vertices')} vertices, {facts.get('triangles')} triangles")
    print(f"  size:      {facts.get('size')}")
    print(f"  materials: {', '.join(facts.get('materials') or []) or 'none'}")
    problems = report.problems
    if problems:
        print("  problems:  " + ", ".join(problems))
    print("verdict: " + ("clean" if not problems else "needs repair; run `ms 3d convert`"))
    return EXIT_OK


def cmd_3d_convert(args: argparse.Namespace) -> int:
    """Repair a model and write it in another format."""

    destination = (args.out or args.model.parent) / f"{args.model.stem}{EXPORT_FORMATS[args.to]}"
    try:
        before = inspect_model(args.model)
        conversion = convert_model(args.model, destination, decimate=args.decimate)
    except BlenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"wrote {conversion.destination}")
    print(f"  triangles: {before.triangles} -> {conversion.triangles}")
    print(f"  problems:  {', '.join(conversion.problems) or 'none'}")
    return EXIT_OK if not conversion.problems else EXIT_FAILURE


def cmd_3d_material(args: argparse.Namespace) -> int:
    """Fetch a keyless PBR material, bind it to a model, and prove it rendered."""

    try:
        project = load_project(args.project)
        material = fetch_material(project, args.material, resolution=args.resolution)
        staged_hdri = (
            fetch_hdri(project, args.hdri, resolution=args.resolution) if args.hdri else None
        )
        destination = args.out or annotate_output(project, args.model, "-pbr.glb")
        proof = annotate_output(project, args.model, "-proof.png") if args.render else None
        resolution = _resolution(args.proof_resolution)
        result = bind(
            args.model,
            material.maps,
            destination=destination,
            hdri=staged_hdri.path if staged_hdri else None,
            render=proof,
            resolution=resolution,
        )
    except (ManifestError, BlenderError, MaterialError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{args.material}: bound {', '.join(result.bound_roles)}")
    if material.ignored_roles:
        print(f"  ignored:    {', '.join(material.ignored_roles)} (not wired into the shader)")
    print(f"  staged:     {len(material.staged)} map(s) with sidecars")
    print("  credits:    assets/CREDITS.md and assets/index.json refreshed")
    if result.unwired_slots:
        print(f"  unwired:    {', '.join(result.unwired_slots)} (no slot to use them in)")
    if staged_hdri is not None:
        print(f"  hdri:       {staged_hdri.relative}")
    if destination is not None:
        print(f"  model:      {destination.relative_to(project.root)}")
    if proof is not None and proof.is_file():
        luma = proof_frame_luminance(proof)
        print(f"  proof:      {proof.relative_to(project.root)} (luma {luma:.1f})")
    problems = result.problems
    report = {**result.facts, "material": args.material, "licence": "CC0", "problems": problems}
    write_report(project, report, f"{args.model.stem}-material-qc.json")
    print("verdict: " + ("pass" if not problems else "FAIL " + "; ".join(problems)))
    return EXIT_OK if not problems else EXIT_FAILURE


def annotate_output(project: Project, model: Path, suffix: str) -> Path:
    """A file name inside a project's 3D output, derived from the model's name."""

    return project_output(project, f"{model.stem}{suffix}")


def cmd_3d_turntable(args: argparse.Namespace) -> int:
    """Render a turntable and report its measured frames."""

    try:
        resolution = _resolution(args.resolution)
        if args.project:
            project = load_project(args.project)
            destination = project_output(project, args.model.stem)
        else:
            project = None
            destination = Path("/tmp/opencode/blender") / args.model.stem
        result = turntable(
            args.model,
            destination,
            frames=max(args.frames, 2),
            resolution=resolution,
            baseline=args.baseline,
        )
    except (ManifestError, BlenderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    report = result.facts
    width, height = report["resolution"]
    print(f"rendered {report['frames']} frame(s) at {width}x{height}")
    print(f"  directory: {result.directory}")
    print(f"  sheet:     {result.sheet.name}")
    print(f"  report:    {result.report_path.name}")
    if report.get("environment") and report["environment"] != "clean":
        print(f"  environment: {report['environment']}")
    if project is not None:
        written = write_report(project, report, f"{args.model.stem}-turntable-qc.json")
        print(f"  archived:  {written.relative_to(project.root)}")
    for name, passed in (report.get("checks") or {}).items():
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    print("verdict: " + ("pass" if report.get("ok") else "FAIL"))
    return EXIT_OK if report.get("ok") else EXIT_FAILURE


def cmd_design_render(args: argparse.Namespace) -> int:
    """Render variants and report the checks."""

    try:
        project = load_project(args.project)
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


def cmd_fonts_search(args: argparse.Namespace) -> int:
    """Search installed families, filtered to those that can set the text."""

    try:
        fonts = search_fonts(
            inventory(),
            args.query,
            monospace=True if args.mono else None,
            limit=max(args.limit, 1),
        )
    except FontError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not fonts:
        print(f"no family matches {args.query!r} with Indonesian coverage")
        return EXIT_OK
    for font in fonts:
        print(f"  {font.family}  ({font.style})")
        print(f"    {font.file}")
    print(
        "\nstage one with `ms assets stage <project> --class design "
        "--file <path> --license OFL-1.1`"
    )
    return EXIT_OK


def cmd_fonts_specimen(args: argparse.Namespace) -> int:
    """Render candidate families with the project's own headline."""

    try:
        project = load_project(args.project)
        environment = font_environment(project)
        candidates = search_fonts(inventory(environment), args.query, limit=max(args.limit, 1))
        sheet = specimen_sheet(
            candidates,
            args.text,
            project.root / "out" / "images" / "font-specimen.png",
            environment=environment,
        )
    except (ManifestError, FontError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{len(candidates)} candidate(s): {', '.join(font.family for font in candidates)}")
    print(f"  sheet: {sheet.relative_to(project.root)}")
    print("choose one, stage it into assets/design/fonts, then set design.fonts in project.yaml")
    return EXIT_OK


def _load_registry() -> dict[str, object]:
    """Load and integrity-check the registry, refusing to serve a broken recipe."""

    recipes = load_recipes()
    problems = [problem for recipe in recipes.values() for problem in validate_recipe(recipe)]
    if problems:
        raise PresetError("registry problems: " + "; ".join(problems))
    return recipes


def cmd_presets_list(args: argparse.Namespace) -> int:
    """List recipes, one line each."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    selected = [
        recipe for recipe in recipes.values() if args.kind is None or recipe.kind == args.kind
    ]
    for recipe in sorted(selected, key=lambda item: (item.kind, item.id)):
        print(f"  {recipe.id:<26} {recipe.kind:<13} {recipe.title}")
    print(f"\n{len(selected)} preset(s); search with `ms presets search <intent>`")
    return EXIT_OK


def cmd_presets_search(args: argparse.Namespace) -> int:
    """Search by intent and print what each match is for."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    matches = search_presets(recipes, args.query)
    if not matches:
        print(f"no preset matches {args.query!r}; try `ms presets list`")
        return EXIT_OK
    for recipe in matches:
        print(f"  {recipe.id}  ({recipe.kind})")
        print(f"    {recipe.summary}")
    return EXIT_OK


def cmd_presets_show(args: argparse.Namespace) -> int:
    """Show one recipe: what it does, its parameters, and how to reference it."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    recipe = recipes.get(args.preset_id)
    if recipe is None:
        print(f"error: no preset {args.preset_id!r}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{recipe.id} - {recipe.title}")
    print(f"  kind:       {recipe.kind} ({recipe.engine})")
    print(f"  applies to: {', '.join(recipe.applies_to)}")
    print(f"  intent:     {', '.join(recipe.intent)}")
    print(f"  licence:    {recipe.license}")
    print(f"  provenance: {recipe.provenance}")
    print(f"  {recipe.summary}")
    if recipe.parameters:
        print("\nparameters")
        for parameter in recipe.parameters:
            bounds = ""
            if parameter.minimum is not None and parameter.maximum is not None:
                bounds = f" [{parameter.minimum:g}..{parameter.maximum:g}]"
            unit = f" {parameter.unit}" if parameter.unit else ""
            details = f"default {parameter.default}{unit}{bounds}"
            print(f"  {parameter.name:<10} {parameter.type:<8} {details}")
            if parameter.description:
                print(f"             {parameter.description}")
    print("\nreference it in project.yaml under effects:")
    example = {"id": recipe.id}
    if recipe.parameters:
        example[recipe.parameters[0].name] = recipe.parameters[0].default
    print(f"  effects:\n    - {example}")
    return EXIT_OK


def cmd_triage_index(args: argparse.Namespace) -> int:
    """Measure a library, reusing cached measurements, and report the rejects."""

    source = Path(args.source).expanduser()
    if not source.exists():
        print(f"error: no such source: {source}", file=sys.stderr)
        return EXIT_FAILURE
    paths = clip_paths(source)
    if not paths:
        print(f"error: no clips under {source}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        project = load_project(args.project) if args.project else None
        cached = read_index(project) if project else {}
        index = build_triage_index(
            paths,
            scratch=media.triage_scratch(),
            keywords=tuple(word for word in args.concept.lower().split() if word),
            cache=cached,
        )
        if project is not None:
            path = write_index(project, index)
    except (ManifestError, MediaError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        # Cards on stdout, the summary on stderr, so a script can read the cards alone.
        sys.stdout.write(index.to_jsonl())
    else:
        print(f"{'':6}{'key':<14}{'length':>8}  {'size':>10}  detail")
        for item in index.facts:
            mark = "keep" if item.usable else "drop"
            detail = ", ".join(item.reasons) if item.reasons else f"score {item.score:.2f}"
            size = f"{item.width}x{item.height}" if item.width else "-"
            print(f"  {mark:<6}{item.key:<14}{item.duration:7.2f}s  {size:>10}  {detail}")
    summary = index.summary
    print(
        f"clips {summary['clips']}, usable {summary['usable']}, rejected {summary['rejected']}, "
        f"duplicate groups {summary['duplicate_groups']}, "
        f"measured now {summary['measured_now']}, reused {summary['reused_from_cache']}",
        file=sys.stderr if args.json else sys.stdout,
    )
    if project is not None and not args.json:
        print(f"cards: {path.relative_to(project.root)}")
    return EXIT_OK


def cmd_triage_sheets(args: argparse.Namespace) -> int:
    """Batch the clips that have no verdict yet into labelled contact sheets."""

    try:
        project = load_project(args.project)
        cached = read_index(project)
        if not cached:
            raise ManifestError(
                "no cards yet; run `ms triage index <clips> --project <project>` first"
            )
        index = build_triage_index(
            [item.path for item in cached.values()],
            scratch=media.triage_scratch(),
            cache=cached,
        )
        pending = undecided(index, read_decisions(project), args.concept)
        chosen = pending[: max(args.limit, 1)]
        sheets = build_sheets(chosen, project.root / "footage" / "sheets")
    except (ManifestError, MediaError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not sheets:
        print(f"nothing to review for {args.concept!r}: every usable clip already has a verdict")
        return EXIT_OK
    print(f"{len(chosen)} clip(s) to review, in {len(sheets)} sheet(s)")
    for sheet in sheets:
        print(f"  {sheet.relative_to(project.root)}")
    print("review each sheet and answer with a verdict per key, then run `ms triage decide`")
    return EXIT_OK


def cmd_triage_decide(args: argparse.Namespace) -> int:
    """Record one verdict."""

    try:
        project = load_project(args.project)
        decision = record_decision(
            project,
            key=args.key,
            concept=args.concept,
            verdict=args.verdict,
            reason=args.reason,
        )
    except (ManifestError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(
        f"recorded {decision.verdict} for {decision.key} on concept {concept_key(decision.concept)}"
    )
    return EXIT_OK


def cmd_triage_status(args: argparse.Namespace) -> int:
    """Report review progress for one concept."""

    try:
        project = load_project(args.project)
        cached = read_index(project)
        index = build_triage_index(
            [item.path for item in cached.values()],
            scratch=media.triage_scratch(),
            cache=cached,
        )
        decisions = [item for item in read_decisions(project) if item.concept == args.concept]
    except (ManifestError, MediaError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    counts: dict[str, int] = {}
    for item in decisions:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    pending = undecided(index, read_decisions(project), args.concept)
    print(f"concept: {concept_key(args.concept)}")
    print(f"  usable clips:   {len(index.usable)}")
    summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items())) or "none"
    print(f"  with a verdict: {len(decisions)} ({summary})")
    print(f"  still pending:  {len(pending)}")
    return EXIT_OK


def cmd_video_render(args: argparse.Namespace) -> int:
    """Render a deliverable and report the measured QC checks."""

    try:
        project = load_project(args.project)
        result = render_video(
            project,
            args.deliverable,
            preview=args.preview,
            keep_intermediate=args.keep_intermediate,
        )
    except (ManifestError, VideoError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"rendered {result.deliverable.relative_to(project.root)}")
    print(f"  mlt:      {result.mlt_path.relative_to(project.root)}")
    print(f"  report:   {result.report_path.relative_to(project.root)}")
    print(f"  sheet:    {result.contact_sheet.relative_to(project.root)}")
    return _report_video_qc(result.report)


def cmd_video_handoff(args: argparse.Namespace) -> int:
    """Write a Kdenlive-compatible project and say what it targets and what it references."""

    try:
        project = load_project(args.project)
        result = video_handoff(project, args.deliverable)
    except (ManifestError, VideoError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    target = result.kdenlive_version or "unknown (kdenlive is not on PATH)"
    print(f"handoff {result.path.relative_to(project.root)}")
    print(f"  kdenlive:  {target}")
    print(f"  envelope:  docproperties.version, documentid {result.document_id}")
    print(
        f"  edit:      {result.timeline.duration:.1f}s, "
        f"{len(result.timeline.clips)} clip(s), {len(result.timeline.titles)} title(s)"
    )
    print(f"  render:    out/{result.path.stem}.mlt is untouched; the envelope lives only here")
    for note in result.notes:
        print(f"  note:      {note}")
    for problem in result.problems:
        print(f"  problem:   {problem}")
    print("verdict: " + ("pass" if not result.problems else "FAIL"))
    return EXIT_OK if not result.problems else EXIT_FAILURE


def cmd_video_qc(args: argparse.Namespace) -> int:
    """Measure a file against the project's expectations."""

    try:
        project = load_project(args.project)
        source = Path(args.file).expanduser()
        if not source.is_file():
            raise VideoError(f"no such file: {source}")
        deliverable = next(
            (item for item in project.deliverables if item.kind == "video"),
            None,
        )
        expected = deliverable.duration if deliverable and deliverable.duration else None
        if expected is None:
            expected = media.probe(source).duration
        report = qc_report(
            source,
            expected_duration=expected,
            target_lufs=project.loudness_lufs,
        )
    except (ManifestError, VideoError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    report_path = project.root / "out" / "reports" / f"{source.stem}-qc.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"measured {source.name}")
    print(f"  report:   {report_path.relative_to(project.root)}")
    return _report_video_qc(report)


def _report_video_qc(report: dict[str, object]) -> int:
    """Print every check and return the exit status that matches them."""

    loudness = report.get("loudness")
    if isinstance(loudness, dict):
        print(
            f"  loudness: {loudness['integrated_lufs']} LUFS "
            f"(target {loudness['target_lufs']}, off by {loudness['distance_lu']} LU), "
            f"true peak {loudness['true_peak_dbtp']} dBTP"
        )
    print(
        f"  media:    {report['width']}x{report['height']} @ {report['fps']} fps, "
        f"{report['duration_seconds']}s, {report['video_codec']}/{report['audio_codec']}"
    )
    checks = report.get("checks")
    if isinstance(checks, dict):
        for name, passed in checks.items():
            print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    ok = bool(report.get("ok"))
    print("verdict: " + ("pass" if ok else "FAIL"))
    return EXIT_OK if ok else EXIT_FAILURE


def cmd_video_composite(args: argparse.Namespace) -> int:
    """Composite a rendered deliverable through Natron headless."""

    try:
        project = load_project(args.project)
        deliverable = args.deliverable
        if deliverable is None:
            video_deliverables = [item for item in project.deliverables if item.kind == "video"]
            if len(video_deliverables) != 1:
                raise VideoError("project has multiple video deliverables; pass --deliverable <id>")
            deliverable = video_deliverables[0].id

        compositing: dict = {}
        if any(
            v is not None
            for v in (
                args.lut,
                args.gain,
                args.gamma,
                args.saturation,
                args.glow_size,
                args.glow_mix,
                args.vignette,
            )
        ):
            compositing = {
                "grade": {
                    "lut": str(args.lut) if args.lut else None,
                    "gain": args.gain,
                    "gamma": args.gamma,
                    "saturation": args.saturation,
                },
                "effects": [],
            }
            if args.glow_size > 0:
                compositing["effects"].append(
                    {"glow": {"size": args.glow_size, "mix": args.glow_mix}}
                )
            if args.vignette > 0:
                compositing["effects"].append({"vignette": {"strength": args.vignette}})

        result = composite_project(deliverable, compositing, project_root=project.root)
    except (ManifestError, NatronError, VideoError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"composited {deliverable}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  ntp:     {result.ntp_path.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_motion_render(args: argparse.Namespace) -> int:
    """Render every motion-graphics sequence declared in project.yaml."""

    try:
        project = load_project(args.project)
        sequences = list(project.raw.get("motion", {}).get("sequences", []))
        if not sequences:
            print("no motion sequences declared under motion: in project.yaml")
            return EXIT_OK
        results = motion_project(sequences, project_root=project.root)
    except (ManifestError, EffectCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    all_ok = True
    for result in results:
        print(f"motion {result.ecp_path.stem}")
        print(f"  output:  {result.output.relative_to(project.root)}")
        print(f"  ecp:     {result.ecp_path.relative_to(project.root)}")
        print(f"  report:  {result.report_path.relative_to(project.root)}")
        print(f"  verdict: {'pass' if result.ok else 'FAIL'}")
        all_ok = all_ok and result.ok
    return EXIT_OK if all_ok else EXIT_FAILURE


def cmd_motion_title(args: argparse.Namespace) -> int:
    """Render one animated title sequence through EffectCraft."""

    try:
        project = load_project(args.project)
        result = title_sequence(
            text=args.text,
            output=args.out,
            ecp_path=args.out.with_suffix(".ecp"),
            duration=args.duration,
            animation=args.animation,
            font=args.font,
            font_size=args.font_size,
            color=args.color,
            glow=args.glow,
            glow_intensity=args.glow_intensity,
        )
    except (ManifestError, EffectCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"title {args.text}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  ecp:     {result.ecp_path.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_motion_lyrics(args: argparse.Namespace) -> int:
    """Render a lyric overlay track through EffectCraft."""

    try:
        project = load_project(args.project)
        lines = []
        for item in args.lines.split("|"):
            parts = item.split(",")
            if len(parts) != 3:
                raise EffectCraftError(f"invalid lyric entry {item!r}: expected start,end,text")
            lines.append((float(parts[0]), float(parts[1]), parts[2]))
        result = lyric_overlay(
            lines=lines,
            output=args.out,
            ecp_path=args.out.with_suffix(".ecp"),
            style=args.style,
        )
    except (ManifestError, EffectCraftError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"lyrics ({len(lines)} line(s))")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  ecp:     {result.ecp_path.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report toolchain status and exit non-zero when a required tool is missing."""

    order_problem = path_order_problem(os.environ.get("PATH", ""))
    checks: list[CheckResult] = [
        CheckResult("PATH order", order_problem is None, order_problem or "ok")
    ]
    if args.deep:
        checks.extend(deep_checks())
    report = DoctorReport(tools=collect_toolchain(), checks=checks)

    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_report(report)

    return EXIT_FAILURE if report.missing_required else EXIT_OK


def _print_report(report: DoctorReport) -> None:
    statuses = report.tools
    width = max(len(status.spec.name) for status in statuses)
    print(f"media-studio {__version__} toolchain\n")
    for status in statuses:
        mark = "ok " if status.ok else "MISSING"
        requirement = "required" if status.spec.required else "optional"
        version = status.version or status.path or "-"
        print(f"  {status.spec.name:<{width}}  {mark:<7} {requirement:<8} {version}")
    print("\nchecks")
    for check in report.checks:
        mark = "ok " if check.ok else "FAIL"
        print(f"  {check.name:<22} {mark:<5} {check.detail}")
    if report.missing_required:
        print(f"\nmissing required tools: {', '.join(report.missing_required)}")
    else:
        print("\nall required tools present")
    if report.warnings:
        print(f"warnings (non-fatal): {', '.join(report.warnings)}")


def cmd_project_init(args: argparse.Namespace) -> int:
    """Scaffold a project directory from the template."""

    try:
        created = scaffold_project(args.slug, root=args.root, title=args.title)
    except ProjectError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"created {created.path}")
    print(f"  slug:  {created.slug}")
    print(f"  title: {created.title}")
    print("next: edit project.yaml, then run the /assets workflow to stage licensed assets")
    return EXIT_OK


def cmd_project_validate(args: argparse.Namespace) -> int:
    """Validate a manifest and report every problem it has."""

    try:
        project = load_project(args.project)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    report = validate_project(project)
    report.errors.extend(validate_effects(project.effects))
    report.errors.extend(validate_design(project))
    print(f"{project.slug}: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    for problem in report.errors:
        print(f"  error:   {problem}")
    for problem in report.warnings:
        print(f"  warning: {problem}")
    return EXIT_OK if report.ok else EXIT_FAILURE


def cmd_assets_stage(args: argparse.Namespace) -> int:
    """Stage one asset into the project and write its sidecar."""

    try:
        project = load_project(args.project)
        staged = stage_asset(
            project,
            class_name=args.class_name,
            license_text=args.license_text,
            source_url=args.source_url,
            attribution=args.attribution,
            provider=args.provider,
            local_file=args.file,
            url=args.url,
            expected_sha256=args.expected_sha256,
            allow_noncommercial=args.allow_noncommercial,
        )
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print_staged(staged)
    return EXIT_OK


def print_staged(staged: StagedAsset) -> None:
    """Report one staging result, shared by `assets stage` and `assets pull`."""

    if staged.duplicate_of:
        print(f"already staged: {staged.relative} (duplicate of {staged.duplicate_of})")
    else:
        print(f"staged {staged.relative}")
    print(f"  licence:  {staged.spdx} ({staged.license_label})")
    print(f"  sha256:   {staged.sha256}")
    print(f"  bytes:    {staged.size_bytes}")
    print(f"  declare:  {suggested_asset_id(staged.relative)}: {staged.relative}")
    print("            (paste that line under `assets:` in project.yaml to use it)")
    if staged.quarantined:
        print(
            "  warning:  licence unknown, quarantined under assets/unverified/; "
            "no deliverable may reference it",
            file=sys.stderr,
        )
    elif staged.attribution_required and not staged.attribution:
        print(
            "  warning:  this licence requires attribution and no credit line was recorded",
            file=sys.stderr,
        )


def suggested_asset_id(relative: str) -> str:
    """A paste-ready manifest key derived from the file name."""

    stem = Path(relative).stem.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return (cleaned or "asset")[:32]


def cmd_assets_search(args: argparse.Namespace) -> int:
    """Search the keyless sources and cache the result for `assets pull`."""

    try:
        outcome = run_search(
            args.query,
            args.class_name,
            limit=args.limit,
            sources=tuple(args.source),
            include_unlicensed=args.include_unlicensed,
        )
    except SearchError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        saved = save_outcome(outcome, args.save)
    except OSError as error:
        print(f"error: could not save the results: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        json.dump(outcome.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        print(f"saved: {saved}", file=sys.stderr)
    else:
        print(outcome.render())
        print(f"\nsaved: {saved}")
        print(f"next: ms assets pull <project> --search {saved} --index <n>")

    return EXIT_OK if any(report.ok for report in outcome.reports) else EXIT_FAILURE


def cmd_assets_pull(args: argparse.Namespace) -> int:
    """Stage one candidate chosen from a saved search."""

    try:
        project = load_project(args.project)
        outcome = load_outcome(args.search)
    except (ManifestError, SearchError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not 1 <= args.index <= len(outcome.candidates):
        print(
            f"error: --index must be between 1 and {len(outcome.candidates)} "
            f"({outcome.query!r} returned {len(outcome.candidates)} candidate(s))",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    candidate = outcome.candidates[args.index - 1]
    class_name = args.class_name or STAGING_CLASS_FOR_KIND.get(candidate.kind, candidate.kind)
    resolving = candidate.download_url is None
    try:
        url = candidate.download_url or resolve_candidate(candidate)
    except SourceError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if resolving:
        print(f"resolved from {candidate.source}: {url}")

    if args.dry_run:
        print(f"would stage: {candidate.title}")
        print(f"  source:   {candidate.source}")
        print(f"  licence:  {candidate.license_text or 'unknown; it would be quarantined'}")
        print(f"  credit:   {candidate.creator or '(none recorded)'}")
        print(f"  page:     {candidate.source_page or '-'}")
        print(f"  url:      {url}")
        print(f"  class:    {class_name}")
        return EXIT_OK

    try:
        staged = stage_asset(
            project,
            class_name=class_name,
            license_text=candidate.license_text,
            source_url=candidate.source_page,
            attribution=candidate.creator,
            provider=candidate.source,
            url=url,
            allow_noncommercial=args.allow_noncommercial,
        )
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"from {candidate.source}: {candidate.title}")
    print_staged(staged)
    return EXIT_OK


def resolve_candidate(candidate: Candidate) -> str:
    """Ask a source to turn a page-only candidate into a direct download URL."""

    module = module_named(candidate.source)
    if module is None or not hasattr(module, "resolve"):
        page = candidate.source_page or candidate.key
        raise SourceError(
            f"{candidate.source} candidates have no direct link and this source cannot resolve "
            f"one; open {page} and stage the file with `ms assets stage --url`"
        )
    url, _name = module.resolve(candidate, http_fetch)
    return url


def cmd_assets_index(args: argparse.Namespace) -> int:
    """Regenerate the derived index and credits file."""

    try:
        project = load_project(args.project)
        index_path, credits_path, index = write_index_and_credits(project)
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    totals = index.totals
    quota = index.quota
    print(
        f"wrote {index_path.relative_to(project.root)} and {credits_path.relative_to(project.root)}"
    )
    print(
        f"  assets: {totals.count} staged, {totals.attribution_required} needing credit, "
        f"{totals.quarantined} quarantined"
    )
    print(
        f"  bytes: {quota.project_bytes} of {quota.max_project_bytes} allowed"
        f"{'' if quota.ok else ' (over budget)'}"
    )
    if index.unlicensed:
        print(f"  warning: {len(index.unlicensed)} file(s) have no sidecar")
    missing_credit = [record for record in index.assets if record.attribution_missing]
    if missing_credit:
        print(
            f"  warning: {len(missing_credit)} asset(s) need a credit line and none was recorded:"
        )
        for record in missing_credit:
            print(f"    {record.path} ({record.spdx})")
    return EXIT_OK if quota.ok else EXIT_FAILURE


def cmd_assets_list(args: argparse.Namespace) -> int:
    """List staged assets with their licence and attribution duty."""

    try:
        project = load_project(args.project)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    index = build_index(project)
    if not index.assets:
        print("no staged assets; use `ms assets stage` after picking a candidate")
        return EXIT_OK

    print(f"{'path':<48} {'licence':<14} {'credit':<7} class")
    for record in index.assets:
        credit = "yes" if record.attribution_required else "no"
        print(f"{record.path:<48} {record.spdx:<14} {credit:<7} {record.class_name}")
    print(f"\n{index.totals.count} asset(s), {index.totals.bytes} bytes staged")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point used by the ``ms`` console script."""

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
