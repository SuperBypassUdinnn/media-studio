"""Video ingest, cutout, parallax, scene detection, speed-ramp, and shot-mining subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio.captions import CaptionsError, CaptionWord, render_captions
from media_studio.ingest import PROFILES, IngestError, ingest, ingest_folder, probe_media
from media_studio.parallax import ParallaxError, compose_parallax
from media_studio.ramp import RAMP_CURVES, RampError, apply_speed_ramp
from media_studio.reframe import ReframeError, reframe_video, safe_zone_for
from media_studio.scene import SceneError
from media_studio.shots import ShotsError, mine_shots, render_candidates, write_candidates
from media_studio.video import VideoError, detect_scene_cuts, scene_ranges
from media_studio.video_cutout import (
    VIDEO_CUTOUT_MODELS,
    VideoCutoutError,
    remove_background_video,
)

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_video_ops_parsers(video_sub: argparse._SubParsersAction) -> None:
    """Register ``ingest``, ``remove-bg``, ``parallax``, ``scenes``, ``speed-ramp``, ``shots``."""

    ingest_parser = video_sub.add_parser(
        "ingest",
        help="transcode footage into a codec DaVinci Resolve can import on Linux",
    )
    ingest_parser.add_argument("source", help="a footage file or a folder of footage to transcode")
    ingest_parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="proxy",
        help="target intermediate (default proxy)",
    )
    ingest_parser.add_argument(
        "--out", default=None, help="destination folder; defaults to beside each source"
    )
    ingest_parser.add_argument(
        "--force", action="store_true", help="transcode even when the source is already safe"
    )
    ingest_parser.add_argument(
        "--no-recursive", action="store_true", help="only read the top level of a folder"
    )
    ingest_parser.set_defaults(func=cmd_video_ingest)

    video_cutout = video_sub.add_parser(
        "remove-bg",
        help="remove the video background with SAM 2 into a transparent ProRes 4444 MOV",
    )
    video_cutout.add_argument("source", help="a video file to cut out")
    video_cutout.add_argument(
        "--out", type=Path, default=None, help="destination .mov file or folder"
    )
    video_cutout.add_argument(
        "--select",
        required=True,
        metavar="X,Y",
        help="point on the object to track, in source pixels (example: 640,360)",
    )
    video_cutout.add_argument(
        "--frame", type=int, default=0, help="anchor frame carrying the prompt (default 0)"
    )
    video_cutout.add_argument(
        "--model",
        choices=sorted(VIDEO_CUTOUT_MODELS),
        default="sam2.1-tiny",
        help="SAM 2 checkpoint (default sam2.1-tiny; larger = better, slower)",
    )
    video_cutout.add_argument(
        "--full-vram",
        action="store_true",
        help="keep all frames on the GPU (faster, needs several GB of VRAM)",
    )
    video_cutout.add_argument(
        "--feather",
        type=int,
        default=0,
        metavar="PX",
        help="soft-edge radius in pixels (default 0 = binary mask)",
    )
    video_cutout.add_argument(
        "--chunk-frames",
        type=int,
        default=None,
        metavar="N",
        help="frames per propagation segment (default: sized to a 2 GB state budget)",
    )
    video_cutout.set_defaults(func=cmd_video_remove_bg)

    parallax_p = video_sub.add_parser(
        "parallax", help="compose 2.5D multi-plane parallax camera shots"
    )
    parallax_p.add_argument("--bg", type=Path, required=True, help="background plate")
    parallax_p.add_argument(
        "--subject", type=Path, required=True, help="RGBA subject cutout (alpha required)"
    )
    parallax_p.add_argument("--fg", type=Path, default=None, help="optional foreground layer")
    parallax_p.add_argument(
        "--duration", type=float, required=True, help="shot duration in seconds"
    )
    parallax_p.add_argument("--fps", type=int, default=60, help="output frame rate (default 60)")
    parallax_p.add_argument(
        "--resolution", default="1920x1080", help="output resolution WxH (default 1920x1080)"
    )
    parallax_p.add_argument("--motion", default="push_in", help="camera motion (default push_in)")
    parallax_p.add_argument(
        "--vignette",
        type=float,
        default=0.3,
        help="vignette strength, 0 disables (default 0.3)",
    )
    parallax_p.add_argument("--out", type=Path, default=None, help="destination .mov file")
    parallax_p.set_defaults(func=cmd_video_parallax)

    scenes = video_sub.add_parser(
        "scenes", help="list hard cuts so a SAM 2 prompt can be placed per scene"
    )
    scenes.add_argument("source", help="a video file to analyse")
    scenes.add_argument(
        "--threshold",
        type=float,
        default=0.3,
        help="scene score between 0 and 1 above which a cut is reported (default 0.3)",
    )
    scenes.set_defaults(func=cmd_video_scenes)

    ramp_p = video_sub.add_parser(
        "speed-ramp", help="apply non-linear velocity curves with optical flow interpolation"
    )
    ramp_p.add_argument("source", help="a video file to re-time")
    ramp_p.add_argument(
        "--curve",
        choices=RAMP_CURVES,
        default="fast_slow_fast",
        help="velocity curve (default fast_slow_fast)",
    )
    ramp_p.add_argument(
        "--slow-window",
        nargs=2,
        type=float,
        required=True,
        metavar=("START", "END"),
        help="slow/freeze window in seconds, for example 1.5 3.0",
    )
    ramp_p.add_argument(
        "--slow-factor",
        type=float,
        default=0.25,
        help="playback multiplier inside the window (default 0.25)",
    )
    ramp_p.add_argument(
        "--fast-factor",
        type=float,
        default=2.5,
        help="playback multiplier outside the window for fast_slow_fast (default 2.5)",
    )
    ramp_p.add_argument(
        "--optical-flow",
        action="store_true",
        default=True,
        help="attempt motion-compensated interpolation first (default on)",
    )
    ramp_p.add_argument(
        "--no-optical-flow",
        action="store_false",
        dest="optical_flow",
        help="use blend frame interpolation instead of optical flow",
    )
    ramp_p.add_argument("--fps", type=int, default=60, help="output frame rate (default 60)")
    ramp_p.add_argument(
        "--out", type=Path, default=None, help="destination file (default beside the source)"
    )
    ramp_p.set_defaults(func=cmd_video_speed_ramp)

    shots = video_sub.add_parser("shots", help="mine a long source into shot candidates")
    shots_sub = shots.add_subparsers(dest="shots_command", required=True)
    shots_mine = shots_sub.add_parser(
        "mine", help="split a source into one-scene shot candidates and rank them by motion"
    )
    shots_mine.add_argument("source", type=Path, help="a long source video to mine")
    shots_mine.add_argument(
        "--out",
        type=Path,
        default=Path("shots-mined"),
        help="output directory (default shots-mined)",
    )
    shots_mine.add_argument(
        "--threshold", type=float, default=0.3, help="scene-change threshold (default 0.3)"
    )
    shots_mine.add_argument(
        "--min-duration",
        type=float,
        default=0.5,
        help="drop scenes shorter than this (default 0.5)",
    )
    shots_mine.add_argument(
        "--max-duration",
        type=float,
        default=2.5,
        help="max candidate window in seconds (default 2.5)",
    )
    shots_mine.add_argument(
        "--samples", type=int, default=4, help="frames sampled per motion score (default 4)"
    )
    shots_mine.add_argument("--scale", default="1920x1080", help="render size (default 1920x1080)")
    shots_mine.add_argument(
        "--top", type=int, default=24, help="render only the top N candidates (default 24)"
    )
    shots_mine.add_argument(
        "--no-render", action="store_true", help="write candidates.json only, render nothing"
    )
    shots_mine.set_defaults(func=cmd_video_shots_mine)

    reframe_p = video_sub.add_parser(
        "reframe", help="subject-tracked auto-reframe a clip to a vertical aspect"
    )
    reframe_p.add_argument("source", help="source video file to reframe")
    reframe_p.add_argument(
        "--aspect",
        choices=("9:16", "16:9", "1:1"),
        default="9:16",
        help="target aspect ratio (default 9:16)",
    )
    reframe_p.add_argument(
        "--mode",
        choices=("auto", "track", "blur-pad"),
        default="auto",
        help="reframe mode: track (crop-pan), blur-pad (blurred background), or auto",
    )
    reframe_p.add_argument(
        "--scene-threshold",
        type=float,
        default=0.3,
        help="scene-change threshold for resetting the focal smoother (default 0.3)",
    )
    reframe_p.add_argument("--out", type=Path, required=True, help="destination .mp4 file")
    reframe_p.set_defaults(func=cmd_video_reframe)

    captions_p = video_sub.add_parser("captions", help="burn word-highlight captions into a video")
    captions_p.add_argument("source", help="source video file to caption")
    captions_p.add_argument(
        "--words",
        default="auto",
        help="words.json / .srt sidecar path, or 'auto' to look beside the source",
    )
    captions_p.add_argument(
        "--style",
        choices=("promo-pop", "hormozi-bold", "clean-saas", "amv-lyric"),
        default="promo-pop",
        help="caption style (default promo-pop)",
    )
    captions_p.add_argument(
        "--aspect",
        choices=("9:16", "16:9", "1:1"),
        default="9:16",
        help="target aspect for safe-zone placement (default 9:16)",
    )
    captions_p.add_argument(
        "--accent-color",
        default="#38BDF8",
        help="accent brand color for the active-word highlight (default #38BDF8)",
    )
    captions_p.add_argument(
        "--engine",
        choices=("ass", "html"),
        default="ass",
        help="render engine: ass burn-in or html kinetic overlay (default ass)",
    )
    captions_p.add_argument("--out", type=Path, required=True, help="destination .mp4 file")
    captions_p.set_defaults(func=cmd_video_captions)


def cmd_video_reframe(args: argparse.Namespace) -> int:
    """Subject-tracked auto-reframe a clip to a vertical aspect."""

    source = Path(args.source).expanduser()
    output = Path(args.out).expanduser()
    try:
        result = reframe_video(
            source,
            aspect=args.aspect,
            mode=args.mode,
            output=output,
            scene_threshold=args.scene_threshold,
        )
    except ReframeError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    zone = safe_zone_for(result.aspect, result.width, result.height)
    print(
        f"reframed {source.name} -> {result.output.name} "
        f"({result.width}x{result.height} @ {result.duration:.2f}s, "
        f"{result.scenes} scene(s), mode={result.mode})"
    )
    print(
        f"  safe zone: top={zone['top']} bottom={zone['bottom']} "
        f"left={zone['left']} right={zone['right']}"
    )
    return EXIT_OK


def cmd_video_captions(args: argparse.Namespace) -> int:
    """Burn word-highlight captions into a video."""

    source = Path(args.source).expanduser()
    output = Path(args.out).expanduser()

    words_arg = args.words
    if words_arg == "auto":
        words: Path | list[CaptionWord] = source
    else:
        words = Path(words_arg).expanduser()

    try:
        result = render_captions(
            source,
            words=words,
            style=args.style,
            aspect=args.aspect,
            accent_color=args.accent_color,
            output=output,
            engine=args.engine,
        )
    except CaptionsError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"captioned {source.name} -> {result.name} (style={args.style}, engine={args.engine})")
    return EXIT_OK


def cmd_video_ingest(args: argparse.Namespace) -> int:
    """Transcode footage into a codec DaVinci Resolve can import on Linux."""

    source = Path(args.source).expanduser()
    destination_dir = Path(args.out).expanduser() if args.out else None

    try:
        if source.is_dir():
            results = ingest_folder(
                source,
                destination_dir,
                args.profile,
                force=args.force,
                recursive=not args.no_recursive,
            )
        elif source.is_file():
            destination = None
            if destination_dir is not None:
                destination = destination_dir / f"{source.stem}.{args.profile}.mov"
            results = [ingest(source, destination, args.profile, force=args.force)]
        else:
            raise IngestError(f"no such file or folder: {source}")
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not results:
        print(f"no footage found in {source}")
        return EXIT_OK

    for result in results:
        if result.skipped:
            print(f"skipped  {result.source.name}: already Resolve-safe")
            continue
        size_mb = result.size_bytes / 1_000_000
        print(
            f"ingested {result.source.name} -> {result.destination.name} "
            f"({args.profile}, {size_mb:.1f} MB)"
        )

    transcoded = sum(1 for result in results if not result.skipped)
    print(f"{transcoded} transcoded, {len(results) - transcoded} already Resolve-safe")
    return EXIT_OK


def cmd_video_remove_bg(args: argparse.Namespace) -> int:
    """Remove a video background with SAM 2 into a transparent ProRes MOV."""

    try:
        select_x, _, select_y = args.select.partition(",")
        select_point = (int(select_x), int(select_y))
    except ValueError:
        print(f"error: --select expects X,Y pixels, got {args.select!r}", file=sys.stderr)
        return EXIT_FAILURE

    source = Path(args.source).expanduser()
    destination = Path(args.out).expanduser() if args.out else None
    if destination is not None and destination.suffix != ".mov":
        destination.mkdir(parents=True, exist_ok=True)

    try:
        result = remove_background_video(
            source,
            destination,
            select_point=select_point,
            anchor_frame=args.frame,
            model=args.model,
            full_vram=args.full_vram,
            feather=args.feather,
            chunk_frames=args.chunk_frames,
        )
    except VideoCutoutError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    size_mb = result.size_bytes / 1_000_000
    print(f"removed video background with {result.model} ({result.device})")
    print(f"  tracked:  {result.frames} frames in {result.segments} segment(s)")
    print(f"  point:    {result.select_point[0]},{result.select_point[1]}")
    print(f"  feather:  {result.feather} px")
    print(f"  output:   {result.destination} ({size_mb:.1f} MB)")
    return EXIT_OK


def cmd_video_parallax(args: argparse.Namespace) -> int:
    """Compose a 2.5D multi-plane parallax camera shot."""

    try:
        result = compose_parallax(
            background=args.bg,
            subject=args.subject,
            foreground=args.fg,
            destination=args.out,
            duration=args.duration,
            fps=args.fps,
            resolution=args.resolution,
            motion=args.motion,
            vignette=args.vignette,
        )
    except ParallaxError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"composed {result.motion} parallax shot -> {result.destination}")
    print(f"  resolution: {result.resolution} @ {result.fps} fps")
    print(f"  duration:   {result.duration} s")
    print(f"  layers:     {result.layers_composited}")
    print(f"  size:       {result.size_bytes} bytes")
    return EXIT_OK


def cmd_video_scenes(args: argparse.Namespace) -> int:
    """List a clip's hard cuts and the frame range of every scene."""

    source = Path(args.source).expanduser()
    scene_fn = resolve_monkeypatchable("detect_scene_cuts", detect_scene_cuts)
    try:
        cuts = scene_fn(source, threshold=args.threshold)
    except VideoError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not cuts:
        print(f"{source.name}: no hard cuts at threshold {args.threshold} (one scene)")
        return EXIT_OK

    try:
        info = probe_media(source)
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    total_frames = round(info.duration * info.fps)

    ranges = scene_ranges(cuts, total_frames)
    print(f"{source.name}: {len(cuts)} cut(s) at threshold {args.threshold}")
    for index, (start, end, start_s, end_s) in enumerate(ranges, start=1):
        end_text = f"{end_s:.3f}s" if end_s >= 0 else "end"
        print(f"  scene {index}: frames {start}-{end}  ({start_s:.3f}s - {end_text})")
    return EXIT_OK


