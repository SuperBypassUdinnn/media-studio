"""Project-less visual introspection for any media file.

``peek`` answers "what does this file look like" with two artifacts: a dict of
human-readable facts and a PNG sheet the agent can actually look at. Video gets
a frame contact sheet; videos that carry an alpha channel get their frames
composited over a checkerboard so transparency is visible; images get a
thumbnail (checkerboard when they have real transparency holes); audio gets a
waveform plus loudness numbers.

The command is deliberately project-less: no project.yaml, no staging — the
same contract as ``ms video ingest`` and ``ms image remove-bg``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .cutout import IMAGE_SUFFIXES
from .ingest import MediaInfo, probe_media
from .media import contact_sheet
from .media import probe as media_probe


class SeeError(RuntimeError):
    """Raised when a file cannot be probed or its sheet rendered."""


#: ffprobe pixel formats that carry an alpha plane. Anything else is opaque.
_ALPHA_PIXEL_FORMATS = frozenset(
    {"yuva444p10le", "yuva444p12le", "yuva420p10le", "yuva420p", "yuva444p", "pal8"}
)


@dataclass(frozen=True)
class PeekResult:
    """What one peek produced."""

    source: Path
    kind: str
    sheet: Path
    facts: dict[str, str]

    @property
    def summary_lines(self) -> list[str]:
        """Human-readable lines describing the peek."""

        name = self.source.name
        lines = [f"peeked {name} ({self.kind})"]
        for key, value in self.facts.items():
            lines.append(f"  {key:<9} {value}")
        lines.append(f"  sheet    {self.sheet}")
        return lines


def _run(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise SeeError(f"{argv[0]} is not installed") from error
    if result.returncode != 0:
        raise SeeError(f"{argv[0]} failed:\n{result.stderr.strip()[-800:]}")
    return result.stdout


def _destination(source: Path, destination: Path | None) -> Path:
    if destination is None:
        return source.with_suffix(".see.png")
    # A path without a suffix is meant as a folder even when it does not
    # exist yet; ffmpeg cannot infer an output format from a bare name.
    if destination.is_dir() or not destination.suffix:
        destination.mkdir(parents=True, exist_ok=True)
        return destination / f"{source.stem}.see.png"
    return destination


def _pix_fmt_has_alpha(path: Path) -> bool:
    """Whether the first video stream's pixel format carries alpha."""

    out = _run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=pix_fmt",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    return out.strip() in _ALPHA_PIXEL_FORMATS


def _extract_frames(source: Path, frames_dir: Path, count: int) -> list[Path]:
    """Write the first ``count`` JPEG frames of ``source`` and return them."""

    frames_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-frames:v",
            str(count),
            str(frames_dir / "%06d.png"),
        ]
    )
    return sorted(frames_dir.glob("*.png"))


