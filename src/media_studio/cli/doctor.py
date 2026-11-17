"""Toolchain inspection (`ms doctor`) CLI command."""

from __future__ import annotations

import argparse
import json
import os
import sys

from media_studio import __version__
from media_studio.tools import (
    CheckResult,
    DoctorReport,
    collect_toolchain,
    deep_checks,
    path_order_problem,
)

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_doctor_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``doctor`` subcommand."""

    doctor = subparsers.add_parser("doctor", help="report the local media toolchain")
    doctor.add_argument("--deep", action="store_true", help="also probe GPU files and torch venvs")
    doctor.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    doctor.set_defaults(func=cmd_doctor)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report toolchain status and exit non-zero when a required tool is missing."""

    path_check_fn = resolve_monkeypatchable("path_order_problem", path_order_problem)
    collect_fn = resolve_monkeypatchable("collect_toolchain", collect_toolchain)

    order_problem = path_check_fn(os.environ.get("PATH", ""))
    checks: list[CheckResult] = [
        CheckResult("PATH order", order_problem is None, order_problem or "ok")
    ]
    if args.deep:
        checks.extend(deep_checks())
    report = DoctorReport(tools=collect_fn(), checks=checks)

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
