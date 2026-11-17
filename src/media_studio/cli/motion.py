"""EffectCraft motion graphics (`ms motion`) CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio.effectcraft import (
    AMV_MOTION_PRESETS,
    MOTION_TEMPLATES,
    PROMO_MOTION_PRESETS,
    EffectCraftError,
    EffectCraftRunner,
    lower_third,
    lyric_overlay,
    motion_project,
    new_composition,
    render_amv_preset,
    render_promo_preset,
    title_sequence,
)
from media_studio.html_motion import HtmlMotionError, render_html_motion
from media_studio.manifest import ManifestError, load_project

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_motion_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``motion`` command group."""

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
    motion_title.add_argument(
        "--preset",
        choices=sorted({*AMV_MOTION_PRESETS, *PROMO_MOTION_PRESETS}),
        default=None,
        help="motion preset to render (AMV or promo; overrides --animation/--font/--glow)",
    )
    motion_title.add_argument("--subtext", default="", help="secondary text for promo presets")
    motion_title.add_argument("--font", default="Noto Sans", help="font family")
    motion_title.add_argument("--font-size", type=float, default=72.0, help="font size in points")
    motion_title.add_argument("--color", default="#FFFFFF", help="text color")
    motion_title.add_argument("--glow", action="store_true", help="enable glow effect")
    motion_title.add_argument("--glow-intensity", type=float, default=0.5, help="glow strength")
    motion_title.add_argument(
        "--width", type=int, default=1920, help="output width in pixels (default 1920)"
    )
    motion_title.add_argument(
        "--height", type=int, default=1080, help="output height in pixels (default 1080)"
    )
    motion_title.add_argument(
        "--fps", type=float, default=60.0, help="output frame rate (default 60)"
    )
    motion_title.set_defaults(func=cmd_motion_title)

    motion_lyrics = motion_sub.add_parser("lyrics", help="render a lyric overlay track")
    motion_lyrics.add_argument("project", help="project path or slug")
    motion_lyrics.add_argument("--out", type=Path, required=True, help="output file path")
    motion_lyrics.add_argument(
        "--lines", required=True, help="lyrics as start,end,text|start,end,text"
    )
    motion_lyrics.add_argument("--style", default="default", help="text style name")
    motion_lyrics.set_defaults(func=cmd_motion_lyrics)

    motion_lt = motion_sub.add_parser("lower-third", help="render a lower-third overlay track")
    motion_lt.add_argument("project", help="project path or slug")
    motion_lt.add_argument("--title", required=True, help="primary title text")
    motion_lt.add_argument("--subtitle", default="", help="optional subtitle text")
    motion_lt.add_argument("--out", type=Path, default=None, help="output file path")
    motion_lt.add_argument("--duration", type=float, default=4.0, help="duration in seconds")
    motion_lt.set_defaults(func=cmd_motion_lower_third)

    motion_html = motion_sub.add_parser(
        "html", help="render an HTML/SVG template to video via Playwright"
    )
    motion_html.add_argument("template", help="template name (bundled) or path to an .html file")
    motion_html.add_argument("--params", default="{}", help="JSON object of seek params")
    motion_html.add_argument("--project", help="project path or slug for brand token injection")
    motion_html.add_argument(
        "--out", type=Path, required=True, help="output file path (.mov for alpha)"
    )
    motion_html.add_argument(
        "--width", type=int, default=1080, help="output width in pixels (default 1080)"
    )
    motion_html.add_argument(
        "--height", type=int, default=1920, help="output height in pixels (default 1920)"
    )
    motion_html.add_argument("--fps", type=int, default=30, help="output frame rate (default 30)")
    motion_html.add_argument("--duration", type=float, default=3.0, help="duration in seconds")
    motion_html.add_argument(
        "--transparent",
        action="store_true",
        default=False,
        help="render with alpha channel (forces .mov)",
    )
    motion_html.set_defaults(func=cmd_motion_html)

    motion_insp = motion_sub.add_parser("inspect", help="inspect an EffectCraft project")
    motion_insp.add_argument("project", help="project path, slug, or direct .ecproj file")
    motion_insp.add_argument("--json", action="store_true", help="emit info as JSON")
    motion_insp.set_defaults(func=cmd_motion_inspect)

    motion_new = motion_sub.add_parser(
        "new", help="scaffold a brand-tokenised composition from a template (design 5.7)"
    )
    motion_new.add_argument("project", help="project path or slug")
    motion_new.add_argument(
        "--comp", required=True, help="composition id (file becomes motion/<id>.ecproj)"
    )
    motion_new.add_argument(
        "--template",
        default="lower-third",
        help="one of: lower-third, title-card, logo-sting, lyric-overlay, explainer-beat",
    )
    motion_new.add_argument("--title", default="Title", help="primary text of the template")
    motion_new.add_argument("--subtitle", default="Subtitle", help="secondary text of the template")
    motion_new.add_argument(
        "--duration", type=float, default=None, help="composition duration in seconds"
    )
    motion_new.add_argument(
        "--force", action="store_true", help="overwrite an existing composition scaffold"
    )
    motion_new.set_defaults(func=cmd_motion_new)

    motion_catalog = motion_sub.add_parser(
        "catalog", help="list built-in EffectCraft effects and templates"
    )
    motion_catalog.add_argument(
        "--category", default=None, help="filter effects by category (e.g. stylize, color, blur)"
    )
    motion_catalog.add_argument(
        "--templates", action="store_true", help="list built-in templates instead of effects"
    )
    motion_catalog.add_argument(
        "--json", action="store_true", help="emit output as machine-readable JSON"
    )
    motion_catalog.set_defaults(func=cmd_motion_catalog)


