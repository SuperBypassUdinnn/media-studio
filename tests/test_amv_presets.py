"""Tests for the EffectCraft AMV motion & typography presets (Task 5).

Covers the AMV_MOTION_PRESETS registry, the render_amv_preset renderer, the
unknown-preset error path, and the end-to-end AMV Elite chain:
impact frame -> AMV title preset -> 2.5D parallax composite -> ms see contact sheet.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from media_studio.effectcraft import (
    AMV_MOTION_PRESETS,
    EffectCraftError,
    MotionResult,
    render_amv_preset,
)
from media_studio.impact import generate_impact_frames
from media_studio.parallax import compose_parallax

AMV_PRESET_NAMES = ("amv-title-cyber", "amv-hud-accent", "amv-drop-shutter")


def _run_ffmpeg(arguments: list[str]) -> None:
    """Run ffmpeg, raising on failure (fixture generation helper)."""

    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _video(destination: Path, duration: float = 5.0, fps: int = 60) -> Path:
    """A synthetic 1920x1080 source clip for impact-frame generation."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1920x1080:rate={fps}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ]
    )
    return destination


def _alpha_subject(destination: Path, duration: float = 3.0) -> Path:
    """An RGBA subject cutout (white rectangle on transparent) for parallax."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=black@0.0:size=1920x1080:rate=60:duration={duration},format=rgba",
            "-vf",
            "drawbox=x=840:y=440:w=240:h=200:color=white:t=fill",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p12le",
            str(destination),
        ]
    )
    return destination


def _background_plate(destination: Path, duration: float = 3.0) -> Path:
    """A synthetic background plate for the parallax composite."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1920x1080:rate=60:duration={duration}",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p12le",
            str(destination),
        ]
    )
    return destination


def _probe_video(path: Path) -> str:
    """ffprobe one-line CSV of codec, width, height and frame rate."""

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_amv_presets_defined():
    """All three AMV motion presets are registered."""

    for name in AMV_PRESET_NAMES:
        assert name in AMV_MOTION_PRESETS


def test_amv_preset_spec_generation():
    """The cyber preset carries HD 60fps geometry and a glow layer."""

    cyber_preset = AMV_MOTION_PRESETS["amv-title-cyber"]
    assert cyber_preset["fps"] == 60.0
    assert cyber_preset["width"] == 1920
    assert cyber_preset["height"] == 1080
    assert any(layer.get("params", {}).get("glow") for layer in cyber_preset["layers"])


def test_all_amv_presets_use_hd_60fps_geometry():
    """Every AMV preset targets 1920x1080 at 60 fps."""

    for name in AMV_PRESET_NAMES:
        preset = AMV_MOTION_PRESETS[name]
        assert preset["width"] == 1920, name
        assert preset["height"] == 1080, name
        assert preset["fps"] == 60.0, name


def test_unknown_amv_preset_raises(tmp_path: Path):
    """An unknown preset name raises EffectCraftError."""

    with pytest.raises(EffectCraftError, match="unknown AMV motion preset"):
        render_amv_preset(
            preset_name="unknown-preset",
            output=tmp_path / "out.mov",
            text="TEST",
        )


def test_render_amv_preset_injects_text_and_geometry(tmp_path, monkeypatch):
    """render_amv_preset injects text/duration/fps/width/height into the spec."""

    captured: list[tuple] = []

    def fake_render(
        project_path: Path,
        output: Path,
        *,
        fps: float,
        transparent: bool,
    ) -> MotionResult:
        captured.append((project_path, output, fps, transparent))
        output.write_bytes(b"fake media")
        return MotionResult(
            output=output,
            ecp_path=project_path,
            report_path=output.with_suffix(".json"),
            report={"ok": True},
        )

    monkeypatch.setattr(
        "media_studio.effectcraft.check_installation", lambda: Path("effectcraft-cli")
    )
    monkeypatch.setattr("media_studio.effectcraft.render", fake_render)

    output = tmp_path / "amv-title-cyber.mov"
    result = render_amv_preset(
        "amv-title-cyber",
        output,
        text="CYBER BEAT",
        duration=2.5,
        fps=60.0,
        width=1920,
        height=1080,
    )

    assert result.ok
    assert captured
    assert captured[0][2] == 60.0
    assert captured[0][3] is True

    spec = json.loads((tmp_path / "amv-title-cyber.ecp").read_text(encoding="utf-8"))
    assert spec["composition"]["width"] == 1920
    assert spec["composition"]["height"] == 1080
    assert spec["composition"]["fps"] == 60.0
    assert spec["composition"]["duration"] == 2.5
    title_layer = next(layer for layer in spec["layers"] if layer.get("name") == "title")
    assert title_layer["params"]["text"] == "CYBER BEAT"


