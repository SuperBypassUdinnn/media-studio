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
import tempfile
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
DEFAULT_DAILY_CAP = 6
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


# Supersample factor for the zoompan Ken Burns move: render the still at ~2x
# the target before zooming, then scale back down with lanczos so integer
# x/y rounding no longer jitters.
ZOOM_SUPER_SAMPLE = 2


def _still_to_motion(
    still: Path,
    output: Path,
    *,
    duration: float,
    fps: int,
    resolution: str,
    motion: str = "push_in",
) -> None:
    """Convert one still image to a motion MP4 with a supersampled ``zoompan`` Ken Burns move.

    The still is scaled up (``ZOOM_SUPER_SAMPLE``x) with lanczos before the
    zoompan, then scaled back down with lanczos, so integer x/y rounding no
    longer jitters. The zoom curve is eased (slow-in/slow-out sine) and the
    output is forced to limited range (``yuv420p``).
    """

    width_text, _, height_text = str(resolution).lower().partition("x")
    width, height = int(width_text), int(height_text)
    super_w, super_h = width * ZOOM_SUPER_SAMPLE, height * ZOOM_SUPER_SAMPLE
    total_frames = max(1, round(duration * fps))
    # Slow-in/slow-out (ease-in-out sine) zoom curve: zoom starts at 1, eases
    # toward 1.4, never exceeding it.
    ease_zoom = "if(lt(zoom,1.4),1+0.4*sin(min((zoom-1)/0.4,1)*3.14159/2),1.4)"
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
            f"scale={super_w}x{super_h}:flags=lanczos,"
            f"zoompan=z='{ease_zoom}':x='iw/2-(iw/zoom)/2':"
            f"y='ih/2-(ih/zoom)/2':s={super_w}x{super_h}:fps={fps}:d={total_frames},"
            f"scale={width}x{height}:flags=lanczos,format=yuv420p"
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


def _deterministic_card(
    output: Path,
    *,
    duration: float,
    fps: int,
    resolution: str,
    primary: str = "#0F172A",
    accent: str = "#38BDF8",
) -> None:
    """Write a clean branded dark motion slate when every generative path fails.

    Renders a solid base in the brand ``primary`` with a subtle accent radial
    glow (``geq``) so the fallback is a sleek brand card, never FFmpeg's
    ``testsrc2`` diagnostic pattern which reads as GPU corruption to viewers.
    """

    width_text, _, height_text = str(resolution).lower().partition("x")
    width, height = int(width_text), int(height_text)
    p = primary.lstrip("#")
    a = accent.lstrip("#")
    ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
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
        f"color=c=0x{p}:s={width}x{height}:rate={fps}:duration={duration:.3f}",
        "-vf",
        (
            f"geq=r='clip(r(X,Y)+{ar}/255*0.12*exp(-(hypot(X-W/2,Y-H/2)/({width}/5))^2),0,255)':"
            f"g='clip(g(X,Y)+{ag}/255*0.12*exp(-(hypot(X-W/2,Y-H/2)/({width}/5))^2),0,255)':"
            f"b='clip(b(X,Y)+{ab}/255*0.12*exp(-(hypot(X-W/2,Y-H/2)/({width}/5))^2),0,255)',"
            f"format=yuv420p"
        ),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    _run_ffmpeg(command)


def _deterministic_slate(output: Path, *, size: str = "1024x1024") -> None:
    """Write a clean PIL gradient/slate PNG card when every generative image path fails."""

    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - Pillow is a runtime dependency
        raise GenError(f"cannot render deterministic slate: {error}") from error

    width_text, _, height_text = str(size).lower().partition("x")
    width, height = int(width_text), int(height_text)
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                int(200 * x / max(width - 1, 1)),
                int(150 * y / max(height - 1, 1)),
                int(100 + 100 * (x + y) / max(width + height - 2, 1)),
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")


