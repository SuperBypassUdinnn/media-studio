"""Tests for the decomposed media_studio.cli package structure."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import media_studio.cli as cli_module
from media_studio.cli import build_parser, main


def test_cli_build_parser_and_main_imports() -> None:
    """Verify build_parser and main can be imported and parser constructed."""
    parser = build_parser()
    assert parser is not None
    assert callable(main)


def test_domain_register_hooks_exist() -> None:
    """Verify every domain module exposes its register_*_parser hook."""
    from media_studio.cli.assets import register_assets_parser
    from media_studio.cli.audio import register_audio_parser
    from media_studio.cli.design import register_design_parser
    from media_studio.cli.gen import register_gen_parser
    from media_studio.cli.motion import register_motion_parser
    from media_studio.cli.project import register_project_parser
    from media_studio.cli.promo import register_promo_parser
    from media_studio.cli.three_d import register_three_d_parser
    from media_studio.cli.triage_see import register_triage_see_parser
    from media_studio.cli.video import register_video_parser

    for hook in (
        register_project_parser,
        register_assets_parser,
        register_video_parser,
        register_audio_parser,
        register_design_parser,
        register_motion_parser,
        register_gen_parser,
        register_promo_parser,
        register_three_d_parser,
        register_triage_see_parser,
    ):
        assert callable(hook)


def test_all_subcommands_parse_help() -> None:
    """Verify build_parser constructs parsers for all commands including placeholders."""
    parser = build_parser()
    subcommands = [
        ["doctor", "--help"],
        ["project", "init", "--help"],
        ["project", "validate", "--help"],
        ["assets", "stage", "--help"],
        ["assets", "index", "--help"],
        ["assets", "list", "--help"],
        ["assets", "search", "--help"],
        ["assets", "pull", "--help"],
        ["video", "render", "--help"],
        ["video", "handoff", "--help"],
        ["video", "qc", "--help"],
        ["video", "composite", "--help"],
        ["video", "ingest", "--help"],
        ["video", "remove-bg", "--help"],
        ["video", "parallax", "--help"],
        ["video", "scenes", "--help"],
        ["video", "speed-ramp", "--help"],
        ["video", "build", "--help"],
        ["video", "proxy", "--help"],
        ["video", "impact-frame", "--help"],
        ["video", "stills", "--help"],
        ["video", "shots", "mine", "--help"],
        ["video", "cmd", "--help"],
        ["video", "place", "--help"],
        ["video", "trim", "--help"],
        ["video", "inspect", "--help"],
        ["video", "open", "--help"],
        ["video", "snapshot", "--help"],
        ["video", "diff", "--help"],
        ["video", "transcript", "--help"],
        ["video", "captions", "--help"],
        ["video", "remove-fillers", "--help"],
        ["video", "cut-by-transcript", "--help"],
        ["video", "lut", "search", "--help"],
        ["video", "lut", "preview", "--help"],
        ["video", "lut", "apply", "--help"],
        ["video", "edl", "generate", "--help"],
        ["video", "edl", "render", "--help"],
        ["video", "edl", "preview", "--help"],
        ["video", "edl", "sync-check", "--help"],
        ["video", "edl", "check-cuts", "--help"],
        ["video", "beat-sync", "--help"],
        ["video", "beats", "--help"],
        ["video", "beatfx", "--help"],
        ["video", "beatramp", "--help"],
        ["library", "list", "--help"],
        ["library", "search", "--help"],
        ["library", "inspect", "--help"],
        ["library", "register", "--help"],
        ["library", "forget", "--help"],
        ["acquire", "search", "--help"],
        ["3d", "inspect", "--help"],
        ["3d", "convert", "--help"],
        ["3d", "material", "--help"],
        ["3d", "turntable", "--help"],
        ["3d", "modify", "--help"],
        ["3d", "image-to-mesh", "--help"],
        ["3d", "from-image", "--help"],
        ["design", "render", "--help"],
        ["design", "svg", "build", "--help"],
        ["design", "svg", "trace", "--help"],
        ["design", "svg", "compare", "--help"],
        ["design", "logo", "--help"],
        ["image", "remove-bg", "--help"],
        ["image", "extract-signature", "--help"],
        ["fonts", "search", "--help"],
        ["fonts", "specimen", "--help"],
        ["presets", "list", "--help"],
        ["presets", "search", "--help"],
        ["presets", "show", "--help"],
        ["triage", "index", "--help"],
        ["triage", "sheets", "--help"],
        ["triage", "decide", "--help"],
        ["triage", "status", "--help"],
        ["motion", "new", "--help"],
        ["motion", "render", "--help"],
        ["motion", "title", "--help"],
        ["motion", "lyrics", "--help"],
        ["motion", "lower-third", "--help"],
        ["motion", "inspect", "--help"],
        ["see", "--help"],
        ["audio", "--help"],
        ["gen", "--help"],
        ["promo", "--help"],
    ]

    for args in subcommands:
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(args)
        assert exc_info.value.code == 0, f"failed parsing {args}"


def test_exact_flag_parity_samples() -> None:
    """Verify critical existing CLI argument signatures match original cli.py."""
    parser = build_parser()

    ns_edl = parser.parse_args(["video", "edl", "preview", "plan.json", "--cut", "c1"])
    assert ns_edl.cut == "c1"

    ns_lut_apply = parser.parse_args(["video", "lut", "apply", "clip.mp4", "--lut", "warm"])
    assert ns_lut_apply.source == "clip.mp4"
    assert ns_lut_apply.lut == "warm"

    ns_lut_preview = parser.parse_args(
        ["video", "lut", "preview", "warm", "shot.mp4", "--frame", "12"]
    )
    assert ns_lut_preview.frame == 12


def test_monkeypatchable_symbols_reexported() -> None:
    """Verify symbols monkeypatched by existing tests exist on media_studio.cli."""
    expected = (
        "path_order_problem",
        "collect_toolchain",
        "FilmCraftRunner",
        "load_project",
        "generate_proxy",
        "probe",
        "extract_stills",
        "stage_asset",
        "run_search",
        "apply_speed_ramp",
        "fetch_material",
    )
    for name in expected:
        assert hasattr(cli_module, name), f"missing re-export: {name}"


def test_cli_promo_render_accepts_fps_flag() -> None:
    """`ms promo render` must accept --fps (default 30, supports 60)."""
    parser = build_parser()
    ns = parser.parse_args(["promo", "render", "proj", "--auto", "--fps", "60"])
    assert ns.fps == 60
    ns_default = parser.parse_args(["promo", "render", "proj"])
    assert ns_default.fps == 30


def test_cli_module_execution_entrypoint() -> None:
    """Verify `python -m media_studio.cli --help` executes cleanly."""
    proc = subprocess.run(
        [sys.executable, "-m", "media_studio.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "usage: ms" in proc.stdout


def test_cli_submodule_line_counts() -> None:
    """Assert all submodules under src/media_studio/cli/ are strictly under 600 lines."""
    cli_dir = Path("src/media_studio/cli")
    assert cli_dir.is_dir(), "src/media_studio/cli directory must exist"

    py_files = list(cli_dir.glob("*.py"))
    assert py_files, "No python files found in src/media_studio/cli"
    for path in py_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) < 600, f"{path} has {len(lines)} lines (must be < 600)"
