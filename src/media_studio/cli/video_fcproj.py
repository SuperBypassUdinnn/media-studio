"""FilmCraft timeline (`.fcproj`) subcommands under `ms video`."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from media_studio import ai
from media_studio.fcproj import diff_timelines, snapshot_timeline
from media_studio.filmcraft import FilmCraftError, FilmCraftRunner
from media_studio.manifest import ManifestError, Project, load_project

from .common import EXIT_FAILURE, EXIT_OK, resolve_monkeypatchable


def register_video_fcproj_parsers(video_sub: argparse._SubParsersAction) -> None:
    """Register FilmCraft ``.fcproj`` subcommands under ``video``."""

    cmd_parser = video_sub.add_parser(
        "cmd", help="execute a FilmCraft engine command with typed parameters"
    )
    cmd_parser.add_argument("project", help="project path, slug, or direct .fcproj file")
    cmd_parser.add_argument("command_id", help="command identifier (e.g. timeline.place)")
    cmd_parser.add_argument("--params", default="{}", help="JSON object of command parameters")
    cmd_parser.set_defaults(func=cmd_video_cmd)

    place_parser = video_sub.add_parser(
        "place", help="place an imported asset onto a sequence track"
    )
    place_parser.add_argument("project", help="project path, slug, or direct .fcproj file")
    place_parser.add_argument("--item", type=int, required=True, help="media pool item id")
    place_parser.add_argument("--track", default="V1", help="target track (default V1)")
    place_parser.add_argument("--seconds", type=float, default=0.0, help="start time in seconds")
    place_parser.add_argument(
        "--duration", type=float, default=None, help="clip duration in seconds"
    )
    place_parser.add_argument(
        "--source-in", type=float, default=0.0, help="source in-point in seconds"
    )
    place_parser.set_defaults(func=cmd_video_place)

    trim_parser = video_sub.add_parser("trim", help="trim a clip on the timeline")
    trim_parser.add_argument("project", help="project path, slug, or direct .fcproj file")
    trim_parser.add_argument("--clip", type=int, required=True, help="clip id")
    trim_parser.add_argument("--seconds", type=float, required=True, help="delta or target seconds")
    trim_parser.add_argument(
        "--ripple",
        action="store_true",
        help="ripple delete / shift subsequent clips",
    )
    trim_parser.set_defaults(func=cmd_video_trim)

    inspect_p = video_sub.add_parser(
        "inspect", help="inspect a FilmCraft project or active sequence"
    )
    inspect_p.add_argument("project", help="project path, slug, or direct .fcproj file")
    inspect_p.add_argument(
        "--target",
        choices=["both", "project", "sequence"],
        default="both",
        help="inspection scope",
    )
    inspect_p.add_argument("--json", action="store_true", help="output raw JSON")
    inspect_p.set_defaults(func=cmd_video_inspect)

    open_parser = video_sub.add_parser(
        "open", help="open project in FilmCraft GUI with environment sanitized"
    )
    open_parser.add_argument("project", help="project path, slug, or direct .fcproj file")
    open_parser.set_defaults(func=cmd_video_open)

    snap_parser = video_sub.add_parser(
        "snapshot", help="create a timestamped snapshot of the working .fcproj timeline"
    )
    snap_parser.add_argument("project", help="project path, slug, or direct .fcproj file")
    snap_parser.set_defaults(func=cmd_video_snapshot)

    diff_parser = video_sub.add_parser(
        "diff", help="diff current timeline against a previous snapshot"
    )
    diff_parser.add_argument("project", help="project path, slug, or direct .fcproj file")
    diff_parser.add_argument(
        "--against", type=Path, default=None, help="specific snapshot to compare against"
    )
    diff_parser.set_defaults(func=cmd_video_diff)

    transcript_p = video_sub.add_parser(
        "transcript", help="import word-level transcript into .fcproj"
    )
    transcript_p.add_argument("project", help="project path, slug, or direct .fcproj file")
    transcript_p.add_argument("--file", type=Path, required=True, help="path to transcript JSON")
    transcript_p.set_defaults(func=cmd_video_transcript)

    captions_p = video_sub.add_parser(
        "captions", help="generate captions track from imported transcript"
    )
    captions_p.add_argument("project", help="project path, slug, or direct .fcproj file")
    captions_p.set_defaults(func=cmd_video_captions)

    fillers_p = video_sub.add_parser(
        "remove-fillers", help="cut filler words from transcript and ripple timeline"
    )
    fillers_p.add_argument("project", help="project path, slug, or direct .fcproj file")
    fillers_p.add_argument("--fillers", default="um,uh,like", help="comma-separated words to cut")
    fillers_p.set_defaults(func=cmd_video_remove_fillers)

    cut_p = video_sub.add_parser(
        "cut-by-transcript",
        help="import a transcript and apply AI cuts in one snapshot-guarded pass",
    )
    cut_p.add_argument("project", help="project path, slug, or direct .fcproj file")
    cut_p.add_argument("--file", type=Path, required=True, help="word-level transcript JSON")
    cut_p.add_argument(
        "--remove-fillers", action="store_true", help="cut filler words with ripple after import"
    )
    cut_p.add_argument(
        "--fillers", default=None, help="comma-separated filler words (default um,uh,like)"
    )
    cut_p.add_argument(
        "--remove-pauses", action="store_true", help="compress silent gaps longer than --min-pause"
    )
    cut_p.add_argument(
        "--min-pause", type=float, default=0.6, help="minimum pause length in seconds (default 0.6)"
    )
    cut_p.set_defaults(func=cmd_video_cut_by_transcript)

    beat_p = video_sub.add_parser("beat-sync", help="detect beats and inject timeline markers")
    beat_p.add_argument("project", help="project path, slug, or direct .fcproj file")
    beat_p.add_argument("--audio", type=Path, required=True, help="audio file to analyze")
    beat_p.add_argument("--name", default="Beat", help="marker label name")
    beat_p.set_defaults(func=cmd_video_beat_sync)


def _resolve_fcproj_target(target: str) -> tuple[Path, Project | None]:
    """Resolve an argument to an fcproj file path and optional Project instance."""

    path = Path(target).expanduser()
    if path.suffix == ".fcproj":
        return path, None
    load_fn = resolve_monkeypatchable("load_project", load_project)
    try:
        project = load_fn(target)
        candidates = [
            project.root / f"{project.slug}.fcproj",
            project.root / "out" / f"{project.slug}.fcproj",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate, project
        found = list(project.root.glob("*.fcproj"))
        if found:
            newest = sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[0]
            return newest, project
        return candidates[0], project
    except (ManifestError, OSError):
        return path, None


def cmd_video_cmd(args: argparse.Namespace) -> int:
    """Execute a raw FilmCraft engine command."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        params = json.loads(args.params) if args.params else {}
    except json.JSONDecodeError as err:
        print(f"error: invalid JSON in --params: {err}", file=sys.stderr)
        return EXIT_FAILURE
    runner_cls = resolve_monkeypatchable("FilmCraftRunner", FilmCraftRunner)
    try:
        runner = runner_cls()
        result = runner.exec(args.command_id, params=params, project_path=fcproj_path, save=True)
        res_data = result.result if result.result is not None else {"status": "ok"}
        print(json.dumps(res_data, indent=2))
        return EXIT_OK
    except FilmCraftError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_place(args: argparse.Namespace) -> int:
    """Place an imported asset onto a sequence track."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    params = {
        "item": args.item,
        "track": args.track,
        "seconds": args.seconds,
        "sourceIn": {"seconds": args.source_in},
    }
    if args.duration is not None:
        params["duration"] = {"seconds": args.duration}
    runner_cls = resolve_monkeypatchable("FilmCraftRunner", FilmCraftRunner)
    try:
        runner = runner_cls()
        res = runner.exec("timeline.place", params=params, project_path=fcproj_path, save=True)
        print(f"placed item {args.item} on {args.track} at {args.seconds}s: {res.result}")
        return EXIT_OK
    except FilmCraftError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_trim(args: argparse.Namespace) -> int:
    """Trim a clip on the timeline."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    mode = "ripple" if args.ripple else "regular"
    params = {
        "clip": args.clip,
        "delta": {"seconds": args.seconds},
        "mode": mode,
    }
    runner_cls = resolve_monkeypatchable("FilmCraftRunner", FilmCraftRunner)
    try:
        runner = runner_cls()
        res = runner.exec("timeline.trim", params=params, project_path=fcproj_path, save=True)
        print(f"trimmed clip {args.clip} ({mode}) by {args.seconds}s: {res.result}")
        return EXIT_OK
    except FilmCraftError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_inspect(args: argparse.Namespace) -> int:
    """Inspect a FilmCraft project or sequence."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    runner_cls = resolve_monkeypatchable("FilmCraftRunner", FilmCraftRunner)
    try:
        runner = runner_cls()
        data = runner.inspect(fcproj_path, target=args.target)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            proj = data.get("project", {})
            seq = data.get("sequence", {})
            pool_items = proj.get("items", []) or proj.get("root", {}).get("children", [])
            print(f"project:   {proj.get('name', fcproj_path.stem)}")
            print(f"items:     {len(pool_items)} in media pool")
            print(f"sequence:  {seq.get('name', 'none')}")
            video_tracks = seq.get("video", [])
            audio_tracks = seq.get("audio", [])
            all_clips = list(seq.get("clips", []))
            for track in video_tracks + audio_tracks:
                all_clips.extend(track.get("items", []))
            print(f"clips:     {len(all_clips)} placed")
            tracks = seq.get("tracks", []) or (video_tracks + audio_tracks)
            if tracks:
                track_names = ", ".join(track.get("name", "?") for track in tracks[:6])
                print(f"tracks:    {len(tracks)} ({track_names})")
        return EXIT_OK
    except FilmCraftError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_open(args: argparse.Namespace) -> int:
    """Open project in FilmCraft GUI with clean environment."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    env = dict(os.environ)
    env.pop("MANGOHUD", None)
    try:
        subprocess.Popen(["filmcraft", str(fcproj_path)], env=env)
        print(f"opened {fcproj_path.name} in FilmCraft GUI")
        return EXIT_OK
    except Exception as error:
        print(f"error opening FilmCraft: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_snapshot(args: argparse.Namespace) -> int:
    """Create a timestamped snapshot of the current .fcproj."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    snap = snapshot_timeline(fcproj_path)
    print(f"snapshot: {snap.name}")
    return EXIT_OK


def cmd_video_diff(args: argparse.Namespace) -> int:
    """Diff current .fcproj against a previous snapshot."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    diff = diff_timelines(fcproj_path, args.against)
    print(f"diff against {diff.get('snapshot', 'none')}:")
    print(f"  duration delta: {diff.get('duration_delta', 0.0):+.2f}s")
    print(f"  clips added:    {len(diff.get('added', []))}")
    print(f"  clips removed:  {len(diff.get('removed', []))}")
    print(f"  clips modified: {len(diff.get('modified', []))}")
    return EXIT_OK


def cmd_video_transcript(args: argparse.Namespace) -> int:
    """Import word-level transcript JSON into project timeline."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        data = json.loads(args.file.read_text())
        res = ai.import_transcript(fcproj_path, data)
        print(f"imported transcript: {res.stdout or res.result}")
        return EXIT_OK
    except Exception as error:
        print(f"error importing transcript: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_captions(args: argparse.Namespace) -> int:
    """Generate captions track on project timeline from transcript."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        res = ai.generate_captions(fcproj_path)
        print(f"generated captions: {res.stdout or res.result}")
        return EXIT_OK
    except Exception as error:
        print(f"error generating captions: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_remove_fillers(args: argparse.Namespace) -> int:
    """Cut filler words from timeline with ripple edit."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    fillers = args.fillers.split(",") if args.fillers else None
    try:
        res = ai.remove_fillers(fcproj_path, fillers=fillers)
        print(f"removed fillers: {res.stdout or res.result}")
        return EXIT_OK
    except Exception as error:
        print(f"error removing fillers: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_cut_by_transcript(args: argparse.Namespace) -> int:
    """Import a transcript and apply AI cuts in one snapshot-guarded pass."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    if not args.file.is_file():
        print(f"error: transcript file not found: {args.file}", file=sys.stderr)
        return EXIT_FAILURE
    if not (args.remove_fillers or args.remove_pauses):
        print(
            "error: nothing to do; pass --remove-fillers and/or --remove-pauses",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    fillers = [w.strip() for w in args.fillers.split(",")] if args.fillers else None
    try:
        results = ai.cut_by_transcript(
            fcproj_path,
            args.file,
            remove_fillers_words=args.remove_fillers,
            fillers=fillers,
            remove_pauses=args.remove_pauses,
            min_pause=args.min_pause,
        )
        print(f"cut-by-transcript on {fcproj_path.name} (previous timeline archived as snapshot)")
        steps = [
            "import",
            "fillers" if args.remove_fillers else None,
            f"pauses>={args.min_pause:g}s" if args.remove_pauses else None,
        ]
        print(f"  passes:   {', '.join(s for s in steps if s)}")
        for res in results:
            detail = res.stdout or str(res.result)
            print(f"  {res.command}: {detail}")
        return EXIT_OK
    except FilmCraftError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_video_beat_sync(args: argparse.Namespace) -> int:
    """Detect beats and create timeline markers in project."""

    fcproj_path, _ = _resolve_fcproj_target(args.project)
    if not fcproj_path.is_file():
        print(f"error: project file not found: {fcproj_path}", file=sys.stderr)
        return EXIT_FAILURE
    if not args.audio.is_file():
        print(f"error: audio file not found: {args.audio}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        count = ai.snap_cuts_to_beats(fcproj_path, args.audio, marker_name=args.name)
        print(f"created {count} beat marker(s) on timeline")
        return EXIT_OK
    except Exception as error:
        print(f"error syncing beats: {error}", file=sys.stderr)
        return EXIT_FAILURE
