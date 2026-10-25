"""The design path: brand tokens to rendered variants, with checks that can fail.

Templates are SVG with placeholders, exported by Inkscape and finished by ImageMagick. The checks
are measurements, not opinions: WCAG contrast from the colours actually written into the file, text
boxes from Inkscape's own geometry query against the template's safe area, the smallest font size
present, and whether the export matches the size the variant asked for.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from .fonts import escape, family_of, font_environment
from .manifest import Project, VariantSpec

TEMPLATE_DIRECTORY = "templates/design"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
MIN_FONT_SIZE = 18.0
MIN_BODY_CONTRAST = 4.5
MIN_LARGE_CONTRAST = 3.0
LARGE_TEXT_SIZE = 32.0
DEFAULT_TOKENS = {"primary": "#101010", "accent": "#C8A24A", "ink": "#FFFFFF"}
FONT_ROLES = ("heading", "body")


class DesignError(RuntimeError):
    """Raised when a variant cannot be composed or exported."""


@dataclass(frozen=True)
class DesignReport:
    """What one render produced, and whether it passes."""

    variants: tuple[dict[str, object], ...]
    sheet: Path | None = None
    report_path: Path | None = None
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems and all(bool(item["ok"]) for item in self.variants)


def template_directory() -> Path:
    """The `templates/design` directory inside the checkout."""

    here = Path(__file__).resolve()
    for candidate in here.parents:
        directory = candidate / TEMPLATE_DIRECTORY
        if directory.is_dir() and list(directory.glob("*.svg")):
            return directory
    raise DesignError(
        "no design templates found; run media-studio from its git checkout "
        "(expected <repo>/templates/design/*.svg)"
    )


def load_template(name: str) -> str:
    """Read one template, refusing an unknown or escaping name."""

    path = (template_directory() / f"{name}.svg").resolve()
    if path.parent != template_directory() or not path.is_file():
        raise DesignError(f"no design template {name!r}")
    return path.read_text(encoding="utf-8")


def brand_tokens(project: Project) -> dict[str, str]:
    """Palette entries the templates need, with stated defaults when the manifest is quiet."""

    tokens = dict(DEFAULT_TOKENS)
    for name, value in project.brand.palette.items():
        if name in tokens:
            tokens[name] = value
    return tokens


def font_families(project: Project, environment: dict[str, str]) -> dict[str, str]:
    """Resolve the project's font roles to family names a renderer can ask for."""

    families: dict[str, str] = {}
    for role in FONT_ROLES:
        asset_id = project.design.fonts.get(role) if project.design else None
        if not asset_id:
            continue
        relative = project.assets.get(asset_id)
        if not relative:
            raise DesignError(f"font role {role!r} names asset {asset_id!r}, which is not declared")
        path = project.root / relative
        if not path.is_file():
            raise DesignError(f"font for {role!r} is not staged at {relative}")
        families[role] = family_of(path, environment)
    return families


def compose(project: Project, variant: VariantSpec, environment: dict[str, str]) -> str:
    """Substitute a template with brand tokens, fonts, and the variant's words."""

    tokens = brand_tokens(project)
    families = font_families(project, environment)
    if not families.get("heading") or not families.get("body"):
        # Falling back to a family every machine has keeps a render possible, and the report says
        # which family was actually used.
        families.setdefault("heading", "sans-serif")
        families.setdefault("body", "sans-serif")

    logo_href = ""
    if project.design is not None and project.design.logo:
        relative = project.assets.get(project.design.logo)
        if not relative:
            raise DesignError(
                f"design.logo names asset {project.design.logo!r}, which is undeclared"
            )
        logo_path = project.root / relative
        if not logo_path.is_file():
            raise DesignError(f"logo is not staged at {relative}")
        logo_href = logo_path.as_uri()

    values = {
        "width": str(variant.width),
        "height": str(variant.height),
        "headline": escape(variant.headline),
        "subhead": escape(variant.subhead),
        "primary": tokens["primary"],
        "accent": tokens["accent"],
        "ink": tokens["ink"],
        "font_heading": families["heading"],
        "font_body": families["body"],
        "logo_href": logo_href,
    }
    composed = load_template(variant.template)
    for name, value in values.items():
        composed = composed.replace(f"{{{{{name}}}}}", value)

    if not logo_href:
        # Drop the image element rather than leave an empty href behind.
        composed = re.sub(r"<image id=\"logo\".*?/>", "", composed, flags=re.DOTALL)
    return composed


