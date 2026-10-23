"""Project scaffolding from the packaged template.

A media project is a directory described by a single ``project.yaml`` manifest. Phase 0
implements creation only; rendering and asset staging arrive in phase 1.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")

TEMPLATE_SUBDIRECTORIES = (
    "sources",
    "assets/3d",
    "assets/video",
    "assets/audio",
    "assets/music",
    "assets/design",
    "scripts",
    "out/frames",
    "out/video",
    "out/images",
    "out/reports",
    "docs",
)

TEMPLATE_FILES = ("project.yaml", "README.md", ".gitignore")


class ProjectError(RuntimeError):
    """Raised when a project cannot be created."""


@dataclass(frozen=True)
class ScaffoldedProject:
    """Result of a successful scaffold."""

    path: Path
    slug: str
    title: str


def repository_root() -> Path:
    """Return the checkout root that holds ``templates/project``."""

    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "templates" / "project").is_dir():
            return candidate
    raise ProjectError(
        "template directory not found; run media-studio from its git checkout "
        "(expected <repo>/templates/project)"
    )


def template_root() -> Path:
    """Return the project template directory."""

    return repository_root() / "templates" / "project"


def default_project_root() -> Path:
    """Default parent directory for new projects."""

    return Path.home() / "Projects"


def validate_slug(slug: str) -> str:
    """Return the slug when it is safe to use as a directory name."""

    if not SLUG_PATTERN.match(slug):
        raise ProjectError(
            f"invalid slug {slug!r}: use 2-63 characters of lowercase letters, digits, or hyphens, "
            "starting with a letter or digit"
        )
    return slug


def title_from_slug(slug: str) -> str:
    """Derive a human title from a slug."""

    return " ".join(part.capitalize() for part in slug.split("-"))


def scaffold_project(
    slug: str,
    root: Path | None = None,
    title: str | None = None,
) -> ScaffoldedProject:
    """Create a new project directory from the template.

    The target directory must not exist. Nothing is written outside ``root``.
    """

    validate_slug(slug)
    parent = Path(root).expanduser() if root is not None else default_project_root()
    target = parent / slug
    if target.exists():
        raise ProjectError(f"{target} already exists")

    resolved_title = (title or title_from_slug(slug)).strip()
    if not resolved_title:
        raise ProjectError("title must not be empty")

    template = template_root()
    parent.mkdir(parents=True, exist_ok=True)

    try:
        target.mkdir()
        for relative in TEMPLATE_SUBDIRECTORIES:
            (target / relative).mkdir(parents=True, exist_ok=True)
        for name in TEMPLATE_FILES:
            source = template / name
            if not source.is_file():
                raise ProjectError(f"template file missing: {source}")
            rendered = _render(source.read_text(encoding="utf-8"), slug, resolved_title)
            (target / name).write_text(rendered, encoding="utf-8")
    except OSError as error:
        shutil.rmtree(target, ignore_errors=True)
        raise ProjectError(f"could not create {target}: {error}") from error
    except ProjectError:
        shutil.rmtree(target, ignore_errors=True)
        raise

    return ScaffoldedProject(path=target, slug=slug, title=resolved_title)


def _render(text: str, slug: str, title: str) -> str:
    return text.replace("{{slug}}", slug).replace("{{title}}", title)
