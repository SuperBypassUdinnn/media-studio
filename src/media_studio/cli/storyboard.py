"""CLI subcommands for Visual-First Storyboard Engine (`ms storyboard`)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_studio.manifest import ManifestError, load_project
from media_studio.scene_graph import load_scene_graph
from media_studio.storyboard import build_visual_storyboard

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_storyboard_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``storyboard`` command group."""
    sb = subparsers.add_parser("storyboard", help="build and iterate visual-first storyboards")
    sub = sb.add_subparsers(dest="storyboard_command", required=True)

    build = sub.add_parser("build", help="render full-resolution visual keyframes and bento sheet")
    build.add_argument("project", help="project directory or slug")
    build.add_argument("--scene", default=None, help="re-render only a specific scene id")
    build.add_argument("--out", default=None, help="output path for storyboard contact sheet")
    build.set_defaults(func=cmd_storyboard_build)


def cmd_storyboard_build(args: argparse.Namespace) -> int:
    """Render authentic visual scene keyframes and bento contact sheet."""
    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        sg_path = project.root / "scene-graph.json"
        if not sg_path.is_file():
            print(f"error: scene-graph.json not found in {project.root}", file=sys.stderr)
            return EXIT_FAILURE
        graph = load_scene_graph(sg_path)
        scenes = [args.scene] if args.scene else None
        out_path = Path(args.out) if args.out else None
        sheet = build_visual_storyboard(project, graph, scenes=scenes, output=out_path)
        print(f"visual storyboard written to {sheet}")
        return EXIT_OK
    except (ManifestError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_FAILURE
