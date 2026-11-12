"""Core video rendering, QC, handoff, compositing, build, and review (`ms video`) commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from media_studio import media
from media_studio.fcproj import CompilerError, compile_manifest_to_fcproj, plan_manifest_compile
from media_studio.filmcraft import FilmCraftError
from media_studio.manifest import ManifestError, Project, load_project
from media_studio.media import MediaError, extract_stills, generate_proxy, probe
from media_studio.natron import NatronError, composite_project
from media_studio.transitions import TRANSITION_KINDS, TransitionError, apply_transition
from media_studio.video import VideoError, qc_report, render_filmcraft
from media_studio.video import handoff as video_handoff
from media_studio.video import render as render_video

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable
from .video_edl import register_video_edl_parsers
from .video_fcproj import register_video_fcproj_parsers
from .video_lut import register_video_lut_parsers
from .video_ops import register_video_ops_parsers


def register_video_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``video`` command group and all subcommands."""

    video = subparsers.add_parser("video", help="render and verify video deliverables")
    video_sub = video.add_subparsers(dest="video_command", required=True)

    render = video_sub.add_parser("render", help="render one video deliverable end to end")
    render.add_argument("project", help="project path or slug")
    render.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    render.add_argument("--preview", action="store_true", help="small and fast, for a look")
    render.add_argument(
        "--engine",
        choices=("filmcraft", "mlt"),
        default="filmcraft",
        help="timeline engine: filmcraft (default) or the legacy MLT path",
    )
    render.add_argument(
        "--gpu",
        choices=("auto", "off"),
        default="auto",
        help="FilmCraft GPU rendering; auto falls back to CPU and records it in QC",
    )
    render.add_argument(
        "--burn-captions",
        action="store_true",
        help="burn the transcript captions into the picture at export (FilmCraft engine only)",
    )
    render.add_argument(
        "--keep-intermediate",
        action="store_true",
        help="keep the lossless render under /tmp/opencode for inspection",
    )
    render.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing .fcproj even when it carries manual GUI edits",
    )
    render.set_defaults(func=cmd_video_render)

    handoff = video_sub.add_parser(
        "handoff", help="write a Kdenlive-compatible project beside the render input"
    )
    handoff.add_argument("project", help="project path or slug")
    handoff.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    handoff.set_defaults(func=cmd_video_handoff)

    qc = video_sub.add_parser("qc", help="measure an existing file and write a report")
    qc.add_argument("project", help="project path or slug")
    qc.add_argument("--file", type=Path, required=True, help="file to measure")
    qc.set_defaults(func=cmd_video_qc)

    composite = video_sub.add_parser(
        "composite", help="composite a rendered deliverable through Natron headless"
    )
    composite.add_argument("project", help="project path or slug")
    composite.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    composite.add_argument("--lut", type=Path, default=None, help="OCIO LUT file for the grade")
    composite.add_argument("--gain", type=float, default=1.0, help="grade gain (default 1.0)")
    composite.add_argument("--gamma", type=float, default=1.0, help="grade gamma (default 1.0)")
    composite.add_argument(
        "--saturation", type=float, default=1.0, help="grade saturation (default 1.0)"
    )
    composite.add_argument(
        "--glow-size", type=float, default=0.0, help="glow size, 0 disables (default 0)"
    )
    composite.add_argument("--glow-mix", type=float, default=0.5, help="glow mix (default 0.5)")
    composite.add_argument(
        "--vignette", type=float, default=0.0, help="vignette strength, 0 disables (default 0)"
    )
    composite.set_defaults(func=cmd_video_composite)

    build_fc = video_sub.add_parser(
        "build", help="compile project.yaml manifest into a FilmCraft .fcproj timeline"
    )
    build_fc.add_argument("project", help="project path or slug")
    build_fc.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    build_fc.add_argument("--out", type=Path, default=None, help="destination .fcproj path")
    build_fc.add_argument(
        "--dry-run",
        action="store_true",
        help="print the compilation plan (imports, geometry, clip counts), engine not run",
    )
    build_fc.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing .fcproj (D46: the old timeline is snapshotted first)",
    )
    build_fc.set_defaults(func=cmd_video_build)

    proxy_p = video_sub.add_parser(
        "proxy", help="render a 540p review proxy of a deliverable or file"
    )
    proxy_p.add_argument("project", help="project path or slug")
    proxy_p.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    proxy_p.add_argument(
        "--file", type=Path, default=None, help="source media (default: deliverable's last render)"
    )
    proxy_p.add_argument("--height", type=int, default=540, help="proxy height in pixels")
    proxy_p.add_argument(
        "--out", type=Path, default=None, help="destination path (default out/<stem>-proxy.mp4)"
    )
    proxy_p.set_defaults(func=cmd_video_proxy)

    stills_p = video_sub.add_parser(
        "stills", help="extract review still frames from a deliverable or file"
    )
    stills_p.add_argument("project", help="project path or slug")
    stills_p.add_argument("--deliverable", default=None, help="deliverable id from project.yaml")
    stills_p.add_argument(
        "--file", type=Path, default=None, help="source media (default: deliverable's last render)"
    )
    stills_p.add_argument(
        "--at",
        default=None,
        help="comma-separated percentages of duration, e.g. 15,50,85 (default 15,50,85)",
    )
    stills_p.add_argument(
        "--out", type=Path, default=None, help="destination directory (default out/<stem>-stills)"
    )
    stills_p.set_defaults(func=cmd_video_stills)

    transition_p = video_sub.add_parser(
        "transition", help="render an FFmpeg transition between two clips"
    )
    transition_p.add_argument("clip_a", help="first clip (a file path)")
    transition_p.add_argument("clip_b", help="second clip (a file path)")
    transition_p.add_argument(
        "--kind",
        choices=sorted(TRANSITION_KINDS),
        required=True,
        help="transition kind (default whip_pan_left)",
    )
    transition_p.add_argument(
        "--duration-ms",
        type=int,
        default=250,
        help="transition duration in milliseconds, frame-quantized to fps (default 250)",
    )
    transition_p.add_argument(
        "--width", type=int, default=1080, help="output width in pixels (default 1080)"
    )
    transition_p.add_argument(
        "--height", type=int, default=1920, help="output height in pixels (default 1920)"
    )
    transition_p.add_argument("--fps", type=int, default=30, help="output frame rate (default 30)")
    transition_p.add_argument("--out", type=Path, required=True, help="destination .mp4 file")
    transition_p.set_defaults(func=cmd_video_transition)

    register_video_ops_parsers(video_sub)
    register_video_fcproj_parsers(video_sub)
    register_video_edl_parsers(video_sub)
    register_video_lut_parsers(video_sub)


