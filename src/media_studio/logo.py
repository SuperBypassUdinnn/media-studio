"""Logo derivative kit: recolour a staged logo and render every variant at every size.

The kit reads ``project.design.logo``, traces raster sources through ``svg_kit.trace`` into
``out/images/logo/source.svg``, recolours the SVG into each variant (color / mono / reversed) with
ElementTree, renders each variant at each size through ``svg_kit.render_png``, runs a clear-space
check on the 1024 render and a minimum-size check on the 32 render, then writes a contact sheet and
a JSON report at ``out/reports/<slug>-logo.json``.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from .design import brand_tokens
from .manifest import Project
from .svg_kit import SvgError, render_png, trace

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
LOGO_DIRECTORY = "out/images/logo"
REPORT_DIRECTORY = "out/reports"
DEFAULT_SIZES = (1024, 512, 256, 128, 64, 32)
DEFAULT_VARIANTS = ("color", "mono", "reversed")
DEFAULT_CLEAR_SPACE_PCT = 8.0

MONO_COLOUR = "#101010"  # primary token default; the ink token is NOT used per Ruling R1
REVERSED_COLOUR = "#FFFFFF"


class LogoError(RuntimeError):
    """Raised when a logo variant cannot be derived or checked."""


@dataclass(frozen=True)
class LogoEntry:
    """One rendered logo variant at one size."""

    variant: str
    size: int
    png: Path
    problems: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class LogoReport:
    """Every variant at every size, plus the sheet, report, and aggregate problems."""

    entries: tuple[LogoEntry, ...]
    sheet: Path | None = None
    report_path: Path | None = None
    problems: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems and all(not entry.problems for entry in self.entries)


def _magick_stdout(command: list[str], label: str) -> str:
    """Run one magick command; raise LogoError with the stderr tail on failure."""

    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        tail = completed.stderr.strip()[-2000:]
        raise LogoError(f"{label} failed: {tail}")
    return completed.stdout


def _trimmed_inset(png_path: Path) -> tuple[float, float]:
    """Return ``(smallest_inset, canvas_width)`` from one PNG's non-transparent bounding box.

    ``identify -format "%@"`` reports the content bounding box as ``WxH+X+Y`` where X/Y is the
    left/top inset of the content from the canvas top-left and W/H is the content size. The right
    and bottom insets are ``canvas_width - (X + W)`` and ``canvas_height - (Y + H)``; the check
    uses the minimum inset across all four edges divided by canvas width.
    """

    box = _magick_stdout(
        ["magick", "identify", "-format", "%@\n", str(png_path)],
        f"magick identify {png_path}",
    )
    match = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", box)
    if not match:
        raise LogoError(f"could not read bounding box from {png_path}: {box.strip()!r}")
    content_w, content_h, offset_x, offset_y = (int(value) for value in match.groups())
    canvas = _magick_stdout(
        ["magick", "identify", "-format", "%w\n%h\n", str(png_path)],
        f"magick identify {png_path}",
    )
    try:
        canvas_width, canvas_height = (int(value) for value in canvas.strip().splitlines())
    except ValueError:
        raise LogoError(f"could not read canvas size from {png_path}: {canvas.strip()!r}") from None
    right = canvas_width - (offset_x + content_w)
    bottom = canvas_height - (offset_y + content_h)
    return float(min(offset_x, offset_y, right, bottom)), float(canvas_width)


def _mean_alpha(png_path: Path) -> float:
    """Return the mean alpha channel of one PNG (0..1)."""

    value = _magick_stdout(
        ["magick", "identify", "-format", "%[fx:mean.a]", str(png_path)],
        f"magick mean alpha {png_path}",
    )
    try:
        return float(value.strip())
    except ValueError:
        raise LogoError(f"could not read mean alpha from {png_path}: {value.strip()!r}") from None


def _logo_source(project: Project) -> Path:
    """Resolve the staged logo path declared by ``project.design.logo``."""

    if project.design is None or not project.design.logo:
        raise LogoError("project.yaml declares no design.logo asset")
    asset_id = project.design.logo
    relative = project.assets.get(asset_id)
    if not relative:
        raise LogoError(f"design.logo names asset {asset_id!r}, which is not declared")
    path = project.root / relative
    if not path.is_file():
        raise LogoError(f"logo is not staged at {relative}")
    return path


def _is_raster(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}


def _trace_source(source: Path, out_svg: Path) -> None:
    """Trace a raster logo into ``out_svg`` (kept as ``source.svg`` beside the variants)."""

    try:
        trace(
            source,
            out_svg,
            engine="auto",
            mode="color",
            colors=6,
            threshold=50,
            flatten_bg="#ffffff",
            fill_holes=1,
        )
    except SvgError as error:
        raise LogoError(f"could not trace logo {source}: {error}") from None


def _recolour(svg_path: Path, out_svg: Path, colour: str) -> list[str]:
    """Write ``svg_path`` recoloured to ``colour`` into ``out_svg``.

    Every solid ``fill``/``stroke`` attribute and ``style`` declaration becomes ``colour``;
    gradients (``url(...)`` references), ``none`` paints, and embedded images are left untouched.
    Returns notes for each paint left untouched so the report can flag them.
    """

    tree = ElementTree.parse(str(svg_path))
    notes: list[str] = []
    for element in tree.iter():
        tag = element.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag in ("linearGradient", "radialGradient", "image"):
            kind = "embedded image" if tag == "image" else "gradient paint"
            notes.append(f"{kind} left untouched in {svg_path.name}")
            continue
        attributes = element.attrib
        fill = attributes.get("fill")
        stroke = attributes.get("stroke")
        if fill is not None and not fill.startswith("url(") and fill != "none":
            attributes["fill"] = colour
        if stroke is not None and not stroke.startswith("url(") and stroke != "none":
            attributes["stroke"] = colour
        style = attributes.get("style")
        if style:
            attributes["style"] = _rewrite_style(style, colour)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(tree.getroot()).write(out_svg, encoding="unicode", xml_declaration=True)
    return notes


_STYLE_COLOUR = re.compile(r"(fill|stroke)\s*:\s*([^;]*)")


def _rewrite_style(style: str, colour: str) -> str:
    def repl(match: re.Match) -> str:
        name, value = match.group(1), match.group(2)
        if value.startswith("url(") or value == "none":
            return match.group(0)
        return f"{name}:{colour}"

    return _STYLE_COLOUR.sub(repl, style)


def _render_one(svg_path: Path, png_path: Path, size: int) -> None:
    """Render one variant at one size; raise LogoError on failure."""

    try:
        render_png(svg_path, png_path, width=size)
    except SvgError as error:
        raise LogoError(f"could not render at {size}: {error}") from None


def _render_all(
    project: Project,
    variants_svg: dict[str, Path],
    variants: tuple[str, ...],
    sizes: tuple[int, ...],
    clear_space_pct: float,
    problems: list[str],
) -> tuple[tuple[LogoEntry, ...], Path | None]:
    """Render every variant at every size; return entries and the contact sheet path.

    The 1024 and 32 renders are always produced (even when absent from ``sizes``) so the clear
    space and minimum-size checks can run; entries only cover the requested sizes.
    """

    logo_dir = project.root / LOGO_DIRECTORY
    logo_dir.mkdir(parents=True, exist_ok=True)

    check_sizes = set(sizes) | {1024, 32}
    rendered: dict[tuple[str, int], Path] = {}
    for variant in variants:
        svg_path = variants_svg[variant]
        for size in check_sizes:
            png_path = logo_dir / f"{variant}-{size}.png"
            _render_one(svg_path, png_path, size)
            rendered[(variant, size)] = png_path

    # Clear-space (1024) and minimum-size (32) checks always run, even when those sizes are not
    # part of the delivered set; their problems go into the aggregate report problems.
    for variant in variants:
        png_1024 = rendered[(variant, 1024)]
        inset, canvas_width = _trimmed_inset(png_1024)
        if canvas_width <= 0:
            problems.append(f"{variant} 1024: canvas width is zero, clear space cannot be measured")
        elif inset / canvas_width < clear_space_pct / 100:
            problems.append(
                f"{variant} 1024: clear space {inset / canvas_width * 100:.1f}% is below "
                f"{clear_space_pct}%"
            )
        png_32 = rendered[(variant, 32)]
        mean_alpha = _mean_alpha(png_32)
        if mean_alpha < 0.005:
            problems.append(f"{variant} 32: mean alpha {mean_alpha:.4f} is below 0.005")

    entries: list[LogoEntry] = []
    for variant in variants:
        for size in sizes:
            entries.append(
                LogoEntry(
                    variant=variant,
                    size=size,
                    png=rendered[(variant, size)],
                    problems=(),
                )
            )

    pngs = [entry.png for entry in entries]
    sheet = logo_dir / "logo-sheet.png"
    completed = subprocess.run(
        [
            "magick",
            "montage",
            *[str(path) for path in pngs],
            "-label",
            "%f",
            "-tile",
            "3x",
            "-geometry",
            "420x420+10+10",
            "-background",
            "#222222",
            "-fill",
            "white",
            "-pointsize",
            "16",
            str(sheet),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0 and sheet.is_file():
        sheet_path = sheet
    else:
        sheet_path = None
        problems.append(
            "magick montage could not build the logo contact sheet: "
            f"{completed.stderr.strip()[:2000]}"
        )
    return tuple(entries), sheet_path


def _write_report(
    project: Project,
    entries: tuple[LogoEntry, ...],
    sheet: Path | None,
    problems: list[str],
    notes: list[str],
) -> Path:
    """Write ``out/reports/<slug>-logo.json`` and return its path."""

    report_path = project.root / REPORT_DIRECTORY / f"{project.slug}-logo.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "project": project.slug,
        "entries": [
            {
                "variant": entry.variant,
                "size": entry.size,
                "png": str(entry.png.relative_to(project.root)),
                "problems": list(entry.problems),
            }
            for entry in entries
        ],
        "sheet": str(sheet.relative_to(project.root)) if sheet else None,
        "problems": list(problems),
        "notes": list(notes),
        "ok": not problems and all(not entry.problems for entry in entries),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report_path


def logo_kit(
    project: Project,
    *,
    sizes: tuple[int, ...] = DEFAULT_SIZES,
    variants: tuple[str, ...] = DEFAULT_VARIANTS,
    clear_space_pct: float = DEFAULT_CLEAR_SPACE_PCT,
) -> LogoReport:
    """Derive the full logo deliverable set for ``project``.

    ``variants`` must be a subset of ``("color", "mono", "reversed")``; unknown names are rejected.
    Returns a :class:`LogoReport` whose ``problems`` are non-empty when any check fails.
    """

    for name in variants:
        if name not in ("color", "mono", "reversed"):
            raise LogoError(f"unknown logo variant {name!r}; use color, mono or reversed")

    source = _logo_source(project)
    logo_dir = project.root / LOGO_DIRECTORY
    logo_dir.mkdir(parents=True, exist_ok=True)

    if _is_raster(source):
        traced = logo_dir / "source.svg"
        _trace_source(source, traced)
        base_svg = traced
        problems: list[str] = []
        notes: list[str] = [f"logo {source.name} is raster; traced to source.svg before derivation"]
    else:
        base_svg = source
        problems = []
        notes = []

    mono_colour = brand_tokens(project).get("primary", MONO_COLOUR)
    recolour_map = {
        "color": None,
        "mono": mono_colour,
        "reversed": REVERSED_COLOUR,
    }
    variants_svg: dict[str, Path] = {}
    for variant in variants:
        colour = recolour_map[variant]
        out_svg = logo_dir / f"{variant}.svg"
        if colour is None:
            out_svg.write_text(base_svg.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            variant_notes = _recolour(base_svg, out_svg, colour)
            notes.extend(variant_notes)
            problems.extend(f"{variant}: {note}" for note in variant_notes)
        variants_svg[variant] = out_svg

    entries, sheet = _render_all(project, variants_svg, variants, sizes, clear_space_pct, problems)
    report_path = _write_report(project, entries, sheet, problems, notes)
    return LogoReport(
        entries=entries,
        sheet=sheet,
        report_path=report_path,
        problems=tuple(problems),
        notes=tuple(notes),
    )
