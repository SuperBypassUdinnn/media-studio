"""Pure-geometry kernel (``taper_path``, ``arc_path``) plus the declarative
``svg_kit`` builder: ``build_spec``, ``write_svg``, ``render_png`` and
``check_safe_area``.

The builder composes a spec (canvas, safe area, background, variables, elements)
into SVG text, writes it, exports PNG/PDF via Inkscape, and measures the safe
area. Geometry kernel functions are unchanged.

Conventions (stated once, relied on everywhere):
- Angles are screen angles: 0 deg points along +x, positive angles run clockwise
  on screen, because SVG's y axis points down. ``angle: 90`` therefore points down.
- A positive ``bend`` (taper) or ``sag`` (arc chord form) bows the spine toward
  -y for an angle-0 spine / left-to-right chord: the bulge rises on screen.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import os
import re
import subprocess
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from xml.etree import ElementTree

from .design import DEFAULT_TOKENS, brand_tokens, font_environment, font_families, query_box
from .fonts import run_inkscape
from .manifest import Project

ElementTree.register_namespace("", "http://www.w3.org/2000/svg")


class SvgError(RuntimeError):
    """Raised when an SVG element cannot be turned into a path ``d`` string."""


def _num(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise SvgError(f"field '{field}' must be a number")
    return float(value)


def _point(value: object, field: str) -> tuple[float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, Real) for item in value)
    ):
        raise SvgError(f"field '{field}' must be an [x, y] pair of numbers")
    return float(value[0]), float(value[1])


def _round2(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "-0.0", "") else text


def _left_normal(tangent: tuple[float, float]) -> tuple[float, float]:
    """The left-hand normal of a unit tangent on screen: for +x it points up (-y)."""

    dx, dy = tangent
    return dy, -dx


def _quadratic_point(
    start: tuple[float, float], control: tuple[float, float], end: tuple[float, float], t: float
) -> tuple[float, float]:
    u = 1.0 - t
    return (
        u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
        u * u * start[1] + 2 * u * t * control[1] + t * t * end[1],
    )


def _spine(element: dict[str, object]) -> tuple[list[tuple[float, float]], bool]:
    """Sample the spine: a vertex list, or a quadratic through from/angle/length/bend."""

    points = element.get("points")
    if points is not None:
        if not isinstance(points, (list, tuple)) or len(points) < 2:
            raise SvgError("field 'points' needs at least two [x, y] vertices")
        return [_point(vertex, "points") for vertex in points], True

    origin = element.get("from")
    angle = element.get("angle")
    length = element.get("length")
    if origin is None or angle is None or length is None:
        raise SvgError("spine needs 'points', or 'from' with 'angle' and 'length'")
    x0, y0 = _point(origin, "from")
    angle_v = _num(angle, "angle")
    length_v = _num(length, "length")
    if length_v == 0:
        raise SvgError("field 'length' must not be zero")
    bend = _num(element.get("bend", 0.0), "bend")
    rad = math.radians(angle_v)
    dx, dy = math.cos(rad), math.sin(rad)
    end = (x0 + dx * length_v, y0 + dy * length_v)
    nx, ny = _left_normal((dx, dy))
    # The quadratic's midpoint bows by exactly `bend`, so its control point
    # sits at twice that offset from the chord midpoint.
    control = (
        (x0 + end[0]) / 2 + nx * 2 * bend,
        (y0 + end[1]) / 2 + ny * 2 * bend,
    )
    samples = [_quadratic_point((x0, y0), control, end, i / 32) for i in range(33)]
    return samples, False


def _arc_lengths(spine: list[tuple[float, float]]) -> list[float]:
    lengths = [0.0]
    for (x0, y0), (x1, y1) in itertools.pairwise(spine):
        lengths.append(lengths[-1] + math.hypot(x1 - x0, y1 - y0))
    return lengths


def _width_profile(
    element: dict[str, object], is_points: bool, vertex_count: int
) -> tuple[str, tuple[float, ...]]:
    widths = element.get("widths")
    constant = element.get("width")
    if widths is not None and constant is not None:
        raise SvgError("fields 'width' and 'widths' are mutually exclusive")
    if constant is not None:
        value = _num(constant, "width")
        if value < 0:
            raise SvgError("field 'width' must not be negative")
        return "const", (value,)
    if not isinstance(widths, (list, tuple)) or not widths:
        raise SvgError("field 'widths' must be a non-empty list of numbers")
    values = tuple(_num(value, "widths") for value in widths)
    if any(value < 0 for value in values):
        raise SvgError("field 'widths' values must not be negative")
    if is_points:
        if len(values) != vertex_count:
            raise SvgError(
                f"field 'widths' needs one value per 'points' vertex: "
                f"{vertex_count} vertices, got {len(values)}"
            )
        return "per_vertex", values
    if len(values) != 2:
        raise SvgError("field 'widths' needs exactly [base, tip] for a from/angle spine")
    return "linear", values


def _half_widths(
    profile: tuple[str, tuple[float, ...]],
    lengths: list[float],
    total: float,
) -> list[float]:
    kind, values = profile
    if kind == "const":
        return [values[0] / 2] * len(lengths)
    if kind == "linear":
        base, tip = values
        return [(base + (tip - base) * (s / total if total else 0.0)) / 2 for s in lengths]
    return [value / 2 for value in values]


def _unit(value: tuple[float, float], fallback: tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(*value)
    if length == 0:
        return fallback
    return value[0] / length, value[1] / length


def _offset_side(
    spine: list[tuple[float, float]], halves: list[float], side: int
) -> list[tuple[float, float]]:
    """Offset the spine by +/- half width along the local unit normal (side +1 = left)."""

    offset: list[tuple[float, float]] = []
    previous = (0.0, -1.0)
    for index, (x, y) in enumerate(spine):
        if index == 0:
            delta = (spine[1][0] - spine[0][0], spine[1][1] - spine[0][1])
        elif index == len(spine) - 1:
            delta = (spine[-1][0] - spine[-2][0], spine[-1][1] - spine[-2][1])
        else:
            delta = (
                spine[index + 1][0] - spine[index - 1][0],
                spine[index + 1][1] - spine[index - 1][1],
            )
        normal = _left_normal(_unit(delta, previous))
        previous = normal
        offset.append((x + side * normal[0] * halves[index], y + side * normal[1] * halves[index]))
    return offset


def _smooth(points: list[tuple[float, float]], move: bool) -> list[str]:
    """Catmull-Rom through the samples, emitted as cubic Beziers."""

    if not points:
        return []
    commands = [f"M {_round2(points[0][0])} {_round2(points[0][1])}"] if move else []
    count = len(points)
    for i in range(count - 1):
        p0 = points[i - 1] if i > 0 else points[0]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < count else points[-1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        commands.append(
            f"C {_round2(c1[0])} {_round2(c1[1])} {_round2(c2[0])} {_round2(c2[1])} "
            f"{_round2(p2[0])} {_round2(p2[1])}"
        )
    return commands


def taper_path(element: dict[str, object]) -> str:
    """Return the ``d`` of one closed filled outline around a spine width profile."""

    if not isinstance(element, dict):
        raise SvgError("element must be a dict")
    spine, is_points = _spine(element)
    lengths = _arc_lengths(spine)
    total = lengths[-1]
    if total == 0:
        raise SvgError("spine has zero length")
    profile = _width_profile(element, is_points, len(spine))
    halves = _half_widths(profile, lengths, total)
    cap = element.get("caps", "round")
    if cap not in ("round", "flat"):
        raise SvgError("field 'caps' must be 'round' or 'flat'")

    left = _offset_side(spine, halves, +1)
    right = _offset_side(spine, halves, -1)
    commands = _smooth(left, move=True)
    if cap == "round":
        radius = halves[-1]
        commands.append(
            f"A {_round2(radius)} {_round2(radius)} 0 0 1 "
            f"{_round2(right[-1][0])} {_round2(right[-1][1])}"
        )
    else:
        commands.append(f"L {_round2(right[-1][0])} {_round2(right[-1][1])}")
    commands.extend(_smooth(list(reversed(right)), move=False))
    if cap == "round":
        radius = halves[0]
        commands.append(
            f"A {_round2(radius)} {_round2(radius)} 0 0 1 "
            f"{_round2(left[0][0])} {_round2(left[0][1])}"
        )
    else:
        commands.append(f"L {_round2(left[0][0])} {_round2(left[0][1])}")
    commands.append("Z")
    return " ".join(commands)


def _point_at(cx: float, cy: float, rx: float, ry: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    return cx + rx * math.cos(rad), cy + ry * math.sin(rad)


def _arc_d(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    start_deg: float,
    end_deg: float,
    close: str,
) -> str:
    """One arc as two ``A`` segments split at the mid angle, plus the requested closure.

    Two segments keep the numbers-per-command even and dodge the 180 deg
    ambiguity a single ``A`` has with a semicircle.
    """

    span = end_deg - start_deg
    if span == 0:
        raise SvgError("fields 'start_deg' and 'end_deg' must differ")
    span = math.copysign(abs(span) % 360 or 360, span)
    sweep = 1 if span > 0 else 0
    start = _point_at(cx, cy, rx, ry, start_deg)
    middle = _point_at(cx, cy, rx, ry, start_deg + span / 2)
    end = _point_at(cx, cy, rx, ry, start_deg + span)
    commands = [
        f"M {_round2(start[0])} {_round2(start[1])}",
        f"A {_round2(rx)} {_round2(ry)} 0 0 {sweep} {_round2(middle[0])} {_round2(middle[1])}",
        f"A {_round2(rx)} {_round2(ry)} 0 0 {sweep} {_round2(end[0])} {_round2(end[1])}",
    ]
    if close == "pie":
        commands.append(f"L {_round2(cx)} {_round2(cy)}")
        commands.append("Z")
    elif close == "chord":
        commands.append("Z")
    return " ".join(commands)


def arc_path(element: dict[str, object]) -> str:
    """Return the ``d`` of one arc path (explicit center form or from/to/sag chord)."""

    if not isinstance(element, dict):
        raise SvgError("element must be a dict")
    close = element.get("close", "open")
    if close not in ("pie", "chord", "open"):
        raise SvgError("field 'close' must be 'pie', 'chord' or 'open'")

    explicit = [element.get(name) for name in ("cx", "cy", "rx", "ry", "start_deg", "end_deg")]
    names = ("cx", "cy", "rx", "ry", "start_deg", "end_deg")
    if any(value is not None for value in explicit):
        missing = [name for value, name in zip(explicit, names, strict=True) if value is None]
        if missing:
            raise SvgError(f"explicit arc is missing required field(s): {', '.join(missing)}")
        cx, cy, rx, ry, start_deg, end_deg = (
            _num(value, name) for value, name in zip(explicit, names, strict=True)
        )
        if rx <= 0:
            raise SvgError("field 'rx' must be positive")
        if ry <= 0:
            raise SvgError("field 'ry' must be positive")
        return _arc_d(cx, cy, rx, ry, start_deg, end_deg, close)

    origin = element.get("from")
    target = element.get("to")
    sag = element.get("sag")
    if origin is None or target is None or sag is None:
        raise SvgError("chord arc needs 'from', 'to' and 'sag'")
    x0, y0 = _point(origin, "from")
    x1, y1 = _point(target, "to")
    sag_v = _num(sag, "sag")
    dx, dy = x1 - x0, y1 - y0
    chord = math.hypot(dx, dy)
    if chord == 0:
        raise SvgError("fields 'from' and 'to' must differ")
    if sag_v == 0:
        raise SvgError("field 'sag' must not be zero")
    ux, uy = dx / chord, dy / chord
    nx, ny = _left_normal((ux, uy))
    mid = ((x0 + x1) / 2, (y0 + y1) / 2)
    half_chord = chord / 2
    radius = (sag_v * sag_v + half_chord * half_chord) / (2 * abs(sag_v))
    offset = radius - abs(sag_v)
    sign = 1 if sag_v > 0 else -1
    cx = mid[0] - nx * offset * sign
    cy = mid[1] - ny * offset * sign

    def angle_of(px: float, py: float) -> float:
        return math.degrees(math.atan2(py - cy, px - cx))

    return _arc_d(cx, cy, radius, radius, angle_of(x0, y0), angle_of(x1, y1), close)


def _format(value: float) -> str:
    """One attribute number, matching the kernel's rounding style."""
    return _round2(value)


