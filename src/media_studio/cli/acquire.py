"""Asset acquisition (`ms acquire`) CLI commands."""

from __future__ import annotations

import argparse
import sys

from media_studio.acquire import AcquireError
from media_studio.library import LibraryError, search_assets

from .common import EXIT_FAILURE, EXIT_OK


def register_acquire_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``acquire`` command group."""

    acquire = subparsers.add_parser("acquire", help="asset acquisition (debug)")
    acquire_sub = acquire.add_subparsers(dest="acquire_command", required=True)
    acquire_search = acquire_sub.add_parser(
        "search", help="search the asset library for acquisition candidates (debug)"
    )
    acquire_search.add_argument(
        "--type",
        required=True,
        help="asset type (video, audio, image, ...)",
    )
    acquire_search.add_argument(
        "--tags",
        action="append",
        default=[],
        metavar="TAG",
        help="tags the asset must carry (repeatable)",
    )
    acquire_search.add_argument("--query", default=None, help="name fragment to match")
    acquire_search.set_defaults(func=cmd_acquire_search)


def cmd_acquire_search(args: argparse.Namespace) -> int:
    """Search the asset library for acquisition candidates (debug command)."""

    try:
        entries = search_assets(args.query or "", type=args.type, tags=args.tags)
    except (AcquireError, LibraryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if not entries:
        print("no assets match")
        return EXIT_OK
    for entry in entries:
        print(f"  {entry.id}  {entry.type:<7} {entry.name}")
    return EXIT_OK
