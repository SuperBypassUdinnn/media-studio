"""Keyless provider backed by the Pollinations image API (no credentials required)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx

from ..base import GenerateOutput, GenProvider, ProviderQuotaError, ProviderUnavailable

API_URL = "https://image.pollinations.ai/prompt/{}"


class KeylessProvider(GenProvider):
    """Pollinations keyless image generation adapter."""

    name = "keyless"

    def available(self) -> bool:
        return True

    def generates(self, kind: str) -> bool:
        return kind == "image"

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
        seed_param = f"&seed={seed}" if seed is not None else ""
        quality_prompt = (
            f"{prompt}, 4k resolution, sharp focus, "
            "cinematic lighting, photorealistic, depth of field"
        )
        url = (
            API_URL.format(quote(quality_prompt, safe=""))
            + f"?width={width}&height={height}{seed_param}&nologo=true"
        )
        try:
            response = httpx.get(url, timeout=120)
        except httpx.HTTPError as error:
            raise RuntimeError(f"keyless request failed: {error}") from error
        if response.status_code == 429:
            raise ProviderQuotaError("keyless rate limited (HTTP 429)", _retry_after(response))
        if response.status_code >= 400:
            raise RuntimeError(
                f"keyless failed with HTTP {response.status_code}: {response.text[:200]}"
            )
        if not response.content:
            raise RuntimeError("keyless returned an empty image")
        output.write_bytes(response.content)
        return GenerateOutput(model="pollinations", seed=seed)

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
        raise ProviderUnavailable("keyless does not offer video generation")


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
