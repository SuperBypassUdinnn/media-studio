"""Project scaffolding and validation (`ms project`) CLI commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_studio.design import validate_design
from media_studio.manifest import ManifestError, load_project, validate_project
from media_studio.presets import validate_effects
from media_studio.project import ProjectError, default_project_root, scaffold_project

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_project_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``project`` command group."""

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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
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
