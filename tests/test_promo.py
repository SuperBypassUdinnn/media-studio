"""Tests for the dual-aspect promotional video orchestrator and CLI."""

from __future__ import annotations

import json
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
    load_promo_plan,
    plan_promo,
    qc_promo,
    render_promo,
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
    leftovers = list(Path("/tmp/opencode").glob("promo-*"))
    assert leftovers == []


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