def _attempt(
    *,
    provider: GenProvider,
    request: GenRequest,
    output: Path,
    quota_store: QuotaStore,
    max_daily: int | None = None,
    kind: str,
) -> GenerateOutput | None:
    """Try one provider once. Returns output metadata on success, None when skipped.

    Failed attempts (429, cap, unavailability) never consume ``max_gen_calls``: they only
    record a cooldown and let the cascade advance. ``max_daily`` defaults to
    :data:`DEFAULT_DAILY_CAP` when ``None``.
    """

    cap = max_daily if max_daily is not None else DEFAULT_DAILY_CAP
    if not provider.available():
        return None
    if not provider.generates(kind):
        return None
    if not quota_store.can_spend(provider.name, cap):
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
    max_daily = max(max_gen_calls, DEFAULT_DAILY_CAP)

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
                max_daily=max_daily,
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
        # The intermediate still lives in a temp dir under /tmp/opencode and is always removed.
        tmp_dir = Path(tempfile.mkdtemp(prefix="gen-", dir="/tmp/opencode"))
        try:
            still = tmp_dir / f"{_slug(request.prompt)}.png"
            for provider in providers:
                if spent >= max_gen_calls:
                    break
                if not provider.generates("image"):
                    continue
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
                    max_daily=max_daily,
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
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        for provider in providers:
            if spent >= max_gen_calls:
                break
            outcome = _attempt(
                provider=provider,
                request=request,
                output=output,
                quota_store=quota_store,
                max_daily=max_daily,
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
            model="ffmpeg-branded-slate",
            seed=None,
            prompt=request.prompt,
            references=(),
            duration=request.duration,
            fallback_used=True,
            fallback_reason=DETERMINISTIC_FALLBACK,
            project=project,
        )

    # Image total failure: acquire from the project library when a project is supplied,
    # otherwise render a deterministic gradient slate PNG.
    if project is not None:
        acquired = acquire_asset(
            type="image",
            tags=[_slug(request.prompt)],
            project_assets_dir=project.root / "assets",
            project_slug=project.slug,
        )
        if acquired is not None and acquired.symlink.is_file():
            shutil.copyfile(acquired.symlink, output)
            return _finalize(
                output=output,
                kind="image",
                provider="library",
                model=acquired.entry.id,
                seed=None,
                prompt=request.prompt,
                references=(),
                duration=None,
                fallback_used=True,
                fallback_reason=DETERMINISTIC_FALLBACK,
                project=project,
            )
    _deterministic_slate(output, size=request.size)
    return _finalize(
        output=output,
        kind="image",
        provider="deterministic",
        model="pil-gradient-slate",
        seed=None,
        prompt=request.prompt,
        references=(),
        duration=None,
        fallback_used=True,
        fallback_reason=DETERMINISTIC_FALLBACK,
        project=project,
    )


def generate_storyboard(
    scenes: list[str],
    *,
    project: Project | None = None,
    out_dir: Path,
    max_gen_calls: int = 6,
) -> list[GenResult]:
    """Generate one image per scene, honouring the shared ``max_gen_calls`` budget.

    Per-scene failures degrade gracefully to a deterministic fallback result rather than
    aborting the whole storyboard. The remaining ``max_gen_calls`` budget is carried across
    scenes and only a successful generative call (no fallback) consumes it.
    """

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
        try:
            result = generate_asset(
                request,
                project=project,
                output=output,
                max_gen_calls=max_gen_calls - spent,
                quota_store=quota_store,
            )
        except GenError:
            # Last-resort deterministic slate for this scene; keep the storyboard going.
            _deterministic_slate(output, size="1024x1024")
            result = GenResult(
                output=output,
                kind="image",
                provider="deterministic",
                model="pil-gradient-slate",
                seed=None,
                prompt=scene,
                references=(),
                sha256=_sha256_of(output),
                size_bytes=output.stat().st_size,
                duration=None,
                fallback_used=True,
                fallback_reason=DETERMINISTIC_FALLBACK,
            )
        results.append(result)
        # Only a successful generative call (no fallback) consumes the storyboard budget.
        if not result.fallback_used:
            spent += 1
    return results