def cmd_video_render(args: argparse.Namespace) -> int:
    """Render a deliverable and report the measured QC checks."""

    engine = getattr(args, "engine", "filmcraft")
    gpu = getattr(args, "gpu", "auto")
    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        if engine == "mlt":
            render_fn = resolve_monkeypatchable("render_video", render_video)
            result = render_fn(
                project,
                args.deliverable,
                preview=args.preview,
                keep_intermediate=args.keep_intermediate,
            )
            print(f"rendered {result.deliverable.relative_to(project.root)}")
            print(f"  mlt:      {result.mlt_path.relative_to(project.root)}")
            print(f"  report:   {result.report_path.relative_to(project.root)}")
            print(f"  sheet:    {result.contact_sheet.relative_to(project.root)}")
            return _report_video_qc(result.report)

        render_fc_fn = resolve_monkeypatchable("render_filmcraft", render_filmcraft)
        result = render_fc_fn(
            project,
            args.deliverable,
            gpu=gpu,
            preview=args.preview,
            burn_captions=getattr(args, "burn_captions", False),
            force=bool(getattr(args, "force", False)),
        )
    except (ManifestError, VideoError, MediaError, FilmCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"rendered {result.deliverable.relative_to(project.root)}")
    print(f"  project:  {result.fcproj_path.relative_to(project.root)}")
    print(f"  report:   {result.report_path.relative_to(project.root)}")
    print(f"  sheet:    {result.contact_sheet.relative_to(project.root)}")
    if result.proxy_path is not None:
        print(f"  proxy:    {result.proxy_path.relative_to(project.root)}")
    if result.stills:
        print(f"  stills:   {len(result.stills)} frame(s)")
    return _report_video_qc(result.report)