def cmd_video_speed_ramp(args: argparse.Namespace) -> int:
    """Apply a non-linear velocity curve to a video file."""

    source = Path(args.source).expanduser()
    destination = Path(args.out).expanduser() if args.out else None
    slow_window = (float(args.slow_window[0]), float(args.slow_window[1]))
    ramp_fn = resolve_monkeypatchable("apply_speed_ramp", apply_speed_ramp)

    try:
        result = ramp_fn(
            source,
            destination,
            curve=args.curve,
            slow_window=slow_window,
            slow_factor=args.slow_factor,
            fast_factor=args.fast_factor,
            optical_flow=args.optical_flow,
            fps=args.fps,
        )
    except (IngestError, RampError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    size_mb = result.size_bytes / 1_000_000
    fallback = " (blend fallback)" if result.fallback_used else ""
    print(
        f"speed ramp {result.source.name} -> {result.destination.name} "
        f"({result.curve}, {result.fps} fps{fallback})"
    )
    print(f"  duration: {result.duration:.2f} s, size: {size_mb:.1f} MB, resolution: 1920x1080")
    return EXIT_OK


def cmd_video_shots_mine(args: argparse.Namespace) -> int:
    """Mine a long source into ranked one-scene shot candidates."""

    source = Path(args.source).expanduser()
    out_dir = Path(args.out).expanduser()

    if args.top < 1:
        print("error: --top must be >= 1", file=sys.stderr)
        return EXIT_FAILURE
    if args.min_duration <= 0 or args.max_duration < args.min_duration:
        print(
            "error: --min-duration must be > 0 and --max-duration must be >= --min-duration",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    try:
        candidates = mine_shots(
            source,
            threshold=args.threshold,
            min_duration_s=args.min_duration,
            max_duration_s=args.max_duration,
            samples=args.samples,
        )
    except (ShotsError, SceneError, IngestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        write_candidates(candidates, out_dir / "candidates.json")
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    rendered_paths: list[Path] = []
    if not args.no_render:
        try:
            rendered_paths = render_candidates(candidates[: args.top], out_dir, scale=args.scale)
        except ShotsError as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_FAILURE

    source_name = Path(candidates[0].source).name
    print(f"source {source_name}  candidates {len(candidates)}")
    print("| # | in | out | dur | motion | path |")
    print("|---|----|-----|-----|--------|------|")
    for index, candidate in enumerate(candidates):
        path_text = rendered_paths[index].name if index < len(rendered_paths) else source_name
        print(
            f"| {candidate.rank} | {candidate.in_s:.3f} | {candidate.out_s:.3f} | "
            f"{candidate.duration_s:.3f} | {candidate.motion} | {path_text} |"
        )

    print(f"wrote {out_dir / 'candidates.json'}")
    if not args.no_render:
        print(f"rendered {len(rendered_paths)} clip(s) to {out_dir}")
    return EXIT_OK
