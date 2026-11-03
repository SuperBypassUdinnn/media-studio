"""Video background removal with SAM 2 prompt propagation.

SAM 2 tracks a prompted object through a whole clip: one point on one frame
is enough, and the model's streaming memory keeps the mask temporally stable
where per-frame cutters flicker. The pipeline is deliberately simple:

1. ``ffmpeg`` extracts every frame as a JPEG.
2. SAM 2 propagates the prompt mask across all frames.
3. Composited RGBA frames stream straight into ``ffmpeg``'s stdin, which
   encodes a ProRes 4444 QuickTime with real alpha — the format DaVinci
   Resolve, Premiere and Final Cut all import as transparent footage.

The mask never leaves memory as images: masks are bit-packed and streamed to
a disk memmap as propagation yields them, and SAM 2's own per-chunk state is
the only large allocation. Clips longer than one state comfortably fits are
propagated in segments: each segment starts a fresh predictor state seeded
with the centroid of the previous segment's boundary mask, so RAM stays
bounded no matter the clip length. A ``--feather`` radius softens the
otherwise binary alpha at encode time without ever materialising a float
mask per frame.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .ingest import IngestError, probe_media

#: Where SAM 2 checkpoints are cached, mirroring rembg's ~/.rembg layout.
SAM2_CACHE_DIR = Path.home() / ".cache" / "media-studio" / "sam2"

#: Friendly model name -> description. All four load the SAM 2.1 weights.
VIDEO_CUTOUT_MODELS = {
    "sam2.1-tiny": "fastest, fits 4 GB GPUs comfortably",
    "sam2.1-small": "better masks, still 4 GB friendly",
    "sam2.1-base": "high quality, needs VRAM headroom",
    "sam2.1-large": "best quality, largest download",
}

#: Friendly name -> (checkpoint file, hydra config inside the sam2 package).
_MODEL_SPECS = {
    "sam2.1-tiny": ("sam2.1_hiera_tiny.pt", "configs/sam2.1/sam2.1_hiera_t.yaml"),
    "sam2.1-small": ("sam2.1_hiera_small.pt", "configs/sam2.1/sam2.1_hiera_s.yaml"),
    "sam2.1-base": ("sam2.1_hiera_base_plus.pt", "configs/sam2.1/sam2.1_hiera_b+.yaml"),
    "sam2.1-large": ("sam2.1_hiera_large.pt", "configs/sam2.1/sam2.1_hiera_l.yaml"),
}

#: Checkpoint URLs from the official SAM 2.1 release (2024-09-28).
_CHECKPOINT_URLS = {
    file: f"https://dl.fbaipublicfiles.com/segment_anything_2/092824/{file}"
    for file, _ in _MODEL_SPECS.values()
}


class VideoCutoutError(RuntimeError):
    """Raised when video background removal cannot complete."""


@dataclass(frozen=True)
class VideoRemoveResult:
    """The outcome of one video cutout."""

    source: Path
    destination: Path
    model: str
    device: str
    frames: int
    anchor_frame: int
    select_point: tuple[int, int]
    fps: float
    size_bytes: int
    feather: int = 0
    segments: int = 1


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as error:  # pragma: no cover - ffmpeg is a dependency
        raise VideoCutoutError("ffmpeg is not installed") from error
    if result.returncode != 0:
        raise VideoCutoutError(f"{argv[0]} failed for {argv[-1]}:\n{result.stderr.strip()[-2000:]}")
    return result


def ensure_checkpoint(model: str, *, download: bool = True, cache_dir: Path | None = None) -> Path:
    """Return the cached checkpoint path for ``model``, downloading if asked.

    Raises:
        VideoCutoutError: the model name is unknown, or the checkpoint is
            missing and ``download`` is False.
    """

    if model not in _MODEL_SPECS:
        known = ", ".join(sorted(VIDEO_CUTOUT_MODELS))
        raise VideoCutoutError(f"unknown video cutout model {model!r}; available: {known}")

    file, _ = _MODEL_SPECS[model]
    directory = cache_dir if cache_dir is not None else SAM2_CACHE_DIR
    path = directory / file
    if path.is_file():
        return path
    if not download:
        raise VideoCutoutError(f"checkpoint not cached at {path}; pass download=True")

    directory.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    try:
        urllib.request.urlretrieve(_CHECKPOINT_URLS[file], partial)
        partial.replace(path)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise VideoCutoutError(f"cannot download SAM 2 checkpoint: {error}") from error
    return path


def _extract_command(source: Path, frames_dir: Path) -> list[str]:
    """The ffmpeg invocation that dumps one JPEG per frame."""

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-start_number",
        "0",
        "-qscale:v",
        "2",
        str(Path(frames_dir) / "%06d.jpg"),
    ]


def _encode_command(width: int, height: int, fps: float, destination: Path) -> list[str]:
    """The ffmpeg invocation turning an rgba rawvideo pipe into ProRes 4444."""

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:g}",
        "-i",
        "-",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4444",
        "-vendor",
        "apl0",
        "-pix_fmt",
        "yuva444p10le",
        "-movflags",
        "+write_colr",
        str(destination),
    ]


def _pack_mask(logits) -> np.ndarray:
    """Threshold SAM 2 logits into a bit-packed boolean mask."""

    mask = (logits[0] > 0).cpu().numpy().astype(bool)
    return np.packbits(mask.reshape(-1))


def _unpack_mask(packed: np.ndarray, width: int, height: int) -> np.ndarray:
    return np.unpackbits(packed, count=width * height).reshape(height, width).astype(bool)


def _frame_name(index: int) -> str:
    """The JPEG name ffmpeg's ``%06d`` pattern gives frame ``index``."""

    return f"{index:06d}.jpg"


