"""Tests for FlowAccountManager / FlowCreditStore with mocked transports.

Zero live network calls. Waterfall rotation order: Free Daily -> Pro Daily ->
Pro Monthly (only when allowed). Predictive pre-check skips accounts with
insufficient daily balance without touching the monthly bank. Date rollover
resets daily counters while preserving monthly counters. ``fcntl.flock`` is
held on every read-modify-write cycle.
"""

from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from media_studio.gen.flow_quota import (
    DEFAULT_ACCOUNTS_PATH,
    DEFAULT_CREDITS_PATH,
    FlowAccountManager,
    FlowCreditStore,
)

ACCOUNTS = [
    {
        "id": "free-1",
        "email": "free1@gmail.com",
        "tier": "free",
        "enabled": True,
        "token": "tok-free-1",
    },
    {
        "id": "pro-1",
        "email": "pro1@gmail.com",
        "tier": "pro",
        "enabled": True,
        "token": "tok-pro-1",
    },
    {
        "id": "pro-2",
        "email": "pro2@gmail.com",
        "tier": "pro",
        "enabled": True,
        "token": "tok-pro-2",
    },
]


def _write_accounts(path: Path, accounts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(accounts), encoding="utf-8")


@pytest.fixture
def accounts_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "flow-accounts.json"
    _write_accounts(path, ACCOUNTS)
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_ACCOUNTS_PATH", str(path))
    return path


@pytest.fixture
def credits_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "flow-credits.json"
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_CREDITS_PATH", str(path))
    return path


# -- manager ----------------------------------------------------------------


def test_default_accounts_path_is_config_dir() -> None:
    assert Path.home() / ".config/media-studio/flow-accounts.json" == DEFAULT_ACCOUNTS_PATH


def test_default_credits_path_is_state_dir() -> None:
    assert Path.home() / ".local/state/media-studio/flow-credits.json" == DEFAULT_CREDITS_PATH


def test_manager_loads_accounts(accounts_path: Path) -> None:
    manager = FlowAccountManager()
    accounts = manager.accounts()
    assert len(accounts) == 3
    assert accounts[0].id == "free-1"
    assert accounts[0].tier == "free"
    assert accounts[0].enabled is True


def test_manager_skips_disabled_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    disabled = [
        {
            "id": "free-1",
            "email": "free1@gmail.com",
            "tier": "free",
            "enabled": False,
            "token": "tok",
        },
        {"id": "pro-1", "email": "pro1@gmail.com", "tier": "pro", "enabled": True, "token": "tok"},
    ]
    path = tmp_path / "flow-accounts.json"
    _write_accounts(path, disabled)
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_ACCOUNTS_PATH", str(path))
    manager = FlowAccountManager()
    assert [a.id for a in manager.accounts()] == ["pro-1"]


def test_manager_constructor_and_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom" / "accounts.json"
    manager = FlowAccountManager(accounts_path=custom)
    assert manager.accounts_path == custom
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_ACCOUNTS_PATH", str(custom))
    assert FlowAccountManager().accounts_path == custom


# -- credit store: waterfall rotation ---------------------------------------


def test_waterfall_picks_free_daily_first(accounts_path: Path, credits_path: Path) -> None:
    store = FlowCreditStore()
    manager = FlowAccountManager()
    account = store.select_account(manager.accounts(), cost=10, allow_monthly_pool=False)
    assert account is not None
    assert account.tier == "free"


def test_waterfall_free_exhausted_then_pro_daily(accounts_path: Path, credits_path: Path) -> None:
    store = FlowCreditStore()
    manager = FlowAccountManager()
    # Drain the free account's daily allowance (60) down to < 10.
    for _ in range(6):
        store.record_spend("free-1", 10, "nano-banana-pro", pool="daily")
    account = store.select_account(manager.accounts(), cost=10, allow_monthly_pool=False)
    assert account is not None
    assert account.tier == "pro"
    assert account.id == "pro-1"


def test_waterfall_pro_daily_exhausted_then_monthly_only_when_allowed(
    accounts_path: Path,
    credits_path: Path,
) -> None:
    store = FlowCreditStore()
    manager = FlowAccountManager()
    # Drain every account's daily allowance so only the monthly bank remains.
    for aid in ("free-1", "pro-1", "pro-2"):
        store.record_spend(aid, 60, "veo-3.1-lite", pool="daily")
    # Not allowed to touch monthly bank: nothing eligible.
    assert store.select_account(manager.accounts(), cost=10, allow_monthly_pool=False) is None
    # Allowed: pro-1 (first in registry) falls back to its monthly bank.
    account = store.select_account(manager.accounts(), cost=10, allow_monthly_pool=True)
    assert account is not None
    assert account.id == "pro-1"


