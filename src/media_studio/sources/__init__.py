"""Asset source clients.

Keyless sources come first, as the design requires: Openverse, Wikimedia Commons, and the
Internet Archive answer without credentials. Keyed providers plug into the same interface once
their keys exist.
"""

from __future__ import annotations

from types import ModuleType

from . import internet_archive, met, openverse, wikimedia

CLIENTS: tuple[ModuleType, ...] = (met, openverse, wikimedia, internet_archive)


def client_names() -> tuple[str, ...]:
    """Names of every registered source."""

    return tuple(module.NAME for module in CLIENTS)


def module_named(name: str) -> ModuleType | None:
    """Look up one client by name, or None when the name is unknown."""

    wanted = name.strip().lower()
    for module in CLIENTS:
        if wanted == module.NAME:
            return module
    return None


def clients_for(kind: str, selected: tuple[str, ...] = ()) -> list[ModuleType]:
    """Clients that can answer for a class, optionally narrowed by name."""

    wanted = {name.lower() for name in selected}
    return [
        module
        for module in CLIENTS
        if kind in module.KINDS and (not wanted or module.NAME in wanted)
    ]
