"""Hugging Face provider keyed by ``HF_TOKEN`` (inference API / public Spaces)."""

from __future__ import annotations

from pathlib import Path

import httpx

from media_studio.keys import key_for

from ..base import GenerateOutput, GenProvider, ProviderQuotaError, ProviderUnavailable

API_KEY_NAME = "HF_TOKEN"
IMAGE_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
API_BASE = "https://api-inference.huggingface.co/models"


class HuggingFaceProvider(GenProvider):
    """Hugging Face inference API adapter."""

    name = "huggingface"

    def __init__(self, api_key: str | None = None, model: str = IMAGE_MODEL):
        self._api_key = api_key or key_for(API_KEY_NAME)
        self._model = model

    def available(self) -> bool:
        return bool(self._api_key)

    def generates(self, kind: str) -> bool:
        return kind == "image"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def generate_image(
        self,
        prompt: str,
        *,
        output: Path,
        size: str,
        seed: int | None,
        references: list[str],
    ) -> GenerateOutput:
        width, height = _parse_size(size)
        url = f"{API_BASE}/{self._model}"
        try:
            response = httpx.post(
                url,
                headers=self._headers(),
                json={
                    "inputs": prompt,
                    "parameters": {"width": width, "height": height, "seed": seed},
                },
                timeout=120,
            )
        except httpx.HTTPError as error:
            raise RuntimeError(f"huggingface request failed: {error}") from error
        if response.status_code == 429:
            raise ProviderQuotaError(
                "huggingface rate limited (HTTP 429)",
                _retry_after(response),
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"huggingface failed with HTTP {response.status_code}: {response.text[:200]}"
            )
        output.write_bytes(response.content)
        return GenerateOutput(model=self._model, seed=seed)

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
        raise ProviderUnavailable("huggingface does not offer free video generation")


def _parse_size(size: str) -> tuple[int, int]:
    try:
        width_text, _, height_text = str(size).lower().partition("x")
        return int(width_text), int(height_text)
    except (TypeError, ValueError):
        return 1024, 1024


def _retry_after(response) -> int | None:
    value = response.headers.get("Retry-After")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None
