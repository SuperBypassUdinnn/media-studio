"""Tests for GoogleFlowProvider with a mocked transport.

Zero live outbound network calls. Covers Nano Banana Pro image generation (0 credits),
Veo 3.1 - Lite video (10 credits), Omni 1.2 Flash fallback (24 credits), and 429 /
quota-exhaustion account failover.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_studio.gen.base import ProviderQuotaError
from media_studio.gen.flow_quota import FlowAccountManager, FlowCreditStore
from media_studio.gen.providers.google_flow import GoogleFlowProvider

IMAGE_MODEL = "nano-banana-pro"
VIDEO_MODEL = "veo-3.1-lite"
OMNI_MODEL = "omni-1.2-flash"
FAKE_IMAGE_BYTES = b"fake-nano-banana-image"
FAKE_VIDEO_BYTES = b"fake-veo-video"
ACCOUNTS = [
    {
        "id": "free-1",
        "email": "free1@gmail.com",
        "tier": "free",
        "enabled": True,
        "token": "tok-free-1",
    },
    {
        "id": "pro-1",
        "email": "pro1@gmail.com",
        "tier": "pro",
        "enabled": True,
        "token": "tok-pro-1",
    },
    {
        "id": "pro-2",
        "email": "pro2@gmail.com",
        "tier": "pro",
        "enabled": True,
        "token": "tok-pro-2",
    },
]


def _write_accounts(path: Path, accounts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(accounts), encoding="utf-8")


@pytest.fixture
def env_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    acc_path = tmp_path / "flow-accounts.json"
    cr_path = tmp_path / "flow-credits.json"
    _write_accounts(acc_path, ACCOUNTS)
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_ACCOUNTS_PATH", str(acc_path))
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_CREDITS_PATH", str(cr_path))
    return acc_path, cr_path


def _make_provider(env_paths, accounts=ACCOUNTS):
    acc_path, cr_path = env_paths
    _write_accounts(acc_path, accounts)
    manager = FlowAccountManager(accounts_path=acc_path)
    store = FlowCreditStore(credits_path=cr_path)
    return GoogleFlowProvider(account_manager=manager, credit_store=store), store


class FakeResponse:
    def __init__(self, status_code: int, content: bytes, text: str = "", headers=None):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.headers = headers or {}


def test_available_when_accounts_configured(env_paths) -> None:
    provider, _ = _make_provider(env_paths)
    assert provider.available() is True


def test_available_false_without_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    acc_path = tmp_path / "empty-accounts.json"
    acc_path.write_text("[]", encoding="utf-8")
    cr_path = tmp_path / "empty-credits.json"
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_ACCOUNTS_PATH", str(acc_path))
    monkeypatch.setenv("MEDIA_STUDIO_FLOW_CREDITS_PATH", str(cr_path))
    manager = FlowAccountManager(accounts_path=acc_path)
    store = FlowCreditStore(credits_path=cr_path)
    assert GoogleFlowProvider(account_manager=manager, credit_store=store).available() is False


def test_generates_image_and_video() -> None:
    provider = GoogleFlowProvider()
    assert provider.generates("image") is True
    assert provider.generates("video") is True
    assert provider.generates("audio") is False


def test_image_uses_nano_banana_pro_zero_credits(env_paths, tmp_path: Path, monkeypatch) -> None:
    provider, store = _make_provider(env_paths)
    output = tmp_path / "out.png"
    captured: dict = {}

    def fake_request(method, url, headers, json, timeout):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = json
        return FakeResponse(200, FAKE_IMAGE_BYTES)

    monkeypatch.setattr("media_studio.gen.providers.google_flow.httpx.request", fake_request)
    result = provider.generate_image(
        "a cinematic product shot",
        output=output,
        size="1080x1920",
        seed=7,
        references=["ref1.png"],
    )

    assert output.read_bytes() == FAKE_IMAGE_BYTES
    assert result.model == IMAGE_MODEL
    assert result.seed == 7
    assert result.references == ("ref1.png",)
    assert captured["payload"]["model"] == "GEM_PIX_2"
    assert captured["payload"]["aspect_ratio"] == "IMAGE_ASPECT_RATIO_PORTRAIT"
    assert captured["payload"]["prompt"] == "a cinematic product shot"
    # Image gen costs 0: nothing spent.
    state = store._read(account_id="free-1")
    assert state is not None and state.get("daily_spent", 0) == 0


def test_image_beluga_harbor_seal_wire_keys(env_paths, tmp_path: Path, monkeypatch) -> None:
    provider, _store = _make_provider(env_paths)

    def make_case(model_name: str, expected_wire_key: str) -> None:
        output = tmp_path / f"out_{model_name}.png"
        captured: dict = {}

        def fake_request(method, url, headers, json, timeout):
            captured["payload"] = json
            return FakeResponse(200, FAKE_IMAGE_BYTES)

        monkeypatch.setattr("media_studio.gen.providers.google_flow.httpx.request", fake_request)
        result = provider.generate_image(
            "a cinematic product shot",
            output=output,
            size="1024x1024",
            seed=1,
            references=[],
            model=model_name,
        )

        assert result.model == model_name
        assert captured["payload"]["model"] == expected_wire_key

    make_case("nano-banana-2.1", "BELUGA")
    make_case("nano-banana-2-lite", "HARBOR_SEAL")


def test_video_uses_veo_3_1_lite_ten_credits(env_paths, tmp_path: Path, monkeypatch) -> None:
    provider, store = _make_provider(env_paths)
    # Drain free-1 (waterfall priority 1) so pro-1 is the first video-eligible account.
    store.record_spend("free-1", 60, "veo-3.1-lite", pool="daily")
    output = tmp_path / "out.mp4"
    captured: dict = {}

    def fake_request(method, url, headers, json, timeout):
        captured["payload"] = json
        return FakeResponse(200, FAKE_VIDEO_BYTES)

    monkeypatch.setattr("media_studio.gen.providers.google_flow.httpx.request", fake_request)
    result = provider.generate_video(
        "a cinematic product shot",
        output=output,
        duration=8.0,
        fps=30,
        resolution="1280x720",
        seed=3,
    )

    assert output.read_bytes() == FAKE_VIDEO_BYTES
    assert result.model == VIDEO_MODEL
    assert result.seed == 3
    assert captured["payload"]["model"] == "veo_3_1_i2v_lite"
    assert captured["payload"]["duration"] == 8.0
    # 10 credits recorded against pro-1 daily.
    state = store._read(account_id="pro-1")
    assert state is not None and state["daily_spent"] == 10


def test_video_falls_back_to_omni_1_2_flash(env_paths, tmp_path: Path, monkeypatch) -> None:
    # Single fresh pro-1 account: Veo request 429s, Omni request succeeds.
    single = [
        {
            "id": "pro-1",
            "email": "pro1@gmail.com",
            "tier": "pro",
            "enabled": True,
            "token": "tok-pro-1",
        }
    ]
    acc_path, cr_path = env_paths
    _write_accounts(acc_path, single)
    manager = FlowAccountManager(accounts_path=acc_path)
    store = FlowCreditStore(credits_path=cr_path)
    provider = GoogleFlowProvider(account_manager=manager, credit_store=store)
    output = tmp_path / "out.mp4"

    def fake_request(method, url, headers, json, timeout):
        if json.get("model") == "veo_3_1_i2v_lite":
            return FakeResponse(429, b"", text="quota exceeded", headers={"Retry-After": "60"})
        return FakeResponse(200, FAKE_VIDEO_BYTES)

    monkeypatch.setattr("media_studio.gen.providers.google_flow.httpx.request", fake_request)
    result = provider.generate_video(
        "prompt", output=output, duration=8.0, fps=30, resolution="1280x720", seed=None
    )

    assert result.model == OMNI_MODEL
    state = store._read(account_id="pro-1")
    assert state is not None and state["daily_spent"] == 24


def test_429_triggers_account_failover(env_paths, tmp_path: Path, monkeypatch) -> None:
    provider, store = _make_provider(env_paths)
    # Drain free-1 (waterfall priority 1) so pro-1 is tried first.
    store.record_spend("free-1", 60, "veo-3.1-lite", pool="daily")
    output = tmp_path / "out.mp4"
    calls = {"n": 0}

    def fake_request(method, url, headers, json, timeout):
        calls["n"] += 1
        # pro-1 (first pro) 429s; pro-2 succeeds.
        return (
            FakeResponse(429, b"", text="quota exceeded")
            if calls["n"] == 1
            else FakeResponse(200, FAKE_VIDEO_BYTES)
        )

    monkeypatch.setattr("media_studio.gen.providers.google_flow.httpx.request", fake_request)
    result = provider.generate_video(
        "prompt", output=output, duration=5.0, fps=30, resolution="1920x1080", seed=1
    )

    assert result.model == VIDEO_MODEL
    assert calls["n"] == 2
    # pro-2 (the account that succeeded) should have the spend recorded.
    state = store._read(account_id="pro-2")
    assert state is not None and state["daily_spent"] == 10


def test_all_accounts_exhausted_raises_provider_quota_error(
    env_paths, tmp_path: Path, monkeypatch
) -> None:
    provider, store = _make_provider(env_paths)
    for aid in ("free-1", "pro-1", "pro-2"):
        store.record_spend(aid, 60, "veo-3.1-lite", pool="daily")
    output = tmp_path / "out.mp4"
    monkeypatch.setattr(
        "media_studio.gen.providers.google_flow.httpx.request",
        lambda *a, **k: FakeResponse(429, b"", text="quota exceeded"),
    )
    with pytest.raises(ProviderQuotaError):
        provider.generate_video(
            "prompt", output=output, duration=5.0, fps=30, resolution="1920x1080", seed=None
        )
