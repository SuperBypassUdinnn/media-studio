"""Unit tests for the TripoSR image-to-3D path (offline, no triposr required)."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from media_studio.triposr import (
    TRIPO_SR_AVAILABLE,
    TRIPOSR_BINARY,
    ImageToMeshResult,
    TripoSRError,
    check_installation,
    image_to_mesh,
    image_to_mesh_project,
    vram_safe_chunk_size,
)


def test_check_installation_raises_when_triposr_missing():
    """check_installation raises TripoSRError when triposr is not available."""

    with (
        mock.patch("media_studio.triposr.TRIPO_SR_AVAILABLE", False),
        mock.patch("media_studio.triposr.TRIPOSR_BINARY", None),
        pytest.raises(TripoSRError, match="TripoSR is not available"),
    ):
        check_installation()


def test_image_to_mesh_raises_when_triposr_missing(tmp_path):
    """image_to_mesh raises TripoSRError when triposr is absent."""

    img = tmp_path / "ref.png"
    img.write_text("not an image", encoding="utf-8")

    with (
        mock.patch("media_studio.triposr.TRIPO_SR_AVAILABLE", False),
        mock.patch("media_studio.triposr.TRIPOSR_BINARY", None),
        pytest.raises(TripoSRError, match="TripoSR is not available"),
    ):
        image_to_mesh(img, tmp_path / "out.obj")


def test_image_to_mesh_project_raises_when_triposr_missing(tmp_path):
    """image_to_mesh_project raises TripoSRError when triposr is absent."""

    img = tmp_path / "ref.png"
    img.write_text("not an image", encoding="utf-8")

    with (
        mock.patch("media_studio.triposr.TRIPO_SR_AVAILABLE", False),
        mock.patch("media_studio.triposr.TRIPOSR_BINARY", None),
        pytest.raises(TripoSRError, match="TripoSR is not available"),
    ):
        image_to_mesh_project(img, project_root=tmp_path)


def test_image_to_mesh_raises_when_image_missing(tmp_path):
    """image_to_mesh raises TripoSRError when the reference image does not exist."""

    with (
        mock.patch("media_studio.triposr.TRIPO_SR_AVAILABLE", False),
        mock.patch("media_studio.triposr.TRIPOSR_BINARY", "/usr/bin/triposr"),
        pytest.raises(TripoSRError, match="no such reference image"),
    ):
        image_to_mesh(tmp_path / "nonexistent.png", tmp_path / "out.obj")


def test_vram_safe_chunk_size_returns_4096_for_4gb(monkeypatch):
    """A 4GB GPU yields chunk_size 4096 (VRAM-safe for GTX 1650 Max-Q)."""

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: mock.Mock(returncode=0, stdout="4096\n"),
    )
    assert vram_safe_chunk_size() == 4096


def test_vram_safe_chunk_size_returns_8192_for_8gb(monkeypatch):
    """An 8GB GPU yields chunk_size 8192."""

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: mock.Mock(returncode=0, stdout="8192\n"),
    )
    assert vram_safe_chunk_size() == 8192


def test_vram_safe_chunk_size_falls_back_to_4096(monkeypatch):
    """When nvidia-smi is unavailable, fall back to 4096."""

    def _boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", _boom)
    assert vram_safe_chunk_size() == 4096


def test_image_to_mesh_result_ok_property():
    """ImageToMeshResult.ok is False without a valid output."""

    result = ImageToMeshResult(
        output=__import__("pathlib").Path("/tmp/nonexistent.obj"),
        report_path=__import__("pathlib").Path("/tmp/out.json"),
        report={"ok": False},
    )
    assert not result.ok


@pytest.mark.skipif(
    not TRIPO_SR_AVAILABLE and TRIPOSR_BINARY is None,
    reason="triposr (tsr module or binary) not installed",
)
def test_integration_image_to_mesh(tmp_path):
    """Convert a small reference image; output exists and report is ok."""

    img = tmp_path / "ref.png"
    img.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    )

    out = tmp_path / "out.obj"
    result = image_to_mesh(img, out)
    assert result.ok, result.report
    assert out.exists()
