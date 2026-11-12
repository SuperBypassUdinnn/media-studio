"""Tests for generative provider adapters with mocked HTTP / SDK transports."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from media_studio.gen.base import ProviderQuotaError, ProviderUnavailable
from media_studio.gen.providers.aistudio import AistudioProvider
from media_studio.gen.providers.huggingface import HuggingFaceProvider
from media_studio.gen.providers.keyless import KeylessProvider
from media_studio.gen.providers.plugins import (
    FalProvider,
    KlingProvider,
    ReplicateProvider,
    SeedanceProvider,
)

FAKE_IMAGE_BYTES = b"fake-image-payload"
FAKE_KEY = "fake-key-1234567890abcdef"


class FakeAistudioImages:
    def __init__(self, payload: bytes = FAKE_IMAGE_BYTES):
        self.image_bytes = payload


class FakeAistudioResponse:
    def __init__(self, payload: bytes = FAKE_IMAGE_BYTES, captured=None):
        self.images = [FakeAistudioImages(payload)]
        self.captured = captured


class FakeAistudioModels:
    def __init__(self, payload: bytes = FAKE_IMAGE_BYTES, captured=None):
        self.payload = payload
        self.captured = captured

    def generate_images(self, model, prompt, config):
        if self.captured is not None:
            self.captured.append((model, prompt, config))
        return FakeAistudioResponse(self.payload, self.captured)


class FakeAistudioClient:
    def __init__(self, api_key, payload: bytes = FAKE_IMAGE_BYTES, captured=None):
        self.api_key = api_key
        self.models = FakeAistudioModels(payload, captured)


def _make_aistudio(
    monkeypatch, payload: bytes = FAKE_IMAGE_BYTES, key=FAKE_KEY, captured: list | None = None
):
    if captured is None:
        captured = []

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.models = FakeAistudioModels(payload, captured)

    monkeypatch.setattr(
        "media_studio.gen.providers.aistudio.key_for",
        lambda name: key if name == "GEMINI_API_KEY" else None,
    )
    monkeypatch.setattr("media_studio.gen.providers.aistudio.genai.Client", FakeClient)
    return AistudioProvider(), captured


def test_aistudio_available_with_key_and_not_without(monkeypatch) -> None:
    provider, _ = _make_aistudio(monkeypatch, key=FAKE_KEY)
    assert provider.available() is True

    no_key, _ = _make_aistudio(monkeypatch, key=None)
    assert no_key.available() is False


def test_aistudio_generates_image_and_video() -> None:
    provider = AistudioProvider(api_key=FAKE_KEY)
    assert provider.generates("image") is True
    assert provider.generates("video") is True


def test_aistudio_image_writes_bytes_and_attaches_references(tmp_path, monkeypatch) -> None:
    captured: list = []
    provider, _ = _make_aistudio(monkeypatch, captured=captured)
    output = tmp_path / "out.png"

    result = provider.generate_image(
        "a cinematic product shot",
        output=output,
        size="1024x1024",
        seed=7,
        references=["ref1.png", "ref2.png"],
    )

    assert output.read_bytes() == FAKE_IMAGE_BYTES
    assert result.model == "imagen-3.0-generate-002"
    assert result.seed == 7
    assert result.references == ("ref1.png", "ref2.png")
    assert captured and captured[0][1] == "a cinematic product shot"
    assert "reference_images" in captured[0][2]
    assert captured[0][2]["reference_images"] == ["ref1.png", "ref2.png"]


def test_aistudio_image_without_references(monkeypatch, tmp_path) -> None:
    captured: list = []
    provider, _ = _make_aistudio(monkeypatch, captured=captured)
    output = tmp_path / "out.png"
    provider.generate_image("prompt", output=output, size="512x512", seed=None, references=[])
    assert captured and "reference_images" not in captured[0][2]


def test_aistudio_429_raises_provider_quota_error(monkeypatch) -> None:
    class BadModels:
        def generate_images(self, model, prompt, config):
            error = httpx.HTTPStatusError(
                "rate limited", request=httpx.Request("POST", "x"), response=httpx.Response(429)
            )
            error.response.headers = {"Retry-After": "60"}
            raise error

    class BadClient:
        models = BadModels()

    monkeypatch.setattr("media_studio.gen.providers.aistudio.key_for", lambda name: FAKE_KEY)
    monkeypatch.setattr(
        "media_studio.gen.providers.aistudio.genai.Client", lambda api_key: BadClient()
    )
    provider = AistudioProvider()
    with pytest.raises(ProviderQuotaError) as exc_info:
        provider.generate_image(
            "prompt", output=Path("/tmp/x.png"), size="512x512", seed=None, references=[]
        )
    assert exc_info.value.retry_after == 60


class FakeHfResponse:
    def __init__(self, status_code: int, content: bytes = FAKE_IMAGE_BYTES, headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


def test_huggingface_available_with_key_and_not_without(monkeypatch) -> None:
    monkeypatch.setattr(
        "media_studio.gen.providers.huggingface.key_for",
        lambda name: FAKE_KEY if name == "HF_TOKEN" else None,
    )
    assert HuggingFaceProvider().available() is True
    monkeypatch.setattr("media_studio.gen.providers.huggingface.key_for", lambda name: None)
    assert HuggingFaceProvider().available() is False


def test_huggingface_generates_image_only() -> None:
    provider = HuggingFaceProvider(api_key=FAKE_KEY)
    assert provider.generates("image") is True
    assert provider.generates("video") is False


def test_huggingface_image_posts_prompt_and_writes_bytes(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["json"] = json
        assert "Bearer" in headers.get("Authorization", "")
        return FakeHfResponse(200)

    monkeypatch.setattr("media_studio.gen.providers.huggingface.httpx.post", fake_post)
    provider = HuggingFaceProvider(api_key=FAKE_KEY)
    output = tmp_path / "out.png"
    result = provider.generate_image(
        "a red apple", output=output, size="512x512", seed=3, references=[]
    )

    assert output.read_bytes() == FAKE_IMAGE_BYTES
    assert result.model == "stabilityai/stable-diffusion-xl-base-1.0"
    assert result.seed == 3
    assert captured["json"]["inputs"] == "a red apple"


def test_huggingface_429_raises_provider_quota_error(monkeypatch) -> None:
    def fake_post(url, headers, json, timeout):
        return FakeHfResponse(429, headers={"Retry-After": "30"})

    monkeypatch.setattr("media_studio.gen.providers.huggingface.httpx.post", fake_post)
    provider = HuggingFaceProvider(api_key=FAKE_KEY)
    with pytest.raises(ProviderQuotaError) as exc_info:
        provider.generate_image(
            "prompt", output=Path("/tmp/x.png"), size="512x512", seed=None, references=[]
        )
    assert exc_info.value.retry_after == 30


class FakeKeylessResponse:
    def __init__(self, status_code: int, content: bytes = FAKE_IMAGE_BYTES):
        self.status_code = status_code
        self.content = content


def test_keyless_is_always_available_without_key() -> None:
    assert KeylessProvider().available() is True
    assert KeylessProvider().generates("image") is True
    assert KeylessProvider().generates("video") is False


def test_keyless_image_gets_pollinations_url(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_get(url, timeout):
        captured["url"] = url
        return FakeKeylessResponse(200)

    monkeypatch.setattr("media_studio.gen.providers.keyless.httpx.get", fake_get)
    provider = KeylessProvider()
    output = tmp_path / "out.png"
    result = provider.generate_image(
        "sunset over mountains", output=output, size="800x600", seed=9, references=[]
    )

    assert output.read_bytes() == FAKE_IMAGE_BYTES
    assert result.model == "pollinations"
    assert result.seed == 9
    assert "pollinations.ai/prompt/" in captured["url"]
    assert "width=800" in captured["url"] and "height=600" in captured["url"]
    assert "seed=9" in captured["url"]


def test_plugins_are_unavailable_without_key(monkeypatch) -> None:
    for provider in (FalProvider(), ReplicateProvider(), KlingProvider(), SeedanceProvider()):
        assert provider.available() is False
        with pytest.raises(ProviderUnavailable):
            provider.generate_image(
                "prompt", output=Path("/tmp/x.png"), size="512x512", seed=None, references=[]
            )
        with pytest.raises(ProviderUnavailable):
            provider.generate_video(
                "prompt",
                output=Path("/tmp/x.mp4"),
                duration=5.0,
                fps=30,
                resolution="1920x1080",
                seed=None,
            )


def test_plugins_available_with_key(monkeypatch) -> None:
    monkeypatch.setattr("media_studio.gen.providers.plugins.key_for", lambda name: FAKE_KEY)
    assert FalProvider().available() is True
    assert ReplicateProvider().available() is True
    assert KlingProvider().available() is True
    assert SeedanceProvider().available() is True
    assert FalProvider().generates("image") and FalProvider().generates("video")
    assert KlingProvider().generates("video") and not KlingProvider().generates("image")
