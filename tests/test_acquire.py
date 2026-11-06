"""Tests for media_studio.acquire: acquisition pipeline and consistency checks."""

from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from media_studio.acquire import (
    AcquireResult,
    acquire_asset,
    check_brief_consistency,
    inspect_audio_preview,
    inspect_thumbnail,
    manual_request_message,
)
from media_studio.library import LibraryEntry

SAMPLE_RATE = 44100


def _write_sine_wav(path: Path, seconds: float = 1.0) -> None:
    """Write a real 1 s 440 Hz sine-wave wav (numpy + stdlib wave)."""

    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n) / SAMPLE_RATE
    signal = (0.3 * np.sin(2 * math.pi * 440.0 * t)).astype(np.float32)
    pcm = (signal * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())


def _make_entry(root: Path, name: str = "demo", type: str = "video") -> LibraryEntry:
    return LibraryEntry(
        id="id-" + name,
        type=type,
        name=name,
        source_url=f"https://example.com/{name}",
        license="CC0",
        attribution="",
        tags=["demo"],
        file_path=root / type / f"{name}.mp4",
    )


def _run_mock(
    palette_text: str,
    bpm_text: str = "bpm 120.0, 4 beats (2 downbeats), 1 high-energy section(s)",
):
    """Build a ``subprocess.run`` fake for ffmpeg / ``ms see`` / ``ms video beats``."""

    def run(cmd, **kwargs):
        cmd = list(cmd)
        if cmd[0] == "ffmpeg":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if len(cmd) > 1 and cmd[1] == "see":
            return subprocess.CompletedProcess(cmd, 0, palette_text, "")
        if len(cmd) > 2 and cmd[1] == "video" and cmd[2] == "beats":
            return subprocess.CompletedProcess(cmd, 0, bpm_text, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


class TestAcquireAsset:
    def test_library_hit_returns_first_ranked_by_used_in(self, tmp_path: Path) -> None:
        root = tmp_path / "library"
        popular = _make_entry(root, name="popular")
        popular.used_in = ["proj-a", "proj-b"]
        rare = _make_entry(root, name="rare")
        rare.used_in = ["proj-c"]

        project_assets = tmp_path / "projects" / "scr2026-promo" / "assets"
        fake_symlink = project_assets / "popular.mp4"

        with (
            patch("media_studio.acquire.search_assets", return_value=[rare, popular]),
            patch(
                "media_studio.acquire.link_asset_to_project", return_value=fake_symlink
            ) as mock_link,
        ):
            result = acquire_asset(
                type="video",
                tags=["demo"],
                project_assets_dir=project_assets,
                project_slug="scr2026-promo",
                library_root=root,
            )

        assert isinstance(result, AcquireResult)
        assert result.source == "library"
        assert result.entry is popular
        assert result.symlink == fake_symlink
        mock_link.assert_called_once_with(popular, project_assets)

    def test_no_library_match_returns_none(self, tmp_path: Path) -> None:
        root = tmp_path / "library"
        project_assets = tmp_path / "projects" / "scr2026-promo" / "assets"

        with patch("media_studio.acquire.search_assets", return_value=[]):
            result = acquire_asset(
                type="video",
                tags=["demo"],
                project_assets_dir=project_assets,
                project_slug="scr2026-promo",
                library_root=root,
            )

        assert result is None


class TestManualRequestMessage:
    def test_output_contains_checklist_and_fields(self, tmp_path: Path) -> None:
        destination = tmp_path / "assets" / "clip.mp4"
        message = manual_request_message(
            name="Forest canopy",
            type="video",
            source_url="https://example.com/forest.mp4",
            license="CC0",
            destination=destination,
        )

        assert "- [ ]" in message
        assert str(destination) in message
        assert "CC0" in message
        assert "https://example.com/forest.mp4" in message
        assert "Forest canopy" in message


class TestCheckBriefConsistency:
    def test_action_brief_with_bright_frames_is_consistent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shots = [tmp_path / "shot-a.mp4", tmp_path / "shot-b.mp4"]
        bgm = tmp_path / "music.wav"
        for p in [*shots, bgm]:
            p.write_bytes(b"fake-media")

        monkeypatch.setattr(
            "media_studio.acquire.subprocess.run",
            _run_mock(
                palette_text=(
                    "peeked frame.png (video)\n  palette   bright saturated warm colors\n"
                ),
                bpm_text="bpm 120.0, 4 beats (2 downbeats), 1 high-energy section(s)",
            ),
        )

        problems = check_brief_consistency(shots, bgm, "an action sequence")
        assert problems == []

    def test_dark_horror_brief_with_saturated_palette_is_inconsistent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shots = [tmp_path / "shot-a.mp4"]
        bgm = tmp_path / "music.wav"
        for p in [*shots, bgm]:
            p.write_bytes(b"fake-media")

        monkeypatch.setattr(
            "media_studio.acquire.subprocess.run",
            _run_mock(
                palette_text=("peeked frame.png (video)\n  palette   saturated warm neon colors\n"),
                bpm_text="bpm 120.0, 4 beats",
            ),
        )

        problems = check_brief_consistency(shots, bgm, "a dark horror scene")
        assert problems != []
        assert any("dark" in p and "saturated" in p for p in problems)


class TestInspectThumbnail:
    def test_downloads_url_to_out_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_png = b"\x89PNG\r\n\x1a\nfake"

        def fake_urlretrieve(url: str, filename: str) -> None:
            Path(filename).write_bytes(fake_png)

        monkeypatch.setattr("media_studio.acquire.urllib.request.urlretrieve", fake_urlretrieve)

        result = inspect_thumbnail("https://example.com/thumb.png", tmp_path)

        assert result == tmp_path / "thumb.png"
        assert result.is_file()
        assert result.read_bytes() == fake_png


class TestInspectAudioPreview:
    def test_returns_valid_metadata_for_real_wav(self, tmp_path: Path) -> None:
        wav = tmp_path / "tone.wav"
        _write_sine_wav(wav, seconds=1.0)

        result = inspect_audio_preview(wav, tmp_path, duration=10.0)

        assert set(result) >= {"duration", "sample_rate", "channels", "bpm_estimate"}
        assert result["duration"] > 0
        assert result["sample_rate"] == SAMPLE_RATE
        assert result["channels"] >= 1
        assert result["bpm_estimate"] is None or isinstance(result["bpm_estimate"], (int, float))
        # ffprobe metadata must be real and JSON-serialisable.
        json.dumps(result)
