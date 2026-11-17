"""Font search and specimen sheet (`ms fonts`) CLI commands."""

from __future__ import annotations

import argparse
import sys

from media_studio.fonts import FontError, font_environment, inventory, specimen_sheet
from media_studio.fonts import search as search_fonts
from media_studio.manifest import ManifestError, load_project

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_fonts_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``fonts`` command group."""

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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
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