def cmd_motion_catalog(args: argparse.Namespace) -> int:
    """List built-in EffectCraft effects and templates."""
    from media_studio.effectcraft_catalog import list_effects, list_templates

    items = list_templates() if args.templates else list_effects(category=args.category)

    if args.json:
        json.dump(items, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK

    title = (
        "EffectCraft Templates"
        if args.templates
        else f"EffectCraft Effects ({args.category or 'All'})"
    )
    print(f"=== {title} ===")
    for item in items:
        desc = item.get("description", "")
        print(f"  {item['id']:<28} {item['name']:<22} {desc}")
    return EXIT_OK


def cmd_motion_new(args: argparse.Namespace) -> int:
    """Scaffold a brand-tokenised EffectCraft composition from a template."""

    if args.template not in MOTION_TEMPLATES:
        print(
            f"error: unknown template {args.template!r}; choose one of: "
            f"{', '.join(MOTION_TEMPLATES)}",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        deliverable = None
        for candidate in project.deliverables:
            if candidate.kind == "video":
                deliverable = candidate
                break
        palette = dict(project.brand.palette) if project.brand.palette else None
        fonts = dict(project.brand.fonts) if project.brand.fonts else None
        path = new_composition(
            project.root,
            args.comp,
            args.template,
            duration=args.duration,
            deliverable_resolution=deliverable.resolution if deliverable else None,
            deliverable_fps=deliverable.fps if deliverable else None,
            palette=palette,
            fonts=fonts,
            title=args.title,
            subtitle=args.subtitle,
            force=args.force,
        )
        print(f"scaffolded {path.relative_to(project.root)}")
        print(f"  template:  {args.template}")
        print("  next:      render the scaffold, then let the compiler place it on V2:")
        print(
            f"             effectcraft-cli --project {path} render"
            f" --out out/motion/{args.comp}.mov --channels rgba"
        )
        print(
            "             declare the sequence under motion: sequences:"
            f" in project.yaml (id: {args.comp})"
        )
        print("             ms video build imports out/motion/<id>.mov onto V2 automatically")
        return EXIT_OK
    except (ManifestError, EffectCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_motion_render(args: argparse.Namespace) -> int:
    """Render every motion-graphics sequence declared in project.yaml."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        if args.preset:
            if args.preset in PROMO_MOTION_PRESETS:
                result = render_promo_preset(
                    preset_name=args.preset,
                    output=args.out,
                    text=args.text,
                    subtext=args.subtext,
                    project=project,
                    width=args.width,
                    height=args.height,
                    fps=args.fps,
                    duration=args.duration,
                )
            else:
                result = render_amv_preset(
                    preset_name=args.preset,
                    output=args.out,
                    text=args.text,
                    duration=args.duration,
                    fps=args.fps,
                    width=args.width,
                    height=args.height,
                )
        else:
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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
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


def cmd_motion_lower_third(args: argparse.Namespace) -> int:
    """Render a lower-third motion graphic with alpha ProRes 4444."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        out_path = args.out or project.root / "out" / "motion" / "lower-third.mov"
        ecp_path = out_path.with_suffix(".ecproj")
        res = lower_third(
            title=args.title,
            subtitle=args.subtitle,
            output=out_path,
            ecp_path=ecp_path,
            duration=args.duration,
        )
        print(f"lower-third: {args.title}")
        print(f"  output:  {res.output.relative_to(project.root)}")
        print(f"  report:  {res.report_path.relative_to(project.root)}")
        print(f"  verdict: {'pass' if res.ok else 'FAIL'}")
        return EXIT_OK if res.ok else EXIT_FAILURE
    except Exception as error:
        print(f"error rendering lower-third: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_motion_html(args: argparse.Namespace) -> int:
    """Render an HTML/SVG template to video via Playwright frame-stepping."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    project = None
    if args.project:
        try:
            project = load_fn(args.project)
        except ManifestError as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_FAILURE
    params = {}
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as error:
            print(f"error: invalid --params JSON: {error}", file=sys.stderr)
            return EXIT_FAILURE
    try:
        result = render_html_motion(
            args.template,
            output=args.out,
            params=params,
            project=project,
            width=args.width,
            height=args.height,
            fps=args.fps,
            duration=args.duration,
            transparent=args.transparent,
        )
    except HtmlMotionError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"html {result.template}")
    print(f"  output:  {result.output}")
    print(f"  frames:  {result.frames} @ {result.fps} fps ({result.duration}s)")
    print(f"  size:    {result.width}x{result.height}, transparent={result.transparent}")
    return EXIT_OK


def cmd_motion_inspect(args: argparse.Namespace) -> int:
    """Inspect EffectCraft project comps and layers."""

    try:
        target = Path(args.project)
        if target.is_dir():
            out_motion = target / "out" / "motion"
            candidates = list(out_motion.glob("*.ecproj")) + list(target.glob("*.ecproj"))
            if not candidates:
                print(f"no .ecproj found in {target}", file=sys.stderr)
                return EXIT_FAILURE
            target = candidates[0]
        runner = EffectCraftRunner()
        info = runner.inspect(target)
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"project: {info.get('name', target.name)}")
            comps = info.get("comps", [])
            print(f"comps:   {len(comps)}")
            for comp in comps:
                w = comp.get("width", 1920)
                h = comp.get("height", 1080)
                fps = comp.get("fps", 30)
                print(f"  comp {comp.get('name', '?')}: {w}x{h} @ {fps}fps")
        return EXIT_OK
    except Exception as error:
        print(f"error inspecting motion project: {error}", file=sys.stderr)
        return EXIT_FAILURE
