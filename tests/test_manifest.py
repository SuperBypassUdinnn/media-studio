"""Tests for manifest loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_studio.manifest import (
    ManifestError,
    load_project,
    resolve_project,
    validate_project,
)
from media_studio.project import scaffold_project


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return scaffold_project("demo-reel", root=tmp_path).path


def write_manifest(root: Path, body: str) -> Path:
    (root / "project.yaml").write_text(body, encoding="utf-8")
    return root


def test_loads_scaffolded_template(project_root: Path) -> None:
    project = load_project(project_root)

    assert project.slug == "demo-reel"
    assert project.title == "Demo Reel"
    assert project.loudness_lufs == -14.0
    assert project.budget.max_asset_bytes == 209715200
    assert project.budget.require_cc0_music is False
    assert project.brand.palette["primary"] == "#0B3D2E"


def test_resolves_slug_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    scaffold_project("from-slug", root=tmp_path / "Projects")

    assert resolve_project("from-slug") == (tmp_path / "Projects" / "from-slug").resolve()


def test_resolves_manifest_path(project_root: Path) -> None:
    assert resolve_project(project_root / "project.yaml") == project_root.resolve()


def test_missing_manifest_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match=r"no project\.yaml"):
        load_project(tmp_path)


def test_invalid_yaml_is_an_error(project_root: Path) -> None:
    write_manifest(project_root, "slug: [unclosed\n")

    with pytest.raises(ManifestError, match="not valid YAML"):
        load_project(project_root)


def test_non_mapping_manifest_is_an_error(project_root: Path) -> None:
    write_manifest(project_root, "- just\n- a list\n")

    with pytest.raises(ManifestError, match="mapping"):
        load_project(project_root)


def test_invalid_slug_in_manifest_is_an_error(project_root: Path) -> None:
    write_manifest(project_root, "slug: Not A Slug\n")

    with pytest.raises(ManifestError, match="invalid slug"):
        load_project(project_root)


def test_deliverable_without_id_is_an_error(project_root: Path) -> None:
    write_manifest(project_root, "slug: demo\ndeliverables:\n  - kind: video\n")

    with pytest.raises(ManifestError, match="needs an id"):
        load_project(project_root)


def test_non_numeric_fps_is_an_error(project_root: Path) -> None:
    write_manifest(project_root, "slug: demo\ndeliverables:\n  - {id: a, fps: fast}\n")

    with pytest.raises(ManifestError, match="fps must be a number"):
        load_project(project_root)


def test_validation_accepts_the_template(project_root: Path) -> None:
    report = validate_project(load_project(project_root))

    assert report.ok, report.errors
    assert report.warnings == []


def test_validation_warns_about_assets_that_are_not_staged(project_root: Path) -> None:
    body = (project_root / "project.yaml").read_text(encoding="utf-8")
    (project_root / "project.yaml").write_text(
        body.replace("assets: {}", "assets:\n  hero: assets/video/hero.mp4"),
        encoding="utf-8",
    )

    report = validate_project(load_project(project_root))

    assert report.ok
    assert report.warnings == ["assets.hero: assets/video/hero.mp4 is not staged yet"]


def test_validation_reports_structural_problems(project_root: Path) -> None:
    write_manifest(
        project_root,
        """
slug: demo
deliverables:
  - {id: master, kind: video, resolution: 1920x1080, loudness_lufs: -14}
  - {id: master, kind: holo, resolution: wide, loudness_lufs: -2}
brand:
  palette: {primary: "0B3D2E"}
budget:
  max_asset_bytes: 0
  max_project_bytes: 0
assets:
  clip: /etc/passwd
  escaped: ../outside.png
""",
    )

    report = validate_project(load_project(project_root))

    assert not report.ok
    joined = "\n".join(report.errors)
    assert "duplicate deliverable id: master" in joined
    assert "kind 'holo'" in joined
    assert "must be WxH" in joined
    assert "outside the plausible range" in joined
    assert "brand.palette.primary" in joined
    assert "budget.max_asset_bytes must be positive" in joined
    assert "must be a path inside the project" in joined


def test_unknown_fields_are_rejected(project_root: Path) -> None:
    write_manifest(
        project_root,
        """
slug: demo
titel: typo
deliverables:
  - {id: a, kind: video, resolutoin: 1920x1080}
brand:
  colours: {primary: "#ffffff"}
budget:
  max_asset_byte: 100
""",
    )

    report = validate_project(load_project(project_root))

    assert not report.ok
    joined = "\n".join(report.errors)
    assert "unknown manifest field: titel" in joined
    assert "unknown deliverable field: resolutoin" in joined
    assert "unknown brand field: colours" in joined
    assert "unknown budget field: max_asset_byte" in joined


def test_video_titles_validate(project_root: Path) -> None:
    """The renderer supports titles, so the validator must accept them, entries and all."""

    write_manifest(
        project_root,
        """
slug: demo
deliverables:
  - {id: a, kind: video, resolution: 640x360}
assets:
  clip: assets/video/clip.mp4
video:
  shots:
    - {asset: clip}
  titles:
    - {text: "Hello", start: 1.0, duration: 2.0, size: 32, position: "+10+10"}
""",
    )

    report = validate_project(load_project(project_root))

    assert report.ok, report.errors
    # The declared clip is not staged yet, which is a warning and never an error.
    assert report.warnings == ["assets.clip: assets/video/clip.mp4 is not staged yet"]


def test_a_bad_field_inside_a_title_is_still_rejected(project_root: Path) -> None:
    write_manifest(
        project_root,
        """
slug: demo
deliverables:
  - {id: a, kind: video, resolution: 640x360}
assets:
  clip: assets/video/clip.mp4
video:
  shots:
    - {asset: clip}
  titles:
    - {text: "Hello", start: 1.0, duration: 2.0, colour: "#ffffff", sizee: 32}
""",
    )

    report = validate_project(load_project(project_root))

    assert not report.ok
    assert "unknown video.titles field: sizee" in "\n".join(report.errors)


def test_declared_asset_needs_a_sidecar(project_root: Path) -> None:
    body = (project_root / "project.yaml").read_text(encoding="utf-8")
    (project_root / "project.yaml").write_text(
        body.replace("assets: {}", "assets:\n  hero: assets/video/hero.mp4"),
        encoding="utf-8",
    )
    hero = project_root / "assets" / "video" / "hero.mp4"
    hero.write_bytes(b"payload")

    report = validate_project(load_project(project_root))

    assert not report.ok
    assert any("has no .license sidecar" in problem for problem in report.errors)

    (hero.parent / "hero.mp4.license").write_text(
        "SPDX-License-Identifier: NONE\nClass: unverified\n", encoding="utf-8"
    )
    report = validate_project(load_project(project_root))

    assert not report.ok
    assert any("quarantined with an unknown licence" in problem for problem in report.errors)

    (hero.parent / "hero.mp4.license").write_text(
        "SPDX-License-Identifier: CC0-1.0\nClass: video\n", encoding="utf-8"
    )
    report = validate_project(load_project(project_root))

    assert report.ok, report.errors
    assert report.warnings == []


def test_non_numeric_budget_is_a_manifest_error(project_root: Path) -> None:
    write_manifest(project_root, "slug: demo\nbudget:\n  max_asset_bytes: lots\n")

    with pytest.raises(ManifestError, match=r"budget\.max_asset_bytes must be a whole number"):
        load_project(project_root)


def test_project_loudness_defaults_to_minus_14(project_root: Path) -> None:
    write_manifest(project_root, "slug: demo\ndeliverables:\n  - {id: a, kind: video}\n")

    assert load_project(project_root).loudness_lufs == -14.0
