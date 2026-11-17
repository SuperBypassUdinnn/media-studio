"""Multi-account Google Flow credit manager with waterfall rotation.

Accounts registry: ``~/.config/media-studio/flow-accounts.json`` (overridable via
``MEDIA_STUDIO_FLOW_ACCOUNTS_PATH`` or the ``accounts_path=`` constructor argument).

Credit store: ``~/.local/state/media-studio/flow-credits.json`` (overridable via
``MEDIA_STUDIO_FLOW_CREDITS_PATH`` or the ``credits_path=`` constructor argument).
Every read-modify-write cycle holds an exclusive ``fcntl.flock`` so concurrent
``ms gen`` invocations cannot double-spend a daily quota. A ledger is appended to
``flow-credits.jsonl`` next to the store.

Rollover: when the stored calendar date differs from today (UTC midnight), every
account's daily spend counter resets while monthly counters are preserved.

Waterfall priority:
  1. Free tier accounts with ``daily_remaining >= cost`` (exhaust daily quota first).
  2. Pro tier accounts with ``daily_remaining >= cost`` (exhaust daily allowance next).
  3. Pro tier accounts with ``monthly_bank_remaining >= cost`` only when the monthly
     pool may be touched (``allow_monthly_pool=True`` or account unlocked).
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_ACCOUNTS_PATH = Path("~/.config/media-studio/flow-accounts.json").expanduser()
DEFAULT_CREDITS_PATH = Path("~/.local/state/media-studio/flow-credits.json").expanduser()
DEFAULT_LEDGER_PATH = DEFAULT_CREDITS_PATH.with_suffix(".jsonl")

DAILY_LIMIT = 60
MONTHLY_BANK = 1000


def _now() -> str:
    return datetime.now(UTC).date().isoformat()


def _now_ts() -> float:
    return datetime.now(UTC).timestamp()


def _empty_store() -> dict:
    return {"date": _now(), "accounts": {}}


@dataclass(frozen=True)
class FlowAccount:
    """One registered Google Flow account."""

    id: str
    email: str
    tier: str
    enabled: bool
    token: str


class FlowAccountManager:
    """Loads and filters the Google Flow accounts registry."""

    def __init__(self, accounts_path: Path | str | None = None):
        override = os.environ.get("MEDIA_STUDIO_FLOW_ACCOUNTS_PATH")
        self.accounts_path = Path(override or accounts_path or DEFAULT_ACCOUNTS_PATH).expanduser()

    def accounts(self) -> list[FlowAccount]:
        """Return enabled accounts in registry order."""
        if not self.accounts_path.exists():
            return []
        try:
            raw = json.loads(self.accounts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        accounts: list[FlowAccount] = []
        for entry in raw:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            accounts.append(
                FlowAccount(
                    id=str(entry["id"]),
                    email=str(entry.get("email", "")),
                    tier=str(entry.get("tier", "free")),
                    enabled=bool(entry.get("enabled", True)),
                    token=str(entry.get("token", "")),
                )
            )
        return accounts


class FlowCreditStore:
    """Process-safe multi-account credit store with waterfall rotation."""

    def __init__(
        self,
        credits_path: Path | str | None = None,
        ledger_path: Path | str | None = None,
    ):
        override = os.environ.get("MEDIA_STUDIO_FLOW_CREDITS_PATH")
        self.credits_path = Path(override or credits_path or DEFAULT_CREDITS_PATH).expanduser()
        if ledger_path is None:
            self.ledger_path = DEFAULT_LEDGER_PATH
        else:
            self.ledger_path = Path(ledger_path).expanduser()

    # -- internal ---------------------------------------------------------

    def _default_state(self, tier: str) -> dict:
        pro = tier == "pro"
        return {
            "tier": tier,
            "daily_limit": DAILY_LIMIT,
            "daily_spent": 0,
            "monthly_bank": MONTHLY_BANK if pro else 0,
            "monthly_spent": 0,
            "monthly_locked": pro,
        }

    def _read_locked(self, handle) -> dict:
        handle.seek(0)
        raw = handle.read()
        if not raw.strip():
            return _empty_store()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _empty_store()
        if not isinstance(data, dict):
            return _empty_store()
        # Daily rollover at UTC midnight: reset daily spend, preserve monthly bank.
        if data.get("date") != _now():
            accounts = data.get("accounts")
            kept: dict = {}
            if isinstance(accounts, dict):
                for aid, state in accounts.items():
                    if isinstance(state, dict):
                        kept[aid] = {
                            "tier": state.get("tier", "free"),
                            "daily_limit": int(state.get("daily_limit", DAILY_LIMIT)),
                            "daily_spent": 0,
                            "monthly_bank": int(state.get("monthly_bank", 0)),
                            "monthly_spent": int(state.get("monthly_spent", 0)),
                            "monthly_locked": bool(state.get("monthly_locked", True)),
                        }
            data = {"date": _now(), "accounts": kept}
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            data["accounts"] = {}
            accounts = data["accounts"]
        for state in accounts.values():
            if isinstance(state, dict):
                state.setdefault("tier", "free")
                state.setdefault("daily_limit", DAILY_LIMIT)
                state.setdefault("daily_spent", 0)
                state.setdefault("monthly_bank", 0)
                state.setdefault("monthly_spent", 0)
                state.setdefault("monthly_locked", True)
        return data

    def _write_locked(self, handle, data: dict) -> None:
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(data, indent=2, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())

    def _account_state(self, data: dict, account_id: str, tier: str) -> dict:
        accounts = data.setdefault("accounts", {})
        state = accounts.get(account_id)
        if not isinstance(state, dict):
            state = self._default_state(tier)
            accounts[account_id] = state
        return state

    def _with_lock(self, fn):
        self.credits_path.parent.mkdir(parents=True, exist_ok=True)
        with self.credits_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return fn(handle)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _append_ledger(self, account_id: str, cost: int, model: str, pool: str) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now_ts(),
            "date": _now(),
            "account_id": account_id,
            "cost": cost,
            "model": model,
            "pool": pool,
        }
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    # -- public API -------------------------------------------------------

    def cost(self, model: str, batch: int = 1) -> int:
        """Credits for one generation of ``model`` (default batch=1)."""
        table = {
            "nano-banana-pro": 0,
            "nano-banana-2.1": 0,
            "nano-banana-2-lite": 0,
            "veo-3.1-lite": 10,
            "omni-1.2-flash": 24,
            "veo-3.1-fast": 20,
        }
        return int(table.get(model, 0)) * max(1, int(batch))

    def _read(self, account_id: str | None = None) -> dict | None:
        """Snapshot of the store (read-only). If ``account_id`` given, that account's state."""

        def read(handle):
            data = self._read_locked(handle)
            if account_id is None:
                return data
            return data.get("accounts", {}).get(account_id)

        return self._with_lock(read)

    def select_account(
        self,
        accounts: list[FlowAccount],
        cost: int,
        allow_monthly_pool: bool = False,
        exclude: set[str] | None = None,
    ) -> FlowAccount | None:
        """Pick the highest-priority account that can afford ``cost``.

        Skips any account whose current daily balance is below ``cost`` (predictive
        pre-check) and never touches the monthly bank unless permitted.
        """

        def pick(handle):
            data = self._read_locked(handle)
            blocked = exclude or set()
            eligible: list[FlowAccount] = []
            for acct in accounts:
                if acct.id in blocked:
                    continue
                state = self._account_state(data, acct.id, acct.tier)
                daily_remaining = int(state["daily_limit"]) - int(state["daily_spent"])
                if acct.tier == "free" and daily_remaining >= cost:
                    eligible.append(acct)
            if eligible:
                return eligible[0]
            for acct in accounts:
                if acct.id in blocked or acct.tier != "pro":
                    continue
                state = self._account_state(data, acct.id, acct.tier)
                daily_remaining = int(state["daily_limit"]) - int(state["daily_spent"])
                if daily_remaining >= cost:
                    return acct
            if allow_monthly_pool:
                for acct in accounts:
                    if acct.id in blocked or acct.tier != "pro":
                        continue
                    state = self._account_state(data, acct.id, acct.tier)
                    monthly_remaining = int(state["monthly_bank"]) - int(state["monthly_spent"])
                    if monthly_remaining >= cost:
                        return acct
            return None

        return self._with_lock(pick)

    def record_spend(
        self,
        account_id: str,
        cost: int,
        model: str,
        pool: str = "daily",
        tier: str = "pro",
    ) -> None:
        """Atomically record ``cost`` credits spent against ``pool`` and append a ledger entry."""

        def write(handle):
            data = self._read_locked(handle)
            state = self._account_state(data, account_id, tier)
            if pool == "monthly":
                state["monthly_spent"] = int(state.get("monthly_spent", 0)) + int(cost)
            else:
                state["daily_spent"] = int(state.get("daily_spent", 0)) + int(cost)
            self._write_locked(handle, data)
            self._append_ledger(account_id, int(cost), model, pool)

        self._with_lock(write)