def _escape(text: str) -> str:
    """Escape SVG attribute/character data."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _resolve_position(
    value: object,
    axis: str,
    canvas: dict[str, float],
    safe_area: dict[str, float],
    size: float = 0.0,
) -> float:
    """Resolve one positional value (number or anchor keyword) to a top-left coordinate."""
    if isinstance(value, str):
        if axis == "x":
            dim = canvas["width"]
            if value == "left":
                return safe_area["left"]
            if value == "right":
                return dim - safe_area["right"] - size
            if value == "center":
                return dim / 2 - size / 2
        else:
            dim = canvas["height"]
            if value == "top":
                return safe_area["top"]
            if value == "bottom":
                return dim - safe_area["bottom"] - size
            if value == "middle":
                return dim / 2 - size / 2
        raise SvgError(f"unknown anchor {value!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SvgError("positional value must be a number or an anchor keyword")
    return float(value)


def _normalize_safe_area(safe_area: object) -> dict[str, float]:
    """Turn a number or per-edge dict into {top, right, bottom, left}."""
    if safe_area is None:
        return {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
    if isinstance(safe_area, (int, float)) and not isinstance(safe_area, bool):
        value = float(safe_area)
        return {"top": value, "right": value, "bottom": value, "left": value}
    if not isinstance(safe_area, dict):
        raise SvgError("field 'safe_area' must be a number or a dict of edges")
    edges = {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
    for name in edges:
        value = safe_area.get(name, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SvgError(f"field 'safe_area.{name}' must be a number")
        edges[name] = float(value)
    return edges


def _substitute(text: object, variables: dict[str, str], tokens: dict[str, str]) -> str:
    """Substitute {name} tokens and {variable} references in a string field."""
    if not isinstance(text, str):
        raise SvgError("field must be a string")
    result = text
    for name, value in {**tokens, **variables}.items():
        result = result.replace("{" + name + "}", str(value))
    return result


def _presentation(
    element: dict[str, object], variables: dict[str, str], tokens: dict[str, str]
) -> str:
    """Common fill/stroke/stroke_width attributes, with token substitution."""
    parts: list[str] = []
    for field in ("fill", "stroke", "stroke_width"):
        value = element.get(field)
        if value is None:
            continue
        if field == "stroke_width":
            parts.append(f'stroke-width="{_num(value, field)}"')
        else:
            parts.append(f'{field}="{_escape(_substitute(str(value), variables, tokens))}"')
    return " ".join(parts)


def _next_id(counters: dict[str, int], kind: str) -> str:
    counters[kind] = counters.get(kind, 0) + 1
    return f"el-{kind}-{counters[kind]}"


def _text_size(element: dict[str, object]) -> float:
    return _num(element.get("size", 12), "size")


def _text_lines(element: dict[str, object]) -> list[str]:
    return str(element.get("text", "")).split("\n")


def _child_height(child: dict[str, object], default_size: float = 12.0) -> float:
    """Estimate a child's height for stack layout (text: size * 1.2 * lines)."""
    kind = child.get("type")
    if kind == "text":
        return _text_size(child) * 1.2 * len(_text_lines(child))
    if kind in ("rect", "spacer"):
        value = child.get("height")
        if value is not None:
            return _num(value, "height")
        return _num(child.get("width", 0), "width")
    if kind in ("circle", "ellipse"):
        return _num(child.get("ry", child.get("r", 0)), "radius") * 2
    return default_size


