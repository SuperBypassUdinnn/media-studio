"""Command line interface package for media-studio."""

from __future__ import annotations

import argparse

from media_studio import __version__
from media_studio.filmcraft import FilmCraftRunner
from media_studio.ledger import stage_asset
from media_studio.manifest import load_project
from media_studio.materials import fetch_material
from media_studio.media import extract_stills, generate_proxy, probe
from media_studio.ramp import apply_speed_ramp
from media_studio.search import run_search
from media_studio.see import peek
from media_studio.sfx import SfxCue, SfxEntry, SfxError, list_bundled_sfx, mix_transition_sfx
from media_studio.tools import collect_toolchain, path_order_problem
from media_studio.transitions import TRANSITION_KINDS, TransitionError, apply_transition
from media_studio.video import (
    detect_scene_cuts,
    qc_report,
    render_filmcraft,
)
from media_studio.video import handoff as video_handoff
from media_studio.video import render as render_video

from .acquire import cmd_acquire_search, register_acquire_parser
from .assets import (
    cmd_assets_index,
    cmd_assets_list,
    cmd_assets_pull,
    cmd_assets_search,
    cmd_assets_stage,
    print_staged,
    register_assets_parser,
    resolve_candidate,
    suggested_asset_id,
)
from .audio import (
    cmd_audio_clean,
    cmd_audio_sfx_list,
    cmd_audio_sfx_mix,
    cmd_audio_tts,
    register_audio_parser,
)
from .common import EXIT_FAILURE, EXIT_OK, STAGING_CLASS_FOR_KIND
from .critique import register_critique_parser
from .design import (
    cmd_design_logo,
    cmd_design_render,
    cmd_design_svg_build,
    cmd_design_svg_compare,
    cmd_design_svg_trace,
    register_design_parser,
)
from .doctor import cmd_doctor, register_doctor_parser
from .fonts import cmd_fonts_search, cmd_fonts_specimen, register_fonts_parser
from .gen import register_gen_parser
from .image import cmd_image_extract_signature, cmd_image_remove_bg, register_image_parser
from .library import (
    cmd_library_forget,
    cmd_library_inspect,
    cmd_library_list,
    cmd_library_register,
    cmd_library_search,
    register_library_parser,
)
from .motion import (
    cmd_motion_html,
    cmd_motion_inspect,
    cmd_motion_lower_third,
    cmd_motion_lyrics,
    cmd_motion_new,
    cmd_motion_render,
    cmd_motion_title,
    register_motion_parser,
)
from .presets import cmd_presets_list, cmd_presets_search, cmd_presets_show, register_presets_parser
from .project import cmd_project_init, cmd_project_validate, register_project_parser
from .promo import register_promo_parser
from .scene import register_scene_parser
from .storyboard import register_storyboard_parser
from .three_d import (
    annotate_output,
    cmd_3d_convert,
    cmd_3d_from_image,
    cmd_3d_image_to_mesh,
    cmd_3d_inspect,
    cmd_3d_material,
    cmd_3d_modify,
    cmd_3d_turntable,
    register_three_d_parser,
)
from .triage_see import (
    cmd_see,
    cmd_triage_decide,
    cmd_triage_index,
    cmd_triage_sheets,
    cmd_triage_status,
    register_triage_see_parser,
)
from .video import (
    cmd_video_build,
    cmd_video_composite,
    cmd_video_handoff,
    cmd_video_proxy,
    cmd_video_qc,
    cmd_video_render,
    cmd_video_stills,
    cmd_video_transition,
    register_video_parser,
)
from .video_edl import (
    cmd_video_beatfx,
    cmd_video_beatramp,
    cmd_video_beats,
    cmd_video_edl_check_cuts,
    cmd_video_edl_generate,
    cmd_video_edl_preview,
    cmd_video_edl_render,
    cmd_video_edl_sync_check,
    cmd_video_impact_frame,
)
from .video_fcproj import (
    cmd_video_beat_sync,
    cmd_video_cmd,
    cmd_video_cut_by_transcript,
    cmd_video_diff,
    cmd_video_inspect,
    cmd_video_open,
    cmd_video_place,
    cmd_video_remove_fillers,
    cmd_video_snapshot,
    cmd_video_transcript,
    cmd_video_trim,
)
from .video_lut import cmd_video_lut_apply, cmd_video_lut_preview, cmd_video_lut_search
from .video_ops import (
    cmd_video_captions,
    cmd_video_ingest,
    cmd_video_parallax,
    cmd_video_reframe,
    cmd_video_remove_bg,
    cmd_video_scenes,
    cmd_video_shots_mine,
    cmd_video_speed_ramp,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``ms`` argument parser."""

    parser = argparse.ArgumentParser(
        prog="ms",
        description="Headless media production toolkit: video, audio, 3D, design, and assets.",
    )
    parser.add_argument("--version", action="version", version=f"media-studio {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    register_doctor_parser(subparsers)
    register_project_parser(subparsers)
    register_assets_parser(subparsers)
    register_video_parser(subparsers)
    register_library_parser(subparsers)
    register_acquire_parser(subparsers)
    register_three_d_parser(subparsers)
    register_design_parser(subparsers)
    register_image_parser(subparsers)
    register_fonts_parser(subparsers)
    register_presets_parser(subparsers)
    register_triage_see_parser(subparsers)
    register_motion_parser(subparsers)

    # Placeholder groups for Tasks 5-9
    register_audio_parser(subparsers)
    register_gen_parser(subparsers)
    register_promo_parser(subparsers)
    register_critique_parser(subparsers)
    register_scene_parser(subparsers)
    register_storyboard_parser(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point used by the ``ms`` console script."""

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


__all__ = [
    "EXIT_FAILURE",
    "EXIT_OK",
    "STAGING_CLASS_FOR_KIND",
    "TRANSITION_KINDS",
    "FilmCraftRunner",
    "SfxCue",
    "SfxEntry",
    "SfxError",
    "TransitionError",
    "annotate_output",
    "apply_speed_ramp",
    "apply_transition",
    "build_parser",
    "cmd_3d_convert",
    "cmd_3d_from_image",
    "cmd_3d_image_to_mesh",
    "cmd_3d_inspect",
    "cmd_3d_material",
    "cmd_3d_modify",
    "cmd_3d_turntable",
    "cmd_acquire_search",
    "cmd_assets_index",
    "cmd_assets_list",
    "cmd_assets_pull",
    "cmd_assets_search",
    "cmd_assets_stage",
    "cmd_audio_clean",
    "cmd_audio_sfx_list",
    "cmd_audio_sfx_mix",
    "cmd_audio_tts",
    "cmd_design_logo",
    "cmd_design_render",
    "cmd_design_svg_build",
    "cmd_design_svg_compare",
    "cmd_design_svg_trace",
    "cmd_doctor",
    "cmd_fonts_search",
    "cmd_fonts_specimen",
    "cmd_image_extract_signature",
    "cmd_image_remove_bg",
    "cmd_library_forget",
    "cmd_library_inspect",
    "cmd_library_list",
    "cmd_library_register",
    "cmd_library_search",
    "cmd_motion_html",
    "cmd_motion_inspect",
    "cmd_motion_lower_third",
    "cmd_motion_lyrics",
    "cmd_motion_new",
    "cmd_motion_render",
    "cmd_motion_title",
    "cmd_presets_list",
    "cmd_presets_search",
    "cmd_presets_show",
    "cmd_project_init",
    "cmd_project_validate",
    "cmd_see",
    "cmd_triage_decide",
    "cmd_triage_index",
    "cmd_triage_sheets",
    "cmd_triage_status",
    "cmd_video_beat_sync",
    "cmd_video_beatfx",
    "cmd_video_beatramp",
    "cmd_video_beats",
    "cmd_video_build",
    "cmd_video_captions",
    "cmd_video_cmd",
    "cmd_video_composite",
    "cmd_video_cut_by_transcript",
    "cmd_video_diff",
    "cmd_video_edl_check_cuts",
    "cmd_video_edl_generate",
    "cmd_video_edl_preview",
    "cmd_video_edl_render",
    "cmd_video_edl_sync_check",
    "cmd_video_handoff",
    "cmd_video_impact_frame",
    "cmd_video_ingest",
    "cmd_video_inspect",
    "cmd_video_lut_apply",
    "cmd_video_lut_preview",
    "cmd_video_lut_search",
    "cmd_video_open",
    "cmd_video_parallax",
    "cmd_video_place",
    "cmd_video_proxy",
    "cmd_video_qc",
    "cmd_video_reframe",
    "cmd_video_remove_bg",
    "cmd_video_remove_fillers",
    "cmd_video_render",
    "cmd_video_scenes",
    "cmd_video_shots_mine",
    "cmd_video_snapshot",
    "cmd_video_speed_ramp",
    "cmd_video_stills",
    "cmd_video_transcript",
    "cmd_video_transition",
    "cmd_video_trim",
    "collect_toolchain",
    "detect_scene_cuts",
    "extract_stills",
    "fetch_material",
    "generate_proxy",
    "list_bundled_sfx",
    "load_project",
    "main",
    "mix_transition_sfx",
    "path_order_problem",
    "peek",
    "print_staged",
    "probe",
    "qc_report",
    "render_filmcraft",
    "render_video",
    "resolve_candidate",
    "run_search",
    "stage_asset",
    "suggested_asset_id",
    "video_handoff",
]