def cmd_video_handoff(args: argparse.Namespace) -> int:
    """Write a Kdenlive-compatible project and say what it targets and what it references."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    handoff_fn = resolve_monkeypatchable("video_handoff", video_handoff)
    try:
        project = load_fn(args.project)
        result = handoff_fn(project, args.deliverable)
    except (ManifestError, VideoError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    target = result.kdenlive_version or "unknown (kdenlive is not on PATH)"
    print(f"handoff {result.path.relative_to(project.root)}")
    print(f"  kdenlive:  {target}")
    print(f"  envelope:  docproperties.version, documentid {result.document_id}")
    print(
        f"  edit:      {result.timeline.duration:.1f}s, "
        f"{len(result.timeline.clips)} clip(s), {len(result.timeline.titles)} title(s)"
    )
    print(f"  render:    out/{result.path.stem}.mlt is untouched; the envelope lives only here")
    for note in result.notes:
        print(f"  note:      {note}")
    for problem in result.problems:
        print(f"  problem:   {problem}")
    print("verdict: " + ("pass" if not result.problems else "FAIL"))
    return EXIT_OK if not result.problems else EXIT_FAILURE


def cmd_video_qc(args: argparse.Namespace) -> int:
    """Measure a file against the project's expectations."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    probe_fn = resolve_monkeypatchable("probe", media.probe)
    qc_fn = resolve_monkeypatchable("qc_report", qc_report)
    try:
        project = load_fn(args.project)
        source = Path(args.file).expanduser()
        if not source.is_file():
            raise VideoError(f"no such file: {source}")
        deliverable = next(
            (item for item in project.deliverables if item.kind == "video"),
            None,
        )
        expected = deliverable.duration if deliverable and deliverable.duration else None
        if expected is None:
            expected = probe_fn(source).duration
        report = qc_fn(
            source,
            expected_duration=expected,
            target_lufs=project.loudness_lufs,
        )
    except (ManifestError, VideoError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    report_path = project.root / "out" / "reports" / f"{source.stem}-qc.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"measured {source.name}")
    print(f"  report:   {report_path.relative_to(project.root)}")
    return _report_video_qc(report)


def _report_video_qc(report: dict[str, object]) -> int:
    """Print every check and return the exit status that matches them."""

    loudness = report.get("loudness")
    if isinstance(loudness, dict):
        print(
            f"  loudness: {loudness['integrated_lufs']} LUFS "
            f"(target {loudness['target_lufs']}, off by {loudness['distance_lu']} LU), "
            f"true peak {loudness['true_peak_dbtp']} dBTP"
        )
    print(
        f"  media:    {report['width']}x{report['height']} @ {report['fps']} fps, "
        f"{report['duration_seconds']}s, {report['video_codec']}/{report['audio_codec']}"
    )
    checks = report.get("checks")
    if isinstance(checks, dict):
        for name, passed in checks.items():
            print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    ok = bool(report.get("ok"))
    print("verdict: " + ("pass" if ok else "FAIL"))
    return EXIT_OK if ok else EXIT_FAILURE


def cmd_video_composite(args: argparse.Namespace) -> int:
    """Composite a rendered deliverable through Natron headless."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        deliverable = args.deliverable
        if deliverable is None:
            video_deliverables = [item for item in project.deliverables if item.kind == "video"]
            if len(video_deliverables) != 1:
                raise VideoError("project has multiple video deliverables; pass --deliverable <id>")
            deliverable = video_deliverables[0].id

        compositing: dict = {}
        if any(
            v is not None
            for v in (
                args.lut,
                args.gain,
                args.gamma,
                args.saturation,
                args.glow_size,
                args.glow_mix,
                args.vignette,
            )
        ):
            compositing = {
                "grade": {
                    "lut": str(args.lut) if args.lut else None,
                    "gain": args.gain,
                    "gamma": args.gamma,
                    "saturation": args.saturation,
                },
                "effects": [],
            }
            if args.glow_size > 0:
                compositing["effects"].append(
                    {"glow": {"size": args.glow_size, "mix": args.glow_mix}}
                )
            if args.vignette > 0:
                compositing["effects"].append({"vignette": {"strength": args.vignette}})

        result = composite_project(deliverable, compositing, project_root=project.root)
    except (ManifestError, NatronError, VideoError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"composited {deliverable}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  ntp:     {result.ntp_path.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_video_build(args: argparse.Namespace) -> int:
    """Compile project.yaml into a FilmCraft .fcproj project."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(args.project)
        if args.dry_run:
            plan = plan_manifest_compile(
                project,
                output_fcproj=args.out,
                deliverable_id=args.deliverable,
            )
            print(f"plan {plan.fcproj_path.name} (dry run, engine not invoked)")
            print(f"  geometry:  {plan.width}x{plan.height} @ {plan.fps:g} fps")
            print(f"  duration:  {plan.duration_seconds:.2f} s")
            print(
                f"  clips:     {plan.shots} shots, {plan.titles} titles, "
                f"{plan.motion_overlays} motion overlays"
            )
            print(
                f"  audio:     {plan.voice_cues} voice, {plan.music_cues} music"
                f"{', duck baked (D48)' if plan.bake_duck else ''}"
            )
            print(f"  imports:   {len(plan.imports)}")
            for asset_id, path in plan.imports.items():
                print(f"    {asset_id}: {path}")
            return EXIT_OK
        res = compile_manifest_to_fcproj(
            project,
            output_fcproj=args.out,
            deliverable_id=args.deliverable,
            force=args.force,
        )
        print(f"compiled {res.fcproj_path.name}")
        print(f"  project:   {project.slug}")
        print(f"  sequence:  {res.sequence_id}")
        print(f"  assets:    {len(res.imported_items)} imported")
        print(
            f"  clips:     {res.shots_placed} shots, "
            f"{res.audio_cues_placed} audio cues, {res.titles_placed} titles"
        )
        return EXIT_OK
    except (ManifestError, CompilerError, FilmCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def _resolve_review_source(
    project: Project, args_file: Path | None, deliverable_id: str | None
) -> Path:
    """Resolve the media a review command operates on: --file, or the deliverable's last render."""

    if args_file is not None:
        if not args_file.is_file():
            raise FileNotFoundError(f"file not found: {args_file}")
        return args_file

    if deliverable_id is None:
        for deliverable in project.deliverables:
            if deliverable.kind == "video":
                deliverable_id = deliverable.id
                break

    candidates: list[Path] = []
    if deliverable_id:
        candidates.append(project.root / "out" / f"{deliverable_id}.mp4")
        candidates.append(project.root / "out" / "video" / f"{deliverable_id}.mp4")
    for pattern in ("*.mp4", "video/*.mp4"):
        existing = sorted(
            (p for p in (project.root / "out").glob(pattern) if "proxy" not in p.stem),
            key=lambda p: p.stat().st_mtime,
        )
        candidates.extend(reversed(existing))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no rendered deliverable found under {project.root / 'out'}; pass --file or render first"
    )


def cmd_video_proxy(args: argparse.Namespace) -> int:
    """Render a 540p review proxy outside the render pipeline."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    proxy_fn = resolve_monkeypatchable("generate_proxy", generate_proxy)
    try:
        project = load_fn(args.project)
        source = _resolve_review_source(project, args.file, args.deliverable)
        destination = args.out or (project.root / "out" / f"{source.stem}-proxy.mp4")
        proxy_fn(source, destination, height=args.height)
        print(f"proxy {source.name} -> {destination.relative_to(project.root)}")
        print(f"  height:  {args.height}px")
        return EXIT_OK
    except (ManifestError, FileNotFoundError, RuntimeError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_stills(args: argparse.Namespace) -> int:
    """Extract review still frames outside the render pipeline."""

    load_fn = resolve_monkeypatchable("load_project", load_project)
    probe_fn = resolve_monkeypatchable("probe", probe)
    stills_fn = resolve_monkeypatchable("extract_stills", extract_stills)
    try:
        project = load_fn(args.project)
        source = _resolve_review_source(project, args.file, args.deliverable)
        destination = args.out or (project.root / "out" / f"{source.stem}-stills")
        timestamps: list[float] | None = None
        if args.at:
            percentages = [float(v) for v in str(args.at).split(",")]
            info = probe_fn(source)
            duration = info.duration or 0.0
            if duration <= 0.0:
                raise RuntimeError(f"cannot determine duration of {source}")
            timestamps = [round(duration * (p / 100.0), 2) for p in percentages]
        paths = stills_fn(source, destination, timestamps=timestamps)
        print(f"stills {source.name} -> {destination.relative_to(project.root)}")
        for path in paths:
            print(f"  {path.name}")
        return EXIT_OK
    except (ManifestError, FileNotFoundError, RuntimeError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_transition(args: argparse.Namespace) -> int:
    """Render an FFmpeg transition between two clips (`ms video transition`)."""

    apply_fn = resolve_monkeypatchable("apply_transition", apply_transition)
    try:
        result = apply_fn(
            Path(args.clip_a),
            Path(args.clip_b),
            kind=args.kind,
            output=args.out,
            duration_ms=args.duration_ms,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
    except TransitionError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"transition {result.kind} -> {result.output.name}")
    print(f"  {result.width}x{result.height} @ {result.fps} fps, {result.duration:.2f}s")
    print(f"  duration_ms: {result.duration_ms}, sfx tag: {result.sfx_tag}")
    return EXIT_OK