def build_spec(
    spec: dict[str, object], *, project: Project | None = None, base: str | None = None
) -> str:
    """Compose one declarative spec into SVG text."""

    if not isinstance(spec, dict):
        raise SvgError("spec must be a JSON object")

    variables = spec.get("variables", {})
    if not isinstance(variables, dict):
        raise SvgError("field 'variables' must be an object")
    variables = {str(k): str(v) for k, v in variables.items()}

    tokens = dict(DEFAULT_TOKENS) if project is None else brand_tokens(project)

    safe_area = _normalize_safe_area(spec.get("safe_area"))

    if base is not None:
        if not isinstance(base, str):
            raise SvgError("field 'base' must be a string")
        if "</svg>" not in base:
            raise SvgError("base SVG is missing a closing </svg> tag")
        head = base[: base.rindex("</svg>")]
        m_width = re.search(r'width="([0-9.]+)"', head)
        m_height = re.search(r'height="([0-9.]+)"', head)
        m_view = re.search(r'viewBox="[0-9.]+ [0-9.]+ ([0-9.]+) ([0-9.]+)"', head)
        width = float(m_width.group(1)) if m_width else (float(m_view.group(1)) if m_view else 0)
        height = float(m_height.group(1)) if m_height else (float(m_view.group(2)) if m_view else 0)
    else:
        canvas = spec.get("canvas")
        if not isinstance(canvas, dict):
            raise SvgError("field 'canvas' is required unless 'base' is given")
        width = _num(canvas.get("width"), "canvas.width")
        height = _num(canvas.get("height"), "canvas.height")
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_format(width)}" '
            f'height="{_format(height)}" viewBox="0 0 {_format(width)} {_format(height)}">'
        )

    parts: list[str] = [head]

    background = spec.get("background")
    if background is not None:
        if not isinstance(background, dict) or background.get("type") != "solid":
            raise SvgError("field 'background' must be {type: solid, color: ...}")
        color = _substitute(str(background.get("color", "{ink}")), variables, tokens)
        parts.append(
            f'<rect data-bg="true" x="0" y="0" width="{_format(width)}" '
            f'height="{_format(height)}" fill="{color}"/>'
        )

    if any(v > 0 for v in safe_area.values()):
        sx = safe_area["left"]
        sy = safe_area["top"]
        sw = width - safe_area["left"] - safe_area["right"]
        sh = height - safe_area["top"] - safe_area["bottom"]
        parts.append(
            f'<rect id="safe" data-safe="true" x="{_format(sx)}" y="{_format(sy)}" '
            f'width="{_format(max(sw, 0))}" height="{_format(max(sh, 0))}" '
            f'fill="none" stroke="none"/>'
        )

    elements = spec.get("elements", [])
    if not isinstance(elements, list):
        raise SvgError("field 'elements' must be a list")
    counters: dict[str, int] = {}

    environment = font_environment(project) if project is not None else {}
    families = font_families(project, environment) if project is not None else {}

    for element in elements:
        if not isinstance(element, dict):
            raise SvgError("each element must be an object")
        kind = element.get("type")
        if kind == "text":
            parts.append(
                _build_text(
                    element, counters, width, height, safe_area, families, variables, tokens
                )
            )
        elif kind == "rect":
            parts.append(
                _build_rect(element, counters, width, height, safe_area, variables, tokens)
            )
        elif kind == "circle":
            parts.append(_build_circle(element, counters, variables, tokens))
        elif kind == "ellipse":
            parts.append(_build_ellipse(element, counters, variables, tokens))
        elif kind == "line":
            parts.append(_build_line(element, counters, variables, tokens))
        elif kind == "polygon":
            parts.append(_build_polygon(element, counters, variables, tokens))
        elif kind == "polyline":
            parts.append(_build_polyline(element, counters, variables, tokens))
        elif kind == "path":
            parts.append(_build_path(element, counters, variables, tokens))
        elif kind == "image":
            parts.append(
                _build_image(element, counters, width, height, safe_area, variables, tokens)
            )
        elif kind == "group":
            parts.append(
                _build_group(
                    element, counters, width, height, safe_area, variables, tokens, families
                )
            )
        elif kind == "stack":
            parts.append(
                _build_stack(
                    element, counters, width, height, safe_area, variables, tokens, families
                )
            )
        elif kind == "spacer":
            parts.append(_build_spacer(element, counters))
        elif kind == "taper":
            parts.append(_build_taper(element, counters, variables, tokens))
        elif kind == "arc":
            parts.append(_build_arc(element, counters, variables, tokens))
        else:
            raise SvgError(f"unknown element type {kind!r}")

    parts.append("</svg>")
    return "".join(parts)


