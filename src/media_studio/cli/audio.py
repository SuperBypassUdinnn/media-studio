"""Audio, TTS, and SFX CLI group (stub for Tasks 5-6)."""

from __future__ import annotations

import argparse

from .common import EXIT_OK


def register_audio_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``audio`` group parser (stub for Tasks 5-6)."""

    parser = subparsers.add_parser("audio", help="audio, TTS, and SFX processing (stub)")
    parser.set_defaults(func=lambda _args: EXIT_OK)