def text_elements(svg_text: str) -> list[dict[str, object]]:
    """Every element the checks care about: text, background, and safe area."""

    root = ElementTree.fromstring(svg_text)
    found: list[dict[str, object]] = []
    for element in root.iter():
        tag = element.tag.replace(f"{{{SVG_NAMESPACE}}}", "")
        if tag not in ("text", "rect"):
            continue
        found.append(
            {
                "tag": tag,
                "id": element.get("id") or "",
                "role": element.get("data-role") or "",
                "background": element.get("data-bg") == "true",
                "safe": element.get("data-safe") == "true",
                "fill": element.get("fill") or "",
                "font_size": float(element.get("font-size") or 0),
            }
        )
    return found


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of `#rrggbb` or `#rrggbbaa`."""

    text = colour.strip().lstrip("#")
    if len(text) not in (6, 8):
        raise DesignError(f"cannot read colour {colour!r}")
    channels = [int(text[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two colours."""

    one, two = relative_luminance(first), relative_luminance(second)
    lighter, darker = max(one, two), min(one, two)
    return (lighter + 0.05) / (darker + 0.05)


def check_contrast(svg_text: str) -> list[str]:
    """Every text element must be readable against the canvas behind it."""

    elements = text_elements(svg_text)
    background = next((item["fill"] for item in elements if item["background"]), "")
    if not background:
        return ["no background element carries data-bg, so contrast cannot be measured"]

    problems: list[str] = []
    for item in elements:
        if item["role"] != "text":
            continue
        fill = str(item["fill"])
        if not fill.startswith("#"):
            continue
        wanted = MIN_LARGE_CONTRAST if item["font_size"] >= LARGE_TEXT_SIZE else MIN_BODY_CONTRAST
        ratio = contrast_ratio(fill, str(background))
        if ratio < wanted:
            problems.append(
                f"{item['id'] or 'text'}: contrast {ratio:.2f}:1 is below {wanted}:1 "
                f"({fill} on {background})"
            )
    return problems


def check_min_font_size(svg_text: str, minimum: float = MIN_FONT_SIZE) -> list[str]:
    """No text may be too small to read on the target medium."""

    return [
        f"{item['id'] or 'text'}: font size {item['font_size']:.0f} is below {minimum:.0f}"
        for item in text_elements(svg_text)
        if item["role"] == "text" and 0 < item["font_size"] < minimum
    ]