def _family_for(role: str, families: dict[str, str]) -> str:
    return families.get(role, "sans-serif") if role in families else "sans-serif"


def _build_text(
    element: dict[str, object],
    counters: dict[str, int],
    width: float,
    height: float,
    safe_area: dict[str, float],
    families: dict[str, str],
    variables: dict[str, str],
    tokens: dict[str, str],
    position: tuple[float, float] | None = None,
) -> str:
    element_id = _next_id(counters, "text")
    role = str(element.get("role", "body"))
    size = _text_size(element)
    if position is not None:
        # A stack positions its children at its own cursor.
        x, y = position[0], position[1] + size
    else:
        x = _resolve_position(
            element.get("x", 0), "x", {"width": width, "height": height}, safe_area
        )
        y = _resolve_position(
            element.get("y", 0), "y", {"width": width, "height": height}, safe_area
        )
    text = _substitute(str(element.get("text", "")), variables, tokens)
    color = _escape(_substitute(str(element.get("color", "{ink}")), variables, tokens))
    anchor = element.get("anchor", "start")
    if anchor not in ("start", "middle", "end"):
        raise SvgError("field 'anchor' must be 'start', 'middle' or 'end'")
    weight = element.get("weight")
    if weight is not None:
        weight_ok = weight in ("normal", "bold", "bolder", "lighter") or (
            isinstance(weight, Real) and not isinstance(weight, bool) and 100 <= weight <= 900
        )
        if not weight_ok:
            raise SvgError("field 'weight' must be normal/bold/bolder/lighter or 100..900")
    letter_spacing = element.get("letter_spacing")
    parts = [
        f'<text id="{element_id}" data-role="text" x="{_format(x)}" y="{_format(y)}" '
        f'font-size="{_format(size)}" font-family="{_escape(_family_for(role, families))}" '
        f'text-anchor="{anchor}" fill="{color}"'
    ]
    if weight is not None:
        parts[-1] += f' font-weight="{weight}"'
    if letter_spacing is not None:
        parts[-1] += f' letter-spacing="{_format(_num(letter_spacing, "letter_spacing"))}"'
    parts[-1] += ">"
    dy = 0.0
    for line in text.split("\n"):
        parts.append(f'<tspan x="{_format(x)}" dy="{_format(dy)}">{_escape(line)}</tspan>')
        dy += size * 1.2
    parts.append("</text>")
    return "".join(parts)


def _build_rect(
    element: dict[str, object],
    counters: dict[str, int],
    width: float,
    height: float,
    safe_area: dict[str, float],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "rect")
    rect_width = _num(element.get("width", 0), "width")
    rect_height = _num(element.get("height", 0), "height")
    x = _resolve_position(
        element.get("x", 0), "x", {"width": width, "height": height}, safe_area, rect_width
    )
    y = _resolve_position(
        element.get("y", 0), "y", {"width": width, "height": height}, safe_area, rect_height
    )
    radius = element.get("radius")
    attrs = [
        f'id="{element_id}"',
        f'x="{_format(x)}"',
        f'y="{_format(y)}"',
        f'width="{_format(rect_width)}"',
        f'height="{_format(rect_height)}"',
    ]
    if radius is not None:
        r = _num(radius, "radius")
        attrs.append(f'rx="{_format(r)}" ry="{_format(r)}"')
    attrs.append(_presentation(element, variables, tokens))
    return f"<rect {' '.join(attrs)}/>"


def _build_circle(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "circle")
    cx = _num(element.get("cx", 0), "cx")
    cy = _num(element.get("cy", 0), "cy")
    r = _num(element.get("r", 0), "r")
    attrs = [
        f'id="{element_id}"',
        f'cx="{_format(cx)}"',
        f'cy="{_format(cy)}"',
        f'r="{_format(r)}"',
        _presentation(element, variables, tokens),
    ]
    return f"<circle {' '.join(attrs)}/>"


def _build_ellipse(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "ellipse")
    cx = _num(element.get("cx", 0), "cx")
    cy = _num(element.get("cy", 0), "cy")
    rx = _num(element.get("rx", 0), "rx")
    ry = _num(element.get("ry", 0), "ry")
    attrs = [
        f'id="{element_id}"',
        f'cx="{_format(cx)}"',
        f'cy="{_format(cy)}"',
        f'rx="{_format(rx)}"',
        f'ry="{_format(ry)}"',
        _presentation(element, variables, tokens),
    ]
    return f"<ellipse {' '.join(attrs)}/>"


def _build_line(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "line")
    attrs = [
        f'id="{element_id}"',
        f'x1="{_format(_num(element.get("x1", 0), "x1"))}"',
        f'y1="{_format(_num(element.get("y1", 0), "y1"))}"',
        f'x2="{_format(_num(element.get("x2", 0), "x2"))}"',
        f'y2="{_format(_num(element.get("y2", 0), "y2"))}"',
        _presentation(element, variables, tokens),
    ]
    cap = element.get("cap")
    if cap is not None:
        attrs.append(f'stroke-linecap="{cap}"')
    return f"<line {' '.join(attrs)}/>"


def _points_string(points: object, field: str) -> str:
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        raise SvgError(f"field '{field}' must be a list of at least two [x, y] pairs")
    pairs = []
    for vertex in points:
        x, y = _point(vertex, field)
        pairs.append(f"{_format(x)} {_format(y)}")
    return " ".join(pairs)


def _build_polygon(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "polygon")
    attrs = [
        f'id="{element_id}"',
        f'points="{_points_string(element.get("points"), "points")}"',
        _presentation(element, variables, tokens),
    ]
    return f"<polygon {' '.join(attrs)}/>"


def _build_polyline(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "polyline")
    attrs = [
        f'id="{element_id}"',
        f'points="{_points_string(element.get("points"), "points")}"',
        _presentation(element, variables, tokens),
    ]
    return f"<polyline {' '.join(attrs)}/>"


def _build_path(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "path")
    d = element.get("d")
    if not isinstance(d, str):
        raise SvgError("field 'd' is required for a path element")
    attrs = [f'id="{element_id}"', f'd="{_escape(d)}"', _presentation(element, variables, tokens)]
    return f"<path {' '.join(attrs)}/>"


