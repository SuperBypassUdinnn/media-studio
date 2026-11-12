"""EDL, beat analysis/effects, and impact-frame subcommands under `ms video`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio import audio
from media_studio.beatfx import BeatFxError, apply_effects, auto_schedule, parse_schedule
from media_studio.beatramp import (
    TRIGGER_MAP,
    BeatRampError,
    apply_ramp_events,
    schedule_ramp_events,
)
from media_studio.edl import (
    EdlError,
    check_cuts,
    check_sync,
    generate_edl,
    load_edl,
    preview_edl_cut,
    render_edl,
)
from media_studio.impact import IMPACT_MODES, ImpactError, generate_impact_frames
from media_studio.ingest import IngestError, probe_media
from media_studio.scene import SceneError

from .common import EXIT_FAILURE, EXIT_OK


def register_video_edl_parsers(video_sub: argparse._SubParsersAction) -> None:
    """Register ``edl``, ``beats``, ``beatfx``, ``beatramp``, and ``impact-frame`` parsers."""

    beats_p = video_sub.add_parser(
        "beats", help="analyze rhythm and write beat JSON next to the audio"
    )
    beats_p.add_argument("audio", type=Path, help="audio file to analyze")
    beats_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="destination JSON (default: <audio-stem>.beats.json beside the audio)",
    )
    beats_p.add_argument(
        "--window", type=float, default=0.5, help="analysis window in seconds (default 0.5)"
    )
    beats_p.set_defaults(func=cmd_video_beats)

    beatfx_p = video_sub.add_parser(
        "beatfx", help="apply beat-mapped micro-effects to a video clip"
    )
    beatfx_p.add_argument("source", help="a video file to process")
    beatfx_p.add_argument(
        "--beats", type=Path, required=True, help="beat JSON file (from ms video beats)"
    )
    beatfx_p.add_argument(
        "--auto", action="store_true", help="auto-schedule effects from the beats"
    )
    beatfx_p.add_argument(
        "--schedule", type=Path, help="explicit schedule JSON file (list of effects)"
    )
    beatfx_p.add_argument(
        "--offset", type=float, default=0.0, help="seconds to subtract from beat times (default 0)"
    )
    beatfx_p.add_argument(
        "--out", type=Path, default=None, help="destination file or folder (default beside source)"
    )
    beatfx_p.add_argument("--fps", type=float, default=60.0, help="output frame rate (default 60)")
    beatfx_p.add_argument(
        "--resolution", default="1920x1080", help="output resolution WxH (default 1920x1080)"
    )
    beatfx_p.add_argument(
        "--codec", choices=("h264", "prores"), default="h264", help="encode codec (default h264)"
    )
    beatfx_p.add_argument(
        "--max-per-second", type=int, default=2, help="max effects per second (default 2)"
    )
    beatfx_p.set_defaults(func=cmd_video_beatfx)

    beatramp_p = video_sub.add_parser(
        "beatramp", help="apply beat-synced velocity ramps to a video clip"
    )
    beatramp_p.add_argument("source", help="a video file to process")
    beatramp_p.add_argument(
        "--beats", type=Path, required=True, help="beat JSON file (from ms video beats)"
    )
    beatramp_p.add_argument(
        "--offset", type=float, default=0.0, help="seconds to subtract from beat times (default 0)"
    )
    beatramp_p.add_argument(
        "--out", type=Path, default=None, help="destination file or folder (default beside source)"
    )
    beatramp_p.add_argument(
        "--fps", type=float, default=60.0, help="output frame rate (default 60)"
    )
    beatramp_p.add_argument(
        "--resolution", default="1920x1080", help="output resolution WxH (default 1920x1080)"
    )
    beatramp_p.add_argument(
        "--codec", choices=("h264", "prores"), default="h264", help="encode codec (default h264)"
    )
    beatramp_p.add_argument(
        "--max-per-second",
        type=float,
        default=1.0,
        help="max ramp events per second (default 1)",
    )
    beatramp_p.add_argument(
        "--map",
        type=json.loads,
        default=None,
        help=(
            "JSON trigger-to-kind override map "
            "(default: downbeat->fast_slow_fast, snare->slow_snap, build_peak->flash_freeze)"
        ),
    )
    beatramp_p.set_defaults(func=cmd_video_beatramp)

    edl = video_sub.add_parser("edl", help="generate, render, and preview EDL cut plans")
    edl_sub = edl.add_subparsers(dest="edl_command", required=True)

    edl_generate = edl_sub.add_parser(
        "generate", help="generate an EDL cut plan from shots and beats"
    )
    edl_generate.add_argument("beats", type=Path, help="beats JSON file (from ms video beats)")
    edl_generate.add_argument("shots", nargs="+", type=Path, help="shot files to cut from")
    edl_generate.add_argument("--music", type=Path, default=None, help="music track to mix")
    edl_generate.add_argument(
        "--duration", type=float, required=True, help="total duration in seconds"
    )
    edl_generate.add_argument(
        "--out", type=Path, default=Path("edl.json"), help="destination EDL JSON (default edl.json)"
    )
    edl_generate.add_argument(
        "--scene-aware",
        action="store_true",
        help="keep every cut inside one scene (splits multi-scene shots into several cuts)",
    )
    edl_generate.set_defaults(func=cmd_video_edl_generate)

    edl_render = edl_sub.add_parser("render", help="render an EDL cut plan to a video file")
    edl_render.add_argument("edl", type=Path, help="EDL JSON file")
    edl_render.add_argument("--out", type=Path, default=None, help="destination video file")
    edl_render.add_argument(
        "--fps", type=float, default=60.0, help="output frame rate (default 60)"
    )
    edl_render.add_argument(
        "--codec", choices=("h264", "prores"), default="h264", help="encode codec (default h264)"
    )
    edl_render.set_defaults(func=cmd_video_edl_render)

    edl_preview = edl_sub.add_parser("preview", help="render a single EDL cut to a video file")
    edl_preview.add_argument("edl", type=Path, help="EDL JSON file")
    edl_preview.add_argument("--cut", required=True, help="cut id to preview")
    edl_preview.add_argument("--out", type=Path, default=None, help="destination video file")
    edl_preview.add_argument(
        "--fps", type=float, default=60.0, help="output frame rate (default 60)"
    )
    edl_preview.set_defaults(func=cmd_video_edl_preview)

    edl_sync_check = edl_sub.add_parser(
        "sync-check", help="check EDL cut points against the beat grid"
    )
    edl_sync_check.add_argument("edl", type=Path, help="EDL JSON file")
    edl_sync_check.add_argument(
        "--beats",
        type=Path,
        default=None,
        help="beats JSON file (default: the EDL's beats_json next to the EDL)",
    )
    edl_sync_check.add_argument(
        "--tolerance",
        type=float,
        default=0.1,
        help="max seconds from an onset to count as on-beat (default 0.1)",
    )
    edl_sync_check.add_argument(
        "--fail-on-unsynced",
        action="store_true",
        help="exit non-zero when the verdict is unsynced",
    )
    edl_sync_check.set_defaults(func=cmd_video_edl_sync_check)

    edl_check_cuts = edl_sub.add_parser(
        "check-cuts", help="flag EDL cuts whose source window spans a scene boundary"
    )
    edl_check_cuts.add_argument("edl", type=Path, help="EDL JSON file")
    edl_check_cuts.add_argument(
        "--threshold",
        type=float,
        default=0.3,
        help="ffmpeg scene score threshold (default 0.3)",
    )
    edl_check_cuts.add_argument(
        "--margin",
        type=float,
        default=0.02,
        help="margin seconds kept clear of each window edge (default 0.02)",
    )
    edl_check_cuts.add_argument(
        "--fail-on-spanning",
        action="store_true",
        help="exit non-zero when the verdict is spanning",
    )
    edl_check_cuts.set_defaults(func=cmd_video_edl_check_cuts)

    impact_p = video_sub.add_parser(
        "impact-frame", help="generate transient impact frames for beat and clash hits"
    )
    impact_p.add_argument("source", help="a video file to sample at the beat")
    impact_p.add_argument(
        "--at", required=True, help="hit time: seconds (2.5) or frame count (f120)"
    )
    impact_p.add_argument(
        "--frames", type=int, default=2, help="number of transient frames to render (1-10)"
    )
    impact_p.add_argument(
        "--mode",
        choices=sorted(IMPACT_MODES),
        default="invert",
        help="impact filter (default invert)",
    )
    impact_p.add_argument("--fps", type=int, default=60, help="output frame rate (default 60)")
    impact_p.add_argument(
        "--out", type=Path, default=None, help="destination .mov (default beside the source)"
    )
    impact_p.set_defaults(func=cmd_video_impact_frame)


def _format_mmss(seconds: float) -> str:
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def cmd_video_impact_frame(args: argparse.Namespace) -> int:
    """Generate transient impact frames at a beat or sword clash hit."""

    source = Path(args.source).expanduser()
    destination = Path(args.out).expanduser() if args.out else None
    try:
        result = generate_impact_frames(
            source,
            destination,
            at=args.at,
            frames=args.frames,
            mode=args.mode,
            fps=args.fps,
        )
    except (ImpactError, IngestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    size_mb = result.size_bytes / 1_000_000
    print(f"impact {result.mode}: {result.frames} frame(s) at {result.at_seconds:.3f}s")
    print(f"  output: {result.destination} ({size_mb:.1f} MB)")
    return EXIT_OK


def cmd_video_edl_generate(args: argparse.Namespace) -> int:
    """Generate an EDL cut plan from shots and beats."""

    try:
        doc = generate_edl(
            args.shots,
            args.beats,
            args.music or Path(""),
            total_duration_s=args.duration,
            out=args.out,
            scene_aware=args.scene_aware,
        )
    except (EdlError, IngestError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"EDL v{doc.version}: {len(doc.cuts)} cut(s), {doc.total_duration_s:g}s total")
    print("| ID | Shot | In | Out | Dur | Effects |")
    print("|----|------|----|-----|-----|---------|")
    for cut in doc.cuts:
        duration_s = (cut.out_ms - cut.in_ms) / 1000
        effects = ", ".join(cut.beatfx or cut.ramp or ["-"])
        print(
            f"| {cut.id} | {Path(cut.shot).name} | "
            f"{_format_mmss(cut.in_ms / 1000)} | {_format_mmss(cut.out_ms / 1000)} | "
            f"{duration_s:g}s | {effects} |"
        )
    print(f"wrote {args.out}")
    return EXIT_OK


def cmd_video_edl_render(args: argparse.Namespace) -> int:
    """Render an EDL cut plan to a video file."""

    try:
        doc = load_edl(args.edl)
        destination = args.out or args.edl.with_suffix(".mp4")
        render_edl(doc, out=destination, fps=args.fps, codec=args.codec)
    except (EdlError, IngestError, BeatFxError, BeatRampError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"rendered {len(doc.cuts)} cuts to {destination}")
    return EXIT_OK


def cmd_video_edl_preview(args: argparse.Namespace) -> int:
    """Render a single EDL cut to a video file."""

    try:
        doc = load_edl(args.edl)
        destination = args.out or Path(f"{args.cut}-preview.mp4")
        preview_edl_cut(doc, args.cut, out=destination, fps=args.fps)
    except (EdlError, IngestError, BeatFxError, BeatRampError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(str(destination))
    return EXIT_OK


def cmd_video_edl_sync_check(args: argparse.Namespace) -> int:
    """Check EDL cut points against the beat grid and print a sync report."""

    try:
        doc = load_edl(args.edl)
        beats_path = args.beats or (args.edl.parent / doc.beats_json)
        if not beats_path.is_file():
            raise EdlError(f"beats file not found: {beats_path}; pass --beats PATH")
        beats = json.loads(beats_path.read_text(encoding="utf-8"))
        report = check_sync(doc, beats, tolerance=args.tolerance)
    except (EdlError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"cuts {report.cuts}")
    print(f"onsets {report.onsets}")
    print(f"tolerance {report.tolerance}s")
    print(f"share on onset {report.share_on_onset}")
    print(f"baseline {report.baseline}")
    print(f"median distance {report.median_distance}s")
    print(f"p90 distance {report.p90_distance}s")
    print(f"verdict: {report.verdict}")

    if args.fail_on_unsynced and report.verdict == "unsynced":
        return EXIT_FAILURE
    return EXIT_OK


def cmd_video_edl_check_cuts(args: argparse.Namespace) -> int:
    """Check EDL cuts for windows spanning sustained scene boundaries."""

    try:
        doc = load_edl(args.edl)
        report = check_cuts(doc, threshold=args.threshold, margin_s=args.margin)
    except (EdlError, SceneError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"cuts {report.cuts}")
    print(f"spanning {report.spanning}")
    print(f"threshold {report.threshold}")
    for finding in report.findings:
        print(
            f"  {finding.cut_id}  timeline {finding.timeline_start_s}s  "
            f"{Path(finding.shot).name}  source {finding.window}s  "
            f"boundary {finding.boundary_s}s"
        )
    print(f"verdict: {report.verdict}")

    if args.fail_on_spanning and report.verdict == "spanning":
        return EXIT_FAILURE
    return EXIT_OK


def cmd_video_beats(args: argparse.Namespace) -> int:
    """Analyze the rhythm of an audio file and write beat JSON."""

    if not args.audio.is_file():
        print(f"error: audio file not found: {args.audio}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        result = audio.analyze_rhythm(args.audio, window=args.window)
        out_path = args.out
        if out_path is None:
            out_path = args.audio.with_suffix(".beats.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        high = sum(1 for section in result["sections"] if section["level"] == "high")
        print(
            f"bpm {result['bpm']}, {len(result['beats'])} beats "
            f"({len(result['downbeats'])} downbeats), {high} high-energy section(s)"
        )
        print(f"wrote {out_path}")
        return EXIT_OK
    except audio.AudioError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_beatfx(args: argparse.Namespace) -> int:
    """Apply beat-mapped micro-effects to a video clip."""

    source = Path(args.source)
    if not source.is_file():
        print(f"error: source file not found: {source}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        with args.beats.open("r", encoding="utf-8") as handle:
            beats = json.loads(handle.read())
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        if args.auto and args.schedule is not None:
            print(
                "error: --auto and --schedule are mutually exclusive; pass exactly one",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        if args.auto:
            info = probe_media(source)
            schedule = auto_schedule(
                beats,
                offset=args.offset,
                duration=info.duration,
                max_per_second=args.max_per_second,
            )
        else:
            if args.schedule is None:
                print(
                    "error: exactly one of --auto or --schedule is required",
                    file=sys.stderr,
                )
                return EXIT_FAILURE
            with args.schedule.open("r", encoding="utf-8") as handle:
                try:
                    schedule = parse_schedule(json.loads(handle.read()))
                except json.JSONDecodeError as error:
                    print(
                        f"error: --schedule {args.schedule} is not valid JSON ({error}); "
                        "pass --auto instead or fix the schedule file",
                        file=sys.stderr,
                    )
                    return EXIT_FAILURE

        result = apply_effects(
            source,
            destination=args.out,
            effects=schedule,
            fps=args.fps,
            resolution=args.resolution,
            codec=args.codec,
        )
    except (BeatFxError, IngestError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"applied {len(result.effects)} effect(s)")
    for effect in result.effects:
        print(f"  {_format_mmss(effect.time)} {effect.kind}")
    size_mb = result.size_bytes / (1024 * 1024)
    print(f"wrote {result.destination} ({size_mb:.2f} MB)")
    return EXIT_OK


def cmd_video_beatramp(args: argparse.Namespace) -> int:
    """Apply beat-synced velocity ramps to a video clip."""

    source = Path(args.source)
    if not source.is_file():
        print(f"error: source file not found: {source}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        beats = json.loads(Path(args.beats).read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        schedule = schedule_ramp_events(
            beats,
            offset=args.offset,
            duration=probe_media(source).duration,
            map=args.map or TRIGGER_MAP,
            max_per_second=args.max_per_second,
        )
        result = apply_ramp_events(
            source,
            schedule,
            destination=args.out,
            fps=args.fps,
            resolution=args.resolution,
            codec=args.codec,
        )
    except (BeatRampError, IngestError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"applied {len(result.events)} ramp event(s)")
    for event in result.events:
        print(f"  {_format_mmss(event.time)} {event.kind} ({event.trigger})")
    size_mb = result.size_bytes / (1024 * 1024)
    print(f"wrote {result.destination} ({size_mb:.2f} MB)")
    return EXIT_OK