def _checkerboard(size: tuple[int, int], square: int = 16) -> "object":  # noqa: UP037
    """Return a PIL image of a grey checkerboard of ``size``."""

    from PIL import Image

    width, height = size
    board = Image.new("RGB", size, (170, 170, 170))
    pixels = board.load()
    for y in range(height):
        for x in range(width):
            if ((x // square) + (y // square)) % 2 == 0:
                pixels[x, y] = (128, 128, 128)
    return board


def _over_checkerboard(rgba_frame: object, tile_width: int) -> object:
    """Composite one RGBA frame over a checkerboard, scaled to ``tile_width``."""

    frame = rgba_frame.convert("RGBA")
    scale = tile_width / frame.width
    frame = frame.resize((tile_width, max(1, int(frame.height * scale))))
    board = _checkerboard(frame.size)
    board.paste(frame, (0, 0), frame)
    return board.convert("RGB")


def _tile_grid(tiles: list[object], columns: int, rows: int) -> object:
    """Paste tiles left-to-right, top-to-bottom, into one sheet."""

    from PIL import Image

    tile_w = max(tile.width for tile in tiles)
    tile_h = max(tile.height for tile in tiles)
    sheet = Image.new("RGB", (columns * tile_w, rows * tile_h), (20, 20, 20))
    for index, tile in enumerate(tiles[: columns * rows]):
        x = (index % columns) * tile_w
        y = (index // columns) * tile_h
        sheet.paste(tile, (x, y))
    return sheet


def _peek_video(
    source: Path, info: MediaInfo, columns: int, rows: int, width: int, sheet_path: Path
) -> tuple[str, dict[str, str]]:
    facts = {
        "codec": f"{info.video_codec}, {info.resolution}",
        "fps": f"{info.fps:g}",
        "duration": f"{info.duration:.2f} s",
        "audio": info.audio_codec or "none",
    }
    has_alpha = _pix_fmt_has_alpha(source)
    if has_alpha:
        frames = _extract_frames(source, sheet_path.parent / ".see-frames", columns * rows)
        if not frames:
            raise SeeError(f"no frames could be extracted from {source}")
        tiles = []
        for frame_path in frames:
            from PIL import Image

            rgba = Image.open(frame_path).convert("RGBA")
            tiles.append(_over_checkerboard(rgba, width))
        sheet = _tile_grid(tiles, columns, rows)
        sheet.save(sheet_path)
        kind = "video (alpha, checkerboard)"
    else:
        contact_sheet(source, sheet_path, columns=columns, rows=rows, width=width)
        kind = "video"
    return kind, facts


def _peek_image(source: Path, width: int, sheet_path: Path) -> tuple[str, dict[str, str]]:
    from PIL import Image

    try:
        image = Image.open(source)
    except Exception as error:
        raise SeeError(f"cannot read image {source}: {error}") from error

    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA", "PA") else None
    has_holes = alpha is not None and alpha.getextrema()[0] < 250

    facts = {
        "mode": image.mode,
        "size": f"{image.width}x{image.height}",
    }
    if has_holes:
        composite = _over_checkerboard(image, width)
        composite.save(sheet_path)
        kind = "image (alpha, checkerboard)"
    else:
        scale = width / image.width
        scaled = image.convert("RGB").resize((width, max(1, int(image.height * scale))))
        scaled.save(sheet_path)
        kind = "image"
    return kind, facts


def _audio_sample_rate(source: Path) -> str:
    """Read the first audio stream's sample rate; 'unknown' when absent."""

    result = _run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "csv=p=0",
            str(source),
        ]
    )
    rate = result.strip().splitlines()
    return f"{rate[0]} Hz" if rate and rate[0] else "unknown"


def _peek_audio(source: Path, sheet_path: Path) -> tuple[str, dict[str, str]]:
    from .audio import measure_loudness

    info = media_probe(source)
    facts = {
        "codec": info.audio_codec or "unknown",
        "channels": str(info.audio_channels),
        "sample_rate": _audio_sample_rate(source),
        "duration": f"{info.duration:.2f} s",
    }
    try:
        loudness = measure_loudness(source)
        facts["loudness"] = f"{loudness['integrated']:.1f} LUFS"
        facts["true_peak"] = f"{loudness['true_peak']:.1f} dBTP"
    except Exception:  # loudness is a bonus, never fatal
        facts["loudness"] = "unavailable"

    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            "showwavespic=s=960x240:colors=0x7aa2f7",
            "-frames:v",
            "1",
            str(sheet_path),
        ]
    )
    return "audio (waveform)", facts


def peek(
    source: Path,
    destination: Path | None = None,
    *,
    columns: int = 4,
    rows: int = 3,
    width: int = 320,
) -> PeekResult:
    """Build a fact sheet plus a visual sheet for ``source``.

    Raises:
        SeeError: the file is missing, unreadable, or rendering failed.
    """

    if not source.is_file():
        raise SeeError(f"no such file: {source}")

    sheet_path = _destination(source, destination)
    sheet_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        info = probe_media(source)
        video_like = True
    except Exception:
        video_like = False

    if video_like and source.suffix.lower() not in IMAGE_SUFFIXES:
        kind, facts = _peek_video(source, info, columns, rows, width, sheet_path)
    elif video_like:
        kind, facts = _peek_image(source, width, sheet_path)
    else:
        kind, facts = _peek_audio(source, sheet_path)

    return PeekResult(source=source, kind=kind, sheet=sheet_path, facts=facts)
