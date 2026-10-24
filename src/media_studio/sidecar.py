"""Sidecar files: the licence record that travels with every staged asset.

Kept in its own module so both the manifest validator and the ledger can read sidecars without
importing each other.
"""

from __future__ import annotations

from pathlib import Path

SUFFIX = ".license"
UNKNOWN_SPDX = "NONE"
ATTRIBUTION_NOT_REQUIRED = "not required"
ATTRIBUTION_NOT_RECORDED = "not recorded"
PLACEHOLDER_ATTRIBUTIONS = (ATTRIBUTION_NOT_REQUIRED, ATTRIBUTION_NOT_RECORDED)


def sidecar_path(asset: Path) -> Path:
    """Where the sidecar for ``asset`` lives."""

    return asset.with_name(f"{asset.name}{SUFFIX}")


def parse_sidecar_text(text: str) -> dict[str, str]:
    """Parse sidecar text into a mapping, tolerating unknown keys and blank lines."""

    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def read_sidecar(asset: Path) -> dict[str, str]:
    """Read the sidecar that belongs to ``asset``, or an empty mapping when absent."""

    path = sidecar_path(asset)
    if not path.is_file():
        return {}
    return parse_sidecar_text(path.read_text(encoding="utf-8"))
