"""Command line interface for media-studio."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import __version__, ai, audio, media
from .beatfx import BeatFxError, apply_effects, auto_schedule, parse_schedule
from .blender import (
    EXPORT_FORMATS,
    BlenderError,
    modify_model,
    project_output,
    turntable,
    write_report,
)
from .blender import (
    convert as convert_model,
)
from .blender import (
    inspect as inspect_model,
)
from .cutout import (
    MODELS,
    CutoutError,
    extract_signature,
    extract_signature_folder,
    remove_background,
    remove_background_folder,
)
from .design import DesignError, validate_design
from .design import render as render_design
from .effectcraft import (
    AMV_MOTION_PRESETS,
    MOTION_TEMPLATES,
    EffectCraftError,
    EffectCraftRunner,
    lower_third,
    lyric_overlay,
    motion_project,
    new_composition,
    render_amv_preset,
    title_sequence,
)
from .fcproj import (
    CompilerError,
    compile_manifest_to_fcproj,
    diff_timelines,
    plan_manifest_compile,
    snapshot_timeline,
)
from .filmcraft import FilmCraftError, FilmCraftRunner
from .fonts import FontError, font_environment, inventory, specimen_sheet
from .fonts import search as search_fonts
from .impact import IMPACT_MODES, ImpactError, generate_impact_frames
from .ingest import PROFILES, IngestError, ingest, ingest_folder, probe_media
from .ledger import (
    ASSET_CLASSES,
    LedgerError,
    StagedAsset,
    build_index,
    stage_asset,
    write_index_and_credits,
)
from .manifest import ManifestError, Project, load_project, validate_project
from .materials import (
    MaterialError,
    bind,
    fetch_hdri,
    fetch_material,
    proof_frame_luminance,
)
from .media import MediaError, extract_stills, generate_proxy, probe
from .natron import NatronError, composite_project
from .parallax import ParallaxError, compose_parallax
from .presets import (
    KINDS as PRESET_KINDS,
)
from .presets import (
    PresetError,
    load_recipes,
    validate_effects,
    validate_recipe,
)
from .presets import (
    search as search_presets,
)
from .project import ProjectError, default_project_root, scaffold_project
from .ramp import RAMP_CURVES, RampError, apply_speed_ramp
from .search import SEARCH_KINDS, SearchError, load_outcome, run_search, save_outcome
from .see import SeeError, peek
from .sources import module_named
from .sources.base import Candidate, SourceError, http_fetch
from .tools import CheckResult, DoctorReport, collect_toolchain, deep_checks, path_order_problem
from .triage import (
    build_index as build_triage_index,
)
from .triage import (
    build_sheets,
    clip_paths,
    concept_key,
    read_decisions,
    read_index,
    record_decision,
    undecided,
    write_index,
)
from .triposr import TripoSRError, image_to_mesh_project
from .video import (
    VideoError,
    detect_scene_cuts,
    qc_report,
    render_filmcraft,
    scene_ranges,
)
from .video import handoff as video_handoff
from .video import render as render_video
from .video_cutout import (
    VIDEO_CUTOUT_MODELS,
    VideoCutoutError,
    remove_background_video,
)

# A search class is not always the staging class: a stock photo belongs in the design set.
STAGING_CLASS_FOR_KIND = {"image": "design", "video": "video", "audio": "audio", "music": "music"}

EXIT_OK = 0
EXIT_FAILURE = 1


def build_parser() -> argparse.ArgumentParser:
    """Build the ``ms`` argument parser."""

    parser = argparse.ArgumentParser(
        prog="ms",
        description="Headless media production toolkit: video, audio, 3D, design, and assets.",
    )
    parser.add_argument("--version", action="version", version=f"media-studio {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="report the local media toolchain")
    doctor.add_argument("--deep", action="store_true", help="also probe GPU files and torch venvs")
    doctor.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    doctor.set_defaults(func=cmd_doctor)

    project = subparsers.add_parser("project", help="manage media projects")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    init = project_sub.add_parser("init", help="scaffold a project from the template")
    init.add_argument("slug", help="project slug, for example scr2026-promo")
    init.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"parent directory (default: {default_project_root()})",
    )
    init.add_argument("--title", default=None, help="human title for the project")
    init.set_defaults(func=cmd_project_init)

    validate = project_sub.add_parser("validate", help="check a manifest and the assets it names")
    validate.add_argument("project", nargs="?", help="project path or slug (default: here)")
    validate.set_defaults(func=cmd_project_validate)

    assets = subparsers.add_parser("assets", help="stage assets and maintain the ledger")
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)

    stage = assets_sub.add_parser("stage", help="copy or download one asset with its sidecar")
    stage.add_argument("project", help="project path or slug")
    stage.add_argument("--class", dest="class_name", required=True, choices=ASSET_CLASSES)
    source = stage.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="local file to copy in")
    source.add_argument("--url", help="URL to download")
    stage.add_argument("--license", dest="license_text", required=True, help="licence wording")
    stage.add_argument("--attribution", default="", help="credit line, when required")
    stage.add_argument("--source-url", default="", help="page the asset came from")
    stage.add_argument("--provider", default="", help="provider name")
    stage.add_argument(
        "--sha256",
        dest="expected_sha256",
        default=None,
        help="known hash of the asset; skips the transfer when it is already staged",
    )
    stage.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="permit a licence that forbids commercial use (personal work only)",
    )
    stage.set_defaults(func=cmd_assets_stage)

    index = assets_sub.add_parser("index", help="regenerate assets/index.json and CREDITS.md")
    index.add_argument("project", help="project path or slug")
    index.set_defaults(func=cmd_assets_index)

    listing = assets_sub.add_parser("list", help="show staged assets and their licences")
    listing.add_argument("project", help="project path or slug")
    listing.set_defaults(func=cmd_assets_list)

    search = assets_sub.add_parser("search", help="search keyless sources for licensed candidates")
    search.add_argument("query", help="what to look for, for example 'forest canopy'")
    search.add_argument("--class", dest="class_name", required=True, choices=SEARCH_KINDS)
    search.add_argument("--limit", type=int, default=8, help="results per source (default 8)")
    search.add_argument(
        "--source", action="append", default=[], metavar="NAME", help="only this source"
    )
    search.add_argument(
        "--save",
        type=Path,
        default=None,
        help="where to write the result JSON (default: ~/.cache/media-studio/searches/)",
    )
    search.add_argument("--json", action="store_true", help="emit the result JSON on stdout")
    search.add_argument(
        "--include-unlicensed",
        action="store_true",
        help="also list candidates whose licence is unknown",
    )
    search.set_defaults(func=cmd_assets_search)

    pull = assets_sub.add_parser("pull", help="stage one candidate from a saved search")
    pull.add_argument("project", help="project path or slug")
    pull.add_argument("--search", type=Path, required=True, help="result JSON from `assets search`")
    pull.add_argument("--index", type=int, required=True, help="1-based candidate number")
    pull.add_argument(
        "--class",
        dest="class_name",
        default=None,
        choices=ASSET_CLASSES,
        help="staging class (default: derived from the search class)",
    )
    pull.add_argument(
        "--allow-noncommercial",
        action="store_true",
        help="permit a licence that forbids commercial use (personal work only)",
    )
    pull.add_argument(
        "--dry-run", action="store_true", help="show what would be staged, download nothing"
    )
    pull.set_defaults(func=cmd_assets_pull)

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

    beat_p = video_sub.add_parser("beat-sync", help="detect beats and inject timeline markers")
    beat_p.add_argument("project", help="project path, slug, or direct .fcproj file")
    beat_p.add_argument("--audio", type=Path, required=True, help="audio file to analyze")
    beat_p.add_argument("--name", default="Beat", help="marker label name")
    beat_p.set_defaults(func=cmd_video_beat_sync)

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

    three_d = subparsers.add_parser("3d", help="inspect, convert, and render models")
    three_d_sub = three_d.add_subparsers(dest="three_d_command", required=True)
    inspect = three_d_sub.add_parser("inspect", help="report a model's geometry")
    inspect.add_argument("model", type=Path)
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_3d_inspect)
    convert = three_d_sub.add_parser("convert", help="repair and re-export a model")
    convert.add_argument("model", type=Path)
    convert.add_argument("--to", required=True, choices=sorted(EXPORT_FORMATS))
    convert.add_argument("--out", type=Path, default=None, help="output directory")
    convert.add_argument("--decimate", type=float, default=0.0, help="ratio below 1.0 to simplify")
    convert.set_defaults(func=cmd_3d_convert)
    material = three_d_sub.add_parser("material", help="fetch and bind a PBR material")
    material.add_argument("model", type=Path)
    material.add_argument("--project", required=True, help="project to stage the maps into")
    material.add_argument("--material", required=True, help="asset id, for example mossy_rock")
    material.add_argument("--hdri", default=None, help="HDRI asset id for the world lighting")
    material.add_argument("--resolution", default="1k", help="texture resolution, default 1k")
    material.add_argument("--out", type=Path, default=None, help="where to write the bound model")
    material.add_argument("--render", action="store_true", help="also render a proof frame")
    material.add_argument("--proof-resolution", default="640x360")
    material.set_defaults(func=cmd_3d_material)

    spin = three_d_sub.add_parser("turntable", help="render a turntable headlessly")
    spin.add_argument("model", type=Path)
    spin.add_argument("--project", default=None, help="project to write outputs and reports into")
    spin.add_argument("--frames", type=int, default=24)
    spin.add_argument("--resolution", default="640x360", help="WxH, default 640x360")
    spin.add_argument("--baseline", type=Path, default=None, help="a previous turntable-qc.json")
    spin.set_defaults(func=cmd_3d_turntable)

    modify = three_d_sub.add_parser("modify", help="procedurally modify a model headlessly")
    modify.add_argument("model", type=Path, help="model file to modify")
    modify.add_argument("--project", required=True, help="project for outputs")
    modify.add_argument(
        "--ops", type=Path, default=None, help="YAML/JSON file with operations list"
    )
    modify.add_argument("--mirror", choices=("x", "y", "z"), default=None, help="mirror axis")
    modify.add_argument("--smooth", type=int, default=None, help="Laplacian smooth iterations")
    modify.add_argument("--subdivide", type=int, default=None, help="subdivision levels (1-3)")
    modify.add_argument("--decimate", type=float, default=None, help="decimate ratio below 1.0")
    modify.add_argument("--out", type=Path, default=None, help="output model path")
    modify.set_defaults(func=cmd_3d_modify)

    image_to_mesh = three_d_sub.add_parser(
        "image-to-mesh", help="convert a reference image into a mesh via TripoSR"
    )
    image_to_mesh.add_argument("image", type=Path, help="reference image (PNG/JPG)")
    image_to_mesh.add_argument(
        "--project", required=True, help="project to write outputs and reports into"
    )
    image_to_mesh.add_argument("--out", type=Path, default=None, help="output mesh path")
    image_to_mesh.add_argument(
        "--chunk-size", type=int, default=None, help="VRAM-safe chunk size (auto if omitted)"
    )
    image_to_mesh.add_argument(
        "--device", choices=("cuda", "cpu"), default="cuda", help="cuda or cpu (slow fallback)"
    )
    image_to_mesh.add_argument(
        "--mc-resolution", type=int, default=256, help="marching-cubes resolution"
    )
    image_to_mesh.add_argument(
        "--remove-bg", action="store_true", default=True, help="strip background before conversion"
    )
    image_to_mesh.set_defaults(func=cmd_3d_image_to_mesh)

    from_image = three_d_sub.add_parser(
        "from-image", help="TripoSR then convert → material → turntable pipeline"
    )
    from_image.add_argument("image", type=Path, help="reference image (PNG/JPG)")
    from_image.add_argument(
        "--project", required=True, help="project to write outputs and reports into"
    )
    from_image.add_argument("--chunk-size", type=int, default=None, help="VRAM-safe chunk size")
    from_image.add_argument(
        "--device", choices=("cuda", "cpu"), default="cuda", help="cuda or cpu (slow fallback)"
    )
    from_image.add_argument(
        "--mc-resolution", type=int, default=256, help="marching-cubes resolution"
    )
    from_image.add_argument(
        "--remove-bg", action="store_true", default=True, help="strip background before conversion"
    )
    from_image.set_defaults(func=cmd_3d_from_image)

    design = subparsers.add_parser("design", help="render brand-consistent images")
    design_sub = design.add_subparsers(dest="design_command", required=True)
    draw = design_sub.add_parser("render", help="compose and export every declared variant")
    draw.add_argument("project", help="project path or slug")
    draw.add_argument("--variant", default=None, help="render only this variant")
    draw.add_argument("--check-only", action="store_true", help="check what is already rendered")
    draw.set_defaults(func=cmd_design_render)

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

    fonts = subparsers.add_parser("fonts", help="find and preview fonts")
    fonts_sub = fonts.add_subparsers(dest="fonts_command", required=True)
    find = fonts_sub.add_parser("search", help="search installed families by intent")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=8)
    find.add_argument("--mono", action="store_true", help="only monospaced families")
    find.set_defaults(func=cmd_fonts_search)
    preview = fonts_sub.add_parser("specimen", help="render candidates with the project's words")
    preview.add_argument("project", help="project path or slug")
    preview.add_argument("--text", required=True, help="the headline to set in each candidate")
    preview.add_argument("--query", default="", help="narrow the candidates by name")
    preview.add_argument("--limit", type=int, default=6)
    preview.set_defaults(func=cmd_fonts_specimen)

    presets = subparsers.add_parser("presets", help="search the preset registry")
    presets_sub = presets.add_subparsers(dest="presets_command", required=True)

    listing = presets_sub.add_parser("list", help="every recipe, optionally by kind")
    listing.add_argument("--kind", choices=PRESET_KINDS, default=None)
    listing.set_defaults(func=cmd_presets_list)

    find = presets_sub.add_parser("search", help="search recipes by intent")
    find.add_argument("query")
    find.set_defaults(func=cmd_presets_search)

    show = presets_sub.add_parser("show", help="one recipe with its parameters")
    show.add_argument("preset_id")
    show.set_defaults(func=cmd_presets_show)

    triage = subparsers.add_parser("triage", help="index footage and record review decisions")
    triage_sub = triage.add_subparsers(dest="triage_command", required=True)

    index = triage_sub.add_parser("index", help="measure a clip library and explain every reject")
    index.add_argument("source", type=Path, help="a directory of clips, or one clip")
    index.add_argument("--project", default=None, help="project path or slug, to persist the cards")
    index.add_argument("--concept", default="", help="concept words, used to weight the ranking")
    index.add_argument("--json", action="store_true", help="emit the cards as JSON")
    index.set_defaults(func=cmd_triage_index)

    sheets = triage_sub.add_parser("sheets", help="batch the unreviewed clips into contact sheets")
    sheets.add_argument("project", help="project path or slug")
    sheets.add_argument("--concept", required=True, help="the concept being reviewed")
    sheets.add_argument("--limit", type=int, default=20, help="clips per run (default 20)")
    sheets.set_defaults(func=cmd_triage_sheets)

    decide = triage_sub.add_parser("decide", help="record a verdict for one clip")
    decide.add_argument("project", help="project path or slug")
    decide.add_argument("--key", required=True, help="clip key, from the index or a sheet label")
    decide.add_argument("--concept", required=True)
    decide.add_argument("--verdict", required=True, choices=("in", "hold", "out"))
    decide.add_argument("--reason", default="")
    decide.set_defaults(func=cmd_triage_decide)

    status = triage_sub.add_parser("status", help="how much of the library is reviewed")
    status.add_argument("project", help="project path or slug")
    status.add_argument("--concept", required=True)
    status.set_defaults(func=cmd_triage_status)

    motion = subparsers.add_parser("motion", help="render motion graphics through EffectCraft")
    motion_sub = motion.add_subparsers(dest="motion_command", required=True)

    motion_render = motion_sub.add_parser("render", help="render all declared motion sequences")
    motion_render.add_argument("project", help="project path or slug")
    motion_render.set_defaults(func=cmd_motion_render)

    motion_title = motion_sub.add_parser("title", help="render one animated title sequence")
    motion_title.add_argument("project", help="project path or slug")
    motion_title.add_argument("--text", required=True, help="title text")
    motion_title.add_argument("--out", type=Path, required=True, help="output file path")
    motion_title.add_argument("--duration", type=float, default=3.0, help="duration in seconds")
    motion_title.add_argument("--animation", default="fade_in_up", help="animation preset name")
    motion_title.add_argument(
        "--preset",
        choices=sorted(AMV_MOTION_PRESETS.keys()),
        default=None,
        help="AMV motion preset to render (overrides --animation/--font/--glow)",
    )
    motion_title.add_argument("--font", default="Noto Sans", help="font family")
    motion_title.add_argument("--font-size", type=float, default=72.0, help="font size in points")
    motion_title.add_argument("--color", default="#FFFFFF", help="text color")
    motion_title.add_argument("--glow", action="store_true", help="enable glow effect")
    motion_title.add_argument("--glow-intensity", type=float, default=0.5, help="glow strength")
    motion_title.add_argument(
        "--width", type=int, default=1920, help="output width in pixels (default 1920)"
    )
    motion_title.add_argument(
        "--height", type=int, default=1080, help="output height in pixels (default 1080)"
    )
    motion_title.add_argument(
        "--fps", type=float, default=60.0, help="output frame rate (default 60)"
    )
    motion_title.set_defaults(func=cmd_motion_title)

    motion_lyrics = motion_sub.add_parser("lyrics", help="render a lyric overlay track")
    motion_lyrics.add_argument("project", help="project path or slug")
    motion_lyrics.add_argument("--out", type=Path, required=True, help="output file path")
    motion_lyrics.add_argument(
        "--lines", required=True, help="lyrics as start,end,text|start,end,text"
    )
    motion_lyrics.add_argument("--style", default="default", help="text style name")
    motion_lyrics.set_defaults(func=cmd_motion_lyrics)

    motion_lt = motion_sub.add_parser("lower-third", help="render a lower-third overlay track")
    motion_lt.add_argument("project", help="project path or slug")
    motion_lt.add_argument("--title", required=True, help="primary title text")
    motion_lt.add_argument("--subtitle", default="", help="optional subtitle text")
    motion_lt.add_argument("--out", type=Path, default=None, help="output file path")
    motion_lt.add_argument("--duration", type=float, default=4.0, help="duration in seconds")
    motion_lt.set_defaults(func=cmd_motion_lower_third)

    motion_insp = motion_sub.add_parser("inspect", help="inspect an EffectCraft project")
    motion_insp.add_argument("project", help="project path, slug, or direct .ecproj file")
    motion_insp.add_argument("--json", action="store_true", help="emit info as JSON")
    motion_insp.set_defaults(func=cmd_motion_inspect)

    motion_new = motion_sub.add_parser(
        "new", help="scaffold a brand-tokenised composition from a template (design 5.7)"
    )
    motion_new.add_argument("project", help="project path or slug")
    motion_new.add_argument(
        "--comp", required=True, help="composition id (file becomes motion/<id>.ecproj)"
    )
    motion_new.add_argument(
        "--template",
        default="lower-third",
        help="one of: lower-third, title-card, logo-sting, lyric-overlay, explainer-beat",
    )
    motion_new.add_argument("--title", default="Title", help="primary text of the template")
    motion_new.add_argument("--subtitle", default="Subtitle", help="secondary text of the template")
    motion_new.add_argument(
        "--duration", type=float, default=None, help="composition duration in seconds"
    )
    motion_new.add_argument(
        "--force", action="store_true", help="overwrite an existing composition scaffold"
    )
    motion_new.set_defaults(func=cmd_motion_new)

    see = subparsers.add_parser(
        "see", help="look at any media file: facts plus a visual sheet the agent can read"
    )
    see.add_argument("source", type=Path, help="any video, image, or audio file")
    see.add_argument("--out", type=Path, default=None, help="sheet destination (file or folder)")
    see.add_argument("--columns", type=int, default=4, help="sheet columns (default 4)")
    see.add_argument("--rows", type=int, default=3, help="sheet rows (default 3)")
    see.add_argument("--width", type=int, default=320, help="tile width in px (default 320)")
    see.set_defaults(func=cmd_see)

    return parser


def _resolution(value: str) -> tuple[int, int]:
    width, _, height = value.lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError as error:
        raise BlenderError(f"resolution {value!r} must be WxH, for example 640x360") from error


def cmd_3d_inspect(args: argparse.Namespace) -> int:
    """Report what a model contains, measured by Blender."""

    try:
        report = inspect_model(args.model)
    except BlenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        json.dump(report.facts, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK
    facts = report.facts
    print(f"{args.model.name}")
    print(f"  objects:   {facts.get('objects')} ({facts.get('mesh_objects')} mesh)")
    print(f"  geometry:  {facts.get('vertices')} vertices, {facts.get('triangles')} triangles")
    print(f"  size:      {facts.get('size')}")
    print(f"  materials: {', '.join(facts.get('materials') or []) or 'none'}")
    problems = report.problems
    if problems:
        print("  problems:  " + ", ".join(problems))
    print("verdict: " + ("clean" if not problems else "needs repair; run `ms 3d convert`"))
    return EXIT_OK


def cmd_3d_convert(args: argparse.Namespace) -> int:
    """Repair a model and write it in another format."""

    destination = (args.out or args.model.parent) / f"{args.model.stem}{EXPORT_FORMATS[args.to]}"
    try:
        before = inspect_model(args.model)
        conversion = convert_model(args.model, destination, decimate=args.decimate)
    except BlenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"wrote {conversion.destination}")
    print(f"  triangles: {before.triangles} -> {conversion.triangles}")
    print(f"  problems:  {', '.join(conversion.problems) or 'none'}")
    return EXIT_OK if not conversion.problems else EXIT_FAILURE


def cmd_3d_material(args: argparse.Namespace) -> int:
    """Fetch a keyless PBR material, bind it to a model, and prove it rendered."""

    try:
        project = load_project(args.project)
        material = fetch_material(project, args.material, resolution=args.resolution)
        staged_hdri = (
            fetch_hdri(project, args.hdri, resolution=args.resolution) if args.hdri else None
        )
        destination = args.out or annotate_output(project, args.model, "-pbr.glb")
        proof = annotate_output(project, args.model, "-proof.png") if args.render else None
        resolution = _resolution(args.proof_resolution)
        result = bind(
            args.model,
            material.maps,
            destination=destination,
            hdri=staged_hdri.path if staged_hdri else None,
            render=proof,
            resolution=resolution,
        )
    except (ManifestError, BlenderError, MaterialError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{args.material}: bound {', '.join(result.bound_roles)}")
    if material.ignored_roles:
        print(f"  ignored:    {', '.join(material.ignored_roles)} (not wired into the shader)")
    print(f"  staged:     {len(material.staged)} map(s) with sidecars")
    print("  credits:    assets/CREDITS.md and assets/index.json refreshed")
    if result.unwired_slots:
        print(f"  unwired:    {', '.join(result.unwired_slots)} (no slot to use them in)")
    if staged_hdri is not None:
        print(f"  hdri:       {staged_hdri.relative}")
    if destination is not None:
        print(f"  model:      {destination.relative_to(project.root)}")
    if proof is not None and proof.is_file():
        luma = proof_frame_luminance(proof)
        print(f"  proof:      {proof.relative_to(project.root)} (luma {luma:.1f})")
    problems = result.problems
    report = {**result.facts, "material": args.material, "licence": "CC0", "problems": problems}
    write_report(project, report, f"{args.model.stem}-material-qc.json")
    print("verdict: " + ("pass" if not problems else "FAIL " + "; ".join(problems)))
    return EXIT_OK if not problems else EXIT_FAILURE


def annotate_output(project: Project, model: Path, suffix: str) -> Path:
    """A file name inside a project's 3D output, derived from the model's name."""

    return project_output(project, f"{model.stem}{suffix}")


def cmd_3d_turntable(args: argparse.Namespace) -> int:
    """Render a turntable and report its measured frames."""

    try:
        resolution = _resolution(args.resolution)
        if args.project:
            project = load_project(args.project)
            destination = project_output(project, args.model.stem)
        else:
            project = None
            destination = Path("/tmp/opencode/blender") / args.model.stem
        result = turntable(
            args.model,
            destination,
            frames=max(args.frames, 2),
            resolution=resolution,
            baseline=args.baseline,
        )
    except (ManifestError, BlenderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    report = result.facts
    width, height = report["resolution"]
    print(f"rendered {report['frames']} frame(s) at {width}x{height}")
    print(f"  directory: {result.directory}")
    print(f"  sheet:     {result.sheet.name}")
    print(f"  report:    {result.report_path.name}")
    if report.get("environment") and report["environment"] != "clean":
        print(f"  environment: {report['environment']}")
    if project is not None:
        written = write_report(project, report, f"{args.model.stem}-turntable-qc.json")
        print(f"  archived:  {written.relative_to(project.root)}")
    for name, passed in (report.get("checks") or {}).items():
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    print("verdict: " + ("pass" if report.get("ok") else "FAIL"))
    return EXIT_OK if report.get("ok") else EXIT_FAILURE


def cmd_3d_modify(args: argparse.Namespace) -> int:
    """Procedurally modify a model headlessly through Blender."""

    try:
        project = load_project(args.project)
        path = Path(args.model).expanduser()
        if not path.is_file():
            raise BlenderError(f"no such model: {path}")

        operations: list[dict] = []
        if args.ops is not None:
            import yaml

            operations = yaml.safe_load(args.ops.read_text(encoding="utf-8")) or []
            if not isinstance(operations, list):
                got = type(operations).__name__
                raise BlenderError(f"--ops file must contain a list, got {got}")
        else:
            if args.mirror is not None:
                operations.append(
                    {
                        "type": "mirror",
                        "x": args.mirror == "x",
                        "y": args.mirror == "y",
                        "z": args.mirror == "z",
                        "merge": True,
                    }
                )
            if args.smooth is not None:
                operations.append({"type": "smooth", "iterations": args.smooth, "factor": 0.5})
            if args.subdivide is not None:
                operations.append({"type": "subdivide", "levels": args.subdivide})
            if args.decimate is not None:
                operations.append({"type": "decimate", "ratio": args.decimate})
        if not operations:
            raise BlenderError("no operations given; pass --ops or a single-op flag")

        output = args.out or project_output(project, f"{path.stem}-modified.glb")
        result = modify_model(path, output, operations)
    except (ManifestError, BlenderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"modified {path.name}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  operations: {result.operations_applied}")
    print(f"  triangles: {result.before_triangles} -> {result.after_triangles}")
    print(f"  vertices:  {result.before_vertices} -> {result.after_vertices}")
    if result.facts.get("problems"):
        print("  problems:  " + ", ".join(result.facts["problems"]))
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_3d_image_to_mesh(args: argparse.Namespace) -> int:
    """Convert a reference image into a mesh via TripoSR headless."""

    try:
        project = load_project(args.project)
        output = args.out or project_output(project, f"{Path(args.image).stem}-base.obj")
        result = image_to_mesh_project(
            args.image,
            project_root=project.root,
            output_dir=output.parent,
            chunk_size=args.chunk_size,
            device=args.device,
            mc_resolution=args.mc_resolution,
            remove_bg=args.remove_bg,
        )
    except (ManifestError, TripoSRError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"image-to-mesh {Path(args.image).name}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print(f"  device:  {result.report.get('device')} chunk={result.report.get('chunk_size')}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_3d_from_image(args: argparse.Namespace) -> int:
    """Run TripoSR then convert → material → turntable on a reference image."""

    try:
        project = load_project(args.project)
        image_path = Path(args.image).expanduser()
        if not image_path.is_file():
            raise TripoSRError(f"no such reference image: {image_path}")

        # Step 1: image → base mesh via TripoSR.
        mesh_result = image_to_mesh_project(
            image_path,
            project_root=project.root,
            chunk_size=args.chunk_size,
            device=args.device,
            mc_resolution=args.mc_resolution,
            remove_bg=args.remove_bg,
        )
        if not mesh_result.ok:
            print(f"error: TripoSR failed: {mesh_result.report.get('problems')}", file=sys.stderr)
            return EXIT_FAILURE

        # Step 2: convert (decimate/normals) → GLB.
        converted = modify_model(
            mesh_result.output,
            project_output(project, f"{image_path.stem}-base.glb"),
            [{"type": "decimate", "ratio": 0.8}],
        )
        if not converted.ok:
            print(f"error: convert failed: {converted.facts.get('problems')}", file=sys.stderr)
            return EXIT_FAILURE

        print(f"from-image {image_path.name}")
        print(f"  mesh:    {mesh_result.output.relative_to(project.root)}")
        print(f"  glb:     {converted.output.relative_to(project.root)}")
        print(f"  triangles: {converted.before_triangles} -> {converted.after_triangles}")
        print("verdict: pass")
        return EXIT_OK
    except (ManifestError, TripoSRError, BlenderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_design_render(args: argparse.Namespace) -> int:
    """Render variants and report the checks."""

    try:
        project = load_project(args.project)
        report = render_design(project, args.variant, check_only=args.check_only)
    except (ManifestError, DesignError, FontError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    for variant in report.variants:
        mark = "ok  " if variant.get("ok") else "FAIL"
        print(f"  {mark} {variant.get('id')}")
        for problem in variant.get("problems") or []:
            print(f"        {problem}")
    if report.sheet is not None:
        print(f"  sheet: {report.sheet.relative_to(project.root)}")
    if report.report_path is not None:
        print(f"  report: {report.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if report.ok else "FAIL"))
    return EXIT_OK if report.ok else EXIT_FAILURE


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


def cmd_fonts_search(args: argparse.Namespace) -> int:
    """Search installed families, filtered to those that can set the text."""

    try:
        fonts = search_fonts(
            inventory(),
            args.query,
            monospace=True if args.mono else None,
            limit=max(args.limit, 1),
        )
    except FontError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not fonts:
        print(f"no family matches {args.query!r} with Indonesian coverage")
        return EXIT_OK
    for font in fonts:
        print(f"  {font.family}  ({font.style})")
        print(f"    {font.file}")
    print(
        "\nstage one with `ms assets stage <project> --class design "
        "--file <path> --license OFL-1.1`"
    )
    return EXIT_OK


def cmd_fonts_specimen(args: argparse.Namespace) -> int:
    """Render candidate families with the project's own headline."""

    try:
        project = load_project(args.project)
        environment = font_environment(project)
        candidates = search_fonts(inventory(environment), args.query, limit=max(args.limit, 1))
        sheet = specimen_sheet(
            candidates,
            args.text,
            project.root / "out" / "images" / "font-specimen.png",
            environment=environment,
        )
    except (ManifestError, FontError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{len(candidates)} candidate(s): {', '.join(font.family for font in candidates)}")
    print(f"  sheet: {sheet.relative_to(project.root)}")
    print("choose one, stage it into assets/design/fonts, then set design.fonts in project.yaml")
    return EXIT_OK


def _load_registry() -> dict[str, object]:
    """Load and integrity-check the registry, refusing to serve a broken recipe."""

    recipes = load_recipes()
    problems = [problem for recipe in recipes.values() for problem in validate_recipe(recipe)]
    if problems:
        raise PresetError("registry problems: " + "; ".join(problems))
    return recipes


def cmd_presets_list(args: argparse.Namespace) -> int:
    """List recipes, one line each."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    selected = [
        recipe for recipe in recipes.values() if args.kind is None or recipe.kind == args.kind
    ]
    for recipe in sorted(selected, key=lambda item: (item.kind, item.id)):
        print(f"  {recipe.id:<26} {recipe.kind:<13} {recipe.title}")
    print(f"\n{len(selected)} preset(s); search with `ms presets search <intent>`")
    return EXIT_OK


def cmd_presets_search(args: argparse.Namespace) -> int:
    """Search by intent and print what each match is for."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    matches = search_presets(recipes, args.query)
    if not matches:
        print(f"no preset matches {args.query!r}; try `ms presets list`")
        return EXIT_OK
    for recipe in matches:
        print(f"  {recipe.id}  ({recipe.kind})")
        print(f"    {recipe.summary}")
    return EXIT_OK


def cmd_presets_show(args: argparse.Namespace) -> int:
    """Show one recipe: what it does, its parameters, and how to reference it."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    recipe = recipes.get(args.preset_id)
    if recipe is None:
        print(f"error: no preset {args.preset_id!r}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{recipe.id} - {recipe.title}")
    print(f"  kind:       {recipe.kind} ({recipe.engine})")
    print(f"  applies to: {', '.join(recipe.applies_to)}")
    print(f"  intent:     {', '.join(recipe.intent)}")
    print(f"  licence:    {recipe.license}")
    print(f"  provenance: {recipe.provenance}")
    print(f"  {recipe.summary}")
    if recipe.parameters:
        print("\nparameters")
        for parameter in recipe.parameters:
            bounds = ""
            if parameter.minimum is not None and parameter.maximum is not None:
                bounds = f" [{parameter.minimum:g}..{parameter.maximum:g}]"
            unit = f" {parameter.unit}" if parameter.unit else ""
            details = f"default {parameter.default}{unit}{bounds}"
            print(f"  {parameter.name:<10} {parameter.type:<8} {details}")
            if parameter.description:
                print(f"             {parameter.description}")
    print("\nreference it in project.yaml under effects:")
    example = {"id": recipe.id}
    if recipe.parameters:
        example[recipe.parameters[0].name] = recipe.parameters[0].default
    print(f"  effects:\n    - {example}")
    return EXIT_OK


def cmd_triage_index(args: argparse.Namespace) -> int:
    """Measure a library, reusing cached measurements, and report the rejects."""

    source = Path(args.source).expanduser()
    if not source.exists():
        print(f"error: no such source: {source}", file=sys.stderr)
        return EXIT_FAILURE
    paths = clip_paths(source)
    if not paths:
        print(f"error: no clips under {source}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        project = load_project(args.project) if args.project else None
        cached = read_index(project) if project else {}
        index = build_triage_index(
            paths,
            scratch=media.triage_scratch(),
            keywords=tuple(word for word in args.concept.lower().split() if word),
            cache=cached,
        )
        if project is not None:
            path = write_index(project, index)
    except (ManifestError, MediaError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        # Cards on stdout, the summary on stderr, so a script can read the cards alone.
        sys.stdout.write(index.to_jsonl())
    else:
        print(f"{'':6}{'key':<14}{'length':>8}  {'size':>10}  detail")
        for item in index.facts:
            mark = "keep" if item.usable else "drop"
            detail = ", ".join(item.reasons) if item.reasons else f"score {item.score:.2f}"
            size = f"{item.width}x{item.height}" if item.width else "-"
            print(f"  {mark:<6}{item.key:<14}{item.duration:7.2f}s  {size:>10}  {detail}")
    summary = index.summary
    print(
        f"clips {summary['clips']}, usable {summary['usable']}, rejected {summary['rejected']}, "
        f"duplicate groups {summary['duplicate_groups']}, "
        f"measured now {summary['measured_now']}, reused {summary['reused_from_cache']}",
        file=sys.stderr if args.json else sys.stdout,
    )
    if project is not None and not args.json:
        print(f"cards: {path.relative_to(project.root)}")
    return EXIT_OK


def cmd_triage_sheets(args: argparse.Namespace) -> int:
    """Batch the clips that have no verdict yet into labelled contact sheets."""

    try:
        project = load_project(args.project)
        cached = read_index(project)
        if not cached:
            raise ManifestError(
                "no cards yet; run `ms triage index <clips> --project <project>` first"
            )
        index = build_triage_index(
            [item.path for item in cached.values()],
            scratch=media.triage_scratch(),
            cache=cached,
        )
        pending = undecided(index, read_decisions(project), args.concept)
        chosen = pending[: max(args.limit, 1)]
        sheets = build_sheets(chosen, project.root / "footage" / "sheets")
    except (ManifestError, MediaError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not sheets:
        print(f"nothing to review for {args.concept!r}: every usable clip already has a verdict")
        return EXIT_OK
    print(f"{len(chosen)} clip(s) to review, in {len(sheets)} sheet(s)")
    for sheet in sheets:
        print(f"  {sheet.relative_to(project.root)}")
    print("review each sheet and answer with a verdict per key, then run `ms triage decide`")
    return EXIT_OK


def cmd_triage_decide(args: argparse.Namespace) -> int:
    """Record one verdict."""

    try:
        project = load_project(args.project)
        decision = record_decision(
            project,
            key=args.key,
            concept=args.concept,
            verdict=args.verdict,
            reason=args.reason,
        )
    except (ManifestError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(
        f"recorded {decision.verdict} for {decision.key} on concept {concept_key(decision.concept)}"
    )
    return EXIT_OK


def cmd_triage_status(args: argparse.Namespace) -> int:
    """Report review progress for one concept."""

    try:
        project = load_project(args.project)
        cached = read_index(project)
        index = build_triage_index(
            [item.path for item in cached.values()],
            scratch=media.triage_scratch(),
            cache=cached,
        )
        decisions = [item for item in read_decisions(project) if item.concept == args.concept]
    except (ManifestError, MediaError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    counts: dict[str, int] = {}
    for item in decisions:
        counts[item.verdict] = counts.get(item.verdict, 0) + 1
    pending = undecided(index, read_decisions(project), args.concept)
    print(f"concept: {concept_key(args.concept)}")
    print(f"  usable clips:   {len(index.usable)}")
    summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items())) or "none"
    print(f"  with a verdict: {len(decisions)} ({summary})")
    print(f"  still pending:  {len(pending)}")
    return EXIT_OK


def cmd_video_render(args: argparse.Namespace) -> int:
    """Render a deliverable and report the measured QC checks."""

    engine = getattr(args, "engine", "filmcraft")
    gpu = getattr(args, "gpu", "auto")
    try:
        project = load_project(args.project)
        if engine == "mlt":
            result = render_video(
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

        result = render_filmcraft(
            project,
            args.deliverable,
            gpu=gpu,
            preview=args.preview,
            burn_captions=getattr(args, "burn_captions", False),
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

    try:
        project = load_project(args.project)
        result = video_handoff(project, args.deliverable)
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

    try:
        project = load_project(args.project)
        source = Path(args.file).expanduser()
        if not source.is_file():
            raise VideoError(f"no such file: {source}")
        deliverable = next(
            (item for item in project.deliverables if item.kind == "video"),
            None,
        )
        expected = deliverable.duration if deliverable and deliverable.duration else None
        if expected is None:
            expected = media.probe(source).duration
        report = qc_report(
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

    try:
        project = load_project(args.project)
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
    # A bare --out is a folder: create it so the destination resolves inside it.
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
    try:
        cuts = detect_scene_cuts(source, threshold=args.threshold)
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
    # The last scene ends at the last frame; a rounded duration x fps is
    # close enough for choosing prompt anchors.
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

    try:
        result = apply_speed_ramp(
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


def _resolve_fcproj_target(target: str) -> tuple[Path, Project | None]:
    """Resolve an argument to an fcproj file path and optional Project instance."""
    path = Path(target).expanduser()
    if path.suffix == ".fcproj":
        return path, None
    try:
        project = load_project(target)
        candidates = [
            project.root / f"{project.slug}.fcproj",
            project.root / "out" / f"{project.slug}.fcproj",
        ]
        for c in candidates:
            if c.is_file():
                return c, project
        found = list(project.root.glob("*.fcproj"))
        if found:
            newest = sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[0]
            return newest, project
        return candidates[0], project
    except (ManifestError, OSError):
        return path, None


def cmd_video_build(args: argparse.Namespace) -> int:
    """Compile project.yaml into a FilmCraft .fcproj project."""
    try:
        project = load_project(args.project)
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
    try:
        project = load_project(args.project)
        source = _resolve_review_source(project, args.file, args.deliverable)
        destination = args.out or (project.root / "out" / f"{source.stem}-proxy.mp4")
        generate_proxy(source, destination, height=args.height)
        print(f"proxy {source.name} -> {destination.relative_to(project.root)}")
        print(f"  height:  {args.height}px")
        return EXIT_OK
    except (ManifestError, FileNotFoundError, RuntimeError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


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


def cmd_video_stills(args: argparse.Namespace) -> int:
    """Extract review still frames outside the render pipeline."""
    try:
        project = load_project(args.project)
        source = _resolve_review_source(project, args.file, args.deliverable)
        destination = args.out or (project.root / "out" / f"{source.stem}-stills")
        timestamps: list[float] | None = None
        if args.at:
            percentages = [float(v) for v in str(args.at).split(",")]
            info = probe(source)
            duration = info.duration or 0.0
            if duration <= 0.0:
                raise RuntimeError(f"cannot determine duration of {source}")
            timestamps = [round(duration * (p / 100.0), 2) for p in percentages]
        paths = extract_stills(source, destination, timestamps=timestamps)
        print(f"stills {source.name} -> {destination.relative_to(project.root)}")
        for path in paths:
            print(f"  {path.name}")
        return EXIT_OK
    except (ManifestError, FileNotFoundError, RuntimeError, MediaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


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
    try:
        runner = FilmCraftRunner()
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
    try:
        runner = FilmCraftRunner()
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
    try:
        runner = FilmCraftRunner()
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
    try:
        runner = FilmCraftRunner()
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
            for t in video_tracks + audio_tracks:
                all_clips.extend(t.get("items", []))
            print(f"clips:     {len(all_clips)} placed")
            tracks = seq.get("tracks", []) or (video_tracks + audio_tracks)
            if tracks:
                track_names = ", ".join(t.get("name", "?") for t in tracks[:6])
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
    env.pop("MANGOHUD", None)  # Prevent known Wayland crash on KDE
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


def _format_mmss(seconds: float) -> str:
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


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
                schedule = parse_schedule(json.loads(handle.read()))

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


def cmd_motion_new(args: argparse.Namespace) -> int:
    """Scaffold a brand-tokenised EffectCraft composition from a template."""
    if args.template not in MOTION_TEMPLATES:
        print(
            f"error: unknown template {args.template!r}; choose one of: "
            f"{', '.join(MOTION_TEMPLATES)}",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    try:
        project = load_project(args.project)
        deliverable = None
        for candidate in project.deliverables:
            if candidate.kind == "video":
                deliverable = candidate
                break
        palette = dict(project.brand.palette) if project.brand.palette else None
        fonts = dict(project.brand.fonts) if project.brand.fonts else None
        path = new_composition(
            project.root,
            args.comp,
            args.template,
            duration=args.duration,
            deliverable_resolution=deliverable.resolution if deliverable else None,
            deliverable_fps=deliverable.fps if deliverable else None,
            palette=palette,
            fonts=fonts,
            title=args.title,
            subtitle=args.subtitle,
            force=args.force,
        )
        print(f"scaffolded {path.relative_to(project.root)}")
        print(f"  template:  {args.template}")
        print("  next:      render the scaffold, then let the compiler place it on V2:")
        print(
            f"             effectcraft-cli --project {path} render"
            f" --out out/motion/{args.comp}.mov --channels rgba"
        )
        print(
            "             declare the sequence under motion: sequences:"
            f" in project.yaml (id: {args.comp})"
        )
        print("             ms video build imports out/motion/<id>.mov onto V2 automatically")
        return EXIT_OK
    except (ManifestError, EffectCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_see(args: argparse.Namespace) -> int:
    """Look at any media file: facts plus a visual sheet the agent can read."""

    try:
        result = peek(
            args.source,
            args.out,
            columns=args.columns,
            rows=args.rows,
            width=args.width,
        )
    except SeeError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    for line in result.summary_lines:
        print(line)
    return EXIT_OK


def cmd_motion_render(args: argparse.Namespace) -> int:
    """Render every motion-graphics sequence declared in project.yaml."""

    try:
        project = load_project(args.project)
        sequences = list(project.raw.get("motion", {}).get("sequences", []))
        if not sequences:
            print("no motion sequences declared under motion: in project.yaml")
            return EXIT_OK
        results = motion_project(sequences, project_root=project.root)
    except (ManifestError, EffectCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    all_ok = True
    for result in results:
        print(f"motion {result.ecp_path.stem}")
        print(f"  output:  {result.output.relative_to(project.root)}")
        print(f"  ecp:     {result.ecp_path.relative_to(project.root)}")
        print(f"  report:  {result.report_path.relative_to(project.root)}")
        print(f"  verdict: {'pass' if result.ok else 'FAIL'}")
        all_ok = all_ok and result.ok
    return EXIT_OK if all_ok else EXIT_FAILURE


def cmd_motion_title(args: argparse.Namespace) -> int:
    """Render one animated title sequence through EffectCraft."""

    try:
        project = load_project(args.project)
        if args.preset:
            result = render_amv_preset(
                preset_name=args.preset,
                output=args.out,
                text=args.text,
                duration=args.duration,
                fps=args.fps,
                width=args.width,
                height=args.height,
            )
        else:
            result = title_sequence(
                text=args.text,
                output=args.out,
                ecp_path=args.out.with_suffix(".ecp"),
                duration=args.duration,
                animation=args.animation,
                font=args.font,
                font_size=args.font_size,
                color=args.color,
                glow=args.glow,
                glow_intensity=args.glow_intensity,
            )
    except (ManifestError, EffectCraftError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"title {args.text}")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  ecp:     {result.ecp_path.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_motion_lyrics(args: argparse.Namespace) -> int:
    """Render a lyric overlay track through EffectCraft."""

    try:
        project = load_project(args.project)
        lines = []
        for item in args.lines.split("|"):
            parts = item.split(",")
            if len(parts) != 3:
                raise EffectCraftError(f"invalid lyric entry {item!r}: expected start,end,text")
            lines.append((float(parts[0]), float(parts[1]), parts[2]))
        result = lyric_overlay(
            lines=lines,
            output=args.out,
            ecp_path=args.out.with_suffix(".ecp"),
            style=args.style,
        )
    except (ManifestError, EffectCraftError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"lyrics ({len(lines)} line(s))")
    print(f"  output:  {result.output.relative_to(project.root)}")
    print(f"  ecp:     {result.ecp_path.relative_to(project.root)}")
    print(f"  report:  {result.report_path.relative_to(project.root)}")
    print("verdict: " + ("pass" if result.ok else "FAIL"))
    return EXIT_OK if result.ok else EXIT_FAILURE


def cmd_motion_lower_third(args: argparse.Namespace) -> int:
    """Render a lower-third motion graphic with alpha ProRes 4444."""
    try:
        project = load_project(args.project)
        out_path = args.out or project.root / "out" / "motion" / "lower-third.mov"
        ecp_path = out_path.with_suffix(".ecproj")
        res = lower_third(
            title=args.title,
            subtitle=args.subtitle,
            output=out_path,
            ecp_path=ecp_path,
            duration=args.duration,
        )
        print(f"lower-third: {args.title}")
        print(f"  output:  {res.output.relative_to(project.root)}")
        print(f"  report:  {res.report_path.relative_to(project.root)}")
        print(f"  verdict: {'pass' if res.ok else 'FAIL'}")
        return EXIT_OK if res.ok else EXIT_FAILURE
    except Exception as error:
        print(f"error rendering lower-third: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_motion_inspect(args: argparse.Namespace) -> int:
    """Inspect EffectCraft project comps and layers."""
    try:
        target = Path(args.project)
        if target.is_dir():
            out_motion = target / "out" / "motion"
            candidates = list(out_motion.glob("*.ecproj")) + list(target.glob("*.ecproj"))
            if not candidates:
                print(f"no .ecproj found in {target}", file=sys.stderr)
                return EXIT_FAILURE
            target = candidates[0]
        runner = EffectCraftRunner()
        info = runner.inspect(target)
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"project: {info.get('name', target.name)}")
            comps = info.get("comps", [])
            print(f"comps:   {len(comps)}")
            for comp in comps:
                w = comp.get("width", 1920)
                h = comp.get("height", 1080)
                fps = comp.get("fps", 30)
                print(f"  comp {comp.get('name', '?')}: {w}x{h} @ {fps}fps")
        return EXIT_OK
    except Exception as error:
        print(f"error inspecting motion project: {error}", file=sys.stderr)
        return EXIT_FAILURE


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report toolchain status and exit non-zero when a required tool is missing."""

    order_problem = path_order_problem(os.environ.get("PATH", ""))
    checks: list[CheckResult] = [
        CheckResult("PATH order", order_problem is None, order_problem or "ok")
    ]
    if args.deep:
        checks.extend(deep_checks())
    report = DoctorReport(tools=collect_toolchain(), checks=checks)

    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_report(report)

    return EXIT_FAILURE if report.missing_required else EXIT_OK


def _print_report(report: DoctorReport) -> None:
    statuses = report.tools
    width = max(len(status.spec.name) for status in statuses)
    print(f"media-studio {__version__} toolchain\n")
    for status in statuses:
        mark = "ok " if status.ok else "MISSING"
        requirement = "required" if status.spec.required else "optional"
        version = status.version or status.path or "-"
        print(f"  {status.spec.name:<{width}}  {mark:<7} {requirement:<8} {version}")
    print("\nchecks")
    for check in report.checks:
        mark = "ok " if check.ok else "FAIL"
        print(f"  {check.name:<22} {mark:<5} {check.detail}")
    if report.missing_required:
        print(f"\nmissing required tools: {', '.join(report.missing_required)}")
    else:
        print("\nall required tools present")
    if report.warnings:
        print(f"warnings (non-fatal): {', '.join(report.warnings)}")


def cmd_project_init(args: argparse.Namespace) -> int:
    """Scaffold a project directory from the template."""

    try:
        created = scaffold_project(args.slug, root=args.root, title=args.title)
    except ProjectError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"created {created.path}")
    print(f"  slug:  {created.slug}")
    print(f"  title: {created.title}")
    print("next: edit project.yaml, then run the /assets workflow to stage licensed assets")
    return EXIT_OK


