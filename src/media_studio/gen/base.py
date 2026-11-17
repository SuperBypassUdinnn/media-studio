"""Generative media core types: requests, results, errors, and the provider protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class GenError(RuntimeError):
    """Raised when the generative pipeline cannot produce an asset."""


class ProviderUnavailable(GenError):
    """A provider is not configured or cannot be reached."""


class ProviderQuotaError(GenError):
    """A provider returned HTTP 429 or reported a quota / rate-limit error."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class GenRequest:
    """One generative request."""

    kind: str
    """Either ``"image"`` or ``"video"``."""

    prompt: str
    """Text prompt fed to the generator."""

    size: str = "1024x1024"
    """Requested image size (WxH), ignored for video."""

    duration: float = 5.0
    """Requested video duration in seconds."""

    fps: int = 30
    """Video frame rate."""

    resolution: str = "1920x1080"
    """Video resolution (WxH)."""

    motion: str = "push_in"
    """Still-to-motion Ken Burns style when an image must become video."""

    references: list[str] = field(default_factory=list)
    """Reference asset URLs/paths attached to image generation (aistudio)."""

    seed: int | None = None
    """Optional seed forwarded to providers that support it."""


@dataclass(frozen=True)
class GenResult:
    """What one :func:`generate_asset` call produced."""

    output: Path
    kind: str
    provider: str
    model: str
    seed: int | None
    prompt: str
    references: tuple[str, ...]
    sha256: str
    size_bytes: int
    duration: float | None
    fallback_used: bool = False
    fallback_reason: str | None = None
    license_text: str = "LicenseRef-AI-Generated"


@dataclass(frozen=True)
class GenerateOutput:
    """The decoded result of one provider generation call."""

    model: str
    seed: int | None
    references: tuple[str, ...] = ()


@runtime_checkable
class GenProvider(Protocol):
    """A pluggable generative provider adapter."""

    name: str

    def available(self) -> bool:
        """True when the provider is configured and reachable."""

    def generates(self, kind: str) -> bool:
        """True when this provider can produce ``kind`` (``"image"`` or ``"video"``)."""

    def generate_image(
        self,
        prompt: str,
        *,
        output: Path,
        size: str,
        seed: int | None,
        references: list[str],
    ) -> GenerateOutput:
        """Write one generated image to ``output`` and return its metadata."""

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
        """Write one generated video to ``output`` and return its metadata."""
