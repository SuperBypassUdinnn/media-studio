"""Pluggable free-quota generative layer.

Generate images and video from free-quota providers (Google AI Studio, Hugging Face,
keyless Pollinations, plus fal/replicate/kling/seedance slots) with a daily per-provider
quota store, provider cascade, still-to-motion fallback, deterministic B-roll fallback, and
REUSE-style provenance sidecars.
"""

from __future__ import annotations

from .base import (
    GenError,
    GenProvider,
    GenRequest,
    GenResult,
    ProviderQuotaError,
    ProviderUnavailable,
)
from .pipeline import generate_asset, generate_storyboard
from .quota import DEFAULT_QUOTA_PATH, QuotaStore

__all__ = [
    "DEFAULT_QUOTA_PATH",
    "GenError",
    "GenProvider",
    "GenRequest",
    "GenResult",
    "ProviderQuotaError",
    "ProviderUnavailable",
    "QuotaStore",
    "generate_asset",
    "generate_storyboard",
]
