"""Tests for the key wizard's check mode.

The wizard distinguishes three states, and only `required` fails the check: a keyed provider whose
key cannot be obtained now is deferred, and one the toolkit works without is optional.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WIZARD = REPO_ROOT / "scripts" / "keys-wizard.sh"
REQUIRED = (
    "PIXABAY_API_KEY",
    "FREESOUND_API_KEY",
    "JAMENDO_CLIENT_ID",
    "SKETCHFAB_API_TOKEN",
)
OPTIONAL = ("UNSPLASH_ACCESS_KEY", "BLENDERKIT_API_KEY")
DEFERRED = "PEXELS_API_KEY"
FAKE_VALUE = "unit-test-value-do-not-log-1f4c"


def run_check(config_dir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the wizard in check mode unless a caller passes its own arguments."""

    environment = dict(os.environ)
    environment["OPENCODE_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(
        ["bash", str(WIZARD), *(arguments or ("--check",))],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def write_secrets(config_dir: Path, names: tuple[str, ...]) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{name}={FAKE_VALUE}\n" for name in names)
    (config_dir / "secrets.env").write_text(body, encoding="utf-8")


def test_all_required_keys_present_is_clean(tmp_path: Path) -> None:
    write_secrets(tmp_path, (*REQUIRED, *OPTIONAL, DEFERRED))

    completed = run_check(tmp_path)

    assert completed.returncode == 0
    assert "all required keys present" in completed.stdout
    assert FAKE_VALUE not in completed.stdout
    assert FAKE_VALUE not in completed.stderr


def test_only_optional_and_deferred_missing_still_passes(tmp_path: Path) -> None:
    write_secrets(tmp_path, REQUIRED)

    completed = run_check(tmp_path)

    assert completed.returncode == 0
    assert "optional, not set (the stocky skill uses it" in completed.stdout
    assert "deferred (account suspended by the provider" in completed.stdout
    assert "(2 optional not set)" in completed.stdout
    assert "(1 deferred by the provider)" in completed.stdout


def test_a_missing_required_key_fails(tmp_path: Path) -> None:
    write_secrets(tmp_path, REQUIRED[1:])

    completed = run_check(tmp_path)

    assert completed.returncode == 1
    reported = [line.split() for line in completed.stdout.splitlines() if line.strip()]
    assert [REQUIRED[0], "missing"] in reported
    assert "1 required key(s) missing" in completed.stdout


@pytest.mark.parametrize("name", REQUIRED)
def test_each_required_key_is_reported(tmp_path: Path, name: str) -> None:
    others = tuple(candidate for candidate in REQUIRED if candidate != name)
    write_secrets(tmp_path, others)

    completed = run_check(tmp_path)

    assert completed.returncode == 1
    assert name in completed.stdout
    assert "1 required key(s) missing" in completed.stdout


def test_an_absent_file_reports_every_required_key(tmp_path: Path) -> None:
    completed = run_check(tmp_path)

    assert completed.returncode == 1
    assert "(absent)" in completed.stdout
    assert f"{len(REQUIRED)} required key(s) missing" in completed.stdout


def test_values_are_never_printed(tmp_path: Path) -> None:
    write_secrets(tmp_path, (*REQUIRED, *OPTIONAL, DEFERRED))

    completed = run_check(tmp_path)

    assert FAKE_VALUE not in completed.stdout
    assert "unit-test" not in completed.stdout
    assert "..." in completed.stdout
