"""Daily per-provider quota store protected by ``fcntl.flock``.

The quota store lives at ``~/.local/state/media-studio/gen-quota.json`` by default and may be
relocated with the ``MEDIA_STUDIO_QUOTA_PATH`` environment variable or the ``path=``
constructor argument. Every read-modify-write cycle holds an exclusive ``fcntl.flock`` so
concurrent ``ms gen`` invocations cannot corrupt the store or double-spend a daily quota.

Rollover: when the stored calendar date differs from today, every provider's spend counter
and cooldown are reset, so a new day starts with a fresh quota.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_QUOTA_PATH = Path("~/.local/state/media-studio/gen-quota.json").expanduser()


class QuotaStore:
    """Process-safe daily quota store for generative providers."""

    def __init__(self, path: Path | str | None = None):
        override = os.environ.get("MEDIA_STUDIO_QUOTA_PATH")
        self.path = Path(override or path or DEFAULT_QUOTA_PATH).expanduser()

    # -- internal ---------------------------------------------------------

    def _today(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _now(self) -> float:
        return datetime.now(UTC).timestamp()

    def _empty(self) -> dict:
        return {"date": self._today(), "providers": {}}

    def _read_locked(self, handle) -> dict:
        handle.seek(0)
        raw = handle.read()
        if not raw.strip():
            return self._empty()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        # Daily rollover: fresh date resets spend and cooldowns.
        if data.get("date") != self._today():
            return self._empty()
        providers = data.get("providers")
        if not isinstance(providers, dict):
            data["providers"] = {}
            providers = data["providers"]
        for state in providers.values():
            if isinstance(state, dict):
                state.setdefault("spent", 0)
                state.setdefault("cooldown_until", None)
        return data

    def _write_locked(self, handle, data: dict) -> None:
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(data, indent=2, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())

    def _state(self, data: dict, name: str) -> dict:
        providers = data.setdefault("providers", {})
        state = providers.get(name)
        if not isinstance(state, dict):
            state = {}
            providers[name] = state
        state.setdefault("spent", 0)
        state.setdefault("cooldown_until", None)
        return state

    def _with_lock(self, fn):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return fn(handle)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # -- public API -------------------------------------------------------

    def can_spend(self, provider: str, max_daily: int) -> bool:
        """True when ``provider`` may spend one more call today and is not cooling down."""

        def check(handle):
            data = self._read_locked(handle)
            state = self._state(data, provider)
            now = self._now()
            if state.get("cooldown_until") is not None and now < state["cooldown_until"]:
                return False
            return int(state.get("spent", 0)) < max_daily

        return bool(self._with_lock(check))

    def record_spend(self, provider: str) -> None:
        """Record one successful generative call for ``provider`` today."""

        def write(handle):
            data = self._read_locked(handle)
            state = self._state(data, provider)
            state["spent"] = int(state.get("spent", 0)) + 1
            state["cooldown_until"] = None
            self._write_locked(handle, data)

        self._with_lock(write)

    def set_cooldown(self, provider: str, seconds: int) -> None:
        """Mark ``provider`` as cooling down for ``seconds`` from now."""

        def write(handle):
            data = self._read_locked(handle)
            state = self._state(data, provider)
            state["cooldown_until"] = self._now() + max(0, int(seconds))
            self._write_locked(handle, data)

        self._with_lock(write)

    def reset(self, provider: str | None = None) -> None:
        """Reset ``provider`` (or every provider when ``None``)."""

        def write(handle):
            data = self._read_locked(handle)
            providers = data.setdefault("providers", {})
            if provider is None:
                providers.clear()
            else:
                providers.pop(provider, None)
            self._write_locked(handle, data)

        self._with_lock(write)

    def status(self) -> dict[str, dict[str, object]]:
        """Current per-provider spend and cooldown state (read-only snapshot)."""

        def read(handle):
            data = self._read_locked(handle)
            now = self._now()
            out: dict[str, dict[str, object]] = {}
            for name in data.get("providers", {}):
                st = self._state(data, name)
                cooldown_until = st.get("cooldown_until")
                out[name] = {
                    "spent": int(st.get("spent", 0)),
                    "cooldown_until": cooldown_until,
                    "cooling_down": cooldown_until is not None and now < cooldown_until,
                }
            return out

        return self._with_lock(read)
