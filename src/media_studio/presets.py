"""The preset and template registry (design decision D33).

A preset is a recipe, not a binary: it names a mechanism the toolkit has already verified, plus
the parameters that mechanism accepts. That is why the library is engine-native — an MLT
transition, an MLT filter, a level plan for audio, or a `qtext` title — instead of an After Effects
template this stack could never execute.

Recipes are plain YAML under `presets/`, so reading one costs a few dozen tokens and searching is
local and textual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .ledger import classify_license

PRESET_DIRECTORY = "presets"
KINDS = ("transition", "video-effect", "audio-effect", "title")
PARAMETER_TYPES = ("number", "integer", "string")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# Keys that place an effect rather than configure it.
PLACEMENT_KEYS = ("id", "at", "text")


class PresetError(RuntimeError):
    """Raised when a recipe is malformed or a parameter is out of range."""


@dataclass(frozen=True)
class Parameter:
    """One knob a recipe exposes."""

    name: str
    type: str = "number"
    default: object = 0.0
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    description: str = ""

    def coerce(self, value: object) -> object:
        """Turn a user value into the declared type, or explain why it cannot."""

        if self.type == "string":
            return str(value)
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise PresetError(f"{self.name} must be a number, got {value!r}") from error
        if self.minimum is not None and number < self.minimum:
            raise PresetError(f"{self.name} must be at least {self.minimum}, got {number}")
        if self.maximum is not None and number > self.maximum:
            raise PresetError(f"{self.name} must be at most {self.maximum}, got {number}")
        return int(number) if self.type == "integer" else number


@dataclass(frozen=True)
class Recipe:
    """A named, parameterised effect."""

    id: str
    title: str
    kind: str
    engine: str
    intent: tuple[str, ...]
    applies_to: tuple[str, ...]
    summary: str
    implementation: dict[str, object]
    parameters: tuple[Parameter, ...]
    license: str
    provenance: str
    path: Path | None = None

    def parameter(self, name: str) -> Parameter | None:
        return next((item for item in self.parameters if item.name == name), None)

    def defaults(self) -> dict[str, object]:
        return {item.name: item.default for item in self.parameters}

    def resolve(self, overrides: dict[str, object] | None = None) -> dict[str, object]:
        """Merge overrides onto the defaults, rejecting unknown or out-of-range values."""

        values = self.defaults()
        for name, value in (overrides or {}).items():
            parameter = self.parameter(name)
            if parameter is None:
                known = ", ".join(item.name for item in self.parameters)
                raise PresetError(f"{self.id} has no parameter {name!r}; it accepts: {known}")
            values[name] = parameter.coerce(value)
        return values

    def render(self, values: dict[str, object]) -> dict[str, str]:
        """Substitute parameters into the implementation's property values."""

        properties = self.implementation.get("properties")
        if not isinstance(properties, dict):
            return {}
        rendered: dict[str, str] = {}
        for key, raw in properties.items():
            text = str(raw)
            for name in PLACEHOLDER.findall(text):
                if name not in values:
                    raise PresetError(f"{self.id} uses {{{name}}} but has no such parameter")
                text = text.replace(f"{{{name}}}", _format(values[name]))
            rendered[str(key)] = text
        return rendered

    def audio_settings(self, values: dict[str, object]) -> dict[str, object]:
        """Resolved values for an audio-effect recipe."""

        settings = self.implementation.get("audio")
        if not isinstance(settings, dict):
            return {}
        resolved: dict[str, object] = {}
        for key, raw in settings.items():
            text = str(raw)
            for name in PLACEHOLDER.findall(text):
                text = text.replace(f"{{{name}}}", _format(values.get(name, "")))
            resolved[str(key)] = float(text) if key != "position" else text
        return resolved

    def title_settings(self, values: dict[str, object]) -> dict[str, object]:
        """Resolved values for a title recipe."""

        settings = self.implementation.get("title")
        if not isinstance(settings, dict):
            return {}
        resolved: dict[str, object] = {}
        for key, raw in settings.items():
            text = str(raw)
            for name in PLACEHOLDER.findall(text):
                text = text.replace(f"{{{name}}}", _format(values.get(name, "")))
            resolved[str(key)] = text
        return resolved

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "engine": self.engine,
            "intent": list(self.intent),
            "applies_to": list(self.applies_to),
            "summary": self.summary,
            "license": self.license,
            "provenance": self.provenance,
            "parameters": [
                {
                    "name": item.name,
                    "type": item.type,
                    "default": item.default,
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                    "unit": item.unit,
                    "description": item.description,
                }
                for item in self.parameters
            ],
        }


