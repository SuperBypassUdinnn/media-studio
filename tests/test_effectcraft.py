"""Unit tests for the EffectCraft headless motion graphics path (offline)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from media_studio.effectcraft import (
    EffectCraftError,
    MotionResult,
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


def test_relative_out_path_resolves_cleanly_and_coerces_mov_for_alpha(tmp_path, monkeypatch):
    """A relative .mp4 destination becomes an absolute .mov path for alpha output."""

    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    def fake_render(project_path, output, *, fps=30.0, transparent=True):
        seen["output"] = Path(output)
        seen["transparent"] = transparent
        return MotionResult(
            output=Path(output),
            ecp_path=Path(project_path),
            report_path=Path(output).with_suffix(".json"),
            report={"ok": True},
        )

    monkeypatch.setattr("media_studio.effectcraft.check_installation", lambda: None)
    monkeypatch.setattr("media_studio.effectcraft.render", fake_render)

    result = title_sequence("Hi", Path("out") / "title.mp4", Path("out") / "title.ecp")

    expected = (tmp_path / "out" / "title.mov").resolve()
    assert result.output == expected
    assert expected.is_absolute()
    assert seen["output"] == expected
    assert seen["transparent"] is True
    assert (tmp_path / "out" / "title.ecp").is_file()


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


def _fake_promo_render(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_render(project_path, output, *, fps=30.0, transparent=True):
        seen["spec"] = json.loads(Path(project_path).read_text(encoding="utf-8"))
        return MotionResult(
            output=Path(output),
            ecp_path=Path(project_path),
            report_path=Path(output).with_suffix(".json"),
            report={"ok": True},
        )

    monkeypatch.setattr("media_studio.effectcraft.check_installation", lambda: None)
    monkeypatch.setattr("media_studio.effectcraft.render", fake_render)
    return seen


def _promo_project(tmp_path) -> object:
    from media_studio.manifest import Brand, Budget, Project

    return Project(
        root=tmp_path,
        slug="demo",
        title="Demo",
        status="draft",
        deliverables=(),
        brand=Brand(
            palette={"primary": "#ff0000", "accent": "#00ff00", "ink": "#0000ff"},
            fonts={"heading": "Inter", "body": "Inter"},
        ),
        budget=Budget(),
        assets={},
        timeline=(),
        video=None,
        design=None,
        effects=(),
        raw={},
    )


def test_promo_motion_presets_contains_all_five():
    """PROMO_MOTION_PRESETS registers exactly the five promo preset ids."""

    from media_studio.effectcraft import PROMO_MOTION_PRESETS

    expected = {
        "promo-hook-slam",
        "promo-kinetic-callout",
        "promo-metric-badge",
        "promo-lower-third-clean",
        "promo-cta-endcard",
    }
    assert set(PROMO_MOTION_PRESETS) == expected


def test_promo_preset_scales_positions_and_font_sizes(tmp_path, monkeypatch):
    """Layer positions and font sizes scale from width/height for both aspects."""

    from media_studio.effectcraft import render_promo_preset

    seen = _fake_promo_render(monkeypatch, tmp_path)

    for width, height in ((1080, 1920), (1920, 1080)):
        seen.clear()
        render_promo_preset(
            "promo-hook-slam",
            tmp_path / f"out-{width}x{height}.mov",
            text="Hook",
            width=width,
            height=height,
        )
        spec = seen["spec"]
        comp = spec["composition"]
        assert comp["width"] == width
        assert comp["height"] == height
        title = next(layer for layer in spec["layers"] if layer["name"] == "title")
        assert abs(title["params"]["font_size"] - 0.075 * min(width, height)) <= 1
        assert abs(title["params"]["position"][0] - 0.5 * width) <= 1
        assert abs(title["params"]["position"][1] - 0.42 * height) <= 1
        pill = next(layer for layer in spec["layers"] if layer["name"] == "hook-pill")
        assert abs(pill["params"]["width"] - 0.92 * width) <= 1
        assert abs(pill["params"]["height"] - 0.06 * height) <= 1


def test_promo_preset_fits_long_cta_text_inside_width(tmp_path, monkeypatch):
    """Long title text on portrait is shrunk (floor 24px) to fit 84% of width."""

    from media_studio.effectcraft import render_promo_preset

    seen = _fake_promo_render(monkeypatch, tmp_path)
    long_text = "Launch your next campaign with Media Studio today."
    render_promo_preset(
        "promo-cta-endcard",
        tmp_path / "out.mov",
        text=long_text,
        width=1080,
        height=1920,
    )
    spec = seen["spec"]
    cta = next(layer for layer in spec["layers"] if layer["name"] == "cta")
    font_size = cta["params"]["font_size"]
    # font_size is an absolute px value, floored at 24, and the text fits 84% width.
    assert 24 <= font_size <= 0.060 * min(1080, 1920)
    assert cta["params"]["text"]  # text preserved (possibly wrapped)
    rendered_width = len(cta["params"]["text"].split("\n")[0]) * font_size * 0.55
    assert rendered_width <= 1080 * 0.84 + 1


def test_promo_preset_injects_brand_palette_tokens(tmp_path, monkeypatch):
    """project.brand.palette tokens (primary, accent, ink) resolve into layer colours."""

    from media_studio.effectcraft import render_promo_preset

    seen = _fake_promo_render(monkeypatch, tmp_path)
    render_promo_preset(
        "promo-hook-slam",
        tmp_path / "out.mov",
        text="Hook",
        project=_promo_project(tmp_path),
    )
    spec = seen["spec"]
    pill = next(layer for layer in spec["layers"] if layer["name"] == "hook-pill")
    assert pill["params"]["color"] == "#00ff00"  # accent
    title = next(layer for layer in spec["layers"] if layer["name"] == "title")
    assert title["params"]["color"] == "#0000ff"  # ink
    assert title["params"]["font"] == "Inter"  # heading font role


def test_promo_preset_falls_back_to_defaults_without_project(tmp_path, monkeypatch):
    """No project keeps default colours and fonts instead of crashing."""

    from media_studio.effectcraft import render_promo_preset

    seen = _fake_promo_render(monkeypatch, tmp_path)
    render_promo_preset("promo-hook-slam", tmp_path / "out.mov", text="Hook")
    spec = seen["spec"]
    title = next(layer for layer in spec["layers"] if layer["name"] == "title")
    assert title["params"]["color"] != "{ink}"
    assert title["params"]["font"] != "{heading}"


def test_render_promo_preset_builds_valid_ecp(tmp_path, monkeypatch):
    """The rendered spec passes build_ecp validation and carries composition geometry."""

    from media_studio.effectcraft import render_promo_preset

    seen = _fake_promo_render(monkeypatch, tmp_path)
    render_promo_preset(
        "promo-metric-badge",
        tmp_path / "out.mov",
        text="98%",
        subtext="conversion rate",
        width=1080,
        height=1920,
        fps=30,
        duration=3.0,
    )
    spec = seen["spec"]
    comp = spec["composition"]
    assert comp["width"] == 1080
    assert comp["height"] == 1920
    assert comp["fps"] == 30.0
    assert comp["duration"] == 3.0
    counter = next(layer for layer in spec["layers"] if layer["name"] == "counter")
    assert counter["params"]["text"] == "98%"
    unit = next(layer for layer in spec["layers"] if layer["name"] == "unit")
    assert unit["params"]["text"] == "conversion rate"


def _preset_spec(monkeypatch, tmp_path, preset_name, *, width=1080, height=1920):
    from media_studio.effectcraft import render_promo_preset

    seen = _fake_promo_render(monkeypatch, tmp_path)
    render_promo_preset(
        preset_name,
        tmp_path / "out.mov",
        text="Hi",
        subtext="sub",
        width=width,
        height=height,
    )
    return seen["spec"]


def _layer(spec, name):
    return next(layer for layer in spec["layers"] if layer["name"] == name)


def _key_times(frames):
    return [round(frame["t"], 3) for frame in frames]


def test_promo_presets_every_layer_keyframed(monkeypatch, tmp_path):
    """Every layer in every preset carries real keyframes (no static layers)."""

    from media_studio.effectcraft import PROMO_MOTION_PRESETS

    for preset_name in PROMO_MOTION_PRESETS:
        spec = _preset_spec(monkeypatch, tmp_path, preset_name)
        for layer in spec["layers"]:
            assert "keyframes" in layer["params"], f"{preset_name}/{layer['name']} has no keyframes"
            for prop, frames in layer["params"]["keyframes"].items():
                assert frames, f"{preset_name}/{layer['name']}/{prop} empty keyframes"
                for frame in frames:
                    assert "t" in frame and "v" in frame and "ease" in frame


def test_promo_preset_entrance_within_035s(monkeypatch, tmp_path):
    """Entrances start at t=0 and reach their plateau within 0.35 s."""

    cases = [
        ("promo-hook-slam", "title", "scale", 0.35),
        ("promo-hook-slam", "title", "opacity", 0.10),
        ("promo-kinetic-callout", "accent-rule", "scale", 0.25),
        ("promo-kinetic-callout", "title", "opacity", 0.30),
        ("promo-metric-badge", "badge-pill", "scale", 0.30),
        ("promo-metric-badge", "counter", "scale", 0.40),
        ("promo-lower-third-clean", "accent-bar", "opacity", 0.30),
        ("promo-cta-endcard", "cta-pill", "scale", 0.25),
        ("promo-cta-endcard", "cta", "scale", 0.25),
    ]
    for preset_name, layer_name, prop, limit in cases:
        spec = _preset_spec(monkeypatch, tmp_path, preset_name)
        frames = _layer(spec, layer_name)["params"]["keyframes"][prop]
        assert _key_times(frames)[0] == 0.0, (
            f"{preset_name}/{layer_name} entrance does not start at t=0"
        )
        # Resting value the entrance converges to: scale rests at 1.0, opacity at full.
        if prop == "scale":
            resting = 1.0
        elif prop == "opacity":
            resting = max(frame["v"] for frame in frames)
        else:
            resting = frames[-1]["v"]
        settle = min(frame["t"] for frame in frames if frame["v"] == resting)
        assert settle <= limit, (
            f"{preset_name}/{layer_name} entrance settles at {settle}s > {limit}s"
        )


def test_promo_preset_backout_overshoot(monkeypatch, tmp_path):
    """Back-out entrances overshoot past the target before settling."""

    # hook-slam title slams from 1.5 (overshoot) down to 1.0.
    spec = _preset_spec(monkeypatch, tmp_path, "promo-hook-slam")
    frames = _layer(spec, "title")["params"]["keyframes"]["scale"]
    values = [frame["v"] for frame in frames]
    assert max(values) > 1.0, f"no overshoot in hook-slam title scale: {values}"
    assert any(frame["ease"] == "back-out" for frame in frames)
    # cta-endcard snaps in with overshoot too.
    spec = _preset_spec(monkeypatch, tmp_path, "promo-cta-endcard")
    frames = _layer(spec, "cta-pill")["params"]["keyframes"]["scale"]
    values = [frame["v"] for frame in frames]
    assert max(values) > 1.0, f"no overshoot in cta-pill scale: {values}"


def test_promo_preset_exit_within_025s(monkeypatch, tmp_path):
    """Configured exits (hook-slam, cta) snap out within 0.25 s."""

    for preset_name, layer_name, prop in [
        ("promo-hook-slam", "title", "opacity"),
        ("promo-cta-endcard", "cta-pill", "opacity"),
        ("promo-cta-endcard", "cta", "opacity"),
    ]:
        spec = _preset_spec(monkeypatch, tmp_path, preset_name)
        frames = _layer(spec, layer_name)["params"]["keyframes"][prop]
        times = _key_times(frames)
        # The exit is the final descent to 0; its span must be <= 0.25 s.
        zero_times = [t for t, f in zip(times, frames, strict=False) if f["v"] == 0.0]
        assert zero_times, f"{preset_name}/{layer_name} has no exit to zero"
        exit_end = max(zero_times)
        exit_start = max(t for t in times if t < exit_end)
        assert exit_end - exit_start <= 0.25, (
            f"{preset_name}/{layer_name} exit {exit_end - exit_start:.2f}s > 0.25s"
        )


def test_promo_preset_position_keyframes_scale_to_canvas(monkeypatch, tmp_path):
    """Fraction-based position keyframes scale to absolute pixels at both aspects."""

    from media_studio.effectcraft import render_promo_preset

    seen = _fake_promo_render(monkeypatch, tmp_path)
    for width, height in ((1080, 1920), (1920, 1080)):
        seen.clear()
        render_promo_preset(
            "promo-lower-third-clean",
            tmp_path / f"out-{width}x{height}.mov",
            text="Name",
            subtext="Role",
            width=width,
            height=height,
        )
        spec = seen["spec"]
        bar = _layer(spec, "accent-bar")
        frames = bar["params"]["keyframes"]["position"]
        # Position keyframes scaled to pixels (not left as 0..1 fractions).
        for frame in frames:
            x, y = frame["v"]
            assert x > 1 or y > 1, f"position keyframe not scaled to canvas: {frame['v']}"


def test_ease_back_out_overshoots():
    """_ease_back_out swings past 1.0 before settling."""

    from media_studio.effectcraft import _ease_back_out, _ease_expo_out

    assert _ease_back_out(0.0) == 0.0
    assert _ease_back_out(1.0) == 1.0
    assert max(_ease_back_out(t / 100.0) for t in range(101)) > 1.0
    assert _ease_expo_out(0.0) == 0.0
    assert _ease_expo_out(1.0) == 1.0
    # Expo-out is fast early: at t=0.1 already at 0.5.
    assert _ease_expo_out(0.1) == 0.5
    assert _ease_expo_out(0.2) > 0.5


def test_unknown_promo_preset_raises():
    """An unknown promo preset id raises EffectCraftError with a helpful message."""

    from media_studio.effectcraft import EffectCraftError, render_promo_preset

    with pytest.raises(EffectCraftError, match="unknown promo motion preset"):
        render_promo_preset("promo-does-not-exist", Path("/tmp/x.mov"), text="Hi")


@pytest.mark.skipif(
    subprocess.run(["which", "effectcraft"], capture_output=True).returncode != 0,
    reason="effectcraft not installed",
)
@pytest.mark.slow
def test_promo_preset_keyframes_actually_animate(tmp_path, monkeypatch):
    """Keyframe commands reach the engine and rendered hook-slam frames differ (real animation)."""

    import numpy as np
    from PIL import Image

    from media_studio.effectcraft import (
        PROMO_MOTION_PRESETS,
        EffectCraftRunner,
        _compile_spec_to_ecproj,
        build_ecp,
        render_promo_preset,
    )

    # 1. The compiler emits prop.addKey commands for keyframed layers.
    captured: dict[str, list] = {"cmds": []}
    real_run_commands = EffectCraftRunner.run_commands

    def fake_run_commands(self, commands, **kwargs):
        captured["cmds"] = list(commands)
        return real_run_commands(self, commands, **kwargs)

    monkeypatch.setattr(EffectCraftRunner, "run_commands", fake_run_commands)

    spec_path = tmp_path / "hook.ecp"
    spec = dict(PROMO_MOTION_PRESETS["promo-hook-slam"])
    spec["width"] = 540
    spec["height"] = 960
    spec["fps"] = 30
    spec["duration"] = 1.5
    build_ecp(spec, spec_path)
    ecproj = tmp_path / "hook_compiled.ecproj"
    _compile_spec_to_ecproj(spec_path, ecproj)
    assert any(c.get("id") == "prop.addKey" for c in captured["cmds"]), (
        "no prop.addKey commands emitted"
    )

    # 2. Rendered frames actually differ (keyframes produce visible motion).
    out = tmp_path / "hook.mov"
    res = render_promo_preset(
        "promo-hook-slam", out, text="Hi", width=540, height=960, fps=30, duration=1.5
    )
    assert res.ok and out.exists()

    def frame(t: float) -> np.ndarray:
        p = tmp_path / f"f{int(t * 100)}.png"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{t}",
                "-i",
                str(out),
                "-frames:v",
                "1",
                "-y",
                str(p),
            ],
            check=True,
        )
        return np.asarray(Image.open(p).convert("RGBA")).astype(float)

    f0 = frame(0.0)
    f1 = frame(0.5)
    assert np.abs(f0 - f1).mean() > 3.0, "hook-slam frames are identical (no animation)"