def cmd_project_validate(args: argparse.Namespace) -> int:
    """Validate a manifest and report every problem it has."""

    try:
        project = load_project(args.project)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    report = validate_project(project)
    report.errors.extend(validate_effects(project.effects))
    report.errors.extend(validate_design(project))
    print(f"{project.slug}: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    for problem in report.errors:
        print(f"  error:   {problem}")
    for problem in report.warnings:
        print(f"  warning: {problem}")
    return EXIT_OK if report.ok else EXIT_FAILURE


def cmd_assets_stage(args: argparse.Namespace) -> int:
    """Stage one asset into the project and write its sidecar."""

    try:
        project = load_project(args.project)
        staged = stage_asset(
            project,
            class_name=args.class_name,
            license_text=args.license_text,
            source_url=args.source_url,
            attribution=args.attribution,
            provider=args.provider,
            local_file=args.file,
            url=args.url,
            expected_sha256=args.expected_sha256,
            allow_noncommercial=args.allow_noncommercial,
        )
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print_staged(staged)
    return EXIT_OK


def print_staged(staged: StagedAsset) -> None:
    """Report one staging result, shared by `assets stage` and `assets pull`."""

    if staged.duplicate_of:
        print(f"already staged: {staged.relative} (duplicate of {staged.duplicate_of})")
    else:
        print(f"staged {staged.relative}")
    print(f"  licence:  {staged.spdx} ({staged.license_label})")
    print(f"  sha256:   {staged.sha256}")
    print(f"  bytes:    {staged.size_bytes}")
    print(f"  declare:  {suggested_asset_id(staged.relative)}: {staged.relative}")
    print("            (paste that line under `assets:` in project.yaml to use it)")
    if staged.quarantined:
        print(
            "  warning:  licence unknown, quarantined under assets/unverified/; "
            "no deliverable may reference it",
            file=sys.stderr,
        )
    elif staged.attribution_required and not staged.attribution:
        print(
            "  warning:  this licence requires attribution and no credit line was recorded",
            file=sys.stderr,
        )


def suggested_asset_id(relative: str) -> str:
    """A paste-ready manifest key derived from the file name."""

    stem = Path(relative).stem.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return (cleaned or "asset")[:32]


def cmd_assets_search(args: argparse.Namespace) -> int:
    """Search the keyless sources and cache the result for `assets pull`."""

    try:
        outcome = run_search(
            args.query,
            args.class_name,
            limit=args.limit,
            sources=tuple(args.source),
            include_unlicensed=args.include_unlicensed,
        )
    except SearchError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        saved = save_outcome(outcome, args.save)
    except OSError as error:
        print(f"error: could not save the results: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        json.dump(outcome.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        print(f"saved: {saved}", file=sys.stderr)
    else:
        print(outcome.render())
        print(f"\nsaved: {saved}")
        print(f"next: ms assets pull <project> --search {saved} --index <n>")

    return EXIT_OK if any(report.ok for report in outcome.reports) else EXIT_FAILURE


def cmd_assets_pull(args: argparse.Namespace) -> int:
    """Stage one candidate chosen from a saved search."""

    try:
        project = load_project(args.project)
        outcome = load_outcome(args.search)
    except (ManifestError, SearchError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if not 1 <= args.index <= len(outcome.candidates):
        print(
            f"error: --index must be between 1 and {len(outcome.candidates)} "
            f"({outcome.query!r} returned {len(outcome.candidates)} candidate(s))",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    candidate = outcome.candidates[args.index - 1]
    class_name = args.class_name or STAGING_CLASS_FOR_KIND.get(candidate.kind, candidate.kind)
    resolving = candidate.download_url is None
    try:
        url = candidate.download_url or resolve_candidate(candidate)
    except SourceError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if resolving:
        print(f"resolved from {candidate.source}: {url}")

    if args.dry_run:
        print(f"would stage: {candidate.title}")
        print(f"  source:   {candidate.source}")
        print(f"  licence:  {candidate.license_text or 'unknown; it would be quarantined'}")
        print(f"  credit:   {candidate.creator or '(none recorded)'}")
        print(f"  page:     {candidate.source_page or '-'}")
        print(f"  url:      {url}")
        print(f"  class:    {class_name}")
        return EXIT_OK

    try:
        staged = stage_asset(
            project,
            class_name=class_name,
            license_text=candidate.license_text,
            source_url=candidate.source_page,
            attribution=candidate.creator,
            provider=candidate.source,
            url=url,
            allow_noncommercial=args.allow_noncommercial,
        )
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"from {candidate.source}: {candidate.title}")
    print_staged(staged)
    return EXIT_OK


def resolve_candidate(candidate: Candidate) -> str:
    """Ask a source to turn a page-only candidate into a direct download URL."""

    module = module_named(candidate.source)
    if module is None or not hasattr(module, "resolve"):
        page = candidate.source_page or candidate.key
        raise SourceError(
            f"{candidate.source} candidates have no direct link and this source cannot resolve "
            f"one; open {page} and stage the file with `ms assets stage --url`"
        )
    url, _name = module.resolve(candidate, http_fetch)
    return url


def cmd_assets_index(args: argparse.Namespace) -> int:
    """Regenerate the derived index and credits file."""

    try:
        project = load_project(args.project)
        index_path, credits_path, index = write_index_and_credits(project)
    except (ManifestError, LedgerError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    totals = index.totals
    quota = index.quota
    print(
        f"wrote {index_path.relative_to(project.root)} and {credits_path.relative_to(project.root)}"
    )
    print(
        f"  assets: {totals.count} staged, {totals.attribution_required} needing credit, "
        f"{totals.quarantined} quarantined"
    )
    print(
        f"  bytes: {quota.project_bytes} of {quota.max_project_bytes} allowed"
        f"{'' if quota.ok else ' (over budget)'}"
    )
    if index.unlicensed:
        print(f"  warning: {len(index.unlicensed)} file(s) have no sidecar")
    missing_credit = [record for record in index.assets if record.attribution_missing]
    if missing_credit:
        print(
            f"  warning: {len(missing_credit)} asset(s) need a credit line and none was recorded:"
        )
        for record in missing_credit:
            print(f"    {record.path} ({record.spdx})")
    return EXIT_OK if quota.ok else EXIT_FAILURE


def cmd_assets_list(args: argparse.Namespace) -> int:
    """List staged assets with their licence and attribution duty."""

    try:
        project = load_project(args.project)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    index = build_index(project)
    if not index.assets:
        print("no staged assets; use `ms assets stage` after picking a candidate")
        return EXIT_OK

    print(f"{'path':<48} {'licence':<14} {'credit':<7} class")
    for record in index.assets:
        credit = "yes" if record.attribution_required else "no"
        print(f"{record.path:<48} {record.spdx:<14} {credit:<7} {record.class_name}")
    print(f"\n{index.totals.count} asset(s), {index.totals.bytes} bytes staged")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point used by the ``ms`` console script."""

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
