"""Preset registry (`ms presets`) CLI commands."""

from __future__ import annotations

import argparse
import sys

from media_studio.presets import KINDS as PRESET_KINDS
from media_studio.presets import PresetError, load_recipes, validate_recipe
from media_studio.presets import search as search_presets

from .common import EXIT_FAILURE, EXIT_OK


def register_presets_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``presets`` command group."""

    presets = subparsers.add_parser("presets", help="search the preset registry")
    presets_sub = presets.add_subparsers(dest="presets_command", required=True)

    listing = presets_sub.add_parser("list", help="every recipe, optionally by kind")
    listing.add_argument("--kind", choices=PRESET_KINDS, default=None)
    listing.set_defaults(func=cmd_presets_list)

    find = presets_sub.add_parser("search", help="search recipes by intent")
    find.add_argument("query")
    find.set_defaults(func=cmd_presets_search)

    show = presets_sub.add_parser("show", help="one recipe with its parameters")
    show.add_argument("preset_id")
    show.set_defaults(func=cmd_presets_show)


def _load_registry() -> dict[str, object]:
    """Load and integrity-check the registry, refusing to serve a broken recipe."""

    recipes = load_recipes()
    problems = [problem for recipe in recipes.values() for problem in validate_recipe(recipe)]
    if problems:
        raise PresetError("registry problems: " + "; ".join(problems))
    return recipes


def cmd_presets_list(args: argparse.Namespace) -> int:
    """List recipes, one line each."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    selected = [
        recipe for recipe in recipes.values() if args.kind is None or recipe.kind == args.kind
    ]
    for recipe in sorted(selected, key=lambda item: (item.kind, item.id)):
        print(f"  {recipe.id:<26} {recipe.kind:<13} {recipe.title}")
    print(f"\n{len(selected)} preset(s); search with `ms presets search <intent>`")
    return EXIT_OK


def cmd_presets_search(args: argparse.Namespace) -> int:
    """Search by intent and print what each match is for."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    matches = search_presets(recipes, args.query)
    if not matches:
        print(f"no preset matches {args.query!r}; try `ms presets list`")
        return EXIT_OK
    for recipe in matches:
        print(f"  {recipe.id}  ({recipe.kind})")
        print(f"    {recipe.summary}")
    return EXIT_OK


def cmd_presets_show(args: argparse.Namespace) -> int:
    """Show one recipe: what it does, its parameters, and how to reference it."""

    try:
        recipes = _load_registry()
    except PresetError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILURE

    recipe = recipes.get(args.preset_id)
    if recipe is None:
        print(f"error: no preset {args.preset_id!r}", file=sys.stderr)
        return EXIT_FAILURE

    print(f"{recipe.id} - {recipe.title}")
    print(f"  kind:       {recipe.kind} ({recipe.engine})")
    print(f"  applies to: {', '.join(recipe.applies_to)}")
    print(f"  intent:     {', '.join(recipe.intent)}")
    print(f"  licence:    {recipe.license}")
    print(f"  provenance: {recipe.provenance}")
    print(f"  {recipe.summary}")
    if recipe.parameters:
        print("\nparameters")
        for parameter in recipe.parameters:
            bounds = ""
            if parameter.minimum is not None and parameter.maximum is not None:
                bounds = f" [{parameter.minimum:g}..{parameter.maximum:g}]"
            unit = f" {parameter.unit}" if parameter.unit else ""
            details = f"default {parameter.default}{unit}{bounds}"
            print(f"  {parameter.name:<10} {parameter.type:<8} {details}")
            if parameter.description:
                print(f"             {parameter.description}")
    print("\nreference it in project.yaml under effects:")
    example = {"id": recipe.id}
    if recipe.parameters:
        example[recipe.parameters[0].name] = recipe.parameters[0].default
    print(f"  effects:\n    - {example}")
    return EXIT_OK
