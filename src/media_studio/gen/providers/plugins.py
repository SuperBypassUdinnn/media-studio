"""Paid provider slots (fal, replicate, kling, seedance) that need an API key.

These are wired into the provider registry but report ``available() == False`` until their
credential is registered, so the cascade simply skips them and continues to keyless or the
deterministic fallback. They are placeholders for future paid-tier integration.
"""

from __future__ import annotations

from pathlib import Path

from media_studio.keys import key_for

from ..base import GenerateOutput, GenProvider, ProviderUnavailable


class _PluginProvider(GenProvider):
    name: str = ""
    api_key_name: str = ""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or key_for(self.api_key_name)

    def available(self) -> bool:
        return bool(self._api_key)

    def generate_image(
        self,
        prompt: str,
        *,
        output: Path,
        size: str,
        seed: int | None,
        references: list[str],
    ) -> GenerateOutput:
        raise ProviderUnavailable(f"{self.name} is not configured (missing {self.api_key_name})")

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
        raise ProviderUnavailable(f"{self.name} is not configured (missing {self.api_key_name})")


class FalProvider(_PluginProvider):
    name = "fal"
    api_key_name = "FAL_API_KEY"

    def generates(self, kind: str) -> bool:
        return kind in ("image", "video")


class ReplicateProvider(_PluginProvider):
    name = "replicate"
    api_key_name = "REPLICATE_API_KEY"

    def generates(self, kind: str) -> bool:
        return kind in ("image", "video")


class KlingProvider(_PluginProvider):
    name = "kling"
    api_key_name = "KLING_API_KEY"

    def generates(self, kind: str) -> bool:
        return kind == "video"


class SeedanceProvider(_PluginProvider):
    name = "seedance"
    api_key_name = "SEEDANCE_API_KEY"

    def generates(self, kind: str) -> bool:
        return kind == "video"
