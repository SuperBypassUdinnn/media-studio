"""CLI subcommands for Composable Scene-Graph (`ms scene`)."""

from __future__ import annotations

import argparse
import sys

from media_studio.manifest import ManifestError, load_project
from media_studio.scene_graph import load_scene_graph, render_scene_graph

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_scene_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``scene`` command group."""
    scene = subparsers.add_parser("scene", help="manage and render composable scene-graph nodes")
    sub = scene.add_subparsers(dest="scene_command", required=True)

    render = sub.add_parser("render", help="render scene-graph deliverables")
    render.add_argument("project", help="project directory or slug")
    render.add_argument("--scene", default=None, help="render only a specific scene id")
    render.add_argument("--force", action="store_true", help="force re-render bypassing cache")
    render.set_defaults(func=cmd_scene_render)


def cmd_scene_render(args: argparse.Namespace) -> int:
    """Render scene-graph deliverables with scene-level caching."""
    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        sg_path = project.root / "scene-graph.json"
        if not sg_path.is_file():
            print(f"error: scene-graph.json not found in {project.root}", file=sys.stderr)
            return EXIT_FAILURE
        graph = load_scene_graph(sg_path)
        scenes = [args.scene] if args.scene else None
        renders = render_scene_graph(project, graph, scenes=scenes, force=args.force)
        for deliv, path in renders.items():
            print(f"rendered {deliv}: {path}")
        return EXIT_OK
    except (ManifestError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_FAILURE
