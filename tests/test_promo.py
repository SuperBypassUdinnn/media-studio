"""Tests for the dual-aspect promotional video orchestrator and CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from media_studio.cli import main
from media_studio.effectcraft import MotionResult
from media_studio.gen.base import GenResult
from media_studio.html_motion import HtmlMotionResult
from media_studio.manifest import load_project
from media_studio.promo import (
    PromoBeat,
    PromoError,
    PromoPlan,
    PromoRenderResult,
    check_layout_collisions,
    load_promo_plan,
    plan_promo,
    qc_promo,
    render_promo,
    resolve_layout_placement,
)
from media_studio.tts import TtsResult, WordTiming


def _make_promo_project(tmp_path: Path, *, with_assets: bool = False) -> Path:
    root = tmp_path / "demo-promo"
    for rel in (
        "assets/video",
        "assets/audio",
        "assets/music",
        "assets/design",
        "out/video",
        "out/promo",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)

    assets_map: dict[str, str] = {}
    if with_assets:
        product_png = root / "assets" / "design" / "product-cutout.png"
        _ffmpeg_test_png(product_png, 320, 320, "0x38bdf8")
        assets_map["product-cutout"] = "assets/design/product-cutout.png"

        logo_png = root / "assets" / "design" / "logo-main.png"
        _ffmpeg_test_png(logo_png, 256, 128, "0xf8fafc")
        assets_map["logo-main"] = "assets/design/logo-main.png"

    manifest = {
        "slug": "demo-promo",
        "title": "Demo Promo",
        "status": "draft",
        "deliverables": [
            {
                "id": "reel-9x16",
                "kind": "video",
                "container": "mp4",
                "resolution": "1080x1920",
                "fps": 30,
                "duration": 4.0,
                "loudness_lufs": -14.0,
            },
            {
                "id": "master-16x9",
                "kind": "video",
                "container": "mp4",
                "resolution": "1920x1080",
                "fps": 30,
                "duration": 4.0,
                "loudness_lufs": -14.0,
            },
        ],
        "brand": {
            "palette": {"primary": "#0F172A", "accent": "#38BDF8", "ink": "#F8FAFC"},
            "fonts": {"heading": "Noto Sans", "body": "Noto Sans"},
            "references": {
                "product": ["product-cutout"] if with_assets else ["product-hero"],
                "spokesperson": ["host-front"],
                "style_board": ["mood-01"],
            },
            "voice": {"engine": "espeak-ng", "voice_id": "en-US", "rate": "+0%"},
            "safe_zones": {
                "reel_9x16": {"top": 220, "bottom": 380, "left": 60, "right": 140},
            },
        },
        "assets": assets_map,
    }
    (root / "project.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def _ffmpeg_test_png(path: Path, width: int, height: int, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={width}x{height}:d=0.1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
    )


def _ffmpeg_test_mp4(path: Path, width: int, height: int, duration: float, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=30:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-af",
            "volume=-14dB",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(path),
        ],
        check=True,
    )


def _ffmpeg_test_wav(path: Path, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=330:sample_rate=48000:duration={duration}",
            "-af",
            "volume=-16dB",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )


def test_plan_promo_decomposes_beats_injects_references_and_saves(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan_path = root / "out" / "promo" / "promo-plan.json"

    script = (
        "Stop wasting hours editing promos by hand. "
        "Manual timelines break every time you switch from 16:9 to 9:16. "
        "Media Studio builds both deliverables from one deterministic pipeline. "
        "Teams ship 4x faster with broadcast loudness. "
        "Try Media Studio today."
    )
    plan = plan_promo(project, script=script, out_path=plan_path, target_duration=30.0)

    assert isinstance(plan, PromoPlan)
    assert plan.project_slug == "demo-promo"
    assert plan.aspects == ("9:16", "16:9")
    assert pytest.approx(plan.total_duration, abs=0.05) == 30.0
    kinds = [b.kind for b in plan.beats]
    assert kinds == ["hook", "problem", "demo", "proof", "cta"]
    assert all(isinstance(b, PromoBeat) and b.duration > 0 for b in plan.beats)
    assert "product-cutout" in plan.references
    assert "host-front" in plan.references
    assert "mood-01" in plan.references
    assert plan.estimated_gen_calls >= 1
    assert plan_path.is_file()

    reloaded = load_promo_plan(plan_path)
    assert reloaded.project_slug == plan.project_slug
    assert reloaded.beats == plan.beats
    assert reloaded.references == plan.references


def test_plan_promo_rejects_empty_script_and_bad_aspect(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)

    with pytest.raises(PromoError):
        plan_promo(project, script="   ")

    with pytest.raises(PromoError):
        plan_promo(project, script="Hello world.", aspects=("21:9",))


def test_render_promo_enforces_storyboard_gate_without_calling_gen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook line. Problem line. Demo line. Proof line. CTA line.",
        target_duration=10.0,
    )

    gen_called = False

    def _forbid_gen(*_args: object, **_kwargs: object) -> GenResult:
        nonlocal gen_called
        gen_called = True
        raise AssertionError("generate_asset must not be called when auto=False")

    monkeypatch.setattr("media_studio.promo.generate_asset", _forbid_gen)

    result = render_promo(project, plan, auto=False)
    assert isinstance(result, PromoRenderResult)
    assert result.gated is True
    assert result.storyboard is not None
    assert result.storyboard.is_file()
    assert result.deliverables == {}
    assert gen_called is False


def test_render_promo_guards_existing_deliverables_unless_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook. Problem. Demo. Proof. CTA.",
        target_duration=4.0,
    )
    existing = root / "out" / "video" / "reel-9x16.mp4"
    existing.write_bytes(b"already-here")

    monkeypatch.setattr("media_studio.promo.generate_asset", lambda *_a, **_kw: None)
    with pytest.raises(PromoError, match="already exists"):
        render_promo(project, plan, auto=True, force=False)


def test_render_promo_auto_assembles_dual_aspect_and_qc_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    def _fake_tts(
        text: str,
        *,
        output: Path,
        words_out: Path | None = None,
        **_kwargs: object,
    ) -> TtsResult:
        _ffmpeg_test_wav(output, 4.0)
        timings = [
            WordTiming("Hook", 0.1, 0.7),
            WordTiming("fast", 0.8, 1.4),
            WordTiming("Demo", 1.5, 2.4),
            WordTiming("CTA", 2.6, 3.6),
        ]
        wpath = words_out or output.with_suffix(".json")
        wpath.write_text(
            json.dumps([{"word": t.word, "start": t.start, "end": t.end} for t in timings]),
            encoding="utf-8",
        )
        sidecar = output.with_suffix(output.suffix + ".license")
        sidecar.write_text("SPDX-License-Identifier: CC0-1.0\n", encoding="utf-8")
        return TtsResult(
            audio_path=output,
            words_path=wpath,
            sidecar_path=sidecar,
            duration=4.0,
            engine="mock",
            voice="mock",
            words=tuple(timings),
        )

    def _fake_gen(request: object, *, output: Path, **_kwargs: object) -> GenResult:
        dur = float(getattr(request, "duration", 1.0))
        _ffmpeg_test_mp4(output, 1920, 1080, max(0.8, dur), "0x1e293b")
        return GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="mock-v1",
            seed=42,
            prompt=str(getattr(request, "prompt", "")),
            references=(),
            sha256="0" * 64,
            size_bytes=output.stat().st_size,
            duration=dur,
        )

    def _fake_html(
        _template: str,
        *,
        output: Path,
        width: int = 1920,
        height: int = 1080,
        duration: float = 1.0,
        **_kwargs: object,
    ) -> HtmlMotionResult:
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=width,
            height=height,
            fps=30,
            frames=max(1, round(duration * 30)),
            duration=duration,
            transparent=False,
        )

    def _fake_effectcraft(
        preset_name: str,
        output: Path,
        *,
        width: int = 1920,
        height: int = 1080,
        duration: float = 1.0,
        **_kwargs: object,
    ) -> MotionResult:
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True, "preset": preset_name},
        )

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_effectcraft)

    result = render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert result.gated is False
    assert set(result.deliverables) == {"reel-9x16", "master-16x9"}
    for path in result.deliverables.values():
        assert path.is_file()

    qc = qc_promo(project, deliverables=("reel-9x16", "master-16x9"), use_media_mcp=False)
    summary = {
        k: (v["ok"], v["duration_seconds"], v["loudness"], v["checks"])
        for k, v in qc["deliverables"].items()
    }
    assert qc["ok"] is True, summary
    assert qc["deliverables"]["reel-9x16"]["width"] == 1080
    assert qc["deliverables"]["reel-9x16"]["height"] == 1920
    assert qc["deliverables"]["master-16x9"]["width"] == 1920
    assert qc["deliverables"]["master-16x9"]["height"] == 1080
    assert (root / "out" / "promo" / "reel-9x16-sheet.png").is_file()
    assert (root / "out" / "promo" / "master-16x9-sheet.png").is_file()

    # Temp dirs under /tmp/opencode/promo-* must be cleaned up.
    leftovers = list(Path("/tmp/opencode").glob(f"promo-{os.getpid()}-*"))
    assert leftovers == []


def test_render_promo_passes_snappy_transition_duration_ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """render_promo passes ~180 ms duration_ms to apply_transition per beat."""
    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    captured: dict[str, object] = {}

    def _fake_tts(text, *, output, words_out=None, **_kwargs):
        _ffmpeg_test_wav(output, 4.0)
        wpath = words_out or output.with_suffix(".json")
        wpath.write_text(json.dumps([{"word": "Hook", "start": 0.1, "end": 0.7}]), encoding="utf-8")
        return TtsResult(
            audio_path=output,
            words_path=wpath,
            sidecar_path=output.with_suffix(output.suffix + ".license"),
            duration=4.0,
            engine="mock",
            voice="mock",
            words=(WordTiming("Hook", 0.1, 0.7),),
        )

    def _fake_gen(request, *, output, **_kwargs):
        _ffmpeg_test_mp4(output, 1920, 1080, 0.8, "0x1e293b")
        return GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="mock-v1",
            seed=42,
            prompt="",
            references=(),
            sha256="0" * 64,
            size_bytes=1,
            duration=1.0,
        )

    def _fake_html(_template, *, output, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=width,
            height=height,
            fps=30,
            frames=max(1, round(duration * fps)),
            duration=duration,
            transparent=False,
        )

    def _fake_ec(preset_name, output, *, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True, "preset": preset_name},
        )

    def _fake_apply(a, b, *, kind, output, duration_ms, width, height, fps):
        captured["duration_ms"] = duration_ms
        captured["kind"] = kind
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"fake-transition")
        return output

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_ec)
    monkeypatch.setattr("media_studio.promo.apply_transition", _fake_apply)
    monkeypatch.setattr("media_studio.promo._normalize_clip", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._render_solid_clip", lambda out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo._composite_overlay",
        lambda base, overlay, output, width, height, **_kw: output,
    )
    monkeypatch.setattr(
        "media_studio.promo.media.probe",
        lambda _p: type(
            "Info", (), {"duration": 2.0, "width": 1920, "height": 1080, "fps": 30.0}
        )(),
    )
    monkeypatch.setattr("media_studio.promo._pad_audio", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._mix_vo_and_music", lambda _v, _m, out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.mix_transition_sfx", lambda _b, _c, *, output, **_kw: output
    )
    monkeypatch.setattr("media_studio.promo.reframe_video", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr("media_studio.promo._mux_video_and_audio", lambda _v, _a, out: out)
    monkeypatch.setattr("media_studio.promo.render_captions", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr(
        "media_studio.promo.video.encode_with_loudness", lambda _i, d, *_a, **_kw: d
    )
    monkeypatch.setattr("media_studio.promo.video.qc_report", lambda _p, **_kw: {"ok": True})

    result = render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert result.gated is False
    # Promo uses a snappy ~180 ms default transition (cap dropped from 250 to 180;
    # the exact value is frame-quantized to min_beat_dur, so it is <= 180 ms).
    assert captured["duration_ms"] <= 180, captured
    assert captured["duration_ms"] < 250, captured


def test_render_promo_renders_native_aspect_html_and_effectcraft_9x16(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    def _fake_tts(
        text: str,
        *,
        output: Path,
        words_out: Path | None = None,
        **_kwargs: object,
    ) -> TtsResult:
        _ffmpeg_test_wav(output, 4.0)
        timings = [
            WordTiming("Hook", 0.1, 0.7),
            WordTiming("fast", 0.8, 1.4),
        ]
        wpath = words_out or output.with_suffix(".json")
        wpath.write_text(
            json.dumps([{"word": t.word, "start": t.start, "end": t.end} for t in timings]),
            encoding="utf-8",
        )
        return TtsResult(
            audio_path=output,
            words_path=wpath,
            sidecar_path=output.with_suffix(output.suffix + ".license"),
            duration=4.0,
            engine="mock",
            voice="mock",
            words=tuple(timings),
        )

    def _fake_gen(request: object, *, output: Path, **_kwargs: object) -> GenResult:
        dur = float(getattr(request, "duration", 1.0))
        _ffmpeg_test_mp4(output, 1920, 1080, max(0.8, dur), "0x1e293b")
        return GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="mock-v1",
            seed=42,
            prompt=str(getattr(request, "prompt", "")),
            references=(),
            sha256="0" * 64,
            size_bytes=output.stat().st_size,
            duration=dur,
        )

    html_calls: list[dict[str, object]] = []
    ec_calls: list[dict[str, object]] = []

    def _fake_html(
        _template: str,
        *,
        output: Path,
        width: int = 1920,
        height: int = 1080,
        duration: float = 1.0,
        params: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> HtmlMotionResult:
        html_calls.append({"width": width, "height": height, "params": params or {}})
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=width,
            height=height,
            fps=30,
            frames=max(1, round(duration * 30)),
            duration=duration,
            transparent=False,
        )

    def _fake_effectcraft(
        preset_name: str,
        output: Path,
        *,
        width: int = 1920,
        height: int = 1080,
        duration: float = 1.0,
        **_kwargs: object,
    ) -> MotionResult:
        ec_calls.append({"width": width, "height": height})
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True, "preset": preset_name},
        )

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_effectcraft)

    result = render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert result.gated is False

    # 9:16 deliverable renders HTML cards natively at (1080, 1920).
    html_9x16 = [c for c in html_calls if c["width"] == 1080 and c["height"] == 1920]
    assert html_9x16, f"no 9:16 HTML render; calls={html_calls}"
    params = html_9x16[0]["params"]
    assert params.get("headline") is not None
    assert params.get("value") is not None
    assert params.get("suffix") == "%"
    assert params.get("ink") == "#0F172A"

    # EffectCraft presets render natively at (1080, 1920) for 9:16.
    ec_9x16 = [c for c in ec_calls if c["width"] == 1080 and c["height"] == 1920]
    assert ec_9x16, f"no 9:16 EffectCraft render; calls={ec_calls}"

    qc = qc_promo(project, deliverables=("reel-9x16", "master-16x9"), use_media_mcp=False)
    assert qc["ok"] is True


def test_cli_promo_plan_render_gate_and_qc_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _make_promo_project(tmp_path)
    plan_file = root / "out" / "promo" / "plan.json"

    rc = main(
        [
            "promo",
            "plan",
            str(root),
            "--script",
            "Hook. Problem. Demo. Proof. CTA.",
            "--out",
            str(plan_file),
        ]
    )
    assert rc == 0
    assert plan_file.is_file()

    rc_gate = main(["promo", "render", str(root), "--plan", str(plan_file)])
    assert rc_gate == 0
    captured = capsys.readouterr()
    assert "gated" in captured.out.lower()
    assert (root / "out" / "promo" / "storyboard.png").is_file()

    rc_qc = main(["promo", "qc", str(root), "--no-media-mcp"])
    assert rc_qc == 1
    captured_qc = capsys.readouterr()
    assert "error:" in captured_qc.err


def test_render_promo_accepts_fps_param_and_threads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """render_promo must accept fps (default 30) and thread fps=60 through every beat/encode."""

    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    captured: dict = {}

    def _fake_tts(text, *, output, words_out=None, **_kwargs):
        _ffmpeg_test_wav(output, 4.0)
        wpath = words_out or output.with_suffix(".json")
        wpath.write_text(
            json.dumps([{"word": "Hook", "start": 0.1, "end": 0.7}]),
            encoding="utf-8",
        )
        return TtsResult(
            audio_path=output,
            words_path=wpath,
            sidecar_path=output.with_suffix(output.suffix + ".license"),
            duration=4.0,
            engine="mock",
            voice="mock",
            words=(WordTiming("Hook", 0.1, 0.7),),
        )

    def _fake_gen(request, *, output, **_kwargs):
        _ffmpeg_test_mp4(output, 1920, 1080, 0.8, "0x1e293b")
        return GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="mock-v1",
            seed=42,
            prompt="",
            references=(),
            sha256="0" * 64,
            size_bytes=1,
            duration=1.0,
        )

    def _fake_html(_template, *, output, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        captured["html_fps"] = fps
        captured["html_w"] = width
        captured["html_h"] = height
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=width,
            height=height,
            fps=fps,
            frames=max(1, round(duration * fps)),
            duration=duration,
            transparent=False,
        )

    def _fake_ec(preset_name, output, *, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        captured["ec_fps"] = fps
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True, "preset": preset_name},
        )

    def _fake_norm(source, output, *, width, height, duration, **_kwargs):
        captured["norm_fps"] = None  # _normalize_clip signature has no fps; check vf instead
        Path(output).write_bytes(b"fake-norm")
        return output

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_ec)

    render_promo(project, plan, auto=True, max_gen_calls=2, force=True, fps=60)

    assert captured.get("html_fps") == 60, captured
    assert captured.get("ec_fps") == 60, captured

    # Default fps is 30 when omitted.
    captured.clear()
    render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert captured.get("html_fps") == 30, captured
    assert captured.get("ec_fps") == 30, captured


def test_render_promo_rejects_empty_beats(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    empty_plan = PromoPlan(
        project_slug="demo-promo",
        aspects=("9:16", "16:9"),
        script="Empty",
        beats=(),
        estimated_gen_calls=0,
        references=(),
        total_duration=0.0,
    )

    with pytest.raises(PromoError, match="no beats"):
        render_promo(project, empty_plan, auto=True)


def test_render_promo_synthesized_vo_cleans_with_tts_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthesized VO is cleaned with profile="tts" (no denoise chain)."""
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=2.0,
    )

    captured: dict[str, object] = {}

    def _fake_tts(text, *, output, words_out=None, **_kwargs):
        _ffmpeg_test_wav(output, 2.0)
        wpath = words_out or output.with_suffix(".json")
        wpath.write_text(json.dumps([{"word": "Hook", "start": 0.1, "end": 0.7}]), encoding="utf-8")
        sidecar = output.with_suffix(output.suffix + ".license")
        sidecar.write_text("SPDX-License-Identifier: CC0-1.0\n", encoding="utf-8")
        return TtsResult(
            audio_path=output,
            words_path=wpath,
            sidecar_path=sidecar,
            duration=2.0,
            engine="mock",
            voice="mock",
            words=(WordTiming("Hook", 0.1, 0.7),),
        )

    def _fake_clean_vo(
        source: Path, *, output: Path, profile: str = "broadcast", **_kwargs: object
    ) -> Path:
        captured["profile"] = profile
        _ffmpeg_test_wav(output, 2.0)
        return output

    def _fake_gen(request, *, output, **_kwargs):
        _ffmpeg_test_mp4(output, 1920, 1080, 0.8, "0x1e293b")
        return GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="mock-v1",
            seed=42,
            prompt="",
            references=(),
            sha256="0" * 64,
            size_bytes=1,
            duration=1.0,
        )

    def _fake_html(_template, *, output, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=width,
            height=height,
            fps=30,
            frames=max(1, round(duration * 30)),
            duration=duration,
            transparent=False,
        )

    def _fake_ec(preset_name, output, *, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True, "preset": preset_name},
        )

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.audio.clean_voiceover", _fake_clean_vo)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_ec)
    monkeypatch.setattr("media_studio.promo._normalize_clip", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._render_solid_clip", lambda out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo._composite_overlay",
        lambda base, overlay, output, width, height, **_kw: output,
    )
    monkeypatch.setattr(
        "media_studio.promo.apply_transition", lambda _a, _b, *, output, **_kw: output
    )
    monkeypatch.setattr(
        "media_studio.promo.media.probe",
        lambda _p: type(
            "Info", (), {"duration": 2.0, "width": 1920, "height": 1080, "fps": 30.0}
        )(),
    )
    monkeypatch.setattr("media_studio.promo._pad_audio", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._mix_vo_and_music", lambda _v, _m, out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.mix_transition_sfx", lambda _b, _c, *, output, **_kw: output
    )
    monkeypatch.setattr("media_studio.promo.reframe_video", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr("media_studio.promo._mux_video_and_audio", lambda _v, _a, out: out)
    monkeypatch.setattr("media_studio.promo.render_captions", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr(
        "media_studio.promo.video.encode_with_loudness", lambda _i, d, *_a, **_kw: d
    )
    monkeypatch.setattr("media_studio.promo.video.qc_report", lambda _p, **_kw: {"ok": True})

    res = render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert res.gated is False
    assert set(res.deliverables) == {"reel-9x16", "master-16x9"}
    assert captured["profile"] == "tts"


def test_render_promo_ducks_music_and_extracts_prerecorded_vo_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
    vo_file = root / "assets" / "audio" / "vo.wav"
    bgm_file = root / "assets" / "music" / "bgm.wav"
    _ffmpeg_test_wav(vo_file, 2.0)
    _ffmpeg_test_wav(bgm_file, 2.0)

    manifest_path = root / "project.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_data["assets"]["voiceover"] = "assets/audio/vo.wav"
    manifest_data["assets"]["bgm"] = "assets/music/bgm.wav"
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")

    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=2.0)

    duck_calls: list[tuple[Path, Path]] = []
    extract_calls: list[Path] = []
    vo_profile: dict[str, str] = {}

    def _fake_clean_vo(
        source: Path, *, output: Path, profile: str = "broadcast", **_kwargs: object
    ) -> Path:
        vo_profile["profile"] = profile
        _ffmpeg_test_wav(output, 2.0)
        return output

    def _fake_extract(media_path: Path) -> list[WordTiming]:
        extract_calls.append(media_path)
        return [WordTiming("Hello", 0.1, 0.5)]

    def _fake_duck(music_path: Path, voice_path: Path, output_path: Path, **_kw: object) -> Path:
        duck_calls.append((music_path, voice_path))
        _ffmpeg_test_wav(output_path, 2.0)
        return output_path

    def _fake_probe(_path: Path) -> object:
        return type("Info", (), {"duration": 2.0, "width": 1920, "height": 1080, "fps": 30.0})()

    monkeypatch.setattr("media_studio.promo.audio.clean_voiceover", _fake_clean_vo)
    monkeypatch.setattr("media_studio.promo.extract_word_timestamps", _fake_extract)
    monkeypatch.setattr("media_studio.promo.audio.duck_music", _fake_duck)
    monkeypatch.setattr(
        "media_studio.promo.generate_asset",
        lambda *_a, output, **_kw: GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="m",
            seed=1,
            prompt="p",
            references=(),
            sha256="0" * 64,
            size_bytes=1,
            duration=1.0,
        ),
    )
    monkeypatch.setattr(
        "media_studio.promo.render_html_motion",
        lambda _t, *, output, **_kw: HtmlMotionResult(
            output=output,
            template="t",
            width=1920,
            height=1080,
            fps=30,
            frames=30,
            duration=1.0,
            transparent=False,
        ),
    )
    monkeypatch.setattr(
        "media_studio.promo.render_promo_preset",
        lambda _p, output, **_kw: MotionResult(
            output=output,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": False},
        ),
    )
    monkeypatch.setattr("media_studio.promo._normalize_clip", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._render_solid_clip", lambda out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.apply_transition", lambda _a, _b, *, output, **_kw: output
    )
    monkeypatch.setattr("media_studio.promo.media.probe", _fake_probe)
    monkeypatch.setattr("media_studio.promo._pad_audio", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._mix_vo_and_music", lambda _v, _m, out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.mix_transition_sfx", lambda _b, _c, *, output, **_kw: output
    )
    monkeypatch.setattr("media_studio.promo.reframe_video", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr("media_studio.promo._mux_video_and_audio", lambda _v, _a, out: out)
    monkeypatch.setattr("media_studio.promo.render_captions", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr(
        "media_studio.promo.video.encode_with_loudness", lambda _i, d, *_a, **_kw: d
    )
    monkeypatch.setattr("media_studio.promo.video.qc_report", lambda _p, **_kw: {"ok": True})

    res = render_promo(project, plan, auto=True, deliverables=("reel-9x16",), force=True)
    assert res.gated is False
    assert extract_calls == [vo_file]
    assert len(duck_calls) == 1
    assert duck_calls[0][0].name == "music-trimmed.wav"
    assert duck_calls[0][1].name == "vo-padded.wav"
    assert vo_profile["profile"] == "broadcast"


def test_render_promo_prerecorded_vo_handles_captions_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_studio.captions import CaptionsError

    root = _make_promo_project(tmp_path, with_assets=True)
    vo_file = root / "assets" / "audio" / "vo.wav"
    _ffmpeg_test_wav(vo_file, 2.0)
    manifest_path = root / "project.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_data["assets"]["voiceover"] = "assets/audio/vo.wav"
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8")

    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=2.0)

    monkeypatch.setattr(
        "media_studio.promo.audio.clean_voiceover", lambda _s, *, output, **_kw: output
    )

    def _fail_extract(_path: Path) -> list[WordTiming]:
        raise CaptionsError("no whisper")

    monkeypatch.setattr("media_studio.promo.extract_word_timestamps", _fail_extract)
    monkeypatch.setattr(
        "media_studio.promo.generate_asset",
        lambda *_a, output, **_kw: GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="m",
            seed=1,
            prompt="p",
            references=(),
            sha256="0" * 64,
            size_bytes=1,
            duration=1.0,
        ),
    )
    monkeypatch.setattr(
        "media_studio.promo.render_html_motion",
        lambda _t, *, output, **_kw: HtmlMotionResult(
            output=output,
            template="t",
            width=1920,
            height=1080,
            fps=30,
            frames=30,
            duration=1.0,
            transparent=False,
        ),
    )
    monkeypatch.setattr(
        "media_studio.promo.render_promo_preset",
        lambda _p, output, **_kw: MotionResult(
            output=output,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": False},
        ),
    )
    monkeypatch.setattr("media_studio.promo._normalize_clip", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._render_solid_clip", lambda out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.apply_transition", lambda _a, _b, *, output, **_kw: output
    )
    monkeypatch.setattr(
        "media_studio.promo.media.probe",
        lambda _p: type(
            "Info", (), {"duration": 2.0, "width": 1920, "height": 1080, "fps": 30.0}
        )(),
    )
    monkeypatch.setattr("media_studio.promo._pad_audio", lambda _s, out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.mix_transition_sfx", lambda _b, _c, *, output, **_kw: output
    )
    monkeypatch.setattr("media_studio.promo.reframe_video", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr("media_studio.promo._mux_video_and_audio", lambda _v, _a, out: out)
    monkeypatch.setattr(
        "media_studio.promo.video.encode_with_loudness", lambda _i, d, *_a, **_kw: d
    )
    monkeypatch.setattr("media_studio.promo.video.qc_report", lambda _p, **_kw: {"ok": True})

    res = render_promo(project, plan, auto=True, deliverables=("master-16x9",), force=True)
    assert res.gated is False
    assert any("captions skipped for pre-recorded VO" in n for n in res.notes)


def test_render_solid_clip_command_carries_intermediate_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_render_solid_clip must use intermediate CPU quality (veryfast/crf 18) when NVENC is off."""
    from media_studio import promo

    captured: dict = {}

    def fake_run(command, *args, **kwargs):
        captured["command"] = list(command)

    monkeypatch.setattr(promo.media, "run_command", fake_run)
    monkeypatch.setattr(promo.media, "detect_nvenc_available", lambda: False)

    output = tmp_path / "solid.mp4"
    promo._render_solid_clip(output, width=1080, height=1920, duration=1.0, color="#0F172A", fps=30)

    cmd = captured["command"]
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "veryfast", cmd
    assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "18", cmd
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p", cmd
    vf_idx = cmd.index("-vf")
    vf = cmd[vf_idx + 1]
    assert "flags=lanczos" in vf, f"no lanczos in solid clip vf: {vf}"
    assert "in_range=auto:out_range=limited" in vf, f"no limited range in solid clip vf: {vf}"


def test_encode_with_loudness_carries_youtube_bitrate_and_bt709(tmp_path, monkeypatch) -> None:
    """Final deliverable encode targets YouTube bitrate and tags BT.709 color metadata."""
    from media_studio import video

    captured: dict = {}

    def fake_run_command(command, *, label, expect_stdout=True):
        captured["command"] = list(command)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(video.media, "run_command", fake_run_command)
    monkeypatch.setattr(
        video.media,
        "measure_loudness",
        lambda path, target=-14.0: type(
            "L",
            (),
            {
                "integrated_lufs": -14.0,
                "true_peak_dbtp": -1.5,
                "loudness_range": 11.0,
                "threshold": -14.0,
                "offset": 0.0,
                "as_loudnorm_arguments": lambda self: (
                    "measured_I=-14.00:measured_LRA=11.00:measured_TP=-1.50:measured_thresh=-14.00:offset=0.00:linear=true"
                ),
            },
        )(),
    )

    output = tmp_path / "deliverable.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"fake")

    video.encode_with_loudness(tmp_path / "intermediate.mkv", output, target_lufs=-14.0, fps=60)

    cmd = captured["command"]
    joined = " ".join(cmd)
    assert "-b:v" in joined and joined.split("-b:v")[1].split()[0] == "12M", joined
    assert "-maxrate" in joined and joined.split("-maxrate")[1].split()[0] == "14M", joined
    assert "-bufsize" in joined and joined.split("-bufsize")[1].split()[0] == "24M", joined
    assert "-color_range" in joined and joined.split("-color_range")[1].split()[0] == "tv", joined
    assert "-colorspace" in joined and joined.split("-colorspace")[1].split()[0] == "bt709", joined
    assert (
        "-color_primaries" in joined and joined.split("-color_primaries")[1].split()[0] == "bt709"
    ), joined
    assert "-color_trc" in joined and joined.split("-color_trc")[1].split()[0] == "bt709", joined

    captured.clear()
    video.encode_with_loudness(tmp_path / "intermediate.mkv", output, target_lufs=-14.0, fps=30)
    joined30 = " ".join(captured["command"])
    assert "-b:v" in joined30 and joined30.split("-b:v")[1].split()[0] == "8M", joined30


def test_render_promo_passes_fps_into_render_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """render_promo must thread fps into render_captions."""

    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=2.0,
    )

    captured: dict = {}

    def _fake_tts(text, *, output, words_out=None, **_kwargs):
        _ffmpeg_test_wav(output, 2.0)
        wpath = words_out or output.with_suffix(".json")
        wpath.write_text(
            json.dumps([{"word": "Hook", "start": 0.1, "end": 0.7}]),
            encoding="utf-8",
        )
        return TtsResult(
            audio_path=output,
            words_path=wpath,
            sidecar_path=output.with_suffix(output.suffix + ".license"),
            duration=2.0,
            engine="mock",
            voice="mock",
            words=(WordTiming("Hook", 0.1, 0.7),),
        )

    def _fake_gen(request, *, output, **_kwargs):
        _ffmpeg_test_mp4(output, 1920, 1080, 0.8, "0x1e293b")
        return GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="mock-v1",
            seed=42,
            prompt="",
            references=(),
            sha256="0" * 64,
            size_bytes=1,
            duration=1.0,
        )

    def _fake_html(_template, *, output, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=width,
            height=height,
            fps=fps,
            frames=max(1, round(duration * fps)),
            duration=duration,
            transparent=False,
        )

    def _fake_ec(preset_name, output, *, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True, "preset": preset_name},
        )

    def _fake_norm(source, output, *, width, height, duration, **_kwargs):
        Path(output).write_bytes(b"fake-norm")
        return output

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_ec)
    monkeypatch.setattr("media_studio.promo._normalize_clip", _fake_norm)
    monkeypatch.setattr("media_studio.promo._render_solid_clip", lambda out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo._composite_overlay",
        lambda base, overlay, output, width, height, **_kw: (
            Path(output).parent.mkdir(parents=True, exist_ok=True),
            Path(output).write_bytes(b""),
            output,
        )[-1],
    )
    monkeypatch.setattr(
        "media_studio.promo.apply_transition", lambda _a, _b, *, output, **_kw: output
    )
    monkeypatch.setattr(
        "media_studio.promo.media.probe",
        lambda _p: type(
            "Info", (), {"duration": 2.0, "width": 1920, "height": 1080, "fps": 30.0}
        )(),
    )
    monkeypatch.setattr("media_studio.promo._pad_audio", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._mix_vo_and_music", lambda _v, _m, out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.mix_transition_sfx", lambda _b, _c, *, output, **_kw: output
    )
    monkeypatch.setattr("media_studio.promo.reframe_video", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr("media_studio.promo._mux_video_and_audio", lambda _v, _a, out: out)

    def _fake_captions(source, *, words, style, aspect, accent_color, output, engine, fps=30):
        captured["fps"] = fps
        Path(output).write_bytes(b"fake-captioned")
        return output

    monkeypatch.setattr("media_studio.promo.render_captions", _fake_captions)
    monkeypatch.setattr(
        "media_studio.promo.video.encode_with_loudness", lambda _i, d, *_a, **_kw: d
    )
    monkeypatch.setattr("media_studio.promo.video.qc_report", lambda _p, **_kw: {"ok": True})

    render_promo(project, plan, auto=True, max_gen_calls=2, force=True, fps=60)
    assert captured["fps"] == 60, captured

    captured.clear()
    render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert captured["fps"] == 30, captured


def test_promo_beat_has_layout_zone_default():
    beat = PromoBeat(kind="demo", text="hello", duration=1.0)
    assert beat.layout_zone == "hero-center"


def test_check_layout_collisions_no_collision_at_9x16():
    beat = PromoBeat(kind="demo", text="hello", duration=1.0, layout_zone="hero-center")
    assert check_layout_collisions(beat, aspect="9:16", width=1080, height=1920) == []


def test_check_layout_collisions_all_zones_clean_at_9x16():
    for zone in ("hero-center", "split-stage", "card-focus", "cta-centered"):
        beat = PromoBeat(kind="demo", text="hello", duration=1.0, layout_zone=zone)
        assert check_layout_collisions(beat, aspect="9:16", width=1080, height=1920) == []


def test_check_layout_collisions_detects_safe_zone_intrusion():
    # A tiny canvas forces the hero-center card into the platform safe zone.
    beat = PromoBeat(kind="demo", text="hello", duration=1.0, layout_zone="hero-center")
    issues = check_layout_collisions(beat, aspect="1:1", width=100, height=100)
    assert issues, "expected safe-zone intrusion to be flagged"
    assert any("safe zone" in issue for issue in issues)


def test_resolve_layout_placement_no_adjustment_when_clean():
    beat = PromoBeat(kind="demo", text="hello", duration=1.0, layout_zone="hero-center")
    placement = resolve_layout_placement(beat, aspect="9:16", width=1080, height=1920)
    assert placement["adjusted"] is False
    assert placement["scale"] == 1.0
    assert placement["collision_avoided"] is True
    assert placement["reason"] == "no collision"


def test_resolve_layout_placement_adjusts_when_crowded():
    # A 9:16 canvas small enough to push the hero-center card into the top/bottom
    # safe zone forces placement to scale down and shift anchor Y while avoiding collision.
    beat = PromoBeat(kind="demo", text="hello", duration=1.0, layout_zone="hero-center")
    placement = resolve_layout_placement(beat, aspect="9:16", width=394, height=700)
    assert placement["adjusted"] is True
    assert placement["scale"] < 1.0
    assert 0.0 < placement["anchor_y"] < 700.0
    assert placement["collision_avoided"] is True


def test_resolve_layout_placement_all_zones_avoid_collision():
    for zone in ("hero-center", "split-stage", "card-focus", "cta-centered"):
        beat = PromoBeat(kind="demo", text="hello", duration=1.0, layout_zone=zone)
        placement = resolve_layout_placement(beat, aspect="9:16", width=1080, height=1920)
        assert placement["collision_avoided"] is True, (zone, placement)


def test_render_promo_passes_adjusted_placement_params(tmp_path, monkeypatch):
    """render_promo threads resolve_layout_placement output into HTML motion params."""
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    def _fake_tts(text, *, output, words_out=None, **_kwargs):
        _ffmpeg_test_wav(output, 4.0)
        wpath = words_out or output.with_suffix(".json")
        wpath.write_text(json.dumps([{"word": "Hook", "start": 0.1, "end": 0.7}]), encoding="utf-8")
        sidecar = output.with_suffix(output.suffix + ".license")
        sidecar.write_text("SPDX-License-Identifier: CC0-1.0\n", encoding="utf-8")
        return TtsResult(
            audio_path=output,
            words_path=wpath,
            sidecar_path=sidecar,
            duration=4.0,
            engine="mock",
            voice="mock",
            words=(WordTiming("Hook", 0.1, 0.7),),
        )

    def _fake_gen(request, *, output, **_kwargs):
        _ffmpeg_test_mp4(output, 1920, 1080, 0.8, "0x1e293b")
        return GenResult(
            output=output,
            kind="video",
            provider="mock",
            model="mock-v1",
            seed=42,
            prompt=str(getattr(request, "prompt", "")),
            references=(),
            sha256="0" * 64,
            size_bytes=1,
            duration=1.0,
        )

    captured_params: dict[str, object] = {}

    def _fake_html(_template, *, output, width, height, fps, duration, params=None, **_kwargs):
        captured_params["params"] = dict(params or {})
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=width,
            height=height,
            fps=fps,
            frames=max(1, round(duration * fps)),
            duration=duration,
            transparent=False,
        )

    def _fake_ec(preset_name, output, *, width=1920, height=1080, fps=30, duration=1.0, **_kwargs):
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, width, height, max(0.8, duration), "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True, "preset": preset_name},
        )

    def _fake_apply(a, b, *, kind, output, duration_ms, width, height, fps):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"fake-transition")
        return output

    # Monkeypatch placement resolution to return an adjusted placement, then assert
    # render_promo threads it into the HTML motion params.
    monkeypatch.setattr(
        "media_studio.promo.resolve_layout_placement",
        lambda beat, *, aspect, width, height: {
            "zone": "hero-center",
            "anchor_y": 0.30,
            "scale": 0.50,
            "adjusted": True,
            "collision_avoided": True,
            "reason": "test adjustment",
        },
    )

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_ec)
    monkeypatch.setattr("media_studio.promo.apply_transition", _fake_apply)
    monkeypatch.setattr("media_studio.promo._normalize_clip", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._render_solid_clip", lambda out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo._composite_overlay",
        lambda base, overlay, output, width, height, **_kw: output,
    )
    monkeypatch.setattr(
        "media_studio.promo.media.probe",
        lambda _p: type(
            "Info", (), {"duration": 2.0, "width": 1920, "height": 1080, "fps": 30.0}
        )(),
    )
    monkeypatch.setattr("media_studio.promo._pad_audio", lambda _s, out, **_kw: out)
    monkeypatch.setattr("media_studio.promo._mix_vo_and_music", lambda _v, _m, out, **_kw: out)
    monkeypatch.setattr(
        "media_studio.promo.mix_transition_sfx", lambda _b, _c, *, output, **_kw: output
    )
    monkeypatch.setattr("media_studio.promo.reframe_video", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr("media_studio.promo._mux_video_and_audio", lambda _v, _a, out: out)
    monkeypatch.setattr("media_studio.promo.render_captions", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr(
        "media_studio.promo.video.encode_with_loudness", lambda _i, d, *_a, **_kw: d
    )
    monkeypatch.setattr("media_studio.promo.video.qc_report", lambda _p, **_kw: {"ok": True})

    render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    params = captured_params.get("params", {})
    assert params.get("card_anchor_y") == 0.30, params
    assert params.get("card_scale") == 0.50, params
    assert params.get("layout_zone") == "hero-center", params