def _build_image(
    element: dict[str, object],
    counters: dict[str, int],
    width: float,
    height: float,
    safe_area: dict[str, float],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "image")
    href = element.get("href")
    if not isinstance(href, str) or not href:
        raise SvgError("field 'href' is required for an image element")
    img_width = element.get("width")
    img_height = element.get("height")
    if img_width is None and img_height is None:
        raise SvgError("an image needs 'width' or 'height'")
    if img_width is not None:
        img_width = _num(img_width, "width")
    if img_height is not None:
        img_height = _num(img_height, "height")
    x = _resolve_position(
        element.get("x", 0), "x", {"width": width, "height": height}, safe_area, img_width or 0
    )
    y = _resolve_position(
        element.get("y", 0), "y", {"width": width, "height": height}, safe_area, img_height or 0
    )
    attrs = [
        f'id="{element_id}"',
        f'href="{_escape(href)}"',
        f'x="{_format(x)}"',
        f'y="{_format(y)}"',
    ]
    if img_width is not None:
        attrs.append(f'width="{_format(img_width)}"')
    if img_height is not None:
        attrs.append(f'height="{_format(img_height)}"')
    opacity = element.get("opacity")
    if opacity is not None:
        attrs.append(f'opacity="{_num(opacity, "opacity")}"')
    return f"<image {' '.join(attrs)}/>"


def _build_group(
    element: dict[str, object],
    counters: dict[str, int],
    width: float,
    height: float,
    safe_area: dict[str, float],
    variables: dict[str, str],
    tokens: dict[str, str],
    families: dict[str, str],
) -> str:
    element_id = _next_id(counters, "group")
    transform = element.get("transform")
    opacity = element.get("opacity")
    children = element.get("children", [])
    if not isinstance(children, list):
        raise SvgError("field 'children' must be a list")
    parts = [f'<g id="{element_id}"']
    if transform is not None:
        parts.append(f' transform="{_escape(str(transform))}"')
    if opacity is not None:
        parts.append(f' opacity="{_num(opacity, "opacity")}"')
    parts.append(">")
    for child in children:
        if not isinstance(child, dict):
            raise SvgError("each child must be an object")
        kind = child.get("type")
        if kind == "text":
            parts.append(
                _build_text(child, counters, width, height, safe_area, families, variables, tokens)
            )
        elif kind == "rect":
            parts.append(_build_rect(child, counters, width, height, safe_area, variables, tokens))
        elif kind == "circle":
            parts.append(_build_circle(child, counters, variables, tokens))
        elif kind == "ellipse":
            parts.append(_build_ellipse(child, counters, variables, tokens))
        elif kind == "line":
            parts.append(_build_line(child, counters, variables, tokens))
        elif kind == "polygon":
            parts.append(_build_polygon(child, counters, variables, tokens))
        elif kind == "polyline":
            parts.append(_build_polyline(child, counters, variables, tokens))
        elif kind == "path":
            parts.append(_build_path(child, counters, variables, tokens))
        elif kind == "image":
            parts.append(_build_image(child, counters, width, height, safe_area, variables, tokens))
        elif kind == "group":
            parts.append(
                _build_group(child, counters, width, height, safe_area, variables, tokens, families)
            )
        elif kind == "stack":
            parts.append(
                _build_stack(child, counters, width, height, safe_area, variables, tokens, families)
            )
        elif kind == "spacer":
            parts.append(_build_spacer(child, counters))
        elif kind == "taper":
            parts.append(_build_taper(child, counters, variables, tokens))
        elif kind == "arc":
            parts.append(_build_arc(child, counters, variables, tokens))
        else:
            raise SvgError(f"unknown element type {kind!r}")
    parts.append("</g>")
    return "".join(parts)


def _build_stack(
    element: dict[str, object],
    counters: dict[str, int],
    width: float,
    height: float,
    safe_area: dict[str, float],
    variables: dict[str, str],
    tokens: dict[str, str],
    families: dict[str, str],
) -> str:
    element_id = _next_id(counters, "stack")
    direction = str(element.get("direction", "vertical"))
    if direction not in ("vertical", "horizontal"):
        raise SvgError("field 'direction' must be 'vertical' or 'horizontal'")
    gap = _num(element.get("gap", 0), "gap")
    x = _resolve_position(element.get("x", 0), "x", {"width": width, "height": height}, safe_area)
    y = _resolve_position(element.get("y", 0), "y", {"width": width, "height": height}, safe_area)
    align = element.get("align", "start")
    if align not in ("start", "center", "end"):
        raise SvgError("field 'align' must be 'start', 'center' or 'end'")
    children = element.get("children", [])
    if not isinstance(children, list):
        raise SvgError("field 'children' must be a list")
    parts = [f'<g id="{element_id}"']
    if align != "start":
        parts.append(f' text-anchor="{align}"')
    parts.append(">")
    cx = x
    cy = y
    for child in children:
        if not isinstance(child, dict):
            raise SvgError("each child must be an object")
        if child.get("type") == "spacer":
            spacer_size = _child_height(child)
            if direction == "vertical":
                cy += spacer_size + gap
            else:
                cx += spacer_size + gap
            continue
        kind = child.get("type")
        if kind == "text":
            parts.append(
                _build_text(
                    child,
                    counters,
                    width,
                    height,
                    safe_area,
                    families,
                    variables,
                    tokens,
                    position=(cx, cy),
                )
            )
            step = _child_height(child) + gap
            cy += step if direction == "vertical" else 0
            cx += step if direction == "horizontal" else 0
        elif kind == "rect":
            child_width = _num(child.get("width", 0), "width")
            child_height = _num(child.get("height", 0), "height")
            child_x = cx
            child_y = cy
            parts.append(
                f'<rect id="{_next_id(counters, "rect")}" '
                f'x="{_format(child_x)}" y="{_format(child_y)}" '
                f'width="{_format(child_width)}" height="{_format(child_height)}" '
                f"{_presentation(child, variables, tokens)}/>"
            )
            cy += child_height + gap if direction == "vertical" else 0
            cx += child_width + gap if direction == "horizontal" else 0
        else:
            # Generic child: render at the current cursor position.
            child_x = cx
            child_y = cy
            if kind == "circle":
                parts.append(_build_circle(child, counters, variables, tokens))
                step = _child_height(child)
            elif kind == "ellipse":
                parts.append(_build_ellipse(child, counters, variables, tokens))
                step = _child_height(child)
            else:
                step = _child_height(child)
            cy += step + gap if direction == "vertical" else 0
            cx += step + gap if direction == "horizontal" else 0
    parts.append("</g>")
    return "".join(parts)


def _build_spacer(element: dict[str, object], counters: dict[str, int]) -> str:
    element_id = _next_id(counters, "spacer")
    width = element.get("width")
    height = element.get("height")
    if width is None and height is None:
        raise SvgError("a spacer needs 'width' or 'height'")
    attrs = [f'id="{element_id}"', 'fill="none"']
    if width is not None:
        attrs.append(f'width="{_format(_num(width, "width"))}"')
    if height is not None:
        attrs.append(f'height="{_format(_num(height, "height"))}"')
    return f"<rect {' '.join(attrs)}/>"


def _build_taper(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "taper")
    d = taper_path(element)
    fill = _substitute(str(element.get("fill", "{ink}")), variables, tokens)
    return f'<path id="{element_id}" d="{_escape(d)}" fill="{fill}"/>'


