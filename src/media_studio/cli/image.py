"""Standalone image cutout and signature extraction (`ms image`) CLI commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from media_studio.cutout import (
    MODELS,
    CutoutError,
    extract_signature,
    extract_signature_folder,
    remove_background,
    remove_background_folder,
)

from .common import EXIT_FAILURE, EXIT_OK


def register_image_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``image`` command group."""

    image = subparsers.add_parser(
        "image", help="project-less image operations (cutouts, signatures)"
    )
    image_sub = image.add_subparsers(dest="image_command", required=True)

    remove_bg = image_sub.add_parser(
        "remove-bg", help="remove an image's background with AI segmentation"
    )
    remove_bg.add_argument("source", help="image file or folder to process")
    remove_bg.add_argument(
        "--out", default=None, help="destination; defaults to <stem>.nobg.png beside the source"
    )
    remove_bg.add_argument(
        "--model",
        choices=sorted(MODELS),
        default="u2net",
        help="segmentation model (default u2net; u2netp is fastest, birefnet-lite is strongest)",
    )
    remove_bg.add_argument(
        "--select",
        default=None,
        metavar="X,Y",
        help="keep only the object under this pixel (SAM model, ~375 MB download on first use)",
    )
    remove_bg.add_argument(
        "--post-process",
        action="store_true",
        help="tighten the mask edges (morphological post-processing)",
    )
    remove_bg.add_argument(
        "--no-recursive",
        action="store_true",
        help="when the source is a folder, skip its subfolders",
    )
    remove_bg.set_defaults(func=cmd_image_remove_bg)

    signature = image_sub.add_parser(
        "extract-signature", help="lift a signature from a scan onto a transparent background"
    )
    signature.add_argument("source", help="signature image file or folder to process")
    signature.add_argument(
        "--out",
        default=None,
        help="destination; defaults to <stem>.signature.png beside the source",
    )
    signature.add_argument(
        "--threshold",
        type=int,
        default=None,
        metavar="0-255",
        help="manual ink cutoff; defaults to an automatic Otsu threshold",
    )
    signature.add_argument(
        "--no-crop",
        action="store_true",
        help="keep the full image size instead of cropping to the ink",
    )
    signature.add_argument(
        "--no-recursive",
        action="store_true",
        help="when the source is a folder, skip its subfolders",
    )
    signature.set_defaults(func=cmd_image_extract_signature)


def cmd_image_remove_bg(args: argparse.Namespace) -> int:
    """Remove an image's background, no project needed."""

    select_point: tuple[int, int] | None = None
    if args.select is not None:
        try:
            x_text, _, y_text = args.select.partition(",")
            select_point = (int(x_text), int(y_text))
        except ValueError:
            print(f"error: --select expects X,Y, got {args.select!r}", file=sys.stderr)
            return EXIT_FAILURE

    source = Path(args.source)
    out_dir = Path(args.out) if args.out else None
    try:
        if source.is_dir():
            results = remove_background_folder(
                source,
                out_dir,
                model=args.model,
                post_process=args.post_process,
                recursive=not args.no_recursive,
            )
        else:
            result = remove_background(
                source,
                out_dir,
                model=args.model,
                post_process=args.post_process,
                select_point=select_point,
            )
            results = [result]
    except CutoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not results:
        print(f"no images found in {source}")
        return EXIT_OK

    for result in results:
        mode = "selective SAM" if result.selective else result.model
        device = f" ({result.device})" if result.device != "gpu" else ""
        print(f"removed background with {mode}{device}")
        print(f"  source: {result.source}")
        print(f"  output: {result.destination}")
    return EXIT_OK


def cmd_image_extract_signature(args: argparse.Namespace) -> int:
    """Lift a signature onto a transparent background, no project needed."""

    source = Path(args.source)
    out_dir = Path(args.out) if args.out else None
    try:
        if source.is_dir():
            results = extract_signature_folder(
                source,
                out_dir,
                threshold=args.threshold,
                crop=not args.no_crop,
                recursive=not args.no_recursive,
            )
        else:
            results = [
                extract_signature(
                    source,
                    out_dir,
                    threshold=args.threshold,
                    crop=not args.no_crop,
                )
            ]
    except CutoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not results:
        print(f"no images found in {source}")
        return EXIT_OK

    for result in results:
        print(f"extracted signature (threshold {result.threshold}, {result.ink_pixels} ink pixels)")
        print(f"  source: {result.source}")
        print(f"  output: {result.destination}")
        print(f"  cropped: {'yes' if result.cropped else 'no'}")
    return EXIT_OK
