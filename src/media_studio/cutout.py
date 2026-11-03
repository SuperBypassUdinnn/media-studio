"""Project-less image cutouts: background removal and signature extraction.

These operations stand outside the project workflow on purpose. Cutting a
friend out of a photo or lifting a signature from a scan are single-file,
ad-hoc jobs — the same class as ``ms video ingest`` — so they take a plain
path and write the result beside the source by default.

Two jobs live here:

* ``remove_background`` runs an AI segmentation model through ``rembg``.
* ``extract_signature`` lifts dark ink strokes from a scan into a
  transparent PNG with classic thresholding, which is more precise than
  segmentation for that job.

Models are downloaded by rembg on first use and cached under ``~/.rembg/models/``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


class CutoutError(RuntimeError):
    """Raised when a cutout operation cannot read or write an image."""


#: Friendly model names with what each is for. Sizes are the one-time
#: downloads rembg fetches into ~/.rembg/models/ on first use.
MODELS: dict[str, str] = {
    "u2net": "general subjects (default, ~176 MB)",
    "u2netp": "lightweight general model (~5 MB)",
    "human_seg": "people only, multiple people supported (~170 MB)",
    "anime": "anime characters (~170 MB)",
    "general-use": "stronger general segmentation (~170 MB)",
    "birefnet-lite": "highest quality edges (~930 MB)",
}

#: Friendly name -> rembg session name. rembg's session registry uses its own
#: identifiers (u2net_human_seg, isnet-anime, ...), which do not match the
#: friendly names above for everything except the u2net family.
_REMBG_NAMES: dict[str, str] = {
    "u2net": "u2net",
    "u2netp": "u2netp",
    "human_seg": "u2net_human_seg",
    "anime": "isnet-anime",
    "general-use": "isnet-general-use",
    "birefnet-lite": "birefnet-general-lite",
}

#: The rembg session used when ``select_point`` asks SAM to isolate one object.
SELECT_MODEL = "sam"

_SELECT_PROMPT = [{"type": "point", "label": 1, "data": [0, 0]}]


@dataclass(frozen=True)
class RemoveResult:
    """The outcome of one background removal."""

    source: Path
    destination: Path
    model: str
    selective: bool
    device: str = "gpu"


@dataclass(frozen=True)
class SignatureResult:
    """The outcome of one signature extraction."""

    source: Path
    destination: Path
    threshold: int
    ink_pixels: int
    cropped: bool


def _load(path: Path) -> Image.Image:
    try:
        image = Image.open(path)
        image.load()
    except (OSError, ValueError) as error:
        raise CutoutError(f"cannot read image {path}: {error}") from error
    return image


def _destination(source: Path, destination: Path | None, suffix: str) -> Path:
    if destination is None:
        return source.with_name(f"{source.stem}.{suffix}.png")
    if destination.is_dir():
        return destination / f"{source.stem}.{suffix}.png"
    return destination


def remove_background(
    source: Path,
    destination: Path | None = None,
    model: str = "u2net",
    *,
    post_process: bool = False,
    select_point: tuple[int, int] | None = None,
) -> RemoveResult:
    """Cut the subject of one photo out of its background.

    Args:
        source: the photo to read.
        destination: where to write the RGBA PNG; defaults to
            ``<stem>.nobg.png`` beside the source.
        model: a key of ``MODELS``. Ignored when ``select_point`` is given,
            because selection always runs the SAM model.
        post_process: shrink-and-resharpen the mask to tighten soft edges.
        select_point: an ``(x, y)`` pixel on the object to keep. Everything
            not reachable from that point is removed, which is how you pick
            one subject out of a photo that has many.

    Raises:
        CutoutError: the image is unreadable or the model name is unknown.
    """

    image = _load(source)
    target = _destination(source, destination, "nobg")
    selective = select_point is not None

    if selective:
        x, y = select_point  # type: ignore[misc]
        if not (0 <= x < image.width and 0 <= y < image.height):
            raise CutoutError(
                f"select point ({x}, {y}) is outside the {image.width}x{image.height} image"
            )
        prompt = [
            {"type": "point", "label": 1, "data": [x, y]},
        ]
        return _remove_with_session(
            source,
            image,
            target,
            SELECT_MODEL,
            post_process=post_process,
            sam_prompt=prompt,
            selective=True,
        )

    if model not in MODELS:
        known = ", ".join(sorted(MODELS))
        raise CutoutError(f"unknown cutout model {model!r}; available: {known}")
    return _remove_with_session(source, image, target, model, post_process=post_process)


def _is_oom(error: Exception) -> bool:
    """Whether an inference failure means the accelerator ran out of memory."""

    message = str(error)
    return "ONNXRuntimeError" in message or "Failed to allocate memory" in message


def _infer(
    image: Image.Image,
    rembg_name: str,
    kwargs: dict,
    *,
    new_session_fn: Callable[..., object],
    remove_fn: Callable[..., object],
) -> tuple[Image.Image, str]:
    """Run one removal, retrying on CPU when the accelerator cannot fit it.

    Large models such as birefnet-lite need more VRAM than a 4 GB card has
    free, so the first attempt uses rembg's default providers and an
    out-of-memory failure retries with an explicit CPU session.

    Returns:
        The result image and the device actually used: ``gpu`` when the
        default providers worked, ``cpu`` after the retry.
    """

    try:
        session = new_session_fn(rembg_name)
        return remove_fn(image, session=session, **kwargs), "gpu"
    except Exception as error:
        if not _is_oom(error):
            raise
    session = new_session_fn(rembg_name, providers=["CPUExecutionProvider"])
    return remove_fn(image, session=session, **kwargs), "cpu"


def _remove_with_session(
    source: Path,
    image: Image.Image,
    target: Path,
    model: str,
    *,
    post_process: bool = False,
    sam_prompt: list[dict] | None = None,
    selective: bool = False,
) -> RemoveResult:
    from rembg import new_session, remove

    kwargs: dict = {"post_process_mask": post_process}
    if sam_prompt is not None:
        kwargs["sam_prompt"] = sam_prompt

    try:
        result, device = _infer(
            image,
            _REMBG_NAMES.get(model, model),
            kwargs,
            new_session_fn=new_session,
            remove_fn=remove,
        )
    except Exception as error:  # rembg raises bare RuntimeError variants
        raise CutoutError(f"background removal failed for {source}: {error}") from error

    if not isinstance(result, Image.Image):
        raise CutoutError(f"background removal returned no image for {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target)
    return RemoveResult(
        source=source,
        destination=target,
        model=model,
        selective=selective,
        device=device,
    )


def extract_signature(
    source: Path,
    destination: Path | None = None,
    *,
    threshold: int | None = None,
    crop: bool = True,
) -> SignatureResult:
    """Lift ink strokes from a signature scan into a transparent PNG.

    The scan is thresholded in grayscale: pixels darker than the threshold
    become ink, their darkness becomes opacity, and everything else becomes
    transparent. The original colours are kept, so blue ink stays blue.

    Args:
        source: the scan or photo of the signature.
        destination: where to write; defaults to ``<stem>.signature.png``.
        threshold: 0-255 cutoff for what counts as ink. Defaults to Otsu's
            automatic choice, which handles most clean scans.
        crop: trim the output to the ink's bounding box with a small margin.

    Raises:
        CutoutError: the image is unreadable or has no ink at all.
    """

    image = _load(source).convert("RGB")
    target = _destination(source, destination, "signature")

    gray = np.asarray(image.convert("L"), dtype=np.int16)
    if threshold is None:
        if int(gray.min()) == int(gray.max()):
            raise CutoutError(f"{source} is a flat image with no ink to extract")
        cutoff = int(_otsu_threshold(gray))
    else:
        if not 0 <= threshold <= 255:
            raise CutoutError(f"threshold must be 0-255, got {threshold}")
        cutoff = threshold

    ink = gray <= cutoff
    if not ink.any():
        raise CutoutError(
            f"no ink found in {source} at threshold {cutoff}; try a lower --threshold"
        )

    # Unmix each pixel between the ink and paper class means, so opacity is
    # proportional to how much ink covers it and edges stay soft.
    paper = float(gray[~ink].mean()) if (~ink).any() else 255.0
    ink_level = float(gray[ink].mean())
    span = max(paper - ink_level, 1.0)
    wide = _dilate(ink)  # one pixel around the core strokes keeps AA edges
    alpha = np.where(wide, np.clip((paper - gray) / span * 255.0, 0, 255), 0).astype(np.uint8)

    cropped = False
    if crop:
        rows = np.any(alpha > 0, axis=1)
        cols = np.any(alpha > 0, axis=0)
        top, bottom = np.argmax(rows), len(rows) - np.argmax(rows[::-1])
        left, right = np.argmax(cols), len(cols) - np.argmax(cols[::-1])
        margin = 8
        top = max(0, top - margin)
        left = max(0, left - margin)
        bottom = min(alpha.shape[0], bottom + margin)
        right = min(alpha.shape[1], right + margin)
        if (top, left, bottom, right) != (0, 0, alpha.shape[0], alpha.shape[1]):
            cropped = True
    else:
        top, left, bottom, right = 0, 0, alpha.shape[0], alpha.shape[1]

    rgb = np.asarray(image)[top:bottom, left:right]
    out = np.dstack([rgb, alpha[top:bottom, left:right]])
    result = Image.fromarray(out, "RGBA")

    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target)
    return SignatureResult(
        source=source,
        destination=target,
        threshold=cutoff,
        ink_pixels=int((alpha > 0).sum()),
        cropped=cropped,
    )


#: Image extensions the image commands treat as input.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})


def _image_folder(
    source_dir: Path,
    destination_dir: Path | None,
    recursive: bool,
) -> list[Path]:
    """List the image files under ``source_dir`` the commands would process.

    Raises:
        CutoutError: ``source_dir`` is not a directory.
    """

    if not source_dir.is_dir():
        raise CutoutError(f"not a folder: {source_dir}")

    pattern = "**/*" if recursive else "*"
    return sorted(
        candidate
        for candidate in source_dir.glob(pattern)
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES
    )


def _folder_target(
    source_dir: Path,
    destination_dir: Path | None,
    candidate: Path,
    suffix: str,
) -> Path | None:
    """The output file for one folder candidate, mirroring the input tree.

    ``None`` keeps the output beside the source, which is what
    ``_destination`` already implements for the single-file case. The path is
    built in full because ``_destination``'s directory heuristic cannot see a
    tree that does not exist yet.
    """

    if destination_dir is None:
        return None
    relative_parent = candidate.relative_to(source_dir).parent
    return destination_dir / relative_parent / f"{candidate.stem}.{suffix}.png"


def remove_background_folder(
    source_dir: Path,
    destination_dir: Path | None = None,
    model: str = "u2net",
    *,
    post_process: bool = False,
    recursive: bool = True,
) -> list[RemoveResult]:
    """Remove backgrounds from every image under ``source_dir``.

    Non-image files are ignored, so a mixed folder of videos, sidecars and
    documents is safe to point at. The output tree mirrors the input one.

    Raises:
        CutoutError: the folder is missing or one image fails.
    """

    results: list[RemoveResult] = []
    for candidate in _image_folder(source_dir, destination_dir, recursive):
        target = _folder_target(source_dir, destination_dir, candidate, "nobg")
        results.append(remove_background(candidate, target, model, post_process=post_process))
    return results


def extract_signature_folder(
    source_dir: Path,
    destination_dir: Path | None = None,
    *,
    threshold: int | None = None,
    crop: bool = True,
    recursive: bool = True,
) -> list[SignatureResult]:
    """Extract signatures from every image under ``source_dir``.

    Raises:
        CutoutError: the folder is missing or one image has no ink.
    """

    results: list[SignatureResult] = []
    for candidate in _image_folder(source_dir, destination_dir, recursive):
        target = _folder_target(source_dir, destination_dir, candidate, "signature")
        results.append(extract_signature(candidate, target, threshold=threshold, crop=crop))
    return results


def _dilate(mask: np.ndarray) -> np.ndarray:
    """One 4-neighbour dilation step, without a scipy dependency."""

    wide = mask.copy()
    wide[1:, :] |= mask[:-1, :]
    wide[:-1, :] |= mask[1:, :]
    wide[:, 1:] |= mask[:, :-1]
    wide[:, :-1] |= mask[:, 1:]
    return wide


def _otsu_threshold(gray: np.ndarray) -> float:
    """Otsu's cutoff, computed without importing all of scikit-image."""

    hist, _ = np.histogram(gray, bins=256, range=(0, 256))
    total = gray.size
    sum_total = np.dot(np.arange(256), hist)

    sum_background = 0.0
    weight_background = 0.0
    best_score = -1.0
    best_cutoff = 127.0
    for cutoff in range(256):
        weight_background += hist[cutoff]
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += cutoff * hist[cutoff]
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        score = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if score > best_score:
            best_score = score
            best_cutoff = cutoff
    return best_cutoff
