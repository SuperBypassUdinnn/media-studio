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


def test_motion_templates_match_design():
    """All five design-5.7 templates are registered."""
    from media_studio.effectcraft import MOTION_TEMPLATES

    assert MOTION_TEMPLATES == (
        "lower-third",
        "title-card",
        "logo-sting",
        "lyric-overlay",
        "explainer-beat",
    )


def test_scaffold_spec_injects_brand_tokens():
    """Palette colours and display font land on every scaffold layer."""
    from media_studio.effectcraft import scaffold_spec

    spec = scaffold_spec(
        "lower-third",
        width=1920,
        height=1080,
        fps=30.0,
        duration=4.0,
        palette={"text": "#fefce8", "accent": "#f97316"},
        fonts={"display": "Inter"},
        title="Alex Mercer",
        subtitle="AI Lead",
    )
    by_name = {layer["name"]: layer for layer in spec["layers"]}
    title = by_name["title"]["params"]
    assert title["text"] == "Alex Mercer"
    assert title["fill"] == "#fefce8"
    assert title["font"] == "Inter"
    assert by_name["accent-bar"]["params"]["color"] == "#f97316"
    # Composition geometry survives.
    assert (spec["width"], spec["height"], spec["fps"], spec["duration"]) == (1920, 1080, 30.0, 4.0)


def test_scaffold_spec_covers_every_template():
    """Every registered template yields a spec build_ecp accepts."""
    from media_studio.effectcraft import MOTION_TEMPLATES, scaffold_spec

    for template in MOTION_TEMPLATES:
        spec = scaffold_spec(
            template,
            width=1920,
            height=1080,
            fps=30.0,
            duration=2.5,
        )
        assert spec["layers"], template
        assert spec["duration"] == 2.5


def test_scaffold_spec_rejects_unknown_template():
    from media_studio.effectcraft import scaffold_spec

    with pytest.raises(EffectCraftError, match="unknown template"):
        scaffold_spec("star-wipe", width=1920, height=1080, fps=30.0, duration=2.0)


def test_new_composition_writes_scaffold(tmp_path):
    from media_studio.effectcraft import new_composition

    path = new_composition(
        tmp_path,
        "intro-card",
        "title-card",
        deliverable_resolution="1280x720",
        deliverable_fps=25.0,
        duration=3.0,
        title="Hello",
    )
    assert path == (tmp_path / "motion" / "intro-card.ecproj").resolve()
    data = _load_ecp(path)
    assert data["composition"] == {
        "width": 1280,
        "height": 720,
        "fps": 25.0,
        "duration": 3.0,
    }
    headline = next(layer for layer in data["layers"] if layer["name"] == "headline")
    assert headline["params"]["text"] == "Hello"


def test_new_composition_refuses_existing_without_force(tmp_path):
    from media_studio.effectcraft import new_composition

    first = new_composition(tmp_path, "dupe", "lyric-overlay")
    with pytest.raises(EffectCraftError, match="already exists"):
        new_composition(tmp_path, "dupe", "lyric-overlay")
    # --force overwrites in place (same path, still a valid spec).
    again = new_composition(tmp_path, "dupe", "lyric-overlay", force=True)
    assert again == first
    assert _load_ecp(again)["layers"]


def _fake_run_factory(call_results):
    """Build a subprocess.run replacement that replays scripted outcomes.

    call_results is a list of (match, response) where match is a predicate over
    the argv and response is (returncode, stdout, stderr) or a callable(run_args)
    returning that tuple; a response may also create the output file by listing
    its path under the special key "create".
    """
    import subprocess as _sp

    def fake_run(argv, capture_output=False, text=False, timeout=None):
        for matcher, (returncode, stdout, stderr, create) in call_results:
            if matcher(argv):
                if create is not None:
                    from pathlib import Path as _P

                    _P(create).write_bytes(b"fake media")
                return _sp.CompletedProcess(argv, returncode, stdout, stderr)
        raise AssertionError(f"unexpected subprocess call: {argv}")

    return fake_run


