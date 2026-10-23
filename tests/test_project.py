"""Tests for project scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from media_studio.project import (
    TEMPLATE_FILES,
    TEMPLATE_SUBDIRECTORIES,
    ProjectError,
    scaffold_project,
    title_from_slug,
    validate_slug,
)


def test_scaffold_creates_layout_and_renders_manifest(tmp_path: Path) -> None:
    created = scaffold_project("demo-reel", root=tmp_path)

    assert created.path == tmp_path / "demo-reel"
    assert created.slug == "demo-reel"
    assert created.title == "Demo Reel"
    for relative in TEMPLATE_SUBDIRECTORIES:
        assert (created.path / relative).is_dir(), relative
    for name in TEMPLATE_FILES:
        assert (created.path / name).is_file(), name

    manifest = yaml.safe_load((created.path / "project.yaml").read_text(encoding="utf-8"))
    assert manifest["slug"] == "demo-reel"
    assert manifest["title"] == "Demo Reel"
    assert manifest["deliverables"][0]["kind"] == "video"


def test_scaffold_renders_readme_without_placeholders(tmp_path: Path) -> None:
    created = scaffold_project("brand-kit", root=tmp_path, title="Spring Brand Kit")

    readme = (created.path / "README.md").read_text(encoding="utf-8")
    assert "{{" not in readme
    assert "brand-kit" in readme
    assert "Spring Brand Kit" in readme


def test_scaffold_refuses_existing_directory(tmp_path: Path) -> None:
    (tmp_path / "taken").mkdir()

    with pytest.raises(ProjectError, match="already exists"):
        scaffold_project("taken", root=tmp_path)


@pytest.mark.parametrize(
    "slug", ["", "a", "Upper", "has space", "-leading", "trailing-", "with_underscore"]
)
def test_scaffold_rejects_invalid_slug(slug: str, tmp_path: Path) -> None:
    with pytest.raises(ProjectError):
        scaffold_project(slug, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_scaffold_rejects_blank_title(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="title"):
        scaffold_project("demo", root=tmp_path, title="   ")
    assert list(tmp_path.iterdir()) == []


def test_validate_slug_accepts_minimal_and_maximal(tmp_path: Path) -> None:
    assert validate_slug("ab") == "ab"
    assert validate_slug("a" * 63) == "a" * 63


def test_title_from_slug() -> None:
    assert title_from_slug("scr2026-motion-logo-v3") == "Scr2026 Motion Logo V3"


def test_scaffold_accepts_tilde_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    created = scaffold_project("tilde-project")

    assert created.path == tmp_path / "Projects" / "tilde-project"
