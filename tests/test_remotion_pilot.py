"""Tests for the Remotion CLI wrapper and comparative pilot benchmark.

Tests mock ``npx remotion`` through ``subprocess`` so they run offline without
Node modules or an installed Remotion package.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from media_studio.remotion import (
    RemotionError,
    RemotionResult,
    is_remotion_available,
    render_remotion_clip,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY_FILE = REPO_ROOT / "renderers" / "remotion" / "src" / "Root.tsx"
COMPOSITION_ID = "SaaSFeatureCard"


def _completed(returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def _patch_available(monkeypatch: pytest.MonkeyPatch, available: bool = True) -> None:
    monkeypatch.setattr("media_studio.remotion.is_remotion_available", lambda: available)


def _patch_run(monkeypatch: pytest.MonkeyPatch, returncode: int = 0, stderr: str = "") -> None:
    monkeypatch.setattr(
        "media_studio.remotion.subprocess.run",
        lambda *args, **kwargs: _completed(returncode, stderr),
    )


def _find_flag(command: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` in a command list, or ``None``."""
    try:
        index = command.index(flag)
    except ValueError:
        return None
    return command[index + 1] if index + 1 < len(command) else None


# ---------------------------------------------------------------------------
# is_remotion_available()
# ---------------------------------------------------------------------------


def test_is_remotion_available_true_when_version_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """``npx`` on PATH plus a successful ``npx remotion --version`` means available."""

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/npx" if cmd == "npx" else None

    monkeypatch.setattr("media_studio.remotion.shutil.which", fake_which)
    _patch_run(monkeypatch, returncode=0)
    assert is_remotion_available() is True


def test_is_remotion_available_false_when_npx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``npx`` on PATH means unavailable without spawning anything."""

    monkeypatch.setattr("media_studio.remotion.shutil.which", lambda cmd: None)
    spawned: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        spawned.append(list(command))
        return _completed(0)

    monkeypatch.setattr("media_studio.remotion.subprocess.run", fake_run)
    assert is_remotion_available() is False
    assert spawned == []


def test_is_remotion_available_false_when_version_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing ``npx remotion --version`` means unavailable."""

    def fake_which(cmd: str) -> str | None:
        return "/usr/bin/npx" if cmd == "npx" else None

    monkeypatch.setattr("media_studio.remotion.shutil.which", fake_which)
    _patch_run(monkeypatch, returncode=1, stderr="Command 'remotion' not found")
    assert is_remotion_available() is False


# ---------------------------------------------------------------------------
# render_remotion_clip()
# ---------------------------------------------------------------------------


def test_render_remotion_clip_builds_command_with_json_props(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Props serialize to JSON under --props and the command names composition and output."""

    _patch_available(monkeypatch, available=True)
    _patch_run(monkeypatch, returncode=0)
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        seen.append(list(command))
        return _completed(0)

    monkeypatch.setattr("media_studio.remotion.subprocess.run", fake_run)

    props = {"headline": "Hello world", "accent": "#38BDF8"}
    result = render_remotion_clip(
        COMPOSITION_ID,
        tmp_path / "out.mp4",
        props,
        fps=30,
        duration=2.0,
        width=1080,
        height=1920,
    )

    assert len(seen) == 1
    command = seen[0]
    assert command[:3] == ["npx", "remotion", "render"]
    assert str(ENTRY_FILE) in command
    assert str(tmp_path / "out.mp4") in command
    assert _find_flag(command, "--composition") == COMPOSITION_ID
    assert _find_flag(command, "--fps") == "30"
    assert _find_flag(command, "--frames") == "60"  # round(2.0 * 30)
    assert "--silent" in command
    props_json = _find_flag(command, "--props")
    assert props_json is not None
    assert json.loads(props_json) == props
    assert result == RemotionResult(
        output=tmp_path / "out.mp4",
        composition=COMPOSITION_ID,
        props=props,
        duration=2.0,
        fps=30,
        width=1080,
        height=1920,
    )


def test_render_remotion_clip_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaults: empty props, 3s at 60 fps, 1080x1920 canvas."""

    _patch_available(monkeypatch, available=True)
    _patch_run(monkeypatch, returncode=0)
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        seen.append(list(command))
        return _completed(0)

    monkeypatch.setattr("media_studio.remotion.subprocess.run", fake_run)

    result = render_remotion_clip(COMPOSITION_ID, tmp_path / "card.mov")

    command = seen[0]
    assert json.loads(_find_flag(command, "--props") or "{}") == {}
    assert _find_flag(command, "--fps") == "60"
    assert _find_flag(command, "--frames") == "180"  # round(3.0 * 60)
    assert result.props == {}
    assert result.duration == 3.0
    assert result.fps == 60
    assert result.width == 1080
    assert result.height == 1920


def test_render_remotion_clip_creates_output_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing output parents are created before the command runs."""

    _patch_available(monkeypatch, available=True)
    _patch_run(monkeypatch, returncode=0)
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        seen.append(list(command))
        return _completed(0)

    monkeypatch.setattr("media_studio.remotion.subprocess.run", fake_run)

    nested = tmp_path / "renders" / "pilot" / "card.mp4"
    render_remotion_clip(COMPOSITION_ID, nested)
    assert nested.parent.is_dir()
    assert str(nested) in seen[0]


def test_render_remotion_clip_resolves_bundled_entry_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without entry_file, renderers/remotion/src/Root.tsx is resolved from the checkout."""

    _patch_available(monkeypatch, available=True)
    _patch_run(monkeypatch, returncode=0)
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        seen.append(list(command))
        return _completed(0)

    monkeypatch.setattr("media_studio.remotion.subprocess.run", fake_run)

    render_remotion_clip(COMPOSITION_ID, tmp_path / "out.mp4")
    assert str(ENTRY_FILE) in seen[0]
    assert ENTRY_FILE.is_file()


def test_render_remotion_clip_raises_when_remotion_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unavailable Remotion raises RemotionError without spawning a render."""

    _patch_available(monkeypatch, available=False)
    spawned: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        spawned.append(list(command))
        return _completed(0)

    monkeypatch.setattr("media_studio.remotion.subprocess.run", fake_run)

    with pytest.raises(RemotionError, match="remotion is not available"):
        render_remotion_clip(COMPOSITION_ID, tmp_path / "out.mp4")
    assert spawned == []


def test_render_remotion_clip_raises_on_render_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonzero exit from ``npx remotion render`` raises RemotionError with stderr."""

    _patch_available(monkeypatch, available=True)
    _patch_run(monkeypatch, returncode=1, stderr="Missing composition: Nope")

    with pytest.raises(RemotionError, match="Missing composition: Nope"):
        render_remotion_clip("Nope", tmp_path / "out.mp4")


# ---------------------------------------------------------------------------
# Scaffold presence (the pilot bundle must exist for the wrapper to target)
# ---------------------------------------------------------------------------


def test_remotion_scaffold_files_exist() -> None:
    """renderers/remotion/ is scaffolded with package.json, Root.tsx, and the composition."""

    renderer = REPO_ROOT / "renderers" / "remotion"
    assert (renderer / "package.json").is_file()
    assert (renderer / "src" / "Root.tsx").is_file()
    assert (renderer / "src" / "compositions" / "SaaSFeatureCard.tsx").is_file()
    package = json.loads((renderer / "package.json").read_text(encoding="utf-8"))
    assert "remotion" in package.get("dependencies", {})
    assert "react" in package.get("dependencies", {})
