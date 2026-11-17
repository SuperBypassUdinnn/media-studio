"""Shared CLI constants and monkeypatch-aware symbol resolution."""

from __future__ import annotations

import sys
from typing import Any

# A search class is not always the staging class: a stock photo belongs in the design set.
STAGING_CLASS_FOR_KIND = {"image": "design", "video": "video", "audio": "audio", "music": "music"}

EXIT_OK = 0
EXIT_FAILURE = 1


def resolve_monkeypatchable(name: str, default: Any = None) -> Any:
    """Resolve a symbol through ``media_studio.cli`` so test monkeypatches intercept calls."""

    cli_module = sys.modules.get("media_studio.cli")
    if cli_module is not None and hasattr(cli_module, name):
        return getattr(cli_module, name)
    return default
