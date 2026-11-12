"""Local asset library catalog (`ms library`) CLI commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_studio.library import (
    VALID_TYPES,
    LibraryError,
    forget_asset,
    load_catalog,
    register_asset,
    search_assets,
)

from .common import EXIT_FAILURE, EXIT_OK


def register_library_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``library`` command group."""

    library = subparsers.add_parser("library", help="manage the asset library catalog")
    library_sub = library.add_subparsers(dest="library_command", required=True)

    library_list = library_sub.add_parser(
        "list", help="list catalog entries, optionally by type or tag"
    )
    library_list.add_argument("--type", choices=VALID_TYPES, default=None)
    library_list.add_argument(
        "--tags",
        action="append",
        default=[],
        metavar="TAG",
        help="only entries carrying this tag (repeatable)",
    )
    library_list.set_defaults(func=cmd_library_list)

    library_search = library_sub.add_parser(
        "search", help="search the catalog by name, optionally filtered by type"
    )
    library_search.add_argument("query", help="name fragment to match")
    library_search.add_argument("--type", choices=VALID_TYPES, default=None)
    library_search.set_defaults(func=cmd_library_search)

    library_inspect = library_sub.add_parser("inspect", help="inspect an asset with `ms see`")
    library_inspect.add_argument("id", help="asset id from the catalog")
    library_inspect.set_defaults(func=cmd_library_inspect)

    library_register = library_sub.add_parser(
        "register", help="copy a file into the library and catalog it"
    )
    library_register.add_argument("file", type=Path, help="asset file to register")
    library_register.add_argument("--name", required=True, help="display name")
    library_register.add_argument("--type", choices=VALID_TYPES, required=True, help="asset type")
    library_register.add_argument(
        "--tags",
        action="append",
        default=[],
        metavar="TAG",
        help="tags (repeatable)",
    )
    library_register.add_argument("--source-url", default="", help="source page URL")
    library_register.add_argument("--license", default="CC0", help="SPDX licence id")
    library_register.add_argument("--attribution", default="", help="credit line")
    library_register.set_defaults(func=cmd_library_register)

    library_forget = library_sub.add_parser("forget", help="remove an entry from the catalog")
    library_forget.add_argument("id", help="asset id from the catalog")
    library_forget.add_argument(
        "--purge",
        action="store_true",
        help="also delete the physical file from library storage",
    )
    library_forget.set_defaults(func=cmd_library_forget)


def cmd_library_list(args: argparse.Namespace) -> int:
    """List catalog entries, optionally filtered by type or tag."""

    try:
        entries = search_assets(type=args.type, tags=args.tags)
    except LibraryError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if not entries:
        print("no assets in the library; register one with `ms library register`")
        return EXIT_OK
    for entry in entries:
        print(f"  {entry.id}  {entry.type:<7} {entry.name}")
        print(f"    file:   {entry.file_path}")
        print(f"    sha256: {entry.sha256}")
        if entry.tags:
            print(f"    tags:   {', '.join(entry.tags)}")
        if entry.used_in:
            print(f"    used in:{', '.join(entry.used_in)}")
    return EXIT_OK


def cmd_library_search(args: argparse.Namespace) -> int:
    """Search the catalog by name, optionally filtered by type."""

    try:
        entries = search_assets(args.query, type=args.type)
    except LibraryError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if not entries:
        print(f"no assets match {args.query!r}")
        return EXIT_OK
    for entry in entries:
        print(f"  {entry.id}  {entry.type:<7} {entry.name}")
    return EXIT_OK


def cmd_library_inspect(args: argparse.Namespace) -> int:
    """Inspect an asset by running `ms see` on its file."""

    try:
        entries = load_catalog()
    except LibraryError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    entry = next((item for item in entries if item.id == args.id), None)
    if entry is None:
        print(f"error: no asset with id {args.id!r}", file=sys.stderr)
        return EXIT_FAILURE
    if not entry.file_path.is_file():
        print(f"error: asset file not found: {entry.file_path}", file=sys.stderr)
        return EXIT_FAILURE

    from .triage_see import cmd_see

    namespace = argparse.Namespace(
        source=entry.file_path,
        out=None,
        columns=4,
        rows=3,
        width=320,
        mode="uniform",
        scene_threshold=0.3,
    )
    return cmd_see(namespace)


def cmd_library_register(args: argparse.Namespace) -> int:
    """Copy a file into the library and catalog it."""

    try:
        entry = register_asset(
            args.file,
            name=args.name,
            type=args.type,
            tags=args.tags,
            source_url=args.source_url,
            license=args.license,
            attribution=args.attribution,
        )
    except LibraryError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"registered {entry.name} ({entry.type})")
    print(f"  id:     {entry.id}")
    print(f"  file:   {entry.file_path}")
    print(f"  sha256: {entry.sha256}")
    return EXIT_OK


def cmd_library_forget(args: argparse.Namespace) -> int:
    """Remove an entry from the catalog, optionally deleting its file."""

    try:
        forget_asset(args.id, purge=args.purge)
    except LibraryError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"forgot {args.id}")
    return EXIT_OK
