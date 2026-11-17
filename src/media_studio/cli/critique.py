"""CLI subcommand for the Dual-Gate Visual Critic (`ms critique`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio.critique import critique_project
from media_studio.manifest import ManifestError, load_project

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_critique_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``critique`` subcommand."""
    crit = subparsers.add_parser("critique", help="run dual-gate visual and semantic critique")
    crit.add_argument("project", help="project directory or slug")
    crit.add_argument(
        "--stage",
        choices=("storyboard", "video"),
        default="video",
        help="critique stage (default: video)",
    )
    crit.add_argument("--numbered", action="store_true", help="show row numbers in defects table")
    crit.add_argument("--out", default=None, help="write JSON report to this path")
    crit.set_defaults(func=cmd_critique)


def cmd_critique(args: argparse.Namespace) -> int:
    """Run dual-gate critique and emit formatted table."""
    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        report = critique_project(project, stage=args.stage)
    except (ManifestError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_FAILURE

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    defects = report.get("defects") or []
    if not defects:
        print("No defects found — critique passed.")
        return EXIT_OK

    print(f"=== Critique Report: {project.slug} (Stage: {args.stage}) ===")
    headers = ["Cat", "Sev", "Defect", "Corrective Action"]
    if args.numbered:
        headers.insert(0, "#")

    rows = []
    for idx, d in enumerate(defects, start=1):
        row = [
            d.get("category", ""),
            d.get("severity", "").upper(),
            d.get("defect", ""),
            d.get("corrective_action", ""),
        ]
        if args.numbered:
            row.insert(0, str(idx))
        rows.append(row)

    col_widths = [max(len(r[i]) for r in [headers, *rows]) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)

    print(fmt.format(*headers))
    print("  ".join("-" * w for w in col_widths))
    for r in rows:
        print(fmt.format(*r))

    return EXIT_OK if report.get("ok") else EXIT_FAILURE
