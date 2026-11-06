"""Tests for media_studio.library: the asset catalog and ms library commands."""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from media_studio.library import (
    LibraryEntry,
    LibraryError,
    forget_asset,
    link_asset_to_project,
    load_catalog,
    register_asset,
    save_catalog,
    search_assets,
)

VALID_TYPES = ("video", "audio", "image", "font", "preset")


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_entry(root: Path, name: str = "demo", type: str = "video") -> LibraryEntry:
    source = root.parent / f"source-{name}.mp4"
    _write_file(source, b"x" * 1000)
    return register_asset(
        source,
        name=name,
        type=type,
        tags=["demo", "sample"],
        root=root,
    )


def test_register_asset_copies_into_type_folder(tmp_path: Path) -> None:
    root = tmp_path / "library"
    source = root.parent / "source.mp4"
    _write_file(source, b"hello world")

    entry = register_asset(
        source,
        name="My Clip",
        type="video",
        tags=["intro"],
        source_url="https://example.com/clip",
        license="CC0",
        attribution="Example Corp",
        root=root,
    )

    expected_digest = _sha256(source)
    assert entry.sha256 == expected_digest
    assert entry.file_path.parent == root / "video"
    assert entry.file_path.name.startswith(f"{entry.sha256[:8]}-")
    assert entry.file_path.suffix == ".mp4"
    assert entry.file_path.is_file()
    assert entry.name == "My Clip"
    assert entry.type == "video"
    assert entry.tags == ["intro"]
    assert entry.source_url == "https://example.com/clip"
    assert entry.license == "CC0"
    assert entry.attribution == "Example Corp"
    assert entry.thumbnail_path is None
    assert entry.added_at.endswith("Z")
    assert entry.used_in == []


def test_register_asset_rejects_invalid_type(tmp_path: Path) -> None:
    root = tmp_path / "library"
    source = root.parent / "source.mp4"
    _write_file(source, b"data")

    with pytest.raises(LibraryError, match="invalid asset type"):
        register_asset(source, name="x", type="document", tags=[], root=root)


def test_catalog_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "library"
    entry = _make_entry(root)

    entries = load_catalog(root)
    assert len(entries) == 1
    loaded = entries[0]
    assert loaded.id == entry.id
    assert loaded.file_path == entry.file_path
    assert loaded.sha256 == entry.sha256
    assert loaded.tags == entry.tags


def test_sha256_matches_actual_file(tmp_path: Path) -> None:
    root = tmp_path / "library"
    source = root.parent / "source.mp4"
    _write_file(source, b"content-hash-check")

    entry = register_asset(source, name="n", type="audio", tags=[], root=root)
    assert entry.sha256 == _sha256(entry.file_path)


def test_search_assets_matches_by_tag(tmp_path: Path) -> None:
    root = tmp_path / "library"
    _make_entry(root, name="alpha", type="video")
    _make_entry(root, name="beta", type="audio")

    results = search_assets(root=root, tags=["demo"])
    assert len(results) == 2


def test_search_assets_matches_by_type(tmp_path: Path) -> None:
    root = tmp_path / "library"
    _make_entry(root, name="alpha", type="video")
    _make_entry(root, name="beta", type="audio")

    results = search_assets(root=root, type="audio")
    assert len(results) == 1
    assert results[0].type == "audio"


def test_search_assets_matches_by_query(tmp_path: Path) -> None:
    root = tmp_path / "library"
    _make_entry(root, name="forest-scene", type="image")
    _make_entry(root, name="city-scene", type="image")

    results = search_assets(root=root, query="forest")
    assert len(results) == 1
    assert results[0].name == "forest-scene"


def test_search_assets_unmatched_query_returns_empty(tmp_path: Path) -> None:
    root = tmp_path / "library"
    _make_entry(root, name="alpha", type="video")

    results = search_assets(root=root, query="nothing-like-this")
    assert results == []


def test_link_asset_to_project_creates_relative_symlink(tmp_path: Path) -> None:
    root = tmp_path / "library"
    entry = _make_entry(root, name="clip")

    project_assets_dir = tmp_path / "projects" / "scr2026-promo" / "assets"
    symlink = link_asset_to_project(entry, project_assets_dir)

    assert symlink.is_symlink()
    # The symlink target is relative (os.path.relpath), not an absolute path.
    assert not Path(os.readlink(symlink)).is_absolute()
    # Symlink across filesystem boundaries: resolved target equals the entry file.
    assert symlink.resolve() == entry.file_path.resolve()
    # Relative symlink target.
    assert os.path.relpath(entry.file_path, project_assets_dir) == os.readlink(symlink)


def test_link_asset_to_project_updates_used_in(tmp_path: Path) -> None:
    root = tmp_path / "library"
    entry = _make_entry(root, name="clip")

    project_assets_dir = tmp_path / "projects" / "scr2026-promo" / "assets"
    link_asset_to_project(entry, project_assets_dir)

    entries = load_catalog(root)
    stored = next(item for item in entries if item.id == entry.id)
    assert stored.used_in == ["scr2026-promo"]


def test_link_asset_to_project_inode_check(tmp_path: Path) -> None:
    root = tmp_path / "library"
    entry = _make_entry(root, name="clip")

    project_assets_dir = tmp_path / "projects" / "scr2026-promo" / "assets"

    # Point the symlink at a bogus relative target so it never resolves to the
    # entry file; the internal inode check must then raise LibraryError.
    with (
        patch("os.path.relpath", return_value="../nowhere.mp4"),
        pytest.raises(LibraryError, match="inode check"),
    ):
        link_asset_to_project(entry, project_assets_dir)


def test_save_catalog_uses_file_lock(tmp_path: Path) -> None:
    root = tmp_path / "library"
    entry = _make_entry(root)

    with patch("media_studio.library.fcntl.flock") as mock_flock:
        save_catalog([entry], root)

    assert mock_flock.call_count >= 2
    assert any(args[1] == fcntl.LOCK_EX for args, _ in mock_flock.call_args_list), (
        f"expected LOCK_EX call, got {mock_flock.call_args_list}"
    )


def test_forget_asset_removes_entry(tmp_path: Path) -> None:
    root = tmp_path / "library"
    entry = _make_entry(root, name="clip")
    assert len(load_catalog(root)) == 1

    forget_asset(entry.id, root=root)

    assert load_catalog(root) == []
    # File remains on disk unless purged.
    assert entry.file_path.is_file()


def test_forget_asset_purge_deletes_file(tmp_path: Path) -> None:
    root = tmp_path / "library"
    entry = _make_entry(root, name="clip")
    file_path = entry.file_path

    forget_asset(entry.id, purge=True, root=root)

    assert load_catalog(root) == []
    assert not file_path.is_file()


def test_forget_asset_unknown_id_raises(tmp_path: Path) -> None:
    root = tmp_path / "library"
    with pytest.raises(LibraryError, match="no asset with id"):
        forget_asset("does-not-exist", root=root)