def query_box(svg_path: Path, element_id: str) -> tuple[float, float, float, float] | None:
    """Inkscape's own bounding box for one element."""

    completed = subprocess.run(
        [
            "inkscape",
            f"--query-id={element_id}",
            "--query-x",
            "--query-y",
            "--query-width",
            "--query-height",
            str(svg_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    values = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(values) < 4:
        return None
    try:
        x, y, width, height = (float(value) for value in values[:4])
    except ValueError:
        return None
    return x, y, width, height


def check_overflow(svg_path: Path, svg_text: str, *, tolerance: float = 1.5) -> list[str]:
    """Text must sit inside the template's safe area, measured by the renderer itself."""

    elements = text_elements(svg_text)
    safe_id = next((item["id"] for item in elements if item["safe"] and item["id"]), "")
    if not safe_id:
        return ["no element carries data-safe, so overflow cannot be measured"]
    safe = query_box(svg_path, safe_id)
    if safe is None:
        return [f"could not measure the safe area {safe_id!r}"]

    problems: list[str] = []
    for item in elements:
        if item["role"] != "text" or not item["id"]:
            continue
        box = query_box(svg_path, str(item["id"]))
        if box is None:
            problems.append(f"{item['id']}: could not be measured")
            continue
        x, y, width, height = box
        if (
            x < safe[0] - tolerance
            or y < safe[1] - tolerance
            or x + width > safe[0] + safe[2] + tolerance
            or y + height > safe[1] + safe[3] + tolerance
        ):
            problems.append(
                f"{item['id']}: text box {x:.0f},{y:.0f} {width:.0f}x{height:.0f} leaves the "
                f"safe area at {safe[0]:.0f},{safe[1]:.0f} {safe[2]:.0f}x{safe[3]:.0f}"
            )
    return problems


def render_variant(
    project: Project, variant: VariantSpec, environment: dict[str, str]
) -> dict[str, object]:
    """Compose, export, and check one variant."""

    images = project.root / "out" / "images"
    images.mkdir(parents=True, exist_ok=True)
    svg_path = images / f"{variant.id}.svg"
    composed = compose(project, variant, environment)
    svg_path.write_text(composed, encoding="utf-8")

    png_path = images / f"{variant.id}.png"
    pdf_path = images / f"{variant.id}.pdf"
    for export, extra in (
        (png_path, [f"--export-width={variant.width}"]),
        (pdf_path, []),
    ):
        kind = export.suffix.lstrip(".")
        completed = subprocess.run(
            [
                "inkscape",
                f"--export-type={kind}",
                f"--export-filename={export}",
                *extra,
                str(svg_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode != 0 or not export.is_file():
            raise DesignError(
                f"inkscape could not export {kind} for {variant.id}: "
                f"{completed.stderr.strip()[:200]}"
            )

    problems: list[str] = []
    problems.extend(check_contrast(composed))
    problems.extend(check_min_font_size(composed))
    problems.extend(check_overflow(svg_path, composed))
    actual = _png_size(png_path)
    if actual != (variant.width, variant.height):
        problems.append(
            f"export is {actual[0]}x{actual[1]}, expected {variant.width}x{variant.height}"
        )

    return {
        "id": variant.id,
        "template": variant.template,
        "size": [variant.width, variant.height],
        "export": {
            "svg": str(svg_path.relative_to(project.root)),
            "png": str(png_path.relative_to(project.root)),
            "pdf": str(pdf_path.relative_to(project.root)),
        },
        "checks": {
            "contrast": [problem for problem in problems if "contrast" in problem],
            "overflow": [
                problem for problem in problems if "safe area" in problem or "measured" in problem
            ],
            "font_size": [problem for problem in problems if "font size" in problem],
            "resolution": [problem for problem in problems if "expected" in problem],
        },
        "problems": problems,
        "ok": not problems,
    }


def _png_size(path: Path) -> tuple[int, int]:
    completed = subprocess.run(
        ["magick", "identify", "-format", "%w %h", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        width, height = (int(value) for value in completed.stdout.split()[:2])
    except (ValueError, IndexError):
        return (0, 0)
    return (width, height)


def contact_sheet(project: Project, variants: tuple[dict[str, object], ...]) -> Path | None:
    """One image covering every variant, so the set can be reviewed at a glance."""

    pngs = [
        project.root / str(item["export"]["png"])  # type: ignore[index]
        for item in variants
        if isinstance(item.get("export"), dict)
    ]
    if not pngs:
        return None
    sheet = project.root / "out" / "images" / "design-sheet.png"
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
    return sheet if completed.returncode == 0 and sheet.is_file() else None


def validate_design(project: Project) -> list[str]:
    """Problems a manifest can have before anything is rendered."""

    if project.design is None:
        return []
    problems: list[str] = []
    seen: set[str] = set()
    for variant in project.design.variants:
        if variant.id in seen:
            problems.append(f"design.variants: duplicate id {variant.id!r}")
        seen.add(variant.id)
        try:
            load_template(variant.template)
        except DesignError as error:
            problems.append(f"design.variants[{variant.id}]: {error}")
        if not variant.headline and not variant.subhead:
            problems.append(f"design.variants[{variant.id}]: needs a headline or a subhead")
    for role, asset_id in project.design.fonts.items():
        if role not in FONT_ROLES and role != "mono":
            problems.append(f"design.fonts: {role!r} is not a known role")
        if asset_id not in project.assets:
            problems.append(
                f"design.fonts.{role}: asset {asset_id!r} is not declared under assets:"
            )
    if project.design.logo and project.design.logo not in project.assets:
        problems.append(f"design.logo: asset {project.design.logo!r} is not declared under assets:")
    return problems


def render(
    project: Project,
    variant_id: str | None = None,
    *,
    check_only: bool = False,
) -> DesignReport:
    """Render every declared variant, or just one, and report the measurements."""

    if project.design is None or not project.design.variants:
        raise DesignError(
            "project.yaml has no design.variants; add a design section naming a template and size"
        )
    variants = [
        variant
        for variant in project.design.variants
        if variant_id is None or variant.id == variant_id
    ]
    if not variants:
        raise DesignError(f"no variant {variant_id!r} in project.yaml")

    environment = font_environment(project)
    results: list[dict[str, object]] = []
    problems: list[str] = []
    if check_only:
        for variant in variants:
            svg_path = project.root / "out" / "images" / f"{variant.id}.svg"
            if not svg_path.is_file():
                problems.append(f"{variant.id}: nothing rendered yet")
                continue
            composed = svg_path.read_text(encoding="utf-8")
            found = [
                *check_contrast(composed),
                *check_min_font_size(composed),
                *check_overflow(svg_path, composed),
            ]
            results.append({"id": variant.id, "problems": found, "ok": not found, "export": {}})
            problems.extend(found)
    else:
        for variant in variants:
            result = render_variant(project, variant, environment)
            results.append(result)
            problems.extend(str(problem) for problem in result["problems"])

    tuple_results = tuple(results)
    sheet = None if check_only else contact_sheet(project, tuple_results)
    report = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "project": project.slug,
        "variants": list(tuple_results),
        "sheet": str(sheet.relative_to(project.root)) if sheet else None,
        "problems": list(problems),
        "ok": not problems,
    }
    report_path = project.root / "out" / "reports" / f"{project.slug}-design-qc.json"
    if not check_only:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return DesignReport(
        variants=tuple_results,
        sheet=sheet,
        report_path=None if check_only else report_path,
        problems=tuple(problems),
    )
