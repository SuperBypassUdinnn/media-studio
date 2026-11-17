"""Font discovery, selection, and use.

Fonts are staged like any other asset, but they must also be *resolvable* by whatever renders the
text. Rather than installing them system-wide, a project gets its own fontconfig that adds its
staged font directory, so Inkscape and `melt`/`qtext` find the family and the project stays
self-contained.

Selection is a local funnel too: filter on facts from `fc-query` and `fontTools` (language
coverage, weight, spacing, embedding permission), then judge on pixels with a specimen sheet that
shows the project's own headline text.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .manifest import Project

# Fonts are staged under the design class like any other design asset, so the class directory is
# what a project's fontconfig must add. A `fonts/` subdirectory is honoured too, for projects that
# prefer to keep type separate from imagery. Fontconfig ignores files that are not fonts.
FONT_DIRECTORIES = ("assets/design", "assets/design/fonts")
CONFIG_NAME = "fonts.conf"
# The URN DTD is what modern fontconfig expects; a bare "fonts.dtd" leaves the file unread.
# No `<cachedir>` and no XDG_CACHE_HOME override on purpose: the user's cache keeps every system
# family visible, and the project's own directory is simply added to it.
FONTCONFIG_TEMPLATE = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
{dirs}
</fontconfig>
"""
# fsType bit 1 means the foundry forbids embedding the outlines at all.
EMBEDDING_FORBIDDEN_BIT = 0x0002
SPECIMEN_SIZES = (96, 44, 26)


class FontError(RuntimeError):
    """Raised when a font cannot be found, resolved, or safely embedded."""


@dataclass(frozen=True)
class FontInfo:
    """One installed or staged family."""

    family: str
    style: str
    file: Path
    languages: tuple[str, ...]
    monospace: bool

    @property
    def covers_indonesian(self) -> bool:
        return "id" in self.languages or "en" in self.languages


def font_environment(project: Project) -> dict[str, str]:
    """Environment that makes a project's staged fonts resolvable without installing them."""

    directories = [project.root / relative for relative in FONT_DIRECTORIES]
    directories[0].mkdir(parents=True, exist_ok=True)
    config = project.root / "out" / CONFIG_NAME
    config.parent.mkdir(parents=True, exist_ok=True)
    entries = "\n".join(
        f"  <dir>{directory}</dir>" for directory in directories if directory.is_dir()
    )
    config.write_text(FONTCONFIG_TEMPLATE.format(dirs=entries), encoding="utf-8")

    environment = dict(os.environ)
    environment["FONTCONFIG_FILE"] = str(config)
    return environment


