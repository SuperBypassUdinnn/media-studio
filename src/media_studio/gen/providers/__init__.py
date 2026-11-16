"""Pluggable generative provider adapters.

Order matters: the cascade tries ``google_flow`` first (images at 0 credits and video on
verified Google Flow models backed by a multi-account credit waterfall), then ``aistudio``,
then ``huggingface``, then ``keyless`` (Pollinations), and finally falls back to
deterministic B-roll. The ``fal``/``replicate``/``kling``/``seedance`` slots are wired here
but stay unavailable until their API keys are configured.
"""

from __future__ import annotations

from .aistudio import AistudioProvider
from .google_flow import GoogleFlowProvider
from .huggingface import HuggingFaceProvider
from .keyless import KeylessProvider
from .plugins import FalProvider, KlingProvider, ReplicateProvider, SeedanceProvider

__all__ = [
    "AistudioProvider",
    "FalProvider",
    "GoogleFlowProvider",
    "HuggingFaceProvider",
    "KeylessProvider",
    "KlingProvider",
    "ReplicateProvider",
    "SeedanceProvider",
    "default_providers",
    "plugin_providers",
]


def plugin_providers() -> list:
    """Provider slots that require an API key before they are usable."""

    return [FalProvider(), ReplicateProvider(), KlingProvider(), SeedanceProvider()]


def default_providers() -> list:
    """The default free-quota cascade order.

    ``google_flow`` leads the cascade because it generates images at 0 credits and video on
    verified Google Flow models backed by each user's own multi-account credit waterfall.
    """

    return [GoogleFlowProvider(), AistudioProvider(), HuggingFaceProvider(), KeylessProvider()]
