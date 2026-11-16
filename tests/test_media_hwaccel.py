"""Tests for dGPU-first NVENC encoding with automatic CPU fallback (offline)."""

from __future__ import annotations

import subprocess

import pytest

from media_studio import media, video


@pytest.fixture(autouse=True)
def _reset_nvenc_cache():
    """Reset the in-memory NVENC cache so each test probes independently."""

    media._NVENC_CACHE = None
    yield
    media._NVENC_CACHE = None


def test_detect_nvenc_available_returns_bool():
    result = media.detect_nvenc_available()
    assert isinstance(result, bool)


def test_detect_nvenc_available_caches_result(monkeypatch):
    media._NVENC_CACHE = None
    probed: list = []

    def fake_run(command, **_kwargs):
        probed.append(command)
        return subprocess.CompletedProcess(command, 0, "h264_nvenc\n", "")

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert media.detect_nvenc_available() is True
    assert media.detect_nvenc_available() is True
    assert len(probed) == 1  # cached after the first probe


def test_resolve_video_encoder_flags_gpu_final_uses_nvenc_vbv(monkeypatch):
    monkeypatch.setattr(media, "detect_nvenc_available", lambda: True)
    name, flags = video.resolve_video_encoder_flags(use_gpu=True, fps=30)
    assert name == "h264_nvenc"
    assert flags[:2] == ["-c:v", "h264_nvenc"]
    assert "-preset" in flags
    assert "-b:v" in flags and "-maxrate" in flags and "-bufsize" in flags
    assert "-cq" not in flags


def test_resolve_video_encoder_flags_gpu_intermediate_uses_cq(monkeypatch):
    monkeypatch.setattr(media, "detect_nvenc_available", lambda: True)
    name, flags = video.resolve_video_encoder_flags(use_gpu=True, is_intermediate=True)
    assert name == "h264_nvenc"
    assert "-cq" in flags and "14" in flags
    assert "-b:v" not in flags


def test_resolve_video_encoder_flags_cpu(monkeypatch):
    monkeypatch.setattr(media, "detect_nvenc_available", lambda: False)
    name, flags = video.resolve_video_encoder_flags(use_gpu=True, fps=30)
    assert name == "libx264"
    assert flags[:2] == ["-c:v", "libx264"]
    assert "-preset" in flags
    assert "-crf" in flags


def test_resolve_video_encoder_flags_use_gpu_false_cpu(monkeypatch):
    monkeypatch.setattr(media, "detect_nvenc_available", lambda: True)
    name, _flags = video.resolve_video_encoder_flags(use_gpu=False)
    assert name == "libx264"


def test_run_ffmpeg_encode_with_fallback_retries_with_cpu(tmp_path, monkeypatch):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"")
    captured: list[list[str]] = []

    def command_builder(flags: list[str]) -> list[str]:
        captured.append(list(flags))
        return ["ffmpeg", "-y", *flags, str(output)]

    def fake_run(command, *, label, expect_stdout=False):
        if any(part == "h264_nvenc" for part in command):
            raise media.MediaError("NVENC device unavailable")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media, "run_command", fake_run)

    video.run_ffmpeg_encode_with_fallback(command_builder, output=output, label="encode")

    assert len(captured) == 2
    assert any("h264_nvenc" in flags for flags in captured[0])
    assert captured[1][:2] == ["-c:v", "libx264"]
