"""Unit tests for EffectCraft capabilities catalog."""

from __future__ import annotations

from media_studio.effectcraft_catalog import (
    EFFECTCRAFT_CATEGORIES,
    EFFECTCRAFT_TEMPLATES,
    KEY_BUILTIN_EFFECTS,
    list_effects,
    list_templates,
)


def test_catalog_has_categories() -> None:
    assert len(EFFECTCRAFT_CATEGORIES) >= 15
    assert "stylize" in EFFECTCRAFT_CATEGORIES
    assert "blur" in EFFECTCRAFT_CATEGORIES
    assert "3d" in EFFECTCRAFT_CATEGORIES


def test_list_templates_returns_expected_presets() -> None:
    templates = list_templates()
    assert len(templates) == len(EFFECTCRAFT_TEMPLATES)
    ids = {t["id"] for t in templates}
    assert "logo-reveal" in ids
    assert "title-card" in ids
    assert "kinetic-type" in ids
    assert "text-orbit-3d" in ids


def test_list_effects_filters_by_category() -> None:
    stylize_effects = list_effects(category="stylize")
    assert len(stylize_effects) >= 4
    assert any(e["id"] == "ec.stylize.cclightsweep" for e in stylize_effects)
    assert any(e["id"] == "ec.stylize.glow" for e in stylize_effects)
    assert all(e["category"] == "stylize" for e in stylize_effects)

    all_effects = list_effects()
    assert len(all_effects) == len(KEY_BUILTIN_EFFECTS)