def inventory(environment: dict[str, str] | None = None) -> list[FontInfo]:
    """Every family fontconfig can see, in a stable order."""

    completed = subprocess.run(
        [
            "fc-list",
            # Tabs: fontconfig joins the language list itself with `|`.
            "--format",
            "%{family[0]}\t%{style[0]}\t%{file}\t%{lang}\t%{spacing}\n",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise FontError(f"fc-list failed: {completed.stderr.strip()[:200]}")

    fonts: list[FontInfo] = []
    seen: set[tuple[str, str]] = set()
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or not parts[0] or not parts[2]:
            continue
        key = (parts[0], parts[1])
        if key in seen:
            continue
        seen.add(key)
        fonts.append(
            FontInfo(
                family=parts[0],
                style=parts[1] or "Regular",
                file=Path(parts[2]),
                languages=tuple(part for part in parts[3].split("|") if part),
                monospace=parts[4].strip() == "100",
            )
        )
    return sorted(fonts, key=lambda item: (item.family.lower(), item.style))


def search(
    fonts: list[FontInfo],
    query: str,
    *,
    require_languages: tuple[str, ...] = ("id",),
    monospace: bool | None = None,
    limit: int = 8,
) -> list[FontInfo]:
    """Rank families by name match, after dropping anything that cannot set the text."""

    words = [word for word in query.lower().replace("-", " ").split() if word]
    candidates = [
        font
        for font in fonts
        if all(language in font.languages for language in require_languages)
        and (monospace is None or font.monospace == monospace)
    ]
    if not words:
        return candidates[:limit]

    scored: list[tuple[int, str, FontInfo]] = []
    for font in candidates:
        haystack = f"{font.family} {font.style}".lower()
        score = sum(2 if word in haystack else 0 for word in words)
        if score:
            scored.append((score, font.family.lower(), font))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [font for _score, _family, font in scored[:limit]]


def embedding_permission(path: Path) -> tuple[bool, str]:
    """Whether a font's own flags allow embedding, read from its OS/2 table."""

    from fontTools.ttLib import TTFont  # imported here so the toolkit loads without it

    try:
        with TTFont(path, lazy=True) as font:
            os2 = font.get("OS/2")
            if os2 is None:
                return True, "no fsType recorded"
            value = int(os2.fsType)
    except Exception as error:
        raise FontError(f"{path.name} could not be read: {error}") from error

    if value & EMBEDDING_FORBIDDEN_BIT:
        return False, f"fsType {value:#06x} forbids embedding"
    return True, f"fsType {value:#06x} allows embedding"


def resolved_family(family: str, environment: dict[str, str] | None = None) -> str:
    """The family fontconfig would actually use for a request, or "" when it cannot be asked.

    Text producers take a family name and silently substitute one when it is absent, which is what
    a handed-off project shows as a clip problem. Asking fontconfig turns that into a prediction.
    """

    completed = subprocess.run(
        ["fc-match", "--format", "%{family[0]}", family],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def family_of(path: Path, environment: dict[str, str] | None = None) -> str:
    """The family name a renderer should ask for."""

    completed = subprocess.run(
        ["fc-query", "--format", "%{family[0]}", str(path)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    family = completed.stdout.strip()
    if completed.returncode != 0 or not family:
        raise FontError(f"could not read a family name from {path.name}")
    return family


def specimen_sheet(
    fonts: list[FontInfo],
    text: str,
    destination: Path,
    *,
    environment: dict[str, str] | None = None,
    width: int = 560,
) -> Path:
    """Render candidates side by side with the project's own words.

    One image per batch is what keeps a font decision cheap: a reviewer compares families in
    context instead of reading descriptions of them.
    """

    if not fonts:
        raise FontError("a specimen sheet needs at least one font")
    work = destination.parent / ".specimens"
    work.mkdir(parents=True, exist_ok=True)
    cells: list[Path] = []
    for index, font in enumerate(fonts, start=1):
        svg = work / f"{index:02d}-{font.family.replace(' ', '-')}.svg"
        rows = [
            f'<text id="title-{index}" x="24" y="60" font-family="{font.family}" '
            f'font-size="26" fill="#111111">{font.family} {font.style}</text>'
        ]
        y = 150
        for size in SPECIMEN_SIZES:
            rows.append(
                f'<text x="24" y="{y}" font-family="{font.family}" font-size="{size}" '
                f'fill="#111111">{escape(text)}</text>'
            )
            y += size + 34
        rows.append(
            f'<text x="24" y="{y + 20}" font-family="{font.family}" font-size="30" '
            'fill="#111111">0123456789 HAMBAI 4:5</text>'
        )
        svg.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="880" height="{y + 60}" '
            f'viewBox="0 0 880 {y + 60}"><rect width="880" height="{y + 60}" fill="#ffffff"/>'
            + "".join(rows)
            + "</svg>",
            encoding="utf-8",
        )
        cell = work / f"{index:02d}.png"
        _inkscape(svg, cell, width=width, environment=environment)
        cells.append(cell)

    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "magick",
            "montage",
            *[str(cell) for cell in cells],
            "-tile",
            "1x",
            "-geometry",
            "+8+8",
            "-background",
            "#f2f2f2",
            str(destination),
        ],
        capture_output=True,
        check=False,
        env=environment,
    )
    if not destination.is_file():
        raise FontError(f"could not write the specimen sheet to {destination}")
    return destination


def escape(text: str) -> str:
    """XML-escape text that lands inside an SVG element."""

    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def run_inkscape(
    arguments: list[str],
    *,
    destination: Path,
    environment: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    """Run one Inkscape export and retry once when the process dies during startup.

    Inkscape aborts intermittently under parallel load: a ``Gio::DBus::Error`` escapes as
    SIGABRT before any export work happens, and a second attempt on a fresh process
    succeeds. Only that transient crash is retried; a real error (bad SVG, bad flags)
    fails fast without a second run.
    """

    last: subprocess.CompletedProcess[str] | None = None
    for _attempt in range(2):
        completed = subprocess.run(
            ["inkscape", *arguments],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode == 0 and destination.is_file():
            return completed
        last = completed
        if "Gio::DBus::Error" not in (completed.stderr or ""):
            break
    assert last is not None
    return last


def _inkscape(
    source: Path, destination: Path, *, width: int, environment: dict[str, str] | None
) -> None:
    completed = run_inkscape(
        [
            "--export-type=png",
            f"--export-filename={destination}",
            f"--export-width={width}",
            str(source),
        ],
        destination=destination,
        environment=environment,
    )
    if completed.returncode != 0 or not destination.is_file():
        raise FontError(f"inkscape failed on {source.name}: {completed.stderr.strip()[:200]}")
