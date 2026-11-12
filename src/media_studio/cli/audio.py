"""Audio, TTS, and SFX CLI group (`ms audio`).

Task 5 wires the bundled CC0 SFX pack: ``ms audio sfx list [--tag TAG]`` and
``ms audio sfx mix <audio> --events <json> --total-duration <sec> --out <mixed.wav>``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio import media
from media_studio.media import MediaError
from media_studio.sfx import SfxCue, SfxError, list_bundled_sfx, mix_transition_sfx

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_audio_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``audio`` command group and the ``sfx`` subcommands."""

    parser = subparsers.add_parser("audio", help="audio, TTS, and SFX processing")
    audio_sub = parser.add_subparsers(dest="audio_command", required=True)

    sfx = audio_sub.add_parser("sfx", help="bundled CC0 SFX pack and mixing")
    sfx_sub = sfx.add_subparsers(dest="sfx_command", required=True)

    list_p = sfx_sub.add_parser("list", help="list bundled CC0 SFX entries")
    list_p.add_argument(
        "--tag", default=None, help="filter entries by tag (whoosh, impact, riser, ui)"
    )
    list_p.set_defaults(func=cmd_audio_sfx_list)

    mix_p = sfx_sub.add_parser("mix", help="mix SFX cues over a base audio file")
    mix_p.add_argument(
        "audio",
        nargs="?",
        default=None,
        help="base audio file (a path, or 'none'/omitted for a pure SFX bed)",
    )
    mix_p.add_argument(
        "--events",
        required=True,
        help="SFX cues as a JSON file path or a JSON string",
    )
    mix_p.add_argument(
        "--total-duration",
        type=float,
        default=None,
        help="output duration in seconds (defaults to the base audio duration)",
    )
    mix_p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="alias for --total-duration",
    )
    mix_p.add_argument("--out", type=Path, required=True, help="destination .wav file")
    mix_p.set_defaults(func=cmd_audio_sfx_mix)


def cmd_audio_sfx_list(args: argparse.Namespace) -> int:
    """List bundled SFX entries, optionally filtered by tag."""

    list_fn = resolve_monkeypatchable("list_bundled_sfx", list_bundled_sfx)
    try:
        entries = list_fn(tag=args.tag)
    except SfxError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"bundled CC0 SFX ({len(entries)}):")
    for entry in entries:
        print(
            f"  {entry.id}  tags={','.join(entry.tags)}  {entry.duration:.2f}s  {entry.path.name}"
        )
    return EXIT_OK


def _parse_events(raw: str) -> list[SfxCue]:
    """Parse ``--events`` as a JSON string or a path to a JSON cues file."""

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        path = Path(raw)
        if not path.is_file():
            raise SfxError("--events is not valid JSON or a readable JSON cues file") from error
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SfxError(f"--events could not be parsed: {error}") from error
    if not isinstance(payload, list):
        raise SfxError("--events must be a JSON array of cue objects")
    cues: list[SfxCue] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise SfxError(f"cue #{index} must be a JSON object")
        if "path_or_id" not in item or "at" not in item:
            raise SfxError(f"cue #{index} requires 'path_or_id' and 'at'")
        cues.append(
            SfxCue(
                path_or_id=str(item["path_or_id"]),
                at=float(item["at"]),
                gain_db=float(item.get("gain_db", 0.0)),
            )
        )
    return cues


def cmd_audio_sfx_mix(args: argparse.Namespace) -> int:
    """Mix SFX cues over a base audio file into a WAV."""

    mix_fn = resolve_monkeypatchable("mix_transition_sfx", mix_transition_sfx)
    try:
        total_duration = args.total_duration
        if total_duration is None:
            total_duration = args.duration

        base: Path | None = None
        if args.audio is not None and str(args.audio).strip().lower() != "none":
            base = Path(args.audio)

        if total_duration is None:
            if base is None:
                raise SfxError("--total-duration is required when no base audio is given")
            total_duration = media.probe(base).duration
        if total_duration <= 0:
            raise SfxError(f"total_duration must be positive, got {total_duration}")

        events = _parse_events(args.events)
        output = mix_fn(
            base,
            events,
            output=args.out,
            total_duration=float(total_duration),
        )
    except (SfxError, MediaError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"mixed {len(events)} sfx cue(s) -> {output}")
    return EXIT_OK
