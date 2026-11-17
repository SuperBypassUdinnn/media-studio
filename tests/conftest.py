"""Shared test fixtures for media-studio.

The heavy render engines (EffectCraft, FilmCraft) are host-installed binaries. Unit tests
must stay offline and engine-free, so when a binary is absent every test that only
exercises Python-side logic gets a harmless stand-in instead of an install error. Tests
that explicitly assert the missing-binary error path mock the resolution themselves and
override these stand-ins.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_engine_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for engine binaries that the host does not have installed."""

    if shutil.which("effectcraft-cli") is None and shutil.which("effectcraft") is None:
        monkeypatch.setattr("media_studio.effectcraft.EFFECTCRAFT", Path("/bin/true"))
    if shutil.which("filmcraft-cli") is None and shutil.which("filmcraft") is None:
        monkeypatch.setenv("MS_FILMCRAFT_BIN", "/bin/true")
