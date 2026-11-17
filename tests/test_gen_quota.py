"""Tests for QuotaStore: persistence, daily rollover, cooldown, and ``fcntl.flock``."""

from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from media_studio.gen.quota import DEFAULT_QUOTA_PATH, QuotaStore


@pytest.fixture
def quota_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gen-quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(path))
    return path


def test_default_quota_path_is_state_dir() -> None:
    assert Path.home() / ".local/state/media-studio/gen-quota.json" == DEFAULT_QUOTA_PATH


def test_record_spend_persists_across_instances(quota_path: Path) -> None:
    first = QuotaStore()
    assert first.can_spend("aistudio", max_daily=5)
    first.record_spend("aistudio")
    first.record_spend("aistudio")

    second = QuotaStore()
    assert second.can_spend("aistudio", max_daily=5) is True
    # Two spent of five: three remain.
    assert second.can_spend("aistudio", max_daily=2) is False


def test_hitting_daily_cap_blocks_further_spend(quota_path: Path) -> None:
    store = QuotaStore()
    for _ in range(3):
        store.record_spend("aistudio")
    assert store.can_spend("aistudio", max_daily=3) is False
    # Other providers are unaffected.
    assert store.can_spend("huggingface", max_daily=3) is True


def test_set_cooldown_skips_provider_until_it_expires(quota_path: Path) -> None:
    store = QuotaStore()
    assert store.can_spend("aistudio", max_daily=5) is True
    store.set_cooldown("aistudio", 3600)
    assert store.can_spend("aistudio", max_daily=5) is False
    # Simulate cooldown expiry by rolling the stored timestamp into the past.
    data = json.loads(quota_path.read_text(encoding="utf-8"))
    data["providers"]["aistudio"]["cooldown_until"] = 1.0  # long expired
    quota_path.write_text(json.dumps(data), encoding="utf-8")
    assert QuotaStore().can_spend("aistudio", max_daily=5) is True


def test_daily_rollover_resets_spend_and_cooldowns(quota_path: Path) -> None:
    store = QuotaStore()
    store.record_spend("aistudio")
    store.set_cooldown("huggingface", 3600)

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    quota_path.write_text(
        json.dumps({"date": yesterday, "providers": {"aistudio": {"spent": 5}}}), encoding="utf-8"
    )

    rolled = QuotaStore()
    assert rolled.can_spend("aistudio", max_daily=3) is True
    assert rolled.can_spend("huggingface", max_daily=3) is True


def test_record_spend_clears_cooldown(quota_path: Path) -> None:
    store = QuotaStore()
    store.set_cooldown("aistudio", 3600)
    assert store.can_spend("aistudio", max_daily=5) is False
    store.record_spend("aistudio")
    assert store.can_spend("aistudio", max_daily=5) is True


def test_reset_clears_one_or_all_providers(quota_path: Path) -> None:
    store = QuotaStore()
    store.record_spend("aistudio")
    store.record_spend("huggingface")
    store.reset("aistudio")
    assert store.can_spend("aistudio", max_daily=1) is True
    assert store.can_spend("huggingface", max_daily=1) is False
    store.reset()
    assert store.can_spend("huggingface", max_daily=1) is True


def test_flock_is_acquired_on_read_and_write(quota_path: Path) -> None:
    calls: list[str] = []

    original = fcntl.flock

    def spy(fd, op, *args, **kwargs):
        calls.append("lock" if op == fcntl.LOCK_EX else "unlock")
        return original(fd, op, *args, **kwargs)

    store = QuotaStore()
    with mock.patch("media_studio.gen.quota.fcntl.flock", side_effect=spy):
        store.record_spend("aistudio")
        store.can_spend("aistudio", max_daily=5)

    assert calls.count("lock") >= 2, calls
    assert calls.count("unlock") >= 2, calls


def test_constructor_path_and_env_override(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "custom" / "quota.json"
    store = QuotaStore(path=custom)
    assert store.path == custom
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(custom))
    assert QuotaStore().path == custom
