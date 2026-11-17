"""Asset staging, searching, and indexing (`ms assets`) CLI commands."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from media_studio.ledger import (
    ASSET_CLASSES,
    LedgerError,
    StagedAsset,
    build_index,
    stage_asset,
    write_index_and_credits,
)
from media_studio.manifest import ManifestError, load_project
from media_studio.search import SEARCH_KINDS, SearchError, load_outcome, run_search, save_outcome
from media_studio.sources import module_named
from media_studio.sources.base import Candidate, SourceError, http_fetch

from .common import EXIT_FAILURE, EXIT_OK, STAGING_CLASS_FOR_KIND, resolve_monkeypatchable


def register_assets_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``assets`` command group."""

    assets = subparsers.add_parser("assets", help="stage assets and maintain the ledger")
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)

    stage = assets_sub.add_parser("stage", help="copy or download one asset with its sidecar")
    stage.add_argument("project", help="project path or slug")
    stage.add_argument("--class", dest="class_name", required=True, choices=ASSET_CLASSES)
    source = stage.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="local file to copy in")
    source.add_argument("--url", help="URL to download")
    stage.add_argument("--license", dest="license_text", required=True, help="licence wording")
    stage.add_argument("--attribution", default="", help="credit line, when required")
    stage.add_argument("--source-url", default="", help="page the asset came from")
    stage.add_argument("--provider", default="", help="provider name")
    stage.add_argument(
        "--sha256",
        dest="expected_sha256",
        default=None,
        help="known hash of the asset; skips the transfer when it is already staged",
    )
    stage.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="permit a licence that forbids commercial use (personal work only)",
    )
    stage.set_defaults(func=cmd_assets_stage)

    index = assets_sub.add_parser("index", help="regenerate assets/index.json and CREDITS.md")
    index.add_argument("project", help="project path or slug")
    index.set_defaults(func=cmd_assets_index)

    listing = assets_sub.add_parser("list", help="show staged assets and their licences")
    listing.add_argument("project", help="project path or slug")
    listing.set_defaults(func=cmd_assets_list)

    search = assets_sub.add_parser("search", help="search keyless sources for licensed candidates")
    search.add_argument("query", help="what to look for, for example 'forest canopy'")
    search.add_argument("--class", dest="class_name", required=True, choices=SEARCH_KINDS)
    search.add_argument("--limit", type=int, default=8, help="results per source (default 8)")
    search.add_argument(
        "--source", action="append", default=[], metavar="NAME", help="only this source"
    )
    search.add_argument(
        "--save",
        type=Path,
        default=None,
        help="where to write the result JSON (default: ~/.cache/media-studio/searches/)",
    )
    search.add_argument("--json", action="store_true", help="emit the result JSON on stdout")
    search.add_argument(
        "--include-unlicensed",
        action="store_true",
        help="also list candidates whose licence is unknown",
    )
    search.set_defaults(func=cmd_assets_search)

    pull = assets_sub.add_parser("pull", help="stage one candidate from a saved search")
    pull.add_argument("project", help="project path or slug")
    pull.add_argument("--search", type=Path, required=True, help="result JSON from `assets search`")
    pull.add_argument("--index", type=int, required=True, help="1-based candidate number")
    pull.add_argument(
        "--class",
        dest="class_name",
        default=None,
        choices=ASSET_CLASSES,
        help="staging class (default: derived from the search class)",
    )
    pull.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="permit a licence that forbids commercial use (personal work only)",
    )
    pull.add_argument(
        "--dry-run", action="store_true", help="show what would be staged, download nothing"
    )
    pull.set_defaults(func=cmd_assets_pull)


