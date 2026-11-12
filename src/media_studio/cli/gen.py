"""Generative media CLI group (stub for Task 8)."""

from __future__ import annotations

import argparse

from .common import EXIT_OK


def register_gen_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``gen`` group parser (stub for Task 8)."""

    parser = subparsers.add_parser("gen", help="generative media pipeline (stub)")
    parser.set_defaults(func=lambda _args: EXIT_OK)
