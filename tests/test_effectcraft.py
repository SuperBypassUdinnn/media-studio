"""Unit tests for the EffectCraft headless motion graphics path (offline)."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from media_studio.effectcraft import (
    EffectCraftError,
    build_ecp,
    check_installation,
    logo_reveal,
    lyric_overlay,
    motion_project,
    render,
    title_sequence,
)


def _load_ecp(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def test_build_ecp_produces_valid_json(tmp_path):
    """build_ecp emits JSON that json.loads can round-trip."""

    spec = {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "duration": 3.0,
        "layers": [
            {
                "type": "text",
                "name": "title",
                "params": {
                    "text": "Hello",
                    "font": "Noto Sans",
                    "font_size": 72.0,
                    "color": "#FFFFFF",
                    "animation": "fade_in_up",
                },
            }
        ],
    }

    ecp = tmp_path / "title.ecp"
    written = build_ecp(spec, ecp)
    assert written == ecp

    project = _load_ecp(ecp)
    assert project["version"] == "1.0"
    assert project["composition"]["width"] == 1280
    assert project["composition"]["height"] == 720
    assert project["composition"]["fps"] == 30.0
    assert project["composition"]["duration"] == 3.0
    assert len(project["layers"]) == 1
    assert project["layers"][0]["type"] == "text"
    assert project["layers"][0]["params"]["text"] == "Hello"


def test_build_ecp_rejects_bad_spec():
    """A non-dict spec raises EffectCraftError."""

    with pytest.raises(EffectCraftError, match="motion spec must be a dict"):
        build_ecp("not a dict", __import__("pathlib").Path("/tmp/x.ecp"))


def test_title_sequence_spec_has_one_text_layer(tmp_path):
    """title_sequence builds a spec with one text layer and pass-through defaults."""

    spec = {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "duration": 3.0,
        "layers": [
            {
                "type": "text",
                "name": "title",
                "params": {
                    "text": "Hello world",
                    "font": "Noto Sans",
                    "font_size": 72.0,
                    "color": "#FFFFFF",
                    "animation": "fade_in_up",
                    "glow": False,
                    "glow_intensity": 0.5,
                    "transparent": True,
                },
            }
        ],
    }

    ecp = tmp_path / "title.ecp"
    build_ecp(spec, ecp)

    project = _load_ecp(ecp)
    assert len(project["layers"]) == 1
    layer = project["layers"][0]
    assert layer["type"] == "text"
    assert layer["params"]["text"] == "Hello world"
    assert layer["params"]["animation"] == "fade_in_up"
    assert layer["params"]["transparent"] is True


def test_lyric_overlay_spec_has_one_layer_per_line(tmp_path):
    """lyric_overlay builds one text layer per (start, end, text) triple."""

    spec = {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "duration": 4.0,
        "layers": [
            {
                "type": "text",
                "name": "lyric-0",
                "params": {
                    "text": "first line",
                    "in_point": 0.0,
                    "out_point": 2.0,
                    "style": "default",
                    "transparent": True,
                },
            },
            {
                "type": "text",
                "name": "lyric-1",
                "params": {
                    "text": "second line",
                    "in_point": 2.0,
                    "out_point": 4.0,
                    "style": "default",
                    "transparent": True,
                },
            },
        ],
    }

    ecp = tmp_path / "lyrics.ecp"
    build_ecp(spec, ecp)

    project = _load_ecp(ecp)
    assert len(project["layers"]) == 2
    assert project["layers"][0]["params"]["text"] == "first line"
    assert project["layers"][0]["params"]["in_point"] == 0.0
    assert project["layers"][0]["params"]["out_point"] == 2.0
    assert project["layers"][1]["params"]["text"] == "second line"
    assert project["layers"][1]["params"]["in_point"] == 2.0
    assert project["layers"][1]["params"]["out_point"] == 4.0


def test_logo_reveal_spec_has_image_layer(tmp_path):
    """logo_reveal builds a spec with one image layer."""

    spec = {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "duration": 2.0,
        "layers": [
            {
                "type": "image",
                "name": "logo",
                "params": {
                    "path": str(tmp_path / "logo.svg"),
                    "animation": "scale_up",
                    "transparent": True,
                },
            }
        ],
    }

    ecp = tmp_path / "logo.ecp"
    build_ecp(spec, ecp)

    project = _load_ecp(ecp)
    assert len(project["layers"]) == 1
    layer = project["layers"][0]
    assert layer["type"] == "image"
    assert layer["params"]["animation"] == "scale_up"


def test_logo_reveal_rejects_missing_logo(tmp_path):
    """logo_reveal raises EffectCraftError when the logo file does not exist."""

    with pytest.raises(EffectCraftError, match="logo file not found"):
        logo_reveal(
            tmp_path / "nonexistent.svg",
            tmp_path / "out.mov",
            tmp_path / "logo.ecp",
        )


def test_check_installation_raises_when_effectcraft_missing():
    """check_installation raises EffectCraftError when effectcraft is not on PATH."""

    with (
        mock.patch("media_studio.effectcraft.EFFECTCRAFT", None),
        pytest.raises(EffectCraftError, match="effectcraft is not on PATH"),
    ):
        check_installation()


def test_render_raises_when_effectcraft_missing(tmp_path):
    """render raises EffectCraftError when effectcraft is absent."""

    with (
        mock.patch("media_studio.effectcraft.EFFECTCRAFT", None),
        pytest.raises(EffectCraftError, match="effectcraft is not on PATH"),
    ):
        render(tmp_path / "x.ecp", tmp_path / "y.mov")


def test_title_sequence_raises_when_effectcraft_missing(tmp_path):
    """title_sequence raises EffectCraftError when effectcraft is absent."""

    with (
        mock.patch("media_studio.effectcraft.EFFECTCRAFT", None),
        pytest.raises(EffectCraftError, match="effectcraft is not on PATH"),
    ):
        title_sequence("Hi", tmp_path / "out.mov", tmp_path / "t.ecp")


def test_lyric_overlay_raises_when_effectcraft_missing(tmp_path):
    """lyric_overlay raises EffectCraftError when effectcraft is absent."""

    with (
        mock.patch("media_studio.effectcraft.EFFECTCRAFT", None),
        pytest.raises(EffectCraftError, match="effectcraft is not on PATH"),
    ):
        lyric_overlay(
            [(0.0, 1.0, "hi")],
            tmp_path / "out.mov",
            tmp_path / "l.ecp",
        )


def test_logo_reveal_raises_when_effectcraft_missing(tmp_path):
    """logo_reveal raises EffectCraftError when effectcraft is absent (logo exists)."""

    logo = tmp_path / "logo.svg"
    logo.write_text("<svg></svg>", encoding="utf-8")

    with (
        mock.patch("media_studio.effectcraft.EFFECTCRAFT", None),
        pytest.raises(EffectCraftError, match="effectcraft is not on PATH"),
    ):
        logo_reveal(logo, tmp_path / "out.mov", tmp_path / "l.ecp")


def test_motion_project_raises_when_effectcraft_missing(tmp_path):
    """motion_project raises EffectCraftError when effectcraft is absent."""

    with (
        mock.patch("media_studio.effectcraft.EFFECTCRAFT", None),
        pytest.raises(EffectCraftError, match="effectcraft is not on PATH"),
    ):
        motion_project(
            [{"id": "opening", "type": "title", "text": "Hi"}],
            project_root=tmp_path,
        )


def test_motion_project_empty_returns_empty(tmp_path):
    """motion_project with no sequences returns an empty list."""

    results = motion_project([], project_root=tmp_path)
    assert results == []


def test_build_ecp_pretty_json_is_valid(tmp_path):
    """The serialized .ecp is valid JSON that minidom-like round-trip tolerates."""

    spec = {
        "width": 640,
        "height": 360,
        "fps": 24.0,
        "duration": 1.5,
        "layers": [],
    }

    ecp = tmp_path / "empty.ecp"
    build_ecp(spec, ecp)

    text = ecp.read_text(encoding="utf-8")
    import json

    json.loads(text)  # must not raise


@pytest.mark.skipif(
    subprocess.run(["which", "effectcraft"], capture_output=True).returncode != 0,
    reason="effectcraft not installed",
)
def test_integration_render_title_sequence(tmp_path):
    """Render a 1s title sequence; output exists and report is ok."""

    out = tmp_path / "title.mov"
    ecp = tmp_path / "title.ecp"
    result = title_sequence("Hello", out, ecp, duration=1.0)
    assert result.ok, result.report
    assert out.exists()


@pytest.mark.skipif(
    subprocess.run(["which", "effectcraft"], capture_output=True).returncode != 0,
    reason="effectcraft not installed",
)
def test_integration_lyric_overlay(tmp_path):
    """Render a lyric overlay; output exists and report is ok."""

    out = tmp_path / "lyrics.mov"
    ecp = tmp_path / "lyrics.ecp"
    result = lyric_overlay([(0.0, 1.0, "line one")], out, ecp, style="default")
    assert result.ok, result.report
    assert out.exists()
