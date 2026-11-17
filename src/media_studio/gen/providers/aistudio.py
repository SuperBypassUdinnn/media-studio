"""Google AI Studio provider (Gemini / Imagen / Veo) keyed by ``GEMINI_API_KEY``."""

from __future__ import annotations

from pathlib import Path

from google import genai

from media_studio.keys import key_for

from ..base import GenerateOutput, GenProvider, ProviderQuotaError

API_KEY_NAME = "GEMINI_API_KEY"
IMAGE_MODEL = "imagen-3.0-generate-002"
VIDEO_MODEL = "veo-2.0-generate-001"


class AistudioProvider(GenProvider):
    """Google AI Studio generative adapter."""

    name = "aistudio"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or key_for(API_KEY_NAME)

    def available(self) -> bool:
        return bool(self._api_key)

    def generates(self, kind: str) -> bool:
        return kind in ("image", "video")

    def _client(self):
        return genai.Client(api_key=self._api_key)

    def _is_quota_error(self, error: object) -> tuple[bool, int | None]:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
        message = str(error)
        is_quota = (
            status == 429
            or "429" in message
            or "quota" in message.lower()
            or "rate limit" in message.lower()
        )
        retry_after = None
        if response is not None and hasattr(response, "headers"):
            value = response.headers.get("Retry-After")
            if value is not None:
                try:
                    retry_after = int(value)
                except (TypeError, ValueError):
                    retry_after = None
        return is_quota, retry_after

    def generate_image(
        self,
        prompt: str,
        *,
        output: Path,
        size: str,
        seed: int | None,
        references: list[str],
    ) -> GenerateOutput:
        client = self._client()
        config = {
            "prompt": prompt,
        }
        if references:
            config["reference_images"] = references
        try:
            response = client.models.generate_images(
                model=IMAGE_MODEL,
                prompt=prompt,
                config=config,
            )
        except Exception as error:
            is_quota, retry_after = self._is_quota_error(error)
            if is_quota:
                raise ProviderQuotaError(str(error), retry_after) from error
            raise
        images = getattr(response, "images", None) or []
        if not images:
            raise RuntimeError("aistudio returned no images")
        image = images[0]
        image_bytes = getattr(image, "image_bytes", None)
        if image_bytes is None:
            image_bytes = image
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
        client = self._client()
        try:
            operation = client.models.generate_videos(
                model=VIDEO_MODEL,
                prompt=prompt,
            )
        except Exception as error:
            is_quota, retry_after = self._is_quota_error(error)
            if is_quota:
                raise ProviderQuotaError(str(error), retry_after) from error
            raise
        video = _poll_video(client, operation)
        output.write_bytes(video)
        return GenerateOutput(model=VIDEO_MODEL, seed=seed)


def _poll_video(client, operation):  # pragma: no cover - SDK polling, mocked in tests

    while True:
        updated = client.operations.get(operation)
        if getattr(updated, "done", False):
            response = getattr(updated, "response", None)
            videos = getattr(response, "generated_videos", None) or []
            if videos:
                return videos[0].video_bytes
            raise RuntimeError("aistudio video generation produced no video")
