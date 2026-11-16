"""Promotional video orchestration CLI group."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio.manifest import ManifestError, load_project
from media_studio.promo import (
    DEFAULT_ASPECTS,
    DEFAULT_DELIVERABLES,
    PromoError,
    load_promo_plan,
    plan_promo,
    qc_promo,
    render_promo,
)

from .common import EXIT_FAILURE, EXIT_OK


def register_promo_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``promo`` command group."""

    parser = subparsers.add_parser("promo", help="dual-aspect promotional video orchestration")
    sub = parser.add_subparsers(dest="promo_command", required=True)

    plan_parser = sub.add_parser("plan", help="decompose script into typed promo beats")
    plan_parser.add_argument("project", help="project directory or slug")
    plan_parser.add_argument("--script", required=True, help="script text or path to .txt file")
    plan_parser.add_argument(
        "--aspect",
        default=",".join(DEFAULT_ASPECTS),
        help="comma-separated aspect ratios (default: 9:16,16:9)",
    )
    plan_parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="target promo duration in seconds (default: 30.0)",
    )
    plan_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output path for promo-plan.json (default: <project>/out/promo/promo-plan.json)",
    )
    plan_parser.set_defaults(func=cmd_promo_plan)

    render_parser = sub.add_parser("render", help="render promo deliverables from a plan")
    render_parser.add_argument("project", help="project directory or slug")
    render_parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="path to promo-plan.json (default: <project>/out/promo/promo-plan.json)",
    )
    render_parser.add_argument(
        "--auto",
        action="store_true",
        help="bypass storyboard/quota gate and render deliverables",
    )
    render_parser.add_argument(
        "--max-gen-calls",
        type=int,
        default=6,
        help="maximum generative calls allowed (default: 6)",
    )
    render_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing deliverables",
    )
    render_parser.add_argument(
        "--deliverables",
        default=",".join(DEFAULT_DELIVERABLES),
        help="comma-separated deliverable ids (default: reel-9x16,master-16x9)",
    )
    render_parser.add_argument(
        "--tts-engine",
        default="auto",
        help="TTS engine override (auto, edge, piper, kokoro, espeak-ng)",
    )
    render_parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="deliverable frame rate in fps (default: 30; use 60 for high-motion output)",
    )
    render_parser.add_argument(
        "--beat",
        default=None,
        help="beat index(es) to (re)render: --beat 2, --beat 0,2, or --beat 1-3",
    )
    render_parser.set_defaults(func=cmd_promo_render)

    qc_parser = sub.add_parser("qc", help="verify promo deliverables and generate contact sheets")
    qc_parser.add_argument("project", help="project directory or slug")
    qc_parser.add_argument(
        "--deliverables",
        default=",".join(DEFAULT_DELIVERABLES),
        help="comma-separated deliverable ids (default: reel-9x16,master-16x9)",
    )
    qc_parser.add_argument(
        "--no-media-mcp",
        action="store_true",
        help="skip optional media-mcp verification hook",
    )
    qc_parser.set_defaults(func=cmd_promo_qc)


def _read_script_arg(value: str) -> str:
    if "\n" not in value and len(value) < 256:
        try:
            candidate = Path(value)
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            pass
    return value


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_beats(value: str | None) -> tuple[int, ...] | None:
    """Parse a ``--beat`` value into sorted unique beat indices.

    Accepts ``--beat 2``, ``--beat 0,2``, or ``--beat 1-3``. Returns ``None`` when
    no value is given (meaning every beat renders fresh).
    """

    if value is None:
        return None
    indices: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = int(start_str.strip())
            end = int(end_str.strip())
            if end < start:
                raise ValueError(f"invalid beat range {part!r}: end is before start")
            indices.update(range(start, end + 1))
        else:
            indices.add(int(part))
    return tuple(sorted(indices))


def cmd_promo_plan(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
        script_text = _read_script_arg(args.script)
        aspects = _split_csv(args.aspect)
        out_path = args.out or (project.root / "out" / "promo" / "promo-plan.json")
        plan = plan_promo(
            project,
            script=script_text,
            aspects=aspects,
            out_path=out_path,
            target_duration=args.duration,
        )
    except (PromoError, ManifestError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(
        f"planned {len(plan.beats)} beats ({plan.total_duration:.1f}s, "
        f"estimated_gen_calls={plan.estimated_gen_calls}) -> {out_path}"
    )
    return EXIT_OK


def cmd_promo_render(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
        plan_path = args.plan or (project.root / "out" / "promo" / "promo-plan.json")
        plan = load_promo_plan(plan_path)
        deliverables = _split_csv(args.deliverables)
        beats = _parse_beats(args.beat)
        result = render_promo(
            project,
            plan,
            auto=args.auto,
            max_gen_calls=args.max_gen_calls,
            force=args.force,
            deliverables=deliverables,
            tts_engine=args.tts_engine,
            fps=args.fps,
            beats=beats,
        )
    except (PromoError, ManifestError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if result.gated:
        print(f"gated: storyboard written to {result.storyboard} (re-run with --auto to render)")
        return EXIT_OK

    for deliv_id, path in result.deliverables.items():
        print(f"rendered {deliv_id}: {path}")
    return EXIT_OK


def cmd_promo_qc(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
        deliverables = _split_csv(args.deliverables)
        report = qc_promo(
            project,
            deliverables=deliverables,
            use_media_mcp=not args.no_media_mcp,
        )
    except (PromoError, ManifestError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(json.dumps(report, indent=2))
    return EXIT_OK if report.get("ok") else EXIT_FAILURE
