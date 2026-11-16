"""Tests for the multimodal visual critic (`critique_promo`) and `ms promo critique`.

All tests are 100% offline: fixtures are synthesized with FFmpeg locally and no
live network or generative provider is touched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from media_studio.cli import main
from media_studio.manifest import load_project
from media_studio.promo import (
    DEFAULT_DELIVERABLES,
    LAYOUT_ZONE_BOXES,
    PromoBeat,
    PromoPlan,
    critique_promo,
)

HOOK_TEXT = "Short hook copy."
DEMO_TEXT = "Mid demo copy here."
CTA_TEXT = "Act now."

CLEAN_BEATS = (
    PromoBeat(kind="hook", text=HOOK_TEXT, duration=3.0),
    PromoBeat(kind="demo", text=DEMO_TEXT, duration=5.0),
    PromoBeat(kind="cta", text=CTA_TEXT, duration=4.0),
)


def _make_project(tmp_path: Path, slug: str = "crit-demo") -> Path:
    root = tmp_path / slug
    for rel in ("assets/video", "assets/audio", "out/video", "out/promo"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    manifest = {
        "slug": slug,
        "title": "Critique Demo",
        "status": "draft",
        "deliverables": [
            {
                "id": "reel-9x16",
                "kind": "video",
                "container": "mp4",
                "resolution": "1080x1920",
                "fps": 30,
                "duration": 12.0,
                "loudness_lufs": -14.0,
            },
            {
                "id": "master-16x9",
                "kind": "video",
                "container": "mp4",
                "resolution": "1920x1080",
                "fps": 30,
                "duration": 12.0,
                "loudness_lufs": -14.0,
            },
        ],
        "brand": {
            "palette": {"primary": "#0F172A", "accent": "#38BDF8", "ink": "#F8FAFC"},
            "fonts": {"heading": "Noto Sans", "body": "Noto Sans"},
            "references": {"product": [], "spokesperson": [], "style_board": []},
            "voice": {"engine": "espeak-ng", "voice_id": "en-US", "rate": "+0%"},
        },
        "assets": {},
    }
    (root / "project.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def _encode_mp4(
    path: Path,
    width: int,
    height: int,
    duration: float,
    *,
    color_range: str | None = "tv",
    color_space: str | None = "bt709",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
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
    ]
    if color_range is not None:
        cmd.extend(["-color_range", color_range])
    if color_space is not None:
        cmd.extend(
            ["-colorspace", color_space, "-color_primaries", color_space, "-color_trc", color_space]
        )
    cmd.append(str(path))
    subprocess.run(cmd, check=True)


def _plan(project: Path, beats: tuple[PromoBeat, ...] = CLEAN_BEATS) -> PromoPlan:
    return PromoPlan(
        project_slug=load_project(project).slug,
        aspects=("9:16", "16:9"),
        script=" ".join(beat.text for beat in beats),
        beats=beats,
        estimated_gen_calls=0,
        references=(),
        total_duration=sum(beat.duration for beat in beats),
    )


def _write_deliverables(project: Path, *, compliant: bool = True) -> None:
    kwargs = {} if compliant else {"color_range": "pc", "color_space": "smpte170m"}
    _encode_mp4(project / "out" / "video" / "reel-9x16.mp4", 1080, 1920, 2.0, **kwargs)
    _encode_mp4(project / "out" / "video" / "master-16x9.mp4", 1920, 1080, 2.0, **kwargs)


def test_clean_deliverable_passes_critique_ok_true_no_defects(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)

    report = critique_promo(load_project(project), plan=plan)

    assert report["project"] == "crit-demo"
    assert report["ok"] is True
    assert report["defects"] == []
    assert report["summary"] == {"errors": 0, "warnings": 0, "critique_passed": True}


def test_clean_passes_when_plan_loaded_from_default_path(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)
    from media_studio.promo import save_promo_plan

    save_promo_plan(plan, project / "out" / "promo" / "promo-plan.json")

    report = critique_promo(load_project(project))

    assert report["ok"] is True
    assert report["defects"] == []


def test_caption_collision_is_error_with_corrective_action(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)
    overlapping = dict(LAYOUT_ZONE_BOXES["hero-center"])
    overlapping["card"] = {"top": 0.30, "bottom": 0.90, "left": 0.10, "right": 0.90}
    overlapping["caption"] = {"top": 0.78, "bottom": 0.92, "left": 0.10, "right": 0.90}

    with pytest.MonkeyPatch().context() as m:
        m.setitem(LAYOUT_ZONE_BOXES, "hero-center", overlapping)
        report = critique_promo(load_project(project), plan=plan)

    errors = [d for d in report["defects"] if d["severity"] == "error"]
    assert errors, "expected at least one error defect for the collision"
    collision = [d for d in errors if "intersects caption band" in d["defect"]]
    assert collision, "expected a caption-band intersection defect"
    for defect in collision:
        assert defect["category"] == "layout"
        assert "pixels" in defect["defect"]
        assert defect["corrective_action"] == (
            "Shift card_anchor_y up or reduce card_scale to clear safe zone"
        )
        assert defect["deliverable"] in DEFAULT_DELIVERABLES
    assert any(d["beat_index"] == 0 and d["timestamp"] == 0.0 for d in collision)
    assert report["ok"] is False
    assert report["summary"]["critique_passed"] is False


def test_safe_zone_intrusion_is_error_with_corrective_action(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)
    intruding = dict(LAYOUT_ZONE_BOXES["hero-center"])
    intruding["card"] = {"top": 0.05, "bottom": 0.12, "left": 0.10, "right": 0.90}

    with pytest.MonkeyPatch().context() as m:
        m.setitem(LAYOUT_ZONE_BOXES, "hero-center", intruding)
        report = critique_promo(load_project(project), plan=plan)

    errors = [d for d in report["defects"] if d["severity"] == "error"]
    assert errors
    for defect in errors:
        assert defect["category"] == "layout"
        assert "safe zone" in defect["defect"]
        assert defect["corrective_action"]
        assert report["ok"] is False


def test_high_text_density_is_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    rushed = PromoBeat(kind="demo", text="word " * 40, duration=4.0)
    plan = _plan(project, beats=(rushed,))

    report = critique_promo(load_project(project), plan=plan)

    warnings = [d for d in report["defects"] if d["severity"] == "warning"]
    assert warnings
    for defect in warnings:
        assert defect["category"] == "pacing"
        assert defect["defect"] == "High text density (10.0 wps)"
        assert defect["corrective_action"] == "Extend beat duration or trim text"
        assert defect["beat_index"] == 0
        assert defect["timestamp"] == 0.0
    assert report["ok"] is True
    assert report["summary"]["warnings"] >= 1


def test_pacing_duration_anomalies_are_warnings(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    beats = (
        PromoBeat(kind="hook", text="Too short.", duration=0.5),
        PromoBeat(kind="cta", text="Way too long beat here.", duration=15.0),
    )
    plan = _plan(project, beats=beats)

    report = critique_promo(load_project(project), plan=plan)

    pacing = [d for d in report["defects"] if d["category"] == "pacing"]
    assert len(pacing) == 4  # 2 beats x 2 deliverables
    assert {d["beat_index"] for d in pacing} == {0, 1}
    for defect in pacing:
        assert defect["severity"] == "warning"
        assert defect["corrective_action"] == ("Adjust beat duration to fall within 1.0s-12.0s")
    assert report["ok"] is True


def test_non_bt709_color_metadata_is_flagged(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _encode_mp4(
        project / "out" / "video" / "reel-9x16.mp4",
        1080,
        1920,
        2.0,
        color_range="pc",
        color_space="smpte170m",
    )
    _encode_mp4(project / "out" / "video" / "master-16x9.mp4", 1920, 1080, 2.0)
    plan = _plan(project)

    report = critique_promo(load_project(project), plan=plan)

    color = [d for d in report["defects"] if d["category"] == "color"]
    assert len(color) == 1
    defect = color[0]
    assert defect["severity"] == "error"
    assert defect["deliverable"] == "reel-9x16"
    assert defect["beat_index"] == -1
    assert "yuvj420p" in defect["defect"] or "pc" in defect["defect"]
    assert defect["corrective_action"]
    assert report["ok"] is False


def test_missing_deliverable_is_error(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _encode_mp4(project / "out" / "video" / "reel-9x16.mp4", 1080, 1920, 2.0)
    plan = _plan(project)

    report = critique_promo(load_project(project), plan=plan)

    missing = [d for d in report["defects"] if d["category"] == "missing"]
    assert len(missing) == 1
    assert missing[0]["severity"] == "error"
    assert missing[0]["deliverable"] == "master-16x9"
    assert missing[0]["corrective_action"]
    assert report["ok"] is False


def test_cli_promo_critique_clean_passes(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)
    from media_studio.promo import save_promo_plan

    save_promo_plan(plan, project / "out" / "promo" / "promo-plan.json")

    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        rc = main(["promo", "critique", str(project)])
    assert rc == 0
    assert "No defects found — critique passed." in buffer.getvalue()


def test_cli_promo_critique_numbered_shows_row_column(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)
    from media_studio.promo import save_promo_plan

    save_promo_plan(plan, project / "out" / "promo" / "promo-plan.json")
    overlapping = dict(LAYOUT_ZONE_BOXES["hero-center"])
    overlapping["card"] = {"top": 0.30, "bottom": 0.90, "left": 0.10, "right": 0.90}
    overlapping["caption"] = {"top": 0.78, "bottom": 0.92, "left": 0.10, "right": 0.90}

    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with pytest.MonkeyPatch().context() as m, redirect_stdout(buffer):
        m.setitem(LAYOUT_ZONE_BOXES, "hero-center", overlapping)
        rc = main(["promo", "critique", str(project), "--numbered"])
    assert rc == 1
    output = buffer.getvalue()
    assert "#" in output
    assert "Deliverable" in output
    assert "intersects caption band" in output


def test_cli_promo_critique_errors_return_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)
    from media_studio.promo import save_promo_plan

    save_promo_plan(plan, project / "out" / "promo" / "promo-plan.json")
    overlapping = dict(LAYOUT_ZONE_BOXES["hero-center"])
    overlapping["card"] = {"top": 0.30, "bottom": 0.90, "left": 0.10, "right": 0.90}
    overlapping["caption"] = {"top": 0.78, "bottom": 0.92, "left": 0.10, "right": 0.90}
    monkeypatch.setitem(LAYOUT_ZONE_BOXES, "hero-center", overlapping)
    rc = main(["promo", "critique", str(project), "--numbered"])
    assert rc == 1


def test_cli_promo_critique_out_writes_json_report(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_deliverables(project, compliant=True)
    plan = _plan(project)
    from media_studio.promo import save_promo_plan

    save_promo_plan(plan, project / "out" / "promo" / "promo-plan.json")
    out_path = tmp_path / "critique.json"

    rc = main(
        [
            "promo",
            "critique",
            str(project),
            "--out",
            str(out_path),
        ]
    )
    assert rc == 0
    assert out_path.is_file()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert set(payload) == {"project", "ok", "defects", "summary"}
    assert payload["ok"] is True
    assert payload["summary"]["critique_passed"] is True


def test_cli_promo_critique_deliverables_limits_scope(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _encode_mp4(project / "out" / "video" / "reel-9x16.mp4", 1080, 1920, 2.0)
    plan = _plan(project)
    from media_studio.promo import save_promo_plan

    save_promo_plan(plan, project / "out" / "promo" / "promo-plan.json")

    rc = main(["promo", "critique", str(project), "--deliverables", "reel-9x16"])
    assert rc == 0
