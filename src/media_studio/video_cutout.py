"""Video background removal with SAM 2 prompt propagation.

SAM 2 tracks a prompted object through a whole clip: one point on one frame
is enough, and the model's streaming memory keeps the mask temporally stable
where per-frame cutters flicker. The pipeline is deliberately simple:

1. ``ffmpeg`` extracts every frame as a JPEG.
2. SAM 2 propagates the prompt mask across all frames.
3. Composited RGBA frames stream straight into ``ffmpeg``'s stdin, which
   encodes a ProRes 4444 QuickTime with real alpha — the format DaVinci
   Resolve, Premiere and Final Cut all import as transparent footage.

The mask never leaves memory as images: only one composited frame exists at
a time, and masks are kept bit-packed so a 30-second clip stays under a
hundred megabytes. Longer clips trade RAM for time; the honest ceiling for
a 4 GB GPU is a few minutes of 1080p footage.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

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
        state = predictor.init_state(str(frames_dir), offload_video_to_cpu=not full_vram)
        try:
            predictor.add_new_points_or_box(
                inference_state=state,
                frame_idx=anchor_frame,
                obj_id=1,
                points=[[x, y]],
                labels=[1],
            )
            packed: dict[int, np.ndarray] = {}
            for frame_idx, _, logits in predictor.propagate_in_video(state):
                packed[frame_idx] = _pack_mask(logits)
            if anchor_frame > 0:
                for frame_idx, _, logits in predictor.propagate_in_video(state, reverse=True):
                    packed[frame_idx] = _pack_mask(logits)
        finally:
            del state

        target = _video_destination(source, destination)
        if target.suffix.lower() != ".mov":
            target = target.with_suffix(".mov")
        if len(packed) != len(frame_paths):
            raise VideoCutoutError(
                f"propagation covered {len(packed)} of {len(frame_paths)} frames"
            )
        _encode_frames(packed, frame_paths, width, height, info.fps, target)
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
    )


def _encode_frames(
    packed: dict[int, np.ndarray],
    frame_paths: list[Path],
    width: int,
    height: int,
    fps: float,
    destination: Path,
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
            if index in packed:
                alpha = _unpack_mask(packed[index], width, height) * 255
            else:
                alpha = np.zeros((height, width), dtype=np.uint8)
            rgba = np.dstack([rgb, alpha.astype(np.uint8)])
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