def _build_arc(
    element: dict[str, object],
    counters: dict[str, int],
    variables: dict[str, str],
    tokens: dict[str, str],
) -> str:
    element_id = _next_id(counters, "arc")
    d = arc_path(element)
    fill = _substitute(str(element.get("fill", "none")), variables, tokens)
    stroke = element.get("stroke")
    stroke_width = element.get("stroke_width")
    attrs = [f'id="{element_id}"', f'd="{_escape(d)}"', f'fill="{fill}"']
    if stroke is not None:
        attrs.append(f'stroke="{_substitute(str(stroke), variables, tokens)}"')
    if stroke_width is not None:
        attrs.append(f'stroke-width="{_num(stroke_width, "stroke_width")}"')
    cap = element.get("cap")
    if cap is not None:
        attrs.append(f'stroke-linecap="{cap}"')
    return f"<path {' '.join(attrs)}/>"


def write_svg(text: str, out: Path) -> Path:
    """Write SVG text to ``out`` and return the path."""

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def render_png(
    svg_path: Path,
    png_path: Path,
    *,
    width: int,
    height: int | None = None,
    environment: dict[str, str] | None = None,
) -> Path:
    """Export an SVG to PNG (or PDF) with Inkscape, mirroring ``design.render_variant``."""

    svg_path = Path(svg_path)
    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    kind = png_path.suffix.lstrip(".")
    extra = [f"--export-width={width}"]
    if height is not None:
        extra.append(f"--export-height={height}")
    env = dict(os.environ) if environment is None else dict(environment)
    completed = run_inkscape(
        [
            f"--export-type={kind}",
            f"--export-filename={png_path}",
            *extra,
            str(svg_path),
        ],
        destination=png_path,
        environment=env,
    )
    if completed.returncode != 0 or not png_path.is_file():
        raise SvgError(
            f"inkscape could not export {kind} for {svg_path}: {completed.stderr.strip()[:200]}"
        )
    return png_path


def check_safe_area(svg_path: Path, safe_area: dict[str, float] | float) -> list[str]:
    """Report id'd elements whose boxes cross the safe-area inset."""

    svg_path = Path(svg_path)
    inset = _normalize_safe_area(safe_area)
    root = ElementTree.parse(str(svg_path)).getroot()
    view_box = root.get("viewBox") or ""
    parts = [float(v) for v in view_box.split()] if view_box else []
    if len(parts) < 4:
        width = float(root.get("width", "0") or 0)
        height = float(root.get("height", "0") or 0)
        canvas_x, canvas_y, canvas_w, canvas_h = 0.0, 0.0, width, height
    else:
        canvas_x, canvas_y, canvas_w, canvas_h = parts

    problems: list[str] = []
    for element in root.iter():
        tag = element.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag != "text":
            continue
        element_id = element.get("id")
        if not element_id:
            continue
        box = query_box(svg_path, element_id)
        if box is None:
            problems.append(f"{element_id}: could not be measured against the safe area")
            continue
        x, y, w, h = box
        if (
            x < canvas_x + inset["left"]
            or y < canvas_y + inset["top"]
            or x + w > canvas_x + canvas_w - inset["right"]
            or y + h > canvas_y + canvas_h - inset["bottom"]
        ):
            problems.append(
                f"{element_id}: text box {x:.0f},{y:.0f} {w:.0f}x{h:.0f} leaves the "
                f"safe area inset by {inset}"
            )
    return problems


# ---------------------------------------------------------------------------
# Two-stage raster tracer (Stage 1 of the svg helper suite)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceResult:
    """Outcome of tracing one raster to a layered SVG."""

    svg_path: Path
    input_path: Path
    engine: str
    layers: int


_VECTOR_INSTALL_HINT = "uv pip install -e '.[vector]'"


def _run(cmd: list[str], label: str) -> str:
    """Run one external tool; raise SvgError with the stderr tail on failure."""

    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        tail = completed.stderr.strip()[-2000:]
        raise SvgError(f"{label} failed: {tail}")
    return completed.stdout


def _magick_size(image: Path) -> tuple[int, int]:
    """Return (width, height) of one image in pixels."""

    out = _run(["magick", "identify", "-format", "%w %h", str(image)], "magick identify")
    width, height = out.split()
    return int(width), int(height)


def _magick_preprocess(
    source: Path, intermediate: Path, *, flatten_bg: str | None, fill_holes: int, despeckle: int
) -> None:
    """Flatten alpha, close holes, despeckle; keep the intermediate for stage 2."""

    cmd = ["magick", str(source)]
    if flatten_bg is not None:
        cmd += ["-background", flatten_bg, "-flatten", "-define", "png:color-type=6"]
    if fill_holes and fill_holes > 0:
        cmd += ["-morphology", "Close", f"disk:{fill_holes}"]
    if despeckle and despeckle > 0:
        cmd += ["-morphology", "Open", f"disk:{despeckle}"]
    cmd.append(str(intermediate))
    _run(cmd, "magick preprocess")


def _lower_hex(color: str) -> str:
    """Normalise one colour to lowercase #rrggbb."""

    color = color.strip().lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return f"#{color.lower():06s}"


def _normalise_fill(value: str) -> str:
    """Lowercase hex fills (vtracer emits uppercase); leave named/none/url paints alone."""

    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        return _lower_hex(value)
    return value


def _svg_header(engine: str, params: str, source_sha: str) -> str:
    """Header comment text recording engine, parameters, and source hash."""

    return f"traced with {engine}, {params}; source sha256={source_sha}"


def _potrace_path_d(pbm: Path) -> str:
    """Trace one PBM mask with potrace, return the concatenated path data."""

    svg_text = _run(["potrace", str(pbm), "-s", "-o", "-"], "potrace")
    root = ElementTree.fromstring(svg_text)
    parts: list[str] = []
    for path in root.iter():
        tag = path.tag
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag == "path" and path.get("d"):
            parts.append(path.get("d"))
    return " ".join(parts)


def _quantized_colours(image: Path, colors: int) -> list[tuple[float, str]]:
    """Quantise to ``colors`` and return [(area_percent, #rrggbb), ...]."""

    quantized = image.with_suffix(".quantized.png")
    _run(
        ["magick", str(image), "+dither", "-colors", str(colors), str(quantized)], "magick quantize"
    )
    txt = _run(["magick", str(quantized), "-unique-colors", "txt:-"], "magick unique-colors")
    colours: list[tuple[float, str]] = []
    seen: set[str] = set()
    for line in txt.splitlines():
        m = re.search(r"#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", line)
        if not m:
            continue
        fill = _lower_hex(m.group(1))
        if fill in seen:
            continue
        seen.add(fill)
        pct = _run(
            [
                "magick",
                str(quantized),
                "-fill",
                "white",
                "+opaque",
                fill,
                "-fill",
                "black",
                "-opaque",
                fill,
                "-format",
                "%[fx:100*(1-mean)]",
                "info:",
            ],
            "magick colour-area",
        )
        try:
            area = float(pct)
        except ValueError:
            area = 0.0
        colours.append((area, fill))
    quantized.unlink(missing_ok=True)
    colours.sort(key=lambda item: item[0], reverse=True)
    return colours


