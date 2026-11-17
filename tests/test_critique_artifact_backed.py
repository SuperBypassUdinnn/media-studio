"""Tests for the artifact-fingerprinted, dynamic-duration quality critic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from media_studio.critique import (
    VISION_RUBRIC_AXES,
    critique_palette,
    critique_project,
)
from media_studio.manifest import load_project

FAKE_MP4 = b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00isommp42dummy-payload-for-sha256-binding"

QC_PASS = {
    "ok": True,
    "checks": {
        "has_video": True,
        "has_audio": True,
        "duration_matches": True,
        "loudness_within_half_lu": True,
        "true_peak_below_ceiling": True,
        "no_black_frames": True,
        "no_frozen_frames": True,
    },
}


def _stage_project(
    tmp_path: Path,
    *,
    duration: float | None = 15.0,
    graph_total: float | None = None,
) -> tuple[Path, str]:
    """Write a minimal project with one dummy deliverable MP4 and a passing vision report."""
    proj_dir = tmp_path / "artifact"
    (proj_dir / "out" / "video").mkdir(parents=True)
    (proj_dir / "out" / "reports").mkdir(parents=True)

    deliverable: dict[str, object] = {
        "id": "reel-9x16",
        "kind": "video",
        "container": "mp4",
        "resolution": "1080x1920",
        "fps": 30,
        "loudness_lufs": -14.0,
    }
    if duration is not None:
        deliverable["duration"] = duration

    lines = [
        "slug: artifact",
        "title: Artifact",
        "status: draft",
        "deliverables:",
        f"  - id: {deliverable['id']}",
        f"    kind: {deliverable['kind']}",
        f"    container: {deliverable['container']}",
        f"    resolution: {deliverable['resolution']}",
        f"    fps: {deliverable['fps']}",
        f"    loudness_lufs: {deliverable['loudness_lufs']}",
    ]
    if duration is not None:
        lines.insert(7, f"    duration: {duration}")
    lines += [
        "brand:",
        "  palette:",
        "    primary: '#080B11'",
        "    secondary: '#1E293B'",
        "    ink: '#F8FAFC'",
        "assets: {}",
    ]
    (proj_dir / "project.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    vpath = proj_dir / "out" / "video" / "reel-9x16.mp4"
    vpath.write_bytes(FAKE_MP4)
    digest = hashlib.sha256(FAKE_MP4).hexdigest()

    report = {
        "stage": "video",
        "target_sha256": digest,
        "evaluated_by": "test-suite",
        "keyframes_inspected": ["s1.png"],
        "scores": {axis: 5 for axis in VISION_RUBRIC_AXES},
        "defects": [],
        "passed": True,
    }
    (proj_dir / "out" / "reports" / "vision-critique.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    if graph_total is not None:
        scenes = [{"id": f"s{i}", "duration": graph_total / 2} for i in (1, 2)]
        (proj_dir / "scene-graph.json").write_text(
            json.dumps({"project_slug": "artifact", "scenes": scenes}), encoding="utf-8"
        )

    return proj_dir, digest


def test_critique_derives_expected_duration_from_deliverables(tmp_path: Path) -> None:
    proj_dir, _digest = _stage_project(tmp_path, duration=15.0)
    project = load_project(proj_dir)

    captured: dict[str, object] = {}

    def fake_qc(
        deliverable: Path, *, expected_duration: float | None, **kwargs: object
    ) -> dict[str, object]:
        captured["expected_duration"] = expected_duration
        captured["target_lufs"] = kwargs.get("target_lufs")
        return dict(QC_PASS)

    with patch("media_studio.video.qc_report", side_effect=fake_qc):
        result = critique_project(project, stage="video", deliverables=("reel-9x16",))

    assert result["ok"] is True
    assert captured["expected_duration"] == 15.0
    assert captured["target_lufs"] == -14.0


def test_critique_falls_back_to_scene_graph_total_duration(tmp_path: Path) -> None:
    proj_dir, _digest = _stage_project(tmp_path, duration=None, graph_total=22.5)
    project = load_project(proj_dir)

    captured: dict[str, object] = {}

    def fake_qc(
        deliverable: Path, *, expected_duration: float | None, **kwargs: object
    ) -> dict[str, object]:
        captured["expected_duration"] = expected_duration
        return dict(QC_PASS)

    with patch("media_studio.video.qc_report", side_effect=fake_qc):
        result = critique_project(project, stage="video", deliverables=("reel-9x16",))

    assert result["ok"] is True
    assert captured["expected_duration"] == 22.5


def test_validate_vision_rubric_rejects_hash_mismatch(tmp_path: Path) -> None:
    proj_dir, _digest = _stage_project(tmp_path, duration=15.0)
    project = load_project(proj_dir)
    report_path = proj_dir / "out" / "reports" / "vision-critique.json"
    report_path.write_text(
        json.dumps(
            {
                "stage": "video",
                "target_sha256": "0" * 64,
                "scores": {axis: 5 for axis in VISION_RUBRIC_AXES},
                "passed": True,
            }
        ),
        encoding="utf-8",
    )

    with patch("media_studio.video.qc_report", return_value=dict(QC_PASS)):
        result = critique_project(project, stage="video", deliverables=("reel-9x16",))

    assert result["ok"] is False
    assert any(
        d["severity"] == "error"
        and d["category"] == "vision-rubric"
        and "target_sha256" in d["defect"]
        and "sha256" in d["defect"].lower()
        for d in result["defects"]
    ), result["defects"]


def test_critique_palette_flags_underexposed_frame_below_floor(tmp_path: Path) -> None:
    proj_dir = tmp_path / "dark"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(
        "slug: dark\ntitle: Dark\nstatus: draft\ndeliverables: []\n"
        "brand:\n  palette:\n    primary: '#080B11'\n    secondary: '#1E293B'\n"
        "    ink: '#F8FAFC'\nassets: {}\n",
        encoding="utf-8",
    )
    project = load_project(proj_dir)

    dark_frame = tmp_path / "underexposed.png"
    Image.new("RGB", (64, 36), (10, 10, 10)).save(dark_frame)
    defects = critique_palette(project, [dark_frame])
    assert any(
        d["severity"] == "error"
        and d["category"] == "exposure"
        and "underexposed" in d["defect"].lower()
        for d in defects
    ), defects

    # A frame at/above the floor must not trigger the exposure defect.
    mid_frame = tmp_path / "mid.png"
    Image.new("RGB", (64, 36), (100, 100, 100)).save(mid_frame)
    assert not any(d["category"] == "exposure" for d in critique_palette(project, [mid_frame]))


def test_validate_vision_rubric_accepts_bound_report(tmp_path: Path) -> None:
    """A well-formed report whose target_sha256 matches the artifact adds no binding defect."""
    proj_dir, digest = _stage_project(tmp_path, duration=15.0)
    project = load_project(proj_dir)
    report_path = proj_dir / "out" / "reports" / "vision-critique.json"
    report_path.write_text(
        json.dumps(
            {
                "stage": "video",
                "target_sha256": digest,
                "scores": {axis: 5 for axis in VISION_RUBRIC_AXES},
                "passed": True,
            }
        ),
        encoding="utf-8",
    )

    with patch("media_studio.video.qc_report", return_value=dict(QC_PASS)):
        result = critique_project(project, stage="video", deliverables=("reel-9x16",))

    assert result["ok"] is True
    assert not any(
        d["category"] == "vision-rubric" and "sha256" in d["defect"].lower()
        for d in result["defects"]
    )
