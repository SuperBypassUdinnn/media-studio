"""Footage triage and visual inspection (`ms triage`, `ms see`) CLI commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_studio import media
from media_studio.manifest import ManifestError, load_project
from media_studio.media import MediaError
from media_studio.see import SeeError, peek
from media_studio.triage import (
    build_index as build_triage_index,
)
from media_studio.triage import (
    build_sheets,
    clip_paths,
    concept_key,
    read_decisions,
    read_index,
    record_decision,
    undecided,
    write_index,
)

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_triage_see_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``triage`` command group and ``see`` command."""

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

    see = subparsers.add_parser(
        "see", help="look at any media file: facts plus a visual sheet the agent can read"
    )
    see.add_argument("source", type=Path, help="any video, image, or audio file")
    see.add_argument("--out", type=Path, default=None, help="sheet destination (file or folder)")
    see.add_argument("--columns", type=int, default=4, help="sheet columns (default 4)")
    see.add_argument("--rows", type=int, default=3, help="sheet rows (default 3)")
    see.add_argument("--width", type=int, default=320, help="tile width in px (default 320)")
    see.add_argument(
        "--mode",
        choices=["uniform", "scene", "beats"],
        default="uniform",
        help="frame sampling mode: uniform, scene, or beats (default uniform)",
    )
    see.add_argument(
        "--scene-threshold",
        type=float,
        default=0.3,
        help="scene-change threshold for scene mode (default 0.3)",
    )
    see.set_defaults(func=cmd_see)


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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project) if args.project else None
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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
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

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
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


def cmd_see(args: argparse.Namespace) -> int:
    """Look at any media file: facts plus a visual sheet the agent can read."""

    try:
        result = peek(
            args.source,
            args.out,
            columns=args.columns,
            rows=args.rows,
            width=args.width,
            mode=args.mode,
            scene_threshold=args.scene_threshold,
        )
    except SeeError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    for line in result.summary_lines:
        print(line)
    return EXIT_OK
