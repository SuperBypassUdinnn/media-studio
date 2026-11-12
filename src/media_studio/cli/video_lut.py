"""Color LUT search, preview, and application subcommands under `ms video`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_studio.ingest import IngestError
from media_studio.library import LIBRARY_ROOT, LibraryError, load_catalog
from media_studio.lut import (
    BUNDLED_LUTS_DIR,
    LutError,
    _resolve_lut,
    apply_lut,
    preview_lut,
    search_luts,
)
from media_studio.see import SeeError, peek

from .common import EXIT_FAILURE, EXIT_OK


def register_video_lut_parsers(video_sub: argparse._SubParsersAction) -> None:
    """Register ``lut`` subcommands under ``video``."""

    lut = video_sub.add_parser("lut", help="search, preview, and apply color LUTs")
    lut_sub = lut.add_subparsers(dest="lut_command", required=True)

    lut_search = lut_sub.add_parser("search", help="search bundled and library LUT presets")
    lut_search.add_argument(
        "--tags",
        action="append",
        default=[],
        metavar="TAG",
        help="only entries carrying this tag (repeatable)",
    )
    lut_search.add_argument(
        "--style", default=None, help="style name fragment to match (repeatable via --tags)"
    )
    lut_search.set_defaults(func=cmd_video_lut_search)

    lut_preview = lut_sub.add_parser(
        "preview", help="extract a frame, apply a LUT, and hstack original vs graded"
    )
    lut_preview.add_argument("lut", help="LUT .cube file or library asset id")
    lut_preview.add_argument("reference", help="reference shot to grade")
    lut_preview.add_argument("--frame", type=int, default=0, help="frame to extract (default 0)")
    lut_preview.add_argument(
        "--out", type=Path, default=None, help="destination PNG (default beside the reference)"
    )
    lut_preview.set_defaults(func=cmd_video_lut_preview)

    lut_apply = lut_sub.add_parser(
        "apply", help="apply a LUT to a video file and transcode the result"
    )
    lut_apply.add_argument("source", help="source video file")
    lut_apply.add_argument("--lut", required=True, help="LUT .cube file or library asset id")
    lut_apply.add_argument(
        "--out", type=Path, default=None, help="destination file (default beside the source)"
    )
    lut_apply.add_argument(
        "--codec",
        choices=("h264", "prores"),
        default="h264",
        help="encode codec (default h264)",
    )
    lut_apply.set_defaults(func=cmd_video_lut_apply)


def _lut_display_lines(results: list[Path], library_root: Path = LIBRARY_ROOT) -> list[str]:
    """Render one display line per LUT result: ``<id_or_bundled> <path> <name>``."""

    bundled_dir = BUNDLED_LUTS_DIR
    catalog: dict[str, object] = {}
    try:
        for entry in load_catalog(root=library_root):
            catalog[str(entry.file_path)] = entry
    except LibraryError:
        pass
    lines: list[str] = []
    for path in results:
        try:
            path.relative_to(bundled_dir)
            label = path.stem
            name = path.stem
        except ValueError:
            entry = catalog.get(str(path))
            label = getattr(entry, "id", path.stem) if entry is not None else path.stem
            name = getattr(entry, "name", path.stem) if entry is not None else path.stem
        lines.append(f"{label} {path} {name}")
    return lines


def cmd_video_lut_search(args: argparse.Namespace) -> int:
    """Search bundled and library LUT presets."""

    try:
        results = search_luts(library_root=LIBRARY_ROOT)
    except (LutError, LibraryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if not results:
        print("no LUTs found; register one with `ms library register --type preset --tags lut`")
        return EXIT_OK
    for line in _lut_display_lines(results):
        print(line)
    return EXIT_OK


def cmd_video_lut_preview(args: argparse.Namespace) -> int:
    """Extract a frame, apply a LUT, and hstack original vs graded."""

    lut_path = _resolve_lut(args.lut)
    reference = Path(args.reference).expanduser()
    if not reference.is_file():
        print(f"error: reference file not found: {reference}", file=sys.stderr)
        return EXIT_FAILURE
    out = (
        Path(args.out).expanduser() if args.out else reference.parent / f"{reference.stem}-lut.png"
    )
    try:
        result = preview_lut(lut_path, reference, out)
    except (LutError, LibraryError, IngestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        sheet = peek(result)
    except SeeError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"graded {result}")
    print(f"sheet {sheet.sheet}")
    return EXIT_OK


def cmd_video_lut_apply(args: argparse.Namespace) -> int:
    """Apply a LUT to a video file and transcode the result."""

    source = Path(args.source).expanduser()
    if not source.is_file():
        print(f"error: source file not found: {source}", file=sys.stderr)
        return EXIT_FAILURE
    lut_path = _resolve_lut(args.lut)
    if args.out is not None:
        out = Path(args.out).expanduser()
    elif args.codec == "prores":
        out = source.parent / f"{source.stem}-lut.mov"
    else:
        out = source.parent / f"{source.stem}-lut.mp4"
    try:
        destination = apply_lut(source, lut_path, out, codec=args.codec)
    except (LutError, LibraryError, IngestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    size_mb = destination.stat().st_size / 1_000_000
    print(f"applied {lut_path.name} -> {destination}")
    print(f"  size: {size_mb:.1f} MB")
    return EXIT_OK