def test_render_amv_preset_spared_hud_crosshairs(tmp_path, monkeypatch):
    """--text only updates the title layer; HUD crosshair layers keep "[ + ]"."""

    captured: list[dict] = []

    def fake_render(
        project_path: Path,
        output: Path,
        *,
        fps: float,
        transparent: bool,
    ) -> MotionResult:
        captured.append(project_path)
        output.write_bytes(b"fake media")
        return MotionResult(
            output=output,
            ecp_path=project_path,
            report_path=output.with_suffix(".json"),
            report={"ok": True},
        )

    monkeypatch.setattr(
        "media_studio.effectcraft.check_installation", lambda: Path("effectcraft-cli")
    )
    monkeypatch.setattr("media_studio.effectcraft.render", fake_render)

    output = tmp_path / "amv-hud-accent.mov"
    result = render_amv_preset(
        "amv-hud-accent",
        output,
        text="HUD",
        duration=2.0,
        fps=60.0,
        width=1920,
        height=1080,
    )

    assert result.ok

    spec = json.loads((tmp_path / "amv-hud-accent.ecp").read_text(encoding="utf-8"))
    text_layers = [layer for layer in spec["layers"] if layer.get("type") == "text"]
    # The HUD preset has no layer named "title", so no text is injected.
    assert all(layer["params"].get("text") == "[ + ]" for layer in text_layers)


@pytest.mark.skipif(
    shutil.which("effectcraft-cli") is None and shutil.which("effectcraft") is None,
    reason="effectcraft (effectcraft-cli or effectcraft) not installed",
)
def test_amv_elite_e2e_chain(tmp_path: Path):
    """End-to-end: impact frame -> AMV title preset -> parallax -> ms see sheet."""

    # 1. Impact frame at the beat drop (invert mode).
    source = _video(tmp_path / "source.mp4")
    impact = generate_impact_frames(
        source=source,
        destination=tmp_path / "impact.mov",
        at="f120",
        frames=2,
        mode="invert",
        fps=60,
    )
    assert impact.destination.is_file()
    assert impact.frames == 2

    # 2. AMV title preset render (ProRes 4444 RGBA with alpha).
    title = render_amv_preset(
        "amv-title-cyber",
        tmp_path / "amv-title.mov",
        text="CYBER BEAT",
        duration=3.0,
    )
    assert title.ok
    assert title.output.is_file()

    # 3. 2.5D parallax composite: background + subject cutout + AMV title foreground.
    background = _background_plate(tmp_path / "background.mov")
    subject = _alpha_subject(tmp_path / "subject.mov")
    parallax = compose_parallax(
        background=background,
        subject=subject,
        foreground=title.output,
        destination=tmp_path / "shot.mov",
        duration=3.0,
        fps=60,
        resolution="1920x1080",
        motion="push_in",
    )
    assert parallax.destination.is_file()
    assert parallax.layers_composited == 3

    metrics = _probe_video(parallax.destination)
    assert "prores" in metrics
    assert "1920,1080" in metrics
    assert "60" in metrics

    # 4. ms see -> clean 12-tile contact sheet (4 columns x 3 rows).
    sheet = tmp_path / "contact.see.png"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "media_studio.cli",
            "see",
            str(parallax.destination),
            "--out",
            str(sheet),
            "--columns",
            "4",
            "--rows",
            "3",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert sheet.is_file() and sheet.stat().st_size > 0
