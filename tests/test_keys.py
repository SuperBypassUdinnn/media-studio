"""Tests for credential loading, which must never leak a value."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_studio.keys import (
    config_directory,
    key_for,
    key_status,
    load_secrets,
    mask,
    missing_required_keys,
    parse_secrets,
    secrets_file,
)

FAKE = "example-secret-value-1234567890"


def test_parses_comments_quotes_and_blank_lines() -> None:
    text = "# a comment\n\nA=plain\nB = \"quoted\"\nC='single'\nD=\nE=two=parts\n"

    assert parse_secrets(text) == {
        "A": "plain",
        "B": "quoted",
        "C": "single",
        "E": "two=parts",
    }


def test_config_directory_honours_the_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path))

    assert config_directory() == tmp_path
    assert secrets_file() == tmp_path / "secrets.env"


def test_the_environment_wins_over_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "secrets.env").write_text("FAKE_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_KEY", "from-environment")

    assert key_for("FAKE_KEY") == "from-environment"


def test_the_file_is_used_when_the_environment_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FAKE_KEY", raising=False)
    (tmp_path / "secrets.env").write_text("FAKE_KEY=from-file\n", encoding="utf-8")

    assert key_for("FAKE_KEY") == "from-file"


def test_absent_file_and_missing_key_are_not_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NOPE_KEY", raising=False)

    assert load_secrets() == {}
    assert key_for("NOPE_KEY") is None


def test_an_unreadable_secrets_path_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "secrets.env").mkdir()

    assert load_secrets() == {}


def test_the_wizard_and_this_module_agree_on_every_provider() -> None:
    """The wizard owns the interactive list; this module must not drift from it."""

    import re
    from pathlib import Path as PathType

    from media_studio.keys import DEFERRED_KEYS, OPTIONAL_KEYS, REQUIRED_KEYS

    script = PathType(__file__).resolve().parents[1] / "scripts" / "keys-wizard.sh"
    entries = re.findall(r'^\s*"([A-Z_]+)\|[^|]*\|[^|]*\|[^|]*\|([^"]*)"', script.read_text(), re.M)
    assert entries, "no provider entries parsed from the wizard"

    by_state: dict[str, set[str]] = {"required": set(), "optional": set(), "deferred": set()}
    for name, status in entries:
        state = status.split(":", 1)[0] or "required"
        assert state in by_state, f"unknown wizard state {state!r} for {name}"
        by_state[state].add(name)

    assert by_state["required"] == set(REQUIRED_KEYS)
    assert by_state["optional"] == set(OPTIONAL_KEYS)
    assert by_state["deferred"] == set(DEFERRED_KEYS)


def test_mask_never_reveals_the_middle() -> None:
    assert mask("") == ""
    assert mask("short") == "set (5 chars)"

    masked = mask(FAKE)

    assert FAKE not in masked
    assert masked.startswith("exam")
    assert masked.endswith(f"({len(FAKE)} chars)")


def test_key_status_reports_where_each_value_came_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PIXABAY_API_KEY", FAKE)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    (tmp_path / "secrets.env").write_text("JAMENDO_CLIENT_ID=file-value\n", encoding="utf-8")

    statuses = {status.name: status for status in key_status()}

    assert statuses["PIXABAY_API_KEY"].source == "environment"
    assert statuses["PIXABAY_API_KEY"].present is True
    assert FAKE not in statuses["PIXABAY_API_KEY"].masked
    assert statuses["JAMENDO_CLIENT_ID"].source == "secrets.env"
    assert statuses["PIXABAY_API_KEY"].requirement == "required"
    assert statuses["BLENDERKIT_API_KEY"].requirement == "optional"
    assert statuses["PEXELS_API_KEY"].requirement == "deferred"
    assert missing_required_keys() == ["FREESOUND_API_KEY", "SKETCHFAB_API_TOKEN"]
