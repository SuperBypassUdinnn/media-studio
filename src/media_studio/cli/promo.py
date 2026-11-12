"""Promotional video orchestration CLI group (stub for Task 9)."""

from __future__ import annotations

import argparse

from .common import EXIT_OK


def register_promo_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``promo`` group parser (stub for Task 9)."""

    parser = subparsers.add_parser("promo", help="promo video orchestration (stub)")
    parser.set_defaults(func=lambda _args: EXIT_OK)
