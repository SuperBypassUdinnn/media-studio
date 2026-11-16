"""Google Flow generative provider backed by the multi-account credit waterfall.

Routes image generation to ``Nano Banana Pro`` (wire key ``GEM_PIX_2``, 0 credits) and
video generation to ``Veo 3.1 - Lite`` (wire key ``veo_3_1_i2v_lite``, 10 credits per
batch) with ``Omni 1.2 Flash`` (wire key ``abra_t2v_10s``, 24 credits per 8s @ 720p) as
the quality fallback. Deprecated models (``veo_3_1_fast``, ``veo_3_1_quality``,
``abra_t2v_8s``) are never used.

Coordinates with :class:`FlowAccountManager` / :class:`FlowCreditStore` to acquire the
active account, run the predictive pre-check, and record credit spend. Raises
:class:`ProviderQuotaError` when every account is exhausted.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from media_studio.gen.flow_quota import FlowAccountManager, FlowCreditStore

from ..base import GenerateOutput, GenProvider, ProviderQuotaError

FLOW_BASE = "https://flow.googleapis.com/v1"

IMAGE_MODEL = "nano-banana-pro"
IMAGE_WIRE_KEY = "GEM_PIX_2"
# Also supported image models (wire keys): BELUGA, HARBOR_SEAL.

VIDEO_MODEL = "veo-3.1-lite"
VIDEO_WIRE_KEY = "veo_3_1_i2v_lite"
VIDEO_FALLBACK_MODEL = "omni-1.2-flash"
VIDEO_FALLBACK_WIRE_KEY = "abra_t2v_10s"

DEPRECATED_VIDEO_WIRE_KEYS = {"veo_3_1_fast", "veo_3_1_quality", "abra_t2v_8s"}

ASPECT_RATIOS = {
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
}


def _aspect_ratio(size: str) -> str:
    """Resolve an aspect-ratio enum from a ``WxH`` size string."""
    try:
        width_text, _, height_text = str(size).lower().partition("x")
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError):
        return ASPECT_RATIOS["1:1"]
    if width == 0 or height == 0:
        return ASPECT_RATIOS["1:1"]
    # Reduce to simplest whole ratio and match the closest supported enum.
    ratio = width / height
    if abs(ratio - 16 / 9) <= abs(ratio - 9 / 16) and abs(ratio - 16 / 9) <= abs(ratio - 1):
        return ASPECT_RATIOS["16:9"]
    if abs(ratio - 9 / 16) <= abs(ratio - 1):
        return ASPECT_RATIOS["9:16"]
    return ASPECT_RATIOS["1:1"]


class GoogleFlowProvider(GenProvider):
    """Google Flow generative adapter using the multi-account credit waterfall."""

    name = "google_flow"

    def __init__(
        self,
        account_manager: FlowAccountManager | None = None,
        credit_store: FlowCreditStore | None = None,
    ):
        self._account_manager = account_manager or FlowAccountManager()
        self._credit_store = credit_store or FlowCreditStore()

    def available(self) -> bool:
        """True when at least one account is configured (or session credentials exist)."""
        return bool(self._account_manager.accounts())

    def generates(self, kind: str) -> bool:
        return kind in ("image", "video")

    # -- internal ---------------------------------------------------------

    def _select_account(self, cost: int, excluded: set[str], allow_monthly_pool: bool):
        return self._credit_store.select_account(
            self._account_manager.accounts(),
            cost,
            allow_monthly_pool=allow_monthly_pool,
            exclude=excluded,
        )

    def _request(self, account: object, method: str, path: str, payload: dict) -> bytes:
        url = f"{FLOW_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {getattr(account, 'token', '')}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.request(method, url, headers=headers, json=payload, timeout=120)
        except httpx.HTTPError as error:
            raise ProviderQuotaError(f"google_flow request failed: {error}") from error
        if response.status_code == 429 or "quota" in response.text.lower():
            retry_after = None
            try:
                retry_after = int(response.headers.get("Retry-After") or 0) or None
            except (TypeError, ValueError):
                retry_after = None
            raise ProviderQuotaError(
                f"google_flow quota exceeded for {getattr(account, 'id', '?')}", retry_after
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"google_flow failed with HTTP {response.status_code}: {response.text[:200]}"
            )
        return response.content

    def _generate_image(self, account, prompt: str, size: str, seed, references: list) -> bytes:
        payload = {
            "model": IMAGE_WIRE_KEY,
            "prompt": prompt,
            "aspect_ratio": _aspect_ratio(size),
            "seed": seed,
            "references": references or [],
        }
        return self._request(account, "POST", "/models/generate-image", payload)

    def _generate_video(
        self, account, prompt: str, wire_key: str, duration: float, fps: int, resolution: str, seed
    ) -> bytes:
        payload = {
            "model": wire_key,
            "prompt": prompt,
            "duration": duration,
            "fps": fps,
            "resolution": resolution,
            "seed": seed,
        }
        return self._request(account, "POST", "/models/generate-video", payload)

    # -- public API -------------------------------------------------------

    def generate_image(
        self,
        prompt: str,
        *,
        output: Path,
        size: str,
        seed: int | None,
        references: list[str],
    ) -> GenerateOutput:
        cost = self._credit_store.cost(IMAGE_MODEL)
        excluded: set[str] = set()
        account = self._select_account(cost, excluded, allow_monthly_pool=False)
        if account is None:
            raise ProviderQuotaError(
                "google_flow: no account with sufficient credit for image generation"
            )
        try:
            image_bytes = self._generate_image(account, prompt, size, seed, list(references))
        except ProviderQuotaError:
            excluded.add(account.id)
            account = self._select_account(cost, excluded, allow_monthly_pool=False)
            if account is None:
                raise
            image_bytes = self._generate_image(account, prompt, size, seed, list(references))
        self._credit_store.record_spend(
            account.id, cost, IMAGE_MODEL, pool="daily", tier=account.tier
        )
        output.write_bytes(image_bytes)
        return GenerateOutput(model=IMAGE_MODEL, seed=seed, references=tuple(references))

    def generate_video(
        self,
        prompt: str,
        *,
        output: Path,
        duration: float,
        fps: int,
        resolution: str,
        seed: int | None,
    ) -> GenerateOutput:
        for model, wire_key, cost in (
            (VIDEO_MODEL, VIDEO_WIRE_KEY, self._credit_store.cost(VIDEO_MODEL)),
            (
                VIDEO_FALLBACK_MODEL,
                VIDEO_FALLBACK_WIRE_KEY,
                self._credit_store.cost(VIDEO_FALLBACK_MODEL),
            ),
        ):
            excluded: set[str] = set()
            for _ in range(max(1, len(self._account_manager.accounts()))):
                account = self._select_account(cost, excluded, allow_monthly_pool=False)
                if account is None:
                    break
                try:
                    video_bytes = self._generate_video(
                        account, prompt, wire_key, duration, fps, resolution, seed
                    )
                except ProviderQuotaError:
                    excluded.add(account.id)
                    account = None
                    continue
                except httpx.HTTPError:
                    excluded.add(account.id)
                    account = None
                    continue
                self._credit_store.record_spend(
                    account.id, cost, model, pool="daily", tier=account.tier
                )
                output.write_bytes(video_bytes)
                return GenerateOutput(model=model, seed=seed)
        raise ProviderQuotaError("google_flow: all accounts exhausted for video generation")