def _format(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def repository_directory() -> Path:
    """The `presets/` directory inside the checkout."""

    here = Path(__file__).resolve()
    for candidate in here.parents:
        directory = candidate / PRESET_DIRECTORY
        if directory.is_dir() and list(directory.glob("*.yaml")):
            return directory
    raise PresetError(
        "no presets directory found; run media-studio from its git checkout "
        "(expected <repo>/presets/*.yaml)"
    )


def load_recipe(path: Path) -> Recipe:
    """Read one recipe file."""

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise PresetError(f"{path.name} is not valid YAML: {error}") from error
    if not isinstance(payload, dict):
        raise PresetError(f"{path.name} must contain a mapping")
    if not payload.get("id"):
        raise PresetError(f"{path.name} needs an id")

    parameters: list[Parameter] = []
    for entry in payload.get("parameters") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise PresetError(f"{path.name}: every parameter needs a name, got {entry!r}")
        parameters.append(
            Parameter(
                name=str(entry["name"]),
                type=str(entry.get("type") or "number"),
                default=entry.get("default", 0),
                minimum=entry.get("minimum"),
                maximum=entry.get("maximum"),
                unit=str(entry.get("unit") or ""),
                description=str(entry.get("description") or ""),
            )
        )

    return Recipe(
        id=str(payload["id"]),
        title=str(payload.get("title") or payload["id"]),
        kind=str(payload.get("kind") or ""),
        engine=str(payload.get("engine") or "mlt"),
        intent=tuple(str(item) for item in payload.get("intent") or ()),
        applies_to=tuple(str(item) for item in payload.get("applies_to") or ()),
        summary=str(payload.get("summary") or ""),
        implementation=dict(payload.get("implementation") or {}),
        parameters=tuple(parameters),
        license=str(payload.get("license") or ""),
        provenance=str(payload.get("provenance") or ""),
        path=path,
    )


def load_recipes(directory: Path | None = None) -> dict[str, Recipe]:
    """Every recipe, keyed by id, in a stable order."""

    root = directory or repository_directory()
    recipes: dict[str, Recipe] = {}
    for path in sorted(root.glob("*.yaml")):
        recipe = load_recipe(path)
        recipes[recipe.id] = recipe
    return recipes


def validate_recipe(recipe: Recipe) -> list[str]:
    """Integrity problems, so a malformed recipe fails loudly instead of at render time."""

    problems: list[str] = []
    if recipe.path is not None and recipe.path.stem != recipe.id:
        problems.append(f"{recipe.path.name}: id {recipe.id!r} does not match the file name")
    if recipe.kind not in KINDS:
        problems.append(f"{recipe.id}: kind must be one of {', '.join(KINDS)}, got {recipe.kind!r}")
    if not recipe.intent:
        problems.append(f"{recipe.id}: needs at least one intent tag to be searchable")
    if classify_license(recipe.license) is None:
        problems.append(f"{recipe.id}: licence {recipe.license!r} is not one the ledger knows")
    if not recipe.provenance:
        problems.append(f"{recipe.id}: needs provenance, so a reader can tell where it came from")

    if recipe.kind == "transition" and not recipe.implementation.get("transition"):
        problems.append(f"{recipe.id}: a transition recipe needs implementation.transition")
    if recipe.kind == "video-effect" and not recipe.implementation.get("mlt_filter"):
        problems.append(f"{recipe.id}: a video-effect recipe needs implementation.mlt_filter")
    if recipe.kind == "audio-effect" and not recipe.implementation.get("audio"):
        problems.append(f"{recipe.id}: an audio-effect recipe needs implementation.audio")
    if recipe.kind == "title" and not recipe.implementation.get("title"):
        problems.append(f"{recipe.id}: a title recipe needs implementation.title")

    seen: set[str] = set()
    for parameter in recipe.parameters:
        if parameter.name in seen:
            problems.append(f"{recipe.id}: duplicate parameter {parameter.name}")
        seen.add(parameter.name)
        if parameter.type not in PARAMETER_TYPES:
            problems.append(
                f"{recipe.id}.{parameter.name}: type must be one of {', '.join(PARAMETER_TYPES)}"
            )
        if parameter.type != "string":
            try:
                value = float(parameter.default)
            except (TypeError, ValueError):
                problems.append(f"{recipe.id}.{parameter.name}: default must be a number")
                continue
            if parameter.minimum is not None and value < float(parameter.minimum):
                problems.append(f"{recipe.id}.{parameter.name}: default is below the minimum")
            if parameter.maximum is not None and value > float(parameter.maximum):
                problems.append(f"{recipe.id}.{parameter.name}: default is above the maximum")
    return problems


def search(recipes: dict[str, Recipe], query: str) -> list[Recipe]:
    """Rank recipes by how well their id, title, and intent tags match a query."""

    words = [word for word in re.split(r"[^a-z0-9]+", query.lower()) if word]
    if not words:
        return list(recipes.values())

    scored: list[tuple[int, str, Recipe]] = []
    for recipe in recipes.values():
        haystacks = {
            "id": recipe.id.replace("-", " ").lower(),
            "title": recipe.title.lower(),
            "intent": " ".join(recipe.intent).lower(),
            "summary": recipe.summary.lower(),
        }
        score = 0
        for word in words:
            if word in haystacks["intent"]:
                score += 3
            if word in haystacks["title"]:
                score += 2
            if word in haystacks["id"]:
                score += 2
            if word in haystacks["summary"]:
                score += 1
        if score:
            scored.append((score, recipe.id, recipe))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [recipe for _score, _id, recipe in scored]


def validate_effects(effects: tuple[dict[str, object], ...]) -> list[str]:
    """Unknown ids and bad parameters, reported as problems for `ms project validate`."""

    if not effects:
        return []
    try:
        catalog = load_recipes()
    except PresetError as error:
        return [f"effects: {error}"]
    problems: list[str] = []
    for entry in effects:
        preset_id = str(entry.get("id") or "")
        recipe = catalog.get(preset_id)
        if recipe is None:
            problems.append(f"effects: no preset {preset_id!r}")
            continue
        try:
            resolve_effects([entry], catalog)
        except PresetError as error:
            problems.append(f"effects[{preset_id}]: {error}")
        if recipe.kind == "title" and not entry.get("text"):
            problems.append(f"effects[{preset_id}]: a title effect needs text")
    return problems


def resolve_effects(
    effects: list[dict[str, object]],
    recipes: dict[str, Recipe] | None = None,
) -> list[tuple[Recipe, dict[str, object]]]:
    """Turn manifest effect entries into resolved recipes, failing on anything unknown."""

    catalog = recipes if recipes is not None else load_recipes()
    resolved: list[tuple[Recipe, dict[str, object]]] = []
    for entry in effects:
        preset_id = str(entry.get("id") or "")
        if preset_id not in catalog:
            known = ", ".join(sorted(catalog))
            raise PresetError(f"no preset {preset_id!r}; available: {known}")
        recipe = catalog[preset_id]
        overrides = {key: value for key, value in entry.items() if key not in PLACEMENT_KEYS}
        resolved.append((recipe, recipe.resolve(overrides)))
    return resolved