class _MaskStore:
    """Bit-packed masks for every frame, backed by a disk memmap.

    Propagation results stream here the moment SAM 2 yields them, so peak
    memory stays at one packed mask instead of one per frame. Uncovered
    frames encode as fully transparent.
    """

    def __init__(self, total: int, width: int, height: int, work_dir: Path) -> None:
        self.total = total
        self.width = width
        self.height = height
        self.bytes_per_mask = (width * height + 7) // 8
        self.path = work_dir / "masks.bin"
        self.buffer = np.memmap(
            self.path, dtype=np.uint8, mode="w+", shape=(total, self.bytes_per_mask)
        )
        self.covered = np.zeros(total, dtype=bool)

    def put(self, index: int, packed: np.ndarray) -> None:
        self.buffer[index] = packed
        self.covered[index] = True

    def get(self, index: int) -> np.ndarray:
        return np.asarray(self.buffer[index])

    def covered_count(self) -> int:
        return int(self.covered.sum())

    def discard(self) -> None:
        del self.buffer
        self.path.unlink(missing_ok=True)


#: SAM 2 loads frames as float32 RGB at the video resolution inside its
#: inference state (verified in sam2/utils/misc.py load_video_frames), so a
#: state for WxH frames costs roughly 12 bytes per pixel.
_STATE_BYTES_PER_PIXEL = 3 * 4

#: System-RAM budget for one SAM 2 inference state. Segmented propagation
#: keeps every state at or below this.
_STATE_BUDGET_BYTES = 2 * 1024**3