def test_render_comp_gpu_fallback_to_cpu(tmp_path, monkeypatch):
    """gpu=auto tries --gpu first, then retries without it (design 5.9)."""
    from media_studio.effectcraft import CompRenderResult, EffectCraftRunner

    project = tmp_path / "comp.ecproj"
    project.write_text("{}")
    output = tmp_path / "out.mov"
    calls: list[list[str]] = []

    def fake_run(argv, capture_output=False, text=False, timeout=None):
        calls.append(list(argv))
        if "--gpu" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "gpu device unavailable")
        output.write_bytes(b"fake media")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("media_studio.effectcraft.subprocess.run", fake_run)

    runner = EffectCraftRunner()
    result = runner.render_comp(project, output, channels="rgb", gpu="auto")

    assert isinstance(result, CompRenderResult)
    assert result.ok
    assert result.render_path == "cpu"
    assert result.gpu_requested is True
    assert len(calls) == 2
    assert calls[0][3] == "--gpu"  # global option precedes the subcommand
    assert "--gpu" not in calls[1]
    import json as _json

    report = _json.loads(result.report_path.read_text())
    assert report["renderPath"] == "cpu"
    assert report["gpuRequested"] is True
    assert report["ok"] is True


def test_render_comp_reports_metadata(tmp_path, monkeypatch):
    """Successful render reports duration, alpha coverage and render time."""
    from media_studio.effectcraft import EffectCraftRunner

    project = tmp_path / "comp.ecproj"
    project.write_text("{}")
    output = tmp_path / "out.mov"

    def fake_run(argv, capture_output=False, text=False, timeout=None):
        output.write_bytes(b"fake media")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("media_studio.effectcraft.subprocess.run", fake_run)

    runner = EffectCraftRunner()
    runner._comp_duration = lambda _p: 4.0  # type: ignore[method-assign]
    runner._alpha_coverage = lambda _p: 0.9706  # type: ignore[method-assign]

    result = runner.render_comp(project, output)  # rgba default

    assert result.render_path == "gpu"
    assert result.duration_seconds == 4.0
    assert result.alpha_coverage == 0.9706
    assert result.render_seconds >= 0.0
    import json as _json

    report = _json.loads(result.report_path.read_text())
    assert report["durationSeconds"] == 4.0
    assert report["alphaCoverage"] == 0.9706
    assert report["renderPath"] == "gpu"


def test_render_comp_invalid_gpu_mode(tmp_path):
    from media_studio.effectcraft import EffectCraftRunner

    runner = EffectCraftRunner()
    with pytest.raises(EffectCraftError, match="invalid gpu mode"):
        runner.render_comp(tmp_path / "missing.ecproj", tmp_path / "o.mov", gpu="sideways")


def test_render_comp_raises_when_all_attempts_fail(tmp_path, monkeypatch):
    from media_studio.effectcraft import EffectCraftRunner

    project = tmp_path / "comp.ecproj"
    project.write_text("{}")

    def fake_run(argv, capture_output=False, text=False, timeout=None):
        label = "gpu" if "--gpu" in argv else "cpu"
        return subprocess.CompletedProcess(argv, 1, "", f"{label} exploded")

    monkeypatch.setattr("media_studio.effectcraft.subprocess.run", fake_run)

    runner = EffectCraftRunner()
    with pytest.raises(EffectCraftError, match="cpu exploded"):
        runner.render_comp(project, tmp_path / "o.mov", channels="rgb")


def test_alpha_coverage_parses_signalstats(tmp_path, monkeypatch):
    """metadata=print emits `lavfi.signalstats.YAVG=<n>`; parse both = and :."""
    from media_studio.effectcraft import EffectCraftRunner

    media = tmp_path / "clip.mov"
    media.write_bytes(b"fake")

    def fake_run(argv, capture_output=False, text=False, timeout=None):
        stderr = "\n".join(
            [
                "[Parsed_metadata_2] lavfi.signalstats.YAVG=266.534",
                "[Parsed_metadata_2] lavfi.signalstats.YMAX=3773",
                "[Parsed_metadata_2] lavfi.signalstats.YAVG=188.67",
                "[Parsed_metadata_2] lavfi.signalstats.YMAX=3773",
            ]
        )
        return subprocess.CompletedProcess(argv, 0, "", stderr)

    monkeypatch.setattr("media_studio.effectcraft.subprocess.run", fake_run)

    runner = EffectCraftRunner()
    coverage = runner._alpha_coverage(media)

    assert coverage is not None
    assert abs(coverage - (266.534 / 3773 + 188.67 / 3773) / 2) < 0.0005
    assert 0.0 < coverage <= 1.0


def test_alpha_coverage_returns_none_on_ffmpeg_failure(tmp_path, monkeypatch):
    from media_studio.effectcraft import EffectCraftRunner

    media = tmp_path / "clip.mov"
    media.write_bytes(b"fake")
    monkeypatch.setattr(
        "media_studio.effectcraft.subprocess.run",
        lambda argv, capture_output=False, text=False, timeout=None: subprocess.CompletedProcess(
            argv, 1, "", "no such stream"
        ),
    )
    runner = EffectCraftRunner()
    assert runner._alpha_coverage(media) is None