def _normalise_potrace_svg(svg_text: str, source_w: int, source_h: int, header: str) -> str:
    """Inject header comment and size into a potrace/vtracer SVG root."""

    root = ElementTree.fromstring(svg_text)
    root.set("width", str(source_w))
    root.set("height", str(source_h))
    root.set("viewBox", f"0 0 {source_w} {source_h}")
    comment = ElementTree.Comment(header)
    root.insert(0, comment)
    return ElementTree.tostring(root, encoding="unicode")


def _tag_of(element: ElementTree.Element) -> str:
    """Return an element's local tag name, stripping any namespace prefix."""

    tag = element.tag
    if not isinstance(tag, str):  # comments and processing instructions
        return ""
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    return tag


def _group_paths_by_fill(svg_text: str) -> str:
    """Wrap consecutive same-fill ``<path>`` elements in ``trace-layer-N`` groups.

    Paths are visited in document order; each run of adjacent paths sharing a fill
    becomes one group, numbered from 1 (painter order: the first group is layer 1).
    The group carries the shared fill as its own ``fill`` attribute. Returns the SVG
    text with bare paths replaced by the groups, leaving the header comment and root
    attributes intact.
    """

    parser = ElementTree.XMLParser(target=ElementTree.TreeBuilder(insert_comments=True))
    root = ElementTree.fromstring(svg_text, parser=parser)
    ns = ""
    if "}" in root.tag:
        ns = root.tag.split("}", 1)[0] + "}"
    paths = [element for element in list(root) if _tag_of(element) == "path"]
    if not paths:
        return svg_text
    for element in paths:
        root.remove(element)
    layer = 0
    index = 0
    while index < len(paths):
        fill = _normalise_fill(str(paths[index].get("fill", "none")))
        group = ElementTree.Element(f"{ns}g")
        layer += 1
        group.set("id", f"trace-layer-{layer}")
        group.set("fill", fill)
        while index < len(paths) and _normalise_fill(str(paths[index].get("fill", "none"))) == fill:
            group.append(paths[index])
            index += 1
        root.append(group)
    return ElementTree.tostring(root, encoding="unicode")


def _source_sha(source: Path) -> str:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return digest[:16]


def trace(
    source: Path,
    out: Path,
    *,
    engine: str = "auto",
    mode: str = "color",
    colors: int = 6,
    threshold: int = 50,
    flatten_bg: str | None = None,
    fill_holes: int = 0,
    despeckle: int = 0,
) -> TraceResult:
    """Trace one raster image into a layered SVG.

    Stage 1 of the svg helper suite. ``engine`` selects ``vtracer`` (when
    importable) or ``potrace``; ``auto`` prefers vtracer and falls back to
    potrace. The preprocessed intermediate is kept as
    ``<out stem>.trace-input.png``.
    """

    source = Path(source)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    intermediate = out.with_suffix(".trace-input.png")
    _magick_preprocess(
        source,
        intermediate,
        flatten_bg=flatten_bg,
        fill_holes=fill_holes,
        despeckle=despeckle,
    )
    source_w, source_h = _magick_size(intermediate)
    source_sha = _source_sha(source)

    def _build_vtracer() -> TraceResult:
        try:
            import vtracer
        except ImportError:
            raise SvgError(
                "vtracer is not installed; install the optional 'vector' extra: "
                f"{_VECTOR_INSTALL_HINT}"
            ) from None
        colormode = "color" if mode == "color" else "binary"
        vtracer.convert_image_to_svg_py(
            str(intermediate),
            str(out),
            colormode=colormode,
            color_precision=max(1, min(colors, 256)),
            filter_speckle=despeckle if despeckle > 0 else 1,
        )
        svg_text = out.read_text(encoding="utf-8")
        svg_text = _normalise_potrace_svg(
            svg_text,
            source_w,
            source_h,
            _svg_header("vtracer", f"mode={mode},colors={colors}", source_sha),
        )
        svg_text = _group_paths_by_fill(svg_text)
        out.write_text(svg_text, encoding="utf-8")
        layers = svg_text.count('<g id="trace-layer-')
        return TraceResult(svg_path=out, input_path=intermediate, engine="vtracer", layers=layers)

    def _build_potrace() -> TraceResult:
        if mode == "color":
            return _build_potrace_color()
        return _build_potrace_bw()

    def _build_potrace_bw() -> TraceResult:
        pbm = out.with_suffix(".trace-bw.pbm")
        _run(
            ["magick", str(intermediate), "-threshold", f"{threshold}%", str(pbm)],
            "magick threshold",
        )
        d = _potrace_path_d(pbm)
        pbm.unlink(missing_ok=True)
        header = _svg_header("potrace", f"mode=bw,threshold={threshold}%", source_sha)
        svg_text = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{source_w}" '
            f'height="{source_h}" viewBox="0 0 {source_w} {source_h}">'
            f"<!-- {header} -->"
            f'<g id="trace-layer-1" fill="#000000">'
            f'<path d="{d}"/></g>'
            f"</svg>"
        )
        write_svg(svg_text, out)
        return TraceResult(svg_path=out, input_path=intermediate, engine="potrace", layers=1)

    def _build_potrace_color() -> TraceResult:
        colours = _quantized_colours(intermediate, colors)
        colours.sort(key=lambda item: item[0], reverse=True)
        groups: list[str] = []
        for index, (_count, fill) in enumerate(colours, start=1):
            mask = out.with_suffix(f".trace-mask-{index}.pbm")
            _run(
                [
                    "magick",
                    str(intermediate),
                    "-fill",
                    "white",
                    "+opaque",
                    fill,
                    "-fill",
                    "black",
                    "-opaque",
                    fill,
                    str(mask),
                ],
                "magick mask",
            )
            d = _potrace_path_d(mask)
            mask.unlink(missing_ok=True)
            groups.append(f'<g id="trace-layer-{index}" fill="{fill}"><path d="{d}"/></g>')
        header = _svg_header(
            "potrace", f"mode=color,colors={colors},threshold={threshold}%", source_sha
        )
        svg_text = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{source_w}" '
            f'height="{source_h}" viewBox="0 0 {source_w} {source_h}">'
            f"<!-- {header} -->"
            f"{''.join(groups)}"
            f"</svg>"
        )
        write_svg(svg_text, out)
        return TraceResult(
            svg_path=out, input_path=intermediate, engine="potrace", layers=len(groups)
        )

    if engine == "vtracer":
        result = _build_vtracer()
    elif engine == "potrace":
        result = _build_potrace()
    else:
        try:
            import vtracer  # noqa: F401
        except ImportError:
            result = _build_potrace()
        else:
            result = _build_vtracer()
    return result