def test_predictive_check_skips_insufficient_daily_without_touching_monthly(
    accounts_path: Path,
    credits_path: Path,
) -> None:
    store = FlowCreditStore()
    manager = FlowAccountManager()
    # free-1 has 60 daily; spend down so only pro accounts remain eligible.
    store.record_spend("free-1", 55, "nano-banana-pro", pool="daily")
    # cost=10: free-1 has 5 remaining (< 10) so skip to pro-1 daily.
    account = store.select_account(manager.accounts(), cost=10, allow_monthly_pool=True)
    assert account.id == "pro-1"
    # pro-1 monthly bank must be untouched (1000).
    data = json.loads(credits_path.read_text(encoding="utf-8"))
    pro1 = data["accounts"].get("pro-1", {})
    assert pro1.get("monthly_spent", 0) == 0


def test_select_account_returns_none_when_all_exhausted(
    accounts_path: Path, credits_path: Path
) -> None:
    store = FlowCreditStore()
    manager = FlowAccountManager()
    # Exhaust every account's daily and monthly balance.
    for aid in ("free-1", "pro-1", "pro-2"):
        store.record_spend(aid, 60, "veo-3.1-lite", pool="daily")
    for aid in ("pro-1", "pro-2"):
        store.record_spend(aid, 1000, "veo-3.1-lite", pool="monthly")
    assert store.select_account(manager.accounts(), cost=10, allow_monthly_pool=True) is None


# -- credit store: rollover -------------------------------------------------


def test_date_rollover_resets_daily_preserves_monthly(
    accounts_path: Path, credits_path: Path
) -> None:
    store = FlowCreditStore()
    manager = FlowAccountManager()
    store.record_spend("pro-1", 10, "veo-3.1-lite", pool="daily")
    store.record_spend("pro-1", 100, "veo-3.1-lite", pool="monthly")
    # Drain free-1 (priority 1) so pro-1 is the first eligible account after rollover.
    store.record_spend("free-1", 60, "nano-banana-pro", pool="daily")
    assert store._read(account_id="pro-1") is not None  # ensure file exists

    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    raw = json.loads(credits_path.read_text(encoding="utf-8"))
    raw["date"] = yesterday
    credits_path.write_text(json.dumps(raw), encoding="utf-8")

    rolled = FlowCreditStore()
    # Daily counters reset: free-1 (priority 1) is eligible again.
    account = rolled.select_account(manager.accounts(), cost=10, allow_monthly_pool=False)
    assert account is not None and account.id == "free-1"
    # Drain free-1; pro-1's daily also reset and is next eligible.
    rolled.record_spend("free-1", 60, "nano-banana-pro", pool="daily")
    account = rolled.select_account(manager.accounts(), cost=10, allow_monthly_pool=False)
    assert account is not None and account.id == "pro-1"
    # Monthly bank preserved across rollover.
    data = json.loads(credits_path.read_text(encoding="utf-8"))
    assert data["accounts"]["pro-1"]["monthly_spent"] == 100


# -- credit store: flock ----------------------------------------------------


def test_flock_acquired_on_read_and_write(credits_path: Path) -> None:
    calls: list[str] = []
    original = fcntl.flock

    def spy(fd, op, *args, **kwargs):
        calls.append("lock" if op == fcntl.LOCK_EX else "unlock")
        return original(fd, op, *args, **kwargs)

    store = FlowCreditStore()
    with mock.patch("media_studio.gen.flow_quota.fcntl.flock", side_effect=spy):
        store.record_spend("free-1", 10, "nano-banana-pro", pool="daily")
        store.select_account(FlowAccountManager().accounts(), cost=10, allow_monthly_pool=False)

    assert calls.count("lock") >= 2, calls
    assert calls.count("unlock") >= 2, calls


# -- cost matrix ------------------------------------------------------------


def test_cost_matrix_known_models() -> None:
    store = FlowCreditStore()
    assert store.cost("nano-banana-pro") == 0
    assert store.cost("nano-banana-2.1") == 0
    assert store.cost("nano-banana-2-lite") == 0
    assert store.cost("veo-3.1-lite") == 10
    assert store.cost("omni-1.2-flash") == 24
    assert store.cost("veo-3.1-fast") == 20
    assert store.cost("veo-3.1-lite", batch=3) == 30
    assert store.cost("omni-1.2-flash", batch=2) == 48


def test_cost_zero_image_skips_daily_deduction(accounts_path: Path, credits_path: Path) -> None:
    store = FlowCreditStore()
    manager = FlowAccountManager()
    store.record_spend("free-1", 0, "nano-banana-pro", pool="daily")
    account = store.select_account(manager.accounts(), cost=0, allow_monthly_pool=False)
    assert account.id == "free-1"