def _chunk_frames(width: int, height: int, chunk_frames: int | None = None) -> int:
    """Frames per propagation segment: the override, or the state budget."""

    if chunk_frames is not None:
        if chunk_frames < 1:
            raise VideoCutoutError(f"chunk size must be at least 1, got {chunk_frames}")
        return chunk_frames
    per_frame = width * height * _STATE_BYTES_PER_PIXEL
    return max(1, int(_STATE_BUDGET_BYTES // per_frame))


def _chunk_ranges(total: int, chunk_frames: int) -> list[tuple[int, int]]:
    """Split ``total`` frames into inclusive ``(start, end)`` ranges."""

    return [
        (start, min(start + chunk_frames, total) - 1) for start in range(0, total, chunk_frames)
    ]


def _mask_centroid(packed: np.ndarray, width: int, height: int) -> tuple[int, int] | None:
    """The mean pixel of a packed mask, or ``None`` when it is empty."""

    mask = _unpack_mask(packed, width, height)
    if not mask.any():
        return None
    rows, columns = np.nonzero(mask)
    return round(float(columns.mean())), round(float(rows.mean()))


def _feather_alpha(mask: np.ndarray, radius: int) -> np.ndarray:
    """Turn a boolean mask into a soft uint8 alpha channel.

    ``radius <= 0`` keeps the current binary behaviour. Otherwise the mask
    grows by half the radius (so soft edges never eat the object) and a
    Gaussian blur of ``radius`` smooths the step into a gradient.
    """

    if radius <= 0:
        return mask.astype(np.uint8) * 255
    grow = max(1, radius // 2)
    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    image = image.filter(ImageFilter.MaxFilter(2 * grow + 1))
    image = image.filter(ImageFilter.GaussianBlur(radius))
    alpha = np.asarray(image, dtype=np.uint8)
    # GaussianBlur sags below 255 even on a wide plateau, which would dim
    # the object interior; rescale so the densest pixel is fully opaque.
    peak = int(alpha.max())
    if peak > 0:
        alpha = (alpha.astype(np.float64) * 255.0 / peak).astype(np.uint8)
    # The blur tail paints a faint haze over the whole frame; floor it so
    # untouched background stays exactly transparent.
    alpha[alpha < 8] = 0
    return alpha


def _video_destination(source: Path, destination: Path | None) -> Path:
    """Resolve the output ``.mov`` path the same way cutout resolves its PNG."""

    if destination is None:
        return source.with_suffix(".nobg.mov")
    if destination.is_dir():
        return destination / f"{source.stem}.nobg.mov"
    return destination


def _build_predictor(model: str, checkpoint: Path, device: str | None):
    """Import torch + sam2 lazily and build the video predictor."""

    try:
        import torch
        from sam2.build_sam import build_sam2_video_predictor
    except ImportError as error:
        raise VideoCutoutError(
            "SAM 2 is not installed in this environment; install with "
            "pip install git+https://github.com/facebookresearch/sam2.git"
        ) from error

    _, config = _MODEL_SPECS[model]
    target = device or ("cuda" if torch.cuda.is_available() else "cpu")
    try:
        predictor = build_sam2_video_predictor(config, str(checkpoint), device=target)
    except Exception as error:  # hydra/OOM/build failures all surface here
        raise VideoCutoutError(f"cannot build SAM 2 predictor on {target}: {error}") from error
    return predictor, target


def remove_background_video(
    source: Path,
    destination: Path | None = None,
    *,
    select_point: tuple[int, int],
    anchor_frame: int = 0,
    model: str = "sam2.1-tiny",
    full_vram: bool = False,
    cache_dir: Path | None = None,
    feather: int = 0,
    chunk_frames: int | None = None,
) -> VideoRemoveResult:
    """Cut the prompted object out of a clip into an alpha ProRes 4444 MOV.

    Args:
        source: the footage to read.
        destination: ``.mov`` file or folder; defaults to
            ``<stem>.nobg.mov`` beside the source.
        select_point: pixel (x, y) on ``anchor_frame`` marking the object
            to keep.
        anchor_frame: the frame the point refers to (default first).
        model: one of :data:`VIDEO_CUTOUT_MODELS`.
        full_vram: keep per-frame features on the GPU. Default off, which
            offloads them to system RAM — the safe setting for a 4 GB card.
        cache_dir: override the SAM 2 checkpoint cache location.
        feather: soft-edge radius in pixels; 0 keeps the binary mask.
        chunk_frames: frames per propagation segment; ``None`` picks the
            size that fits the state budget for this resolution.

    Raises:
        VideoCutoutError: probing, frame extraction, prompting, propagation
            or encoding failed.
    """

    if model not in VIDEO_CUTOUT_MODELS:
        known = ", ".join(sorted(VIDEO_CUTOUT_MODELS))
        raise VideoCutoutError(f"unknown video cutout model {model!r}; available: {known}")

    try:
        info = probe_media(source)
    except IngestError as error:
        raise VideoCutoutError(str(error)) from error

    width, height = info.width, info.height
    x, y = select_point
    if not (0 <= x < width and 0 <= y < height):
        raise VideoCutoutError(f"select point ({x}, {y}) is outside the {width}x{height} frame")

    frames_dir = Path("/tmp/opencode") / f"sam2-frames-{source.stem}-{anchor_frame}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    try:
        _run(_extract_command(source, frames_dir))
        frame_paths = sorted(frames_dir.glob("*.jpg"))
        if not frame_paths:
            raise VideoCutoutError(f"ffmpeg extracted no frames from {source}")
        if not 0 <= anchor_frame < len(frame_paths):
            raise VideoCutoutError(
                f"anchor frame {anchor_frame} is outside the clip (0..{len(frame_paths) - 1})"
            )

        checkpoint = ensure_checkpoint(model, cache_dir=cache_dir)
        predictor, device = _build_predictor(model, checkpoint, None)

        per_chunk = _chunk_frames(width, height, chunk_frames)
        ranges = _chunk_ranges(len(frame_paths), per_chunk)
        store = _MaskStore(len(frame_paths), width, height, frames_dir)
        try:
            segments = _propagate_masks(
                predictor,
                frames_dir,
                ranges,
                anchor_frame,
                (x, y),
                store,
                full_vram,
            )
            target = _video_destination(source, destination)
            if target.suffix.lower() != ".mov":
                target = target.with_suffix(".mov")
            if store.covered_count() == 0:
                raise VideoCutoutError(
                    "propagation never found the object; check --select and --frame"
                )
            _encode_frames(store, frame_paths, width, height, info.fps, target, feather)
        finally:
            store.discard()
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)

    return VideoRemoveResult(
        source=source,
        destination=target,
        model=model,
        device=device,
        frames=len(frame_paths),
        anchor_frame=anchor_frame,
        select_point=select_point,
        fps=info.fps,
        size_bytes=target.stat().st_size,
        feather=feather,
        segments=segments,
    )


def _propagate_masks(
    predictor,
    frames_dir: Path,
    ranges: list[tuple[int, int]],
    anchor_frame: int,
    point: tuple[int, int],
    store: _MaskStore,
    full_vram: bool,
) -> int:
    """Fill ``store`` segment by segment and return the segment count.

    The anchor's segment runs first, prompted with the raw point, and covers
    itself with a forward plus (when the anchor is not its first frame) a
    reverse pass — the behaviour the single-segment pipeline always had.
    Segments after it are seeded with the centroid of the previous segment's
    last mask; segments before it run in descending order, seeded with the
    centroid of the next segment's first mask and propagated in reverse.
    A segment whose seed mask is empty is skipped and everything beyond it
    in that direction stays transparent: the object is gone.
    """

    anchor_segment = next(
        i for i, (start, end) in enumerate(ranges) if start <= anchor_frame <= end
    )

    def coverage_boundary(segment: int, direction: str) -> tuple[int, int] | None:
        """Prompt point for the neighbouring segment, from an existing mask."""

        start, end = ranges[segment]
        frame = end if direction == "forward" else start
        if not store.covered[frame]:
            return None
        return _mask_centroid(store.get(frame), store.width, store.height)

    # Anchor segment: prompt with the user's point and cover both sides of
    # it inside one state — the verified single-state behaviour.
    start, end = ranges[anchor_segment]
    _run_segment(
        predictor,
        frames_dir,
        start,
        end,
        anchor_frame - start,
        point,
        store,
        full_vram,
        mode="both",
    )

    # Forward segments: each continues from the previous one's last mask.
    for segment in range(anchor_segment + 1, len(ranges)):
        seed = coverage_boundary(segment - 1, "forward")
        if seed is None:
            break
        start, end = ranges[segment]
        _run_segment(predictor, frames_dir, start, end, 0, seed, store, full_vram, mode="forward")

    # Backward segments: descending, each continues in reverse from the
    # next one's first mask.
    for segment in range(anchor_segment - 1, -1, -1):
        seed = coverage_boundary(segment + 1, "backward")
        if seed is None:
            break
        start, end = ranges[segment]
        _run_segment(
            predictor,
            frames_dir,
            start,
            end,
            end - start,
            seed,
            store,
            full_vram,
            mode="reverse",
        )

    return len(ranges)


def _run_segment(
    predictor,
    frames_dir: Path,
    start: int,
    end: int,
    prompt_index: int,
    point: tuple[int, int],
    store: _MaskStore,
    full_vram: bool,
    *,
    mode: str,
) -> None:
    """Propagate one segment in its own predictor state.

    ``mode`` selects the propagation direction(s): ``"forward"`` covers the
    prompt to the segment end, ``"reverse"`` covers the segment start down
    to the prompt, and ``"both"`` runs a forward then a reverse pass in the
    same state so one prompt covers the whole segment. The segment sees a
    directory of symlinks into the shared frame dump, so SAM 2 treats it as
    an independent clip while ffmpeg extracted the frames exactly once.
    """

    chunk_dir = frames_dir / f"chunk-{start:06d}"
    chunk_dir.mkdir(exist_ok=True)
    for index in range(start, end + 1):
        link = chunk_dir / _frame_name(index)
        if not link.exists():
            link.symlink_to(f"../{_frame_name(index)}")

    try:
        state = predictor.init_state(str(chunk_dir), offload_video_to_cpu=not full_vram)
        try:
            predictor.add_new_points_or_box(
                inference_state=state,
                frame_idx=prompt_index,
                obj_id=1,
                points=[[point[0], point[1]]],
                labels=[1],
            )
            if mode in ("forward", "both"):
                for frame_idx, _, logits in predictor.propagate_in_video(state):
                    store.put(start + frame_idx, _pack_mask(logits))
            if mode in ("reverse", "both"):
                for frame_idx, _, logits in predictor.propagate_in_video(state, reverse=True):
                    store.put(start + frame_idx, _pack_mask(logits))
        finally:
            del state
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)


def _encode_frames(
    store: _MaskStore,
    frame_paths: list[Path],
    width: int,
    height: int,
    fps: float,
    destination: Path,
    feather: int = 0,
) -> None:
    """Composite every frame with its mask and pipe RGBA into ffmpeg."""

    argv = _encode_command(width, height, fps, destination)
    stderr_file = Path("/tmp/opencode") / "sam2-encode-stderr.log"
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=stderr_file.open("wb"),
    )
    try:
        for index, path in enumerate(frame_paths):
            rgb = np.asarray(Image.open(path).convert("RGB"))
            if store.covered[index]:
                packed = store.get(index)
                mask = _unpack_mask(packed, width, height)
                alpha = _feather_alpha(mask, feather)
            else:
                alpha = np.zeros((height, width), dtype=np.uint8)
            rgba = np.dstack([rgb, alpha])
            process.stdin.write(rgba.tobytes())
        process.stdin.close()
        code = process.wait()
    except BrokenPipeError as error:
        raise VideoCutoutError(f"ffmpeg closed the encode pipe: {error}") from error
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
            process.wait()

    if code != 0 or not destination.is_file():
        detail = stderr_file.read_text(errors="replace").strip()[-2000:]
        raise VideoCutoutError(f"ffmpeg failed encoding {destination}:\n{detail}")
    stderr_file.unlink(missing_ok=True)
