"""Tests for scene-level beat caching, selective beat regeneration, and timeline export."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml

from media_studio.cli import main
from media_studio.effectcraft import MotionResult
from media_studio.gen.base import GenResult
from media_studio.html_motion import HtmlMotionResult
from media_studio.manifest import load_project
from media_studio.promo import (
    DEFAULT_DELIVERABLES,
    PromoBeat,
    PromoRenderResult,
    compute_beat_cache_key,
    export_mlt_timeline,
    export_otio_timeline,
    get_cached_beat,
    plan_promo,
    put_cached_beat,
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


def _beat_index(output: Path) -> int:
    match = re.search(r"beat-(\d+)", str(output))
    return int(match.group(1)) if match else -1


# ---------------------------------------------------------------------------
# compute_beat_cache_key
# ---------------------------------------------------------------------------


def test_compute_beat_cache_key_is_deterministic(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=4.0)
    beat = plan.beats[0]

    key_a = compute_beat_cache_key(project, beat, 0, aspect="9:16", fps=30)
    key_b = compute_beat_cache_key(project, beat, 0, aspect="9:16", fps=30)
    assert key_a == key_b
    assert len(key_a) == 64


def test_compute_beat_cache_key_differs_by_aspect_and_fps(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=4.0)
    beat = plan.beats[0]

    key_9x16_30 = compute_beat_cache_key(project, beat, 0, aspect="9:16", fps=30)
    key_16x9_30 = compute_beat_cache_key(project, beat, 0, aspect="16:9", fps=30)
    key_9x16_60 = compute_beat_cache_key(project, beat, 0, aspect="9:16", fps=60)
    assert len({key_9x16_30, key_16x9_30, key_9x16_60}) == 3


def test_compute_beat_cache_key_invalidates_on_beat_change(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=4.0)
    beat = plan.beats[0]

    base = compute_beat_cache_key(project, beat, 0, aspect="9:16", fps=30)

    for field, value in (
        ("text", "Changed hook text."),
        ("duration", 9.0),
        ("template", "other-template"),
        ("preset", "other-preset"),
        ("transition", "zoom_punch_in"),
        ("gen_prompt", "new prompt"),
        ("layout_zone", "split-stage"),
    ):
        mutated = PromoBeat(**{**beat.to_dict(), field: value})
        assert compute_beat_cache_key(project, mutated, 0, aspect="9:16", fps=30) != base


def test_compute_beat_cache_key_invalidates_on_brand_change(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=4.0)
    beat = plan.beats[0]

    base = compute_beat_cache_key(project, beat, 0, aspect="9:16", fps=30)

    from media_studio.manifest import Brand

    rebranded = Brand(
        palette={"primary": "#FF0000", "accent": "#00FF00", "ink": "#0000FF"},
        fonts={"heading": "Other", "body": "Other"},
    )
    object.__setattr__(project, "brand", rebranded)
    assert compute_beat_cache_key(project, beat, 0, aspect="9:16", fps=30) != base


# ---------------------------------------------------------------------------
# put_cached_beat / get_cached_beat
# ---------------------------------------------------------------------------


def test_put_and_get_cached_beat_roundtrip(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    clip = tmp_path / "clip.mp4"
    _ffmpeg_test_mp4(clip, 1080, 1920, 1.0, "0x1e293b")

    key = "abc123"
    assert get_cached_beat(cache_dir, key) is None

    target = put_cached_beat(cache_dir, key, clip)
    assert target == cache_dir / f"{key}.mp4"
    assert target.is_file()
    assert get_cached_beat(cache_dir, key) == target


# ---------------------------------------------------------------------------
# render_promo caching behaviour
# ---------------------------------------------------------------------------


def _install_render_mocks(monkeypatch: pytest.MonkeyPatch, calls: dict) -> None:
    """Stub every per-beat render step so it writes a real file and logs the beat index."""

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

    def _fake_per_beat(*args, **_kw):
        output = _kw.get("output") or (args[-1] if args else None)
        if output is None:
            return None
        idx = _beat_index(Path(output))
        calls["per_beat"][idx] = calls["per_beat"].get(idx, 0) + 1
        _ffmpeg_test_mp4(Path(output), 1080, 1920, 1.0, "0x1e293b")
        return output

    def _fake_html(_template, *, output, **_kwargs):
        idx = _beat_index(output)
        calls["per_beat"][idx] = calls["per_beat"].get(idx, 0) + 1
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, 1080, 1920, 1.0, "0x0f172a")
        return HtmlMotionResult(
            output=out_mp4,
            template="saas-feature-card",
            width=1080,
            height=1920,
            fps=30,
            frames=30,
            duration=1.0,
            transparent=False,
        )

    def _fake_ec(_preset, output, **_kwargs):
        idx = _beat_index(output)
        calls["per_beat"][idx] = calls["per_beat"].get(idx, 0) + 1
        out_mp4 = output.with_suffix(".mp4")
        _ffmpeg_test_mp4(out_mp4, 1080, 1920, 1.0, "0x0284c7")
        return MotionResult(
            output=out_mp4,
            ecp_path=output.with_suffix(".ecp"),
            report_path=output.with_suffix(".report.json"),
            report={"ok": True},
        )

    def _fake_gen(_request, *, output, **_kwargs):
        idx = _beat_index(output)
        calls["per_beat"][idx] = calls["per_beat"].get(idx, 0) + 1
        _ffmpeg_test_mp4(output, 1920, 1080, 1.0, "0x1e293b")
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

    def _fake_apply(_a, _b, *, output, **_kw):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"fake-transition")
        return output

    def _fake_composite(base, overlay, output, width, height, **_kw):
        idx = _beat_index(Path(output))
        calls["per_beat"][idx] = calls["per_beat"].get(idx, 0) + 1
        _ffmpeg_test_mp4(Path(output), 1080, 1920, 1.0, "0x1e293b")
        return output

    monkeypatch.setattr("media_studio.promo.synthesize_speech", _fake_tts)
    monkeypatch.setattr("media_studio.promo.generate_asset", _fake_gen)
    monkeypatch.setattr("media_studio.promo.render_html_motion", _fake_html)
    monkeypatch.setattr("media_studio.promo.render_promo_preset", _fake_ec)
    monkeypatch.setattr("media_studio.promo.apply_transition", _fake_apply)
    monkeypatch.setattr("media_studio.promo._normalize_clip", _fake_per_beat)
    monkeypatch.setattr("media_studio.promo._render_solid_clip", _fake_per_beat)
    monkeypatch.setattr("media_studio.promo.reframe_video", _fake_per_beat)
    monkeypatch.setattr(
        "media_studio.promo._composite_overlay",
        _fake_composite,
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
    monkeypatch.setattr("media_studio.promo._mux_video_and_audio", lambda _v, _a, out: out)
    monkeypatch.setattr("media_studio.promo.render_captions", lambda _s, *, output, **_kw: output)
    monkeypatch.setattr(
        "media_studio.promo.video.encode_with_loudness", lambda _i, d, *_a, **_kw: d
    )
    monkeypatch.setattr("media_studio.promo.video.qc_report", lambda _p, **_kw: {"ok": True})


def test_cache_hit_skips_rerendering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    calls: dict = {"per_beat": {}}
    _install_render_mocks(monkeypatch, calls)

    # Full render populates the cache.
    result1 = render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert result1.gated is False
    assert set(result1.deliverables) == set(DEFAULT_DELIVERABLES)

    # Beat cache files should now exist for both aspects.
    cache_dir = project.root / "out" / "promo" / "cache"
    assert list(cache_dir.glob("*.mp4")), "expected cache to be populated on full render"

    # Re-render with beats=[]: every beat reuses cache, zero per-beat renders.
    calls["per_beat"] = {}
    result2 = render_promo(project, plan, auto=True, max_gen_calls=2, force=True, beats=[])
    assert result2.gated is False
    assert all(count == 0 for count in calls["per_beat"].values()), calls["per_beat"]
    assert sum(calls["per_beat"].values()) == 0, calls["per_beat"]


def test_selective_beat_2_rerenders_only_beat_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    calls: dict = {"per_beat": {}}
    _install_render_mocks(monkeypatch, calls)

    # Full render populates the cache for all five beats.
    result1 = render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert result1.gated is False
    first_counts = dict(calls["per_beat"])
    assert all(first_counts.get(i, 0) > 0 for i in range(5)), first_counts

    # Selective render of beat index 2 only.
    calls["per_beat"] = {}
    result2 = render_promo(project, plan, auto=True, max_gen_calls=2, force=True, beats=[2])
    assert result2.gated is False

    counts = calls["per_beat"]
    # Beat 2 must be freshly rendered; every other beat reuses the cache.
    assert counts.get(2, 0) > 0, f"beat 2 was not re-rendered: {counts}"
    for idx in (0, 1, 3, 4):
        assert counts.get(idx, 0) == 0, f"beat {idx} should reuse cache: {counts}"


def test_cli_promo_render_accepts_beat_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
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

    calls: dict = {"per_beat": {}}
    _install_render_mocks(monkeypatch, calls)

    # Full render populates the cache.
    rc = main(["promo", "render", str(root), "--plan", str(plan_file), "--auto", "--force"])
    assert rc == 0
    assert all(count > 0 for count in calls["per_beat"].values()), calls["per_beat"]

    # Selective render of beat index 2 only.
    calls["per_beat"] = {}
    rc = main(
        ["promo", "render", str(root), "--plan", str(plan_file), "--auto", "--force", "--beat", "2"]
    )
    assert rc == 0

    counts = calls["per_beat"]
    assert counts.get(2, 0) > 0, f"beat 2 was not re-rendered: {counts}"
    for idx in (0, 1, 3, 4):
        assert counts.get(idx, 0) == 0, f"beat {idx} should reuse cache: {counts}"


# ---------------------------------------------------------------------------
# Timeline export
# ---------------------------------------------------------------------------


def test_otio_timeline_schema(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=4.0)
    deliverable_path = root / "out" / "video" / "reel-9x16.mp4"
    deliverable_path.parent.mkdir(parents=True, exist_ok=True)
    deliverable_path.write_bytes(b"placeholder")

    otio_path = export_otio_timeline(project, plan, "reel-9x16", deliverable_path, fps=30)
    assert otio_path == root / "out" / "promo" / "timeline.otio.json"
    assert otio_path.is_file()

    payload = json.loads(otio_path.read_text(encoding="utf-8"))
    assert payload["OTIO_SCHEMA"] == "Timeline.1"
    assert payload["name"] == "demo-promo-promo"
    assert payload["global_start_time"] == {"OTIO_SCHEMA": "RationalTime.1", "value": 0, "rate": 30}

    tracks = payload["tracks"]
    assert tracks["OTIO_SCHEMA"] == "Stack.1"
    children = tracks["children"]

    video_track = children[0]
    assert video_track["OTIO_SCHEMA"] == "Track.1"
    assert video_track["name"] == "Video Track"
    assert video_track["kind"] == "Video"

    clip_children = [c for c in video_track["children"] if c["OTIO_SCHEMA"] == "Clip.1"]
    transition_children = [c for c in video_track["children"] if c["OTIO_SCHEMA"] == "Transition.1"]
    assert len(clip_children) == len(plan.beats)
    assert len(transition_children) == len(plan.beats) - 1
    assert clip_children[0]["name"] == "beat-00"
    assert clip_children[0]["media_reference"]["OTIO_SCHEMA"] == "ExternalReference.1"
    assert transition_children[0]["transition_type"] == "Custom"

    audio_tracks = [c for c in children if c["OTIO_SCHEMA"] == "Track.1" and c["kind"] == "Audio"]
    assert len(audio_tracks) == 2
    names = {t["name"] for t in audio_tracks}
    assert names == {"Voiceover Track", "Music Track"}


def test_mlt_timeline_xml_valid(tmp_path: Path) -> None:
    root = _make_promo_project(tmp_path)
    project = load_project(root)
    plan = plan_promo(project, script="Hook. Problem. Demo. Proof. CTA.", target_duration=4.0)
    deliverable_path = root / "out" / "video" / "reel-9x16.mp4"
    deliverable_path.parent.mkdir(parents=True, exist_ok=True)
    deliverable_path.write_bytes(b"placeholder")

    mlt_path = export_mlt_timeline(project, plan, "reel-9x16", deliverable_path, fps=30)
    assert mlt_path == root / "out" / "promo" / "timeline.mlt"
    assert mlt_path.is_file()

    ElementTree.fromstring(mlt_path.read_text(encoding="utf-8"))


def test_render_promo_returns_otio_and_mlt_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_promo_project(tmp_path, with_assets=True)
    project = load_project(root)
    plan = plan_promo(
        project,
        script="Hook fast. Problem clear. Demo smooth. Proof real. CTA now.",
        target_duration=4.0,
    )

    calls: dict = {"per_beat": {}}
    _install_render_mocks(monkeypatch, calls)

    result = render_promo(project, plan, auto=True, max_gen_calls=2, force=True)
    assert isinstance(result, PromoRenderResult)
    assert result.otio_path == root / "out" / "promo" / "timeline.otio.json"
    assert result.mlt_path == root / "out" / "promo" / "timeline.mlt"
    assert result.otio_path.is_file()
    assert result.mlt_path.is_file()
    json.loads(result.otio_path.read_text(encoding="utf-8"))
    ElementTree.fromstring(result.mlt_path.read_text(encoding="utf-8"))
