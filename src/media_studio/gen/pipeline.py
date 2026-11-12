"""Generative pipeline: provider cascade, still-to-motion, deterministic fallback, provenance.

The public entry points are :func:`generate_asset` and :func:`generate_storyboard`.

Cascade order (free quota first):
  1. ``aistudio``     - Google AI Studio (image + video, ``GEMINI_API_KEY``)
  2. ``huggingface``  - Hugging Face inference API (image only, ``HF_TOKEN``)
  3. ``keyless``      - Pollinations image API (image only, no key)

Paid slots ``fal``, ``replicate``, ``kling``, ``seedance`` are registered but stay
unavailable until their key is configured, so the cascade skips them.

When every generative provider fails or hits its daily cap, the pipeline cascades to a
deterministic B-roll: ``acquire.acquire_asset`` when a project is supplied, else a clean
FFmpeg solid/gradient motion card. A still produced for a ``video`` request is converted
to motion with FFmpeg ``zoompan`` and marked ``fallback_reason="motion-from-still"``.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from media_studio.acquire import acquire_asset
from media_studio.ledger import (
    LicensePolicyError,
    QuotaError,
    stage_asset,
    write_index_and_credits,
)
from media_studio.manifest import Project
from media_studio.sidecar import sidecar_path

from .base import (
    GenerateOutput,
    GenError,
    GenProvider,
    GenRequest,
    GenResult,
    ProviderQuotaError,
    ProviderUnavailable,
)
from .providers import default_providers
from .quota import QuotaStore

STAGING_CLASS_FOR_KIND = {"image": "design", "video": "video"}
LICENSE_TEXT = "LicenseRef-AI-Generated"
DETERMINISTIC_FALLBACK = "deterministic-fallback"
MOTION_FROM_STILL = "motion-from-still"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_ffmpeg(command: list[str]) -> None:
    """Run one ffmpeg command. Swappable in tests via monkeypatch."""
    subprocess.run(command, capture_output=True, text=True, check=True, timeout=3600)


def _seconds_until_midnight() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")
    return cleaned or "scene"


def _gen_sidecar(
    *,
    output: Path,
    provider: str,
    model: str,
    seed: int | None,
    prompt: str,
    references: tuple[str, ...],
    class_name: str,
) -> str:
    relative = output.name
    digest = _sha256_of(output)
    lines = [
        f"SPDX-License-Identifier: {LICENSE_TEXT}",
        f"Attribution: [AI Generated: {provider}/{model}]",
        f"Provider: {provider}",
        f"Model: {model}",
        f"Prompt: {prompt}",
        f"Seed: {seed if seed is not None else ''}",
        f"References: {','.join(references)}",
        f"Generated-At: {_now()}",
        f"SHA256: {digest}",
        f"Class: {class_name}",
        f"File: {relative}",
    ]
    return "\n".join(lines) + "\n"


def _write_sidecar(output: Path, text: str) -> None:
    sidecar_path(output).write_text(text, encoding="utf-8")


def _register_in_project(
    *,
    project: Project,
    output: Path,
    provider: str,
    model: str,
    seed: int | None,
    prompt: str,
    references: tuple[str, ...],
    kind: str,
    fallback_used: bool,
    fallback_reason: str | None,
) -> None:
    class_name = STAGING_CLASS_FOR_KIND[kind]
    attribution = f"[AI Generated: {provider}/{model}]"
    try:
        staged = stage_asset(
            project,
            local_file=output,
            class_name=class_name,
            license_text=LICENSE_TEXT,
            attribution=attribution,
            provider=provider,
            source_url=prompt,
        )
    except (LicensePolicyError, QuotaError) as error:
        raise GenError(f"ledger refused generated asset: {error}") from error
    sidecar_path(staged.path).write_text(
        _gen_sidecar(
            output=staged.path,
            provider=provider,
            model=model,
            seed=seed,
            prompt=prompt,
            references=references,
            class_name=class_name,
        ),
        encoding="utf-8",
    )
    write_index_and_credits(project)


def _finalize(
    *,
    output: Path,
    kind: str,
    provider: str,
    model: str,
    seed: int | None,
    prompt: str,
    references: tuple[str, ...],
    duration: float | None,
    fallback_used: bool,
    fallback_reason: str | None,
    project: Project | None,
) -> GenResult:
    digest = _sha256_of(output)
    size_bytes = output.stat().st_size
    class_name = STAGING_CLASS_FOR_KIND[kind]
    _write_sidecar(
        output,
        _gen_sidecar(
            output=output,
            provider=provider,
            model=model,
            seed=seed,
            prompt=prompt,
            references=references,
            class_name=class_name,
        ),
    )
    if project is not None:
        _register_in_project(
            project=project,
            output=output,
            provider=provider,
            model=model,
            seed=seed,
            prompt=prompt,
            references=references,
            kind=kind,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )
    return GenResult(
        output=output,
        kind=kind,
        provider=provider,
        model=model,
        seed=seed,
        prompt=prompt,
        references=references,
        sha256=digest,
        size_bytes=size_bytes,
        duration=duration,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )


def _still_to_motion(
    still: Path,
    output: Path,
    *,
    duration: float,
    fps: int,
    resolution: str,
    motion: str = "push_in",
) -> None:
    """Convert one still image to a motion MP4 with an FFmpeg ``zoompan`` Ken Burns move."""

    width_text, _, height_text = str(resolution).lower().partition("x")
    width, height = int(width_text), int(height_text)
    total_frames = max(1, round(duration * fps))
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-y",
        "-loop",
        "1",
        "-i",
        str(still),
        "-vf",
        (
            f"zoompan=z='min(zoom+0.0015,1.4)':x='iw/2-(iw/zoom)/2':"
            f"y='ih/2-(ih/zoom)/2':s={width}x{height}:fps={fps}:d={total_frames}"
        ),
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(command)


def _deterministic_card(output: Path, *, duration: float, fps: int, resolution: str) -> None:
    """Write a clean FFmpeg gradient motion card when every generative path fails."""

    width_text, _, height_text = str(resolution).lower().partition("x")
    width, height = int(width_text), int(height_text)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration:.3f}:size={width}x{height}:rate={fps}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(command)


def _attempt(
    *,
    provider: GenProvider,
    request: GenRequest,
    output: Path,
    quota_store: QuotaStore,
    max_daily: int,
    kind: str,
) -> GenerateOutput | None:
    """Try one provider once. Returns output metadata on success, None when skipped.

    Failed attempts (429, cap, unavailability) never consume ``max_gen_calls``: they only
    record a cooldown and let the cascade advance.
    """

    if not provider.available():
        return None
    if not provider.generates(kind):
        return None
    if not quota_store.can_spend(provider.name, max_daily):
        quota_store.set_cooldown(provider.name, _seconds_until_midnight())
        return None
    try:
        if kind == "video":
            return provider.generate_video(
                request.prompt,
                output=output,
                duration=request.duration,
                fps=request.fps,
                resolution=request.resolution,
                seed=request.seed,
            )
        return provider.generate_image(
            request.prompt,
            output=output,
            size=request.size,
            seed=request.seed,
            references=list(request.references),
        )
    except ProviderQuotaError as error:
        quota_store.set_cooldown(provider.name, error.retry_after or _seconds_until_midnight())
        return None
    except ProviderUnavailable:
        return None
    except Exception:  # any other provider failure: skip without spending
        return None


def generate_asset(
    request: GenRequest,
    *,
    project: Project | None = None,
    output: Path,
    max_gen_calls: int = 6,
    quota_store: QuotaStore | None = None,
) -> GenResult:
    """Generate one asset, cascading through free-quota providers then deterministic fallback.

    Only successful generative calls are recorded against the quota store and counted toward
    ``max_gen_calls``; 429 / cap / unavailable providers advance the cascade without spending.
    """

    if request.kind not in ("image", "video"):
        raise GenError(f"unsupported kind {request.kind!r}; expected 'image' or 'video'")

    output = Path(output)
    if quota_store is None:
        quota_store = QuotaStore()

    providers = default_providers()
    spent = 0
    kind = request.kind

    if kind == "video":
        # Phase 1: providers that can natively generate video.
        for provider in providers:
            if spent >= max_gen_calls:
                break
            outcome = _attempt(
                provider=provider,
                request=request,
                output=output,
                quota_store=quota_store,
                max_daily=6,
                kind="video",
            )
            if outcome is None:
                continue
            quota_store.record_spend(provider.name)
            spent += 1
            return _finalize(
                output=output,
                kind="video",
                provider=provider.name,
                model=outcome.model,
                seed=outcome.seed,
                prompt=request.prompt,
                references=outcome.references,
                duration=request.duration,
                fallback_used=False,
                fallback_reason=None,
                project=project,
            )
        # Phase 2: image-only providers become a still, then a motion-from-still conversion.
        for provider in providers:
            if spent >= max_gen_calls:
                break
            if not provider.generates("image"):
                continue
            still = output.with_suffix(".png")
            outcome = _attempt(
                provider=provider,
                request=GenRequest(
                    kind="image",
                    prompt=request.prompt,
                    size=request.size,
                    seed=request.seed,
                    references=list(request.references),
                ),
                output=still,
                quota_store=quota_store,
                max_daily=6,
                kind="image",
            )
            if outcome is None:
                continue
            quota_store.record_spend(provider.name)
            spent += 1
            _still_to_motion(
                still,
                output,
                duration=request.duration,
                fps=request.fps,
                resolution=request.resolution,
                motion=request.motion,
            )
            return _finalize(
                output=output,
                kind="video",
                provider=provider.name,
                model=outcome.model,
                seed=outcome.seed,
                prompt=request.prompt,
                references=outcome.references,
                duration=request.duration,
                fallback_used=True,
                fallback_reason=MOTION_FROM_STILL,
                project=project,
            )
    else:
        for provider in providers:
            if spent >= max_gen_calls:
                break
            outcome = _attempt(
                provider=provider,
                request=request,
                output=output,
                quota_store=quota_store,
                max_daily=6,
                kind="image",
            )
            if outcome is None:
                continue
            quota_store.record_spend(provider.name)
            spent += 1
            return _finalize(
                output=output,
                kind="image",
                provider=provider.name,
                model=outcome.model,
                seed=outcome.seed,
                prompt=request.prompt,
                references=outcome.references,
                duration=None,
                fallback_used=False,
                fallback_reason=None,
                project=project,
            )

    # All generative providers failed or hit quota caps: deterministic B-roll fallback.
    if kind == "video":
        if project is not None:
            acquired = acquire_asset(
                type="video",
                tags=[_slug(request.prompt)],
                project_assets_dir=project.root / "assets",
                project_slug=project.slug,
            )
            if acquired is not None and acquired.symlink.is_file():
                shutil.copyfile(acquired.symlink, output)
                return _finalize(
                    output=output,
                    kind="video",
                    provider="library",
                    model=acquired.entry.id,
                    seed=None,
                    prompt=request.prompt,
                    references=(),
                    duration=request.duration,
                    fallback_used=True,
                    fallback_reason=DETERMINISTIC_FALLBACK,
                    project=project,
                )
        _deterministic_card(
            output,
            duration=request.duration,
            fps=request.fps,
            resolution=request.resolution,
        )
        return _finalize(
            output=output,
            kind="video",
            provider="deterministic",
            model="ffmpeg-testsrc2",
            seed=None,
            prompt=request.prompt,
            references=(),
            duration=request.duration,
            fallback_used=True,
            fallback_reason=DETERMINISTIC_FALLBACK,
            project=project,
        )
    raise GenError(f"all generative providers failed for image: {request.prompt!r}")


def generate_storyboard(
    scenes: list[str],
    *,
    project: Project | None = None,
    out_dir: Path,
    max_gen_calls: int = 6,
) -> list[GenResult]:
    """Generate one image per scene, honouring the shared ``max_gen_calls`` budget."""

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[GenResult] = []
    quota_store = QuotaStore()
    spent = 0
    for index, scene in enumerate(scenes):
        if spent >= max_gen_calls:
            break
        output = out_dir / f"{index:03d}_{_slug(scene)}.png"
        request = GenRequest(kind="image", prompt=scene)
        result = generate_asset(
            request,
            project=project,
            output=output,
            max_gen_calls=max_gen_calls - spent,
            quota_store=quota_store,
        )
        results.append(result)
        # Only a successful generative call (no fallback) consumes the storyboard budget.
        if not result.fallback_used:
            spent += 1
    return results