@dataclass(frozen=True)
class CompareReport:
    """Outcome of comparing one rendered SVG against its source raster."""

    pixel_diff_pct: float
    rmse: float
    components_missing: int
    components_extra: int
    sheet: Path | None
    distorted: bool


def _magick_mask(image: Path, mask: Path) -> None:
    """Write one ink mask: white where the image carries ink, black elsewhere."""

    _run(
        [
            "magick",
            str(image),
            "-background",
            "white",
            "-alpha",
            "remove",
            "-alpha",
            "off",
            "-colorspace",
            "gray",
            "-threshold",
            "50%",
            "-negate",
            str(mask),
        ],
        "magick ink-mask",
    )


def _magick_ae(source: Path, render: Path, diff: Path) -> int:
    """Count differing pixels via ``magick compare -metric AE`` (stderr integer).

    ``compare`` exits 1 when the images differ (expected) and writes the metric
    to stderr, so it is run directly rather than through the stdout-only
    ``_run`` helper; only unexpected failures raise ``SvgError``.
    """

    completed = subprocess.run(
        ["magick", "compare", "-metric", "AE", str(source), str(render), str(diff)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        tail = completed.stderr.strip()[-2000:]
        raise SvgError(f"magick compare AE failed: {tail}")
    return int(float(completed.stderr.strip().split()[0]))


def _magick_rmse(source: Path, render: Path) -> float:
    """Return the RMSE metric between source and render (stderr value)."""

    completed = subprocess.run(
        ["magick", "compare", "-metric", "RMSE", str(source), str(render), "null:"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        tail = completed.stderr.strip()[-2000:]
        raise SvgError(f"magick compare RMSE failed: {tail}")
    return float(completed.stderr.strip().split()[0])


def _count_components(mask: Path, min_area: int) -> int:
    """Count connected ink objects above ``min_area``; id 0 is the background.

    The labelled output of ``-connected-components 4`` assigns each object a
    distinct gray-level id (background is id 0). Counting unique colours minus
    the background id counts objects, not lines.
    """

    out = _run(
        [
            "magick",
            str(mask),
            "-define",
            f"connected-components:area-threshold={min_area}",
            "-connected-components",
            "4",
            "-unique-colors",
            "txt:-",
        ],
        "magick connected-components",
    )
    count = 0
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"\((\d+)\)", line)
        if m and int(m.group(1)) != 0:
            count += 1
    return count


def compare(
    source: Path,
    svg: Path,
    *,
    out: Path | None = None,
    min_area: int = 16,
    project: Project | None = None,
) -> CompareReport:
    """Compare one source raster against a rendered SVG, reporting metrics.

    The SVG is rendered at the source's pixel size (``--export-width`` /
    ``--export-height``) so a mismatched aspect is explicit and recorded as
    ``distorted``; nothing is cropped. ``pixel_diff_pct`` is the
    ``magick compare -metric AE`` differing-pixel count divided by source area
    (as a percent); ``rmse`` is the ``-metric RMSE`` value. Ink masks are
    built with ``-background white -alpha remove -alpha off -colorspace gray
    -threshold 50% -negate``; missing ink = source mask x negated render mask,
    extra ink = the reverse. Connected components (area threshold ``min_area``,
    4-connectivity) are counted on each mask, excluding id 0 which is the
    background. A three-panel labelled sheet (source | render | difference
    blend) is written to ``out`` or beside the source. Only infrastructure
    failures raise ``SvgError``; a bad match is reported, never raised.
    """

    source = Path(source)
    svg = Path(svg)
    if not source.is_file():
        raise SvgError(f"source image not found: {source}")
    if not svg.is_file():
        raise SvgError(f"svg not found: {svg}")

    source_w, source_h = _magick_size(source)
    svg_w, svg_h = _magick_size(svg)
    distorted = (source_w, source_h) != (svg_w, svg_h)

    render = source.with_suffix(".compare-render.png")
    try:
        render_png(svg, render, width=source_w, height=source_h)
    except SvgError:
        render.unlink(missing_ok=True)
        raise

    diff = source.with_suffix(".compare-diff.png")
    missing_mask = source.with_suffix(".compare-missing.png")
    extra_mask = source.with_suffix(".compare-extra.png")
    sheet = Path(out) if out is not None else source.with_stem(source.stem + "-sheet")
    try:
        ae = _magick_ae(source, render, diff)
        rmse = _magick_rmse(source, render)
        pixel_diff_pct = ae / (source_w * source_h) * 100.0

        source_mask = source.with_suffix(".compare-source-mask.png")
        _magick_mask(source, source_mask)
        render_mask = source.with_suffix(".compare-render-mask.png")
        try:
            _magick_mask(render, render_mask)
            _run(
                [
                    "magick",
                    str(source_mask),
                    "(",
                    str(render_mask),
                    "-negate",
                    ")",
                    "-compose",
                    "multiply",
                    "-composite",
                    str(missing_mask),
                ],
                "magick missing-mask",
            )
            _run(
                [
                    "magick",
                    str(render_mask),
                    "(",
                    str(source_mask),
                    "-negate",
                    ")",
                    "-compose",
                    "multiply",
                    "-composite",
                    str(extra_mask),
                ],
                "magick extra-mask",
            )
            components_missing = _count_components(missing_mask, min_area)
            components_extra = _count_components(extra_mask, min_area)
        finally:
            source_mask.unlink(missing_ok=True)
            render_mask.unlink(missing_ok=True)

        difference = source.with_suffix(".compare-difference.png")
        try:
            _run(
                [
                    "magick",
                    str(source),
                    str(render),
                    "-compose",
                    "difference",
                    "-composite",
                    "-auto-level",
                    str(difference),
                ],
                "magick difference-blend",
            )
            sheet.parent.mkdir(parents=True, exist_ok=True)
            _run(
                [
                    "magick",
                    "montage",
                    str(source),
                    str(render),
                    str(difference),
                    "-tile",
                    "1x",
                    "-background",
                    "none",
                    "-label",
                    "%t",
                    str(sheet),
                ],
                "magick montage sheet",
            )
        finally:
            difference.unlink(missing_ok=True)
    finally:
        for temporary in (render, diff, missing_mask, extra_mask):
            temporary.unlink(missing_ok=True)

    return CompareReport(
        pixel_diff_pct=pixel_diff_pct,
        rmse=rmse,
        components_missing=components_missing,
        components_extra=components_extra,
        sheet=sheet if sheet.is_file() else None,
        distorted=distorted,
    )


__all__ = [
    "CompareReport",
    "SvgError",
    "TraceResult",
    "arc_path",
    "build_spec",
    "check_safe_area",
    "compare",
    "render_png",
    "taper_path",
    "trace",
    "write_svg",
]
