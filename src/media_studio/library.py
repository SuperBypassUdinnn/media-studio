"""Asset library catalog for media-studio.

Tracks registered assets in a JSON catalog stored under ``LIBRARY_ROOT``.
All functions accept a ``root: Path`` keyword so tests can point at a
temporary library instead of the real ``~/.local/share/media-studio/library``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VALID_TYPES = ("video", "audio", "image", "font", "preset")

LIBRARY_ROOT: Path = Path.home() / ".local/share/media-studio/library"
CATALOG_PATH: Path = LIBRARY_ROOT / "catalog.json"


class LibraryError(RuntimeError):
    """Raised when the asset library cannot complete an operation."""


@dataclass
class LibraryEntry:
    """One row in the asset catalog."""

    id: str
    type: str
    name: str
    source_url: str
    license: str
    attribution: str
    tags: list[str] = field(default_factory=list)
    file_path: Path = Path()
    sha256: str = ""
    thumbnail_path: Path | None = None
    added_at: str = ""
    used_in: list[str] = field(default_factory=list)


def _sha256_of_file(path: Path) -> str:
    """Compute the SHA-256 digest of a file, reading in binary chunks."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_catalog(root: Path = LIBRARY_ROOT) -> list[LibraryEntry]:
    """Load every catalog entry from ``root / catalog.json``."""

    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        return []
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries: list[LibraryEntry] = []
    for item in data if isinstance(data, list) else []:
        entries.append(
            LibraryEntry(
                id=item["id"],
                type=item["type"],
                name=item["name"],
                source_url=item.get("source_url", ""),
                license=item.get("license", ""),
                attribution=item.get("attribution", ""),
                tags=list(item.get("tags", [])),
                file_path=Path(item["file_path"]),
                sha256=item.get("sha256", ""),
                thumbnail_path=(
                    Path(item["thumbnail_path"]) if item.get("thumbnail_path") else None
                ),
                added_at=item.get("added_at", ""),
                used_in=list(item.get("used_in", [])),
            )
        )
    return entries


def _entry_to_dict(entry: LibraryEntry) -> dict:
    """Convert a LibraryEntry to a JSON-serializable dict (Paths as strings)."""

    return {
        "id": entry.id,
        "type": entry.type,
        "name": entry.name,
        "source_url": entry.source_url,
        "license": entry.license,
        "attribution": entry.attribution,
        "tags": list(entry.tags),
        "file_path": str(entry.file_path),
        "sha256": entry.sha256,
        "thumbnail_path": (str(entry.thumbnail_path) if entry.thumbnail_path is not None else None),
        "added_at": entry.added_at,
        "used_in": list(entry.used_in),
    }


def save_catalog(entries: list[LibraryEntry], root: Path = LIBRARY_ROOT) -> None:
    """Persist the catalog under ``root / catalog.json`` with an exclusive lock.

    Acquires ``fcntl.flock(fd, fcntl.LOCK_EX)`` before writing and releases it
    in a ``finally`` block, so concurrent writers never clobber each other.
    """

    catalog_path = root / "catalog.json"
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([_entry_to_dict(entry) for entry in entries], indent=2) + "\n"
    handle = catalog_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(payload)
        handle.flush()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def register_asset(
    file: Path,
    *,
    name: str,
    type: str,
    tags: list[str],
    source_url: str = "",
    license: str = "CC0",
    attribution: str = "",
    root: Path = LIBRARY_ROOT,
) -> LibraryEntry:
    """Copy ``file`` into ``root / type`` and append a catalog entry.

    The stored file name is ``<sha256[:8]>-<original-stem><suffix>`` so the
    same content re-used across registrations lands on a stable path.
    """

    if type not in VALID_TYPES:
        raise LibraryError(f"invalid asset type {type!r}; expected one of {', '.join(VALID_TYPES)}")

    source = Path(file).expanduser()
    if not source.is_file():
        raise LibraryError(f"no such file: {source}")

    digest = _sha256_of_file(source)
    destination_dir = root / type
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{digest[:8]}-{source.stem}{source.suffix}"

    if not destination.exists():
        shutil.copy2(source, destination)

    entry = LibraryEntry(
        id=str(uuid.uuid4()),
        type=type,
        name=name,
        source_url=source_url,
        license=license,
        attribution=attribution,
        tags=list(tags),
        file_path=destination,
        sha256=digest,
        thumbnail_path=None,
        added_at=datetime.utcnow().isoformat() + "Z",
        used_in=[],
    )

    entries = load_catalog(root)
    entries.append(entry)
    save_catalog(entries, root)
    return entry


def search_assets(
    query: str = "",
    *,
    type: str | None = None,
    tags: list[str] | None = None,
    root: Path = LIBRARY_ROOT,
) -> list[LibraryEntry]:
    """Return catalog entries matching ``query``, ``type`` and/or ``tags``."""

    if tags is None:
        tags = []

    entries = load_catalog(root)
    query_lower = query.lower()
    matched: list[LibraryEntry] = []
    for entry in entries:
        if type is not None and entry.type != type:
            continue
        if tags and not set(tags).issubset(set(entry.tags)):
            continue
        if query_lower and query_lower not in entry.name.lower():
            continue
        matched.append(entry)
    return matched


def link_asset_to_project(entry: LibraryEntry, project_assets_dir: Path) -> Path:
    """Create a relative symlink to ``entry.file_path`` inside ``project_assets_dir``.

    Also records the project slug (``project_assets_dir.parent.name``) in the
    entry's ``used_in`` list and persists the catalog.
    """

    project_assets_dir = Path(project_assets_dir)
    project_assets_dir.mkdir(parents=True, exist_ok=True)

    symlink_name = entry.file_path.name
    symlink_path = project_assets_dir / symlink_name
    if symlink_path.is_symlink() or symlink_path.exists():
        symlink_path.unlink()
    relpath = os.path.relpath(entry.file_path, project_assets_dir)
    symlink_path.symlink_to(relpath)

    if symlink_path.resolve() != entry.file_path.resolve():
        raise LibraryError(
            f"symlink inode check failed: {symlink_path} does not resolve to {entry.file_path}"
        )

    slug = project_assets_dir.parent.name
    root = entry.file_path.parent.parent
    entries = load_catalog(root)
    for catalog_entry in entries:
        if catalog_entry.id == entry.id:
            if slug not in catalog_entry.used_in:
                catalog_entry.used_in.append(slug)
            break
    else:
        raise LibraryError(f"entry {entry.id} not found in catalog")
    save_catalog(entries, root)
    return symlink_path


def forget_asset(id: str, *, purge: bool = False, root: Path = LIBRARY_ROOT) -> None:
    """Remove the entry with ``id`` from the catalog.

    When ``purge`` is true, also delete the physical file from library storage.
    """

    entries = load_catalog(root)
    remaining: list[LibraryEntry] = []
    removed: LibraryEntry | None = None
    for entry in entries:
        if entry.id == id:
            removed = entry
            continue
        remaining.append(entry)

    if removed is None:
        raise LibraryError(f"no asset with id {id!r} in catalog")

    if purge and removed.file_path.is_file():
        removed.file_path.unlink()

    save_catalog(remaining, root)