def cmd_assets_stage(args: argparse.Namespace) -> int:
    """Stage one asset into the project and write its sidecar."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    stage_fn = resolve_monkeypatchable("stage_asset", stage_asset)
    try:
        project = load_fn(args.project)
        staged = stage_fn(
            project,
            class_name=args.class_name,
            license_text=args.license_text,
            source_url=args.source_url,
            attribution=args.attribution,
            provider=args.provider,
            local_file=args.file,
            url=args.url,
            expected_sha256=args.expected_sha256,
            allow_noncommercial=args.allow_noncommercial,
        )
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print_staged(staged)
    return EXIT_OK


def print_staged(staged: StagedAsset) -> None:
    """Report one staging result, shared by `assets stage` and `assets pull`."""

    if staged.duplicate_of:
        print(f"already staged: {staged.relative} (duplicate of {staged.duplicate_of})")
    else:
        print(f"staged {staged.relative}")
    print(f"  licence:  {staged.spdx} ({staged.license_label})")
    print(f"  sha256:   {staged.sha256}")
    print(f"  bytes:    {staged.size_bytes}")
    print(f"  declare:  {suggested_asset_id(staged.relative)}: {staged.relative}")
    print("            (paste that line under `assets:` in project.yaml to use it)")
    if staged.quarantined:
        print(
            "  warning:  licence unknown, quarantined under assets/unverified/; "
            "no deliverable may reference it",
            file=sys.stderr,
        )
    elif staged.attribution_required and not staged.attribution:
        print(
            "  warning:  this licence requires attribution and no credit line was recorded",
            file=sys.stderr,
        )


def suggested_asset_id(relative: str) -> str:
    """A paste-ready manifest key derived from the file name."""

    stem = Path(relative).stem.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return (cleaned or "asset")[:32]


def cmd_assets_search(args: argparse.Namespace) -> int:
    """Search the keyless sources and cache the result for `assets pull`."""

    search_fn = resolve_monkeypatchable("run_search", run_search)
    try:
        outcome = search_fn(
            args.query,
            args.class_name,
            limit=args.limit,
            sources=tuple(args.source),
            include_unlicensed=args.include_unlicensed,
        )
    except SearchError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        saved = save_outcome(outcome, args.save)
    except OSError as error:
        print(f"error: could not save the results: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        json.dump(outcome.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        print(f"saved: {saved}", file=sys.stderr)
    else:
        print(outcome.render())
        print(f"\nsaved: {saved}")
        print(f"next: ms assets pull <project> --search {saved} --index <n>")

    return EXIT_OK if any(report.ok for report in outcome.reports) else EXIT_FAILURE


def cmd_assets_pull(args: argparse.Namespace) -> int:
    """Stage one candidate chosen from a saved search."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    stage_fn = resolve_monkeypatchable("stage_asset", stage_asset)
    try:
        project = load_fn(args.project)
        outcome = load_outcome(args.search)
    except (ManifestError, SearchError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not 1 <= args.index <= len(outcome.candidates):
        print(
            f"error: --index must be between 1 and {len(outcome.candidates)} "
            f"({outcome.query!r} returned {len(outcome.candidates)} candidate(s))",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    candidate = outcome.candidates[args.index - 1]
    class_name = args.class_name or STAGING_CLASS_FOR_KIND.get(candidate.kind, candidate.kind)
    resolving = candidate.download_url is None
    try:
        url = candidate.download_url or resolve_candidate(candidate)
    except SourceError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if resolving:
        print(f"resolved from {candidate.source}: {url}")

    if args.dry_run:
        print(f"would stage: {candidate.title}")
        print(f"  source:   {candidate.source}")
        print(f"  licence:  {candidate.license_text or 'unknown; it would be quarantined'}")
        print(f"  credit:   {candidate.creator or '(none recorded)'}")
        print(f"  page:     {candidate.source_page or '-'}")
        print(f"  url:      {url}")
        print(f"  class:    {class_name}")
        return EXIT_OK

    try:
        staged = stage_fn(
            project,
            class_name=class_name,
            license_text=candidate.license_text,
            source_url=candidate.source_page,
            attribution=candidate.creator,
            provider=candidate.source,
            url=url,
            allow_noncommercial=args.allow_noncommercial,
        )
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"from {candidate.source}: {candidate.title}")
    print_staged(staged)
    return EXIT_OK


def resolve_candidate(candidate: Candidate) -> str:
    """Ask a source to turn a page-only candidate into a direct download URL."""

    module = module_named(candidate.source)
    if module is None or not hasattr(module, "resolve"):
        page = candidate.source_page or candidate.key
        raise SourceError(
            f"{candidate.source} candidates have no direct link and this source cannot resolve "
            f"one; open {page} and stage the file with `ms assets stage --url`"
        )
    url, _name = module.resolve(candidate, http_fetch)
    return url


def cmd_assets_index(args: argparse.Namespace) -> int:
    """Regenerate the derived index and credits file."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        index_path, credits_path, index = write_index_and_credits(project)
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    totals = index.totals
    quota = index.quota
    print(
        f"wrote {index_path.relative_to(project.root)} and {credits_path.relative_to(project.root)}"
    )
    print(
        f"  assets: {totals.count} staged, {totals.attribution_required} needing credit, "
        f"{totals.quarantined} quarantined"
    )
    print(
        f"  bytes: {quota.project_bytes} of {quota.max_project_bytes} allowed"
        f"{'' if quota.ok else ' (over budget)'}"
    )
    if index.unlicensed:
        print(f"  warning: {len(index.unlicensed)} file(s) have no sidecar")
    missing_credit = [record for record in index.assets if record.attribution_missing]
    if missing_credit:
        print(
            f"  warning: {len(missing_credit)} asset(s) need a credit line and none was recorded:"
        )
        for record in missing_credit:
            print(f"    {record.path} ({record.spdx})")
    return EXIT_OK if quota.ok else EXIT_FAILURE


def cmd_assets_list(args: argparse.Namespace) -> int:
    """List staged assets with their licence and attribution duty."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    index = build_index(project)
    if not index.assets:
        print("no staged assets; use `ms assets stage` after picking a candidate")
        return EXIT_OK

    print(f"{'path':<48} {'licence':<14} {'credit':<7} class")
    for record in index.assets:
        credit = "yes" if record.attribution_required else "no"
        print(f"{record.path:<48} {record.spdx:<14} {credit:<7} {record.class_name}")
    print(f"\n{index.totals.count} asset(s), {index.totals.bytes} bytes staged")
    return EXIT_OK
