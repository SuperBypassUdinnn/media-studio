"""Tests for the key wizard's check mode."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WIZARD = REPO_ROOT / "scripts" / "keys-wizard.sh"
# Every provider that is not deferred must be reported when its key is absent.
OBTAINABLE = (
    "PIXABAY_API_KEY",
    "FREESOUND_API_KEY",
    "JAMENDO_CLIENT_ID",
    "UNSPLASH_ACCESS_KEY",
    "SKETCHFAB_API_TOKEN",
    "BLENDERKIT_API_KEY",
)
DEFERRED = "PEXELS_API_KEY"
FAKE_VALUE = "unit-test-value-do-not-log-1f4c"


def run_check(config_dir: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["OPENCODE_CONFIG_DIR"] = str(config_dir)
    return subprocess.run(
        ["bash", str(WIZARD), "--check"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def write_secrets(config_dir: Path, names: tuple[str, ...]) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{name}={FAKE_VALUE}\n" for name in names)
    (config_dir / "secrets.env").write_text(body, encoding="utf-8")


def test_missing_required_key_fails(tmp_path: Path) -> None:
    write_secrets(tmp_path, OBTAINABLE[1:])

    completed = run_check(tmp_path)

    assert completed.returncode == 1
    reported = [line.split() for line in completed.stdout.splitlines() if line.strip()]
    assert [OBTAINABLE[0], "missing"] in reported
    assert "1 key(s) missing" in completed.stdout


def test_only_the_deferred_provider_missing_passes(tmp_path: Path) -> None:
    write_secrets(tmp_path, OBTAINABLE)

    completed = run_check(tmp_path)

    assert completed.returncode == 0
    assert "deferred (account suspended by the provider" in completed.stdout
    assert "all obtainable keys present (1 deferred by the provider)" in completed.stdout


def test_all_keys_present_is_clean_and_silent_about_values(tmp_path: Path) -> None:
    write_secrets(tmp_path, (*OBTAINABLE, DEFERRED))

    completed = run_check(tmp_path)

    assert completed.returncode == 0
    assert "all keys present" in completed.stdout
    assert FAKE_VALUE not in completed.stdout
    assert FAKE_VALUE not in completed.stderr
    assert "unit-test" not in completed.stdout


def test_absent_file_reports_absent_and_masks_nothing(tmp_path: Path) -> None:
    completed = run_check(tmp_path)

    assert completed.returncode == 1
    assert "(absent)" in completed.stdout
    # Six obtainable providers are missing; the deferred one is never counted.
    assert "6 key(s) missing" in completed.stdout


@pytest.mark.parametrize("name", OBTAINABLE)
def test_each_obtainable_key_is_reported_missing(tmp_path: Path, name: str) -> None:
    others = tuple(candidate for candidate in OBTAINABLE if candidate != name)
    write_secrets(tmp_path, others)

    completed = run_check(tmp_path)

    assert completed.returncode == 1
    assert name in completed.stdout
    assert "1 key(s) missing" in completed.stdout
