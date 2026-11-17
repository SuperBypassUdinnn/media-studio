"""Tests for repository skills, architectural rules, and ms motion catalog CLI."""

from __future__ import annotations

import json
from pathlib import Path

from media_studio.cli import main

REQUIRED_SKILLS = (
    "effectcraft-discovery",
    "3d-product-cinematography",
    "kinetic-3d-typography",
    "visual-storyboarding",
    "multimodal-critic-gate",
    "feature-dev-workflow",
)


def test_repo_rules_and_skills_exist_with_valid_frontmatter() -> None:
    repo = Path(__file__).resolve().parents[1]
    arch_rule = repo / ".agents" / "rules" / "architecture.md"
    assert arch_rule.is_file(), "missing .agents/rules/architecture.md"
    text = arch_rule.read_text(encoding="utf-8")
    assert "Asset Priority SOP" in text
    assert "60-30-10" in text

    for skill_id in REQUIRED_SKILLS:
        skill_md = repo / ".agents" / "skills" / skill_id / "SKILL.md"
        assert skill_md.is_file(), f"missing repo skill {skill_id}"
        content = skill_md.read_text(encoding="utf-8")
        assert content.startswith("---\n"), f"{skill_id} missing YAML frontmatter opening"
        assert f"name: {skill_id}" in content, f"{skill_id} missing name in frontmatter"
        assert "description:" in content, f"{skill_id} missing description in frontmatter"


def test_cli_motion_catalog_outputs_effects_and_templates(capsys) -> None:
    rc = main(["motion", "catalog", "--category", "stylize", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(e["id"] == "ec.stylize.cclightsweep" for e in payload)

    rc_tpl = main(["motion", "catalog", "--templates", "--json"])
    assert rc_tpl == 0
    tpl_payload = json.loads(capsys.readouterr().out)
    assert any(t["id"] == "text-orbit-3d" for t in tpl_payload)
