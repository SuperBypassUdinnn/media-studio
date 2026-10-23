"""Command line interface for media-studio."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .project import ProjectError, default_project_root, scaffold_project
from .tools import CheckResult, DoctorReport, collect_toolchain, deep_checks, path_order_problem

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

    return parser


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


def main(argv: list[str] | None = None) -> int:
    """Entry point used by the ``ms`` console script."""

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
