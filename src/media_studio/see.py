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

import math
import re
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
    sheet: Path | None
    facts: dict[str, str]

    @property
    def summary_lines(self) -> list[str]:
        """Human-readable lines describing the peek."""

        name = self.source.name
        lines = [f"peeked {name} ({self.kind})"]
        for key, value in self.facts.items():
            lines.append(f"  {key:<9} {value}")
        if self.sheet is not None:
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
    source: Path,
    info: MediaInfo,
    columns: int,
    rows: int,
    width: int,
    sheet_path: Path,
    mode: str = "uniform",
    scene_threshold: float = 0.3,
) -> tuple[str, dict[str, str]]:
    """Build a video fact sheet; ``mode`` picks the frame sampling strategy."""

    facts = {
        "codec": f"{info.video_codec}, {info.resolution}",
        "fps": f"{info.fps:g}",
        "duration": f"{info.duration:.2f} s",
        "audio": info.audio_codec or "none",
    }
    if mode == "uniform":
        return _peek_video_uniform(source, facts, columns, rows, width, sheet_path)
    if mode == "scene":
        return _peek_video_scene(source, facts, columns, rows, width, sheet_path, scene_threshold)
    if mode == "beats":
        return _peek_video_beats(source, info, facts, columns, rows, width, sheet_path)
    raise SeeError(f"unknown mode {mode!r}; expected uniform, scene, or beats")


def _peek_video_uniform(
    source: Path, facts: dict[str, str], columns: int, rows: int, width: int, sheet_path: Path
) -> tuple[str, dict[str, str]]:
    """Existing uniform contact-sheet behaviour, alpha-aware."""

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


def _extract_frame_at(source: Path, time_s: float, out_path: Path, width: int) -> Path:
    """Extract one frame at ``time_s`` seconds, scaled to ``width`` (aspect preserved)."""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate toward zero so a seek time rounded up never lands past the
    # final frame of the file.
    seek = math.floor(time_s * 1000) / 1000
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{seek:.3f}",
            "-i",
            str(source),
            "-vframes",
            "1",
            "-vf",
            f"scale={width}:-1",
            str(out_path),
        ]
    )
    if not out_path.is_file():
        raise SeeError(f"no frame extracted at {time_s}s from {source}")
    return out_path


def _evenly_spaced(values: list[float], capacity: int) -> list[float]:
    """Return up to ``capacity`` values evenly spaced across ``values``."""

    if len(values) <= capacity:
        return list(values)
    if capacity <= 1:
        return [values[0]] if values else []
    indices = [round(index * (len(values) - 1) / (capacity - 1)) for index in range(capacity)]
    return [values[index] for index in indices]


def _scene_change_timestamps(source: Path, threshold: float) -> list[float]:
    """Detect scene-change timestamps with ffmpeg's select+showinfo filters."""

    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(source),
            "-vf",
            f"select='gt(scene,{threshold:g})',showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SeeError(f"ffmpeg scene detection failed:\n{completed.stderr.strip()[-800:]}")
    return [float(value) for value in re.findall(r"pts_time:([\d.]+)", completed.stderr)]


def _extract_audio_track(source: Path, out_path: Path) -> Path:
    """Demux the source's audio to a WAV file for rhythm analysis."""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ]
    )
    return out_path


def _tiles_at_times(
    source: Path,
    timestamps: list[float],
    width: int,
    sheet_path: Path,
    has_alpha: bool,
) -> list:
    """Extract one scaled frame per timestamp and return them as sheet tiles."""

    from PIL import Image

    frame_dir = sheet_path.parent / ".see-frames"
    tiles = []
    for index, time_s in enumerate(timestamps):
        frame_path = frame_dir / f"{index:06d}.png"
        _extract_frame_at(source, time_s, frame_path, width)
        frame = Image.open(frame_path)
        if has_alpha:
            tiles.append(_over_checkerboard(frame.convert("RGBA"), width))
        else:
            tiles.append(frame.convert("RGB"))
    return tiles


def _peek_video_scene(
    source: Path,
    facts: dict[str, str],
    columns: int,
    rows: int,
    width: int,
    sheet_path: Path,
    threshold: float,
) -> tuple[str, dict[str, str]]:
    """Sample frames at ffmpeg scene-change timestamps."""

    capacity = columns * rows
    timestamps = _scene_change_timestamps(source, threshold)
    facts["scene_threshold"] = f"{threshold:g}"
    if not timestamps:
        contact_sheet(source, sheet_path, columns=columns, rows=rows, width=width)
        facts["scene_sampling"] = "none found; fell back to uniform"
        return "video (scene, uniform fallback)", facts
    chosen = _evenly_spaced(timestamps, capacity)
    has_alpha = _pix_fmt_has_alpha(source)
    tiles = _tiles_at_times(source, chosen, width, sheet_path, has_alpha)
    sheet = _tile_grid(tiles, columns, rows)
    sheet.save(sheet_path)
    facts["scene_frames"] = str(len(chosen))
    return "video (scene)", facts


def _peek_video_beats(
    source: Path,
    info: MediaInfo,
    facts: dict[str, str],
    columns: int,
    rows: int,
    width: int,
    sheet_path: Path,
) -> tuple[str, dict[str, str]]:
    """Sample frames on the audio rhythm grid from :func:`analyze_rhythm`."""

    from .audio import analyze_rhythm

    capacity = columns * rows
    audio_path = sheet_path.parent / ".see-audio.wav"
    try:
        _extract_audio_track(source, audio_path)
        rhythm = analyze_rhythm(audio_path)
    except Exception as error:
        contact_sheet(source, sheet_path, columns=columns, rows=rows, width=width)
        facts["beat_sampling"] = f"unavailable ({error})"
        return "video (beats, uniform fallback)", facts
    finally:
        audio_path.unlink(missing_ok=True)
    beats = rhythm["beats"]
    facts["bpm"] = str(round(rhythm["bpm"]))
    facts["beats"] = str(len(beats))
    chosen = _evenly_spaced(beats, capacity)
    last_frame = max(0.0, info.duration - 1.0 / max(info.fps, 1.0))
    chosen = [min(max(t, 0.0), last_frame) for t in chosen]
    if not chosen:
        contact_sheet(source, sheet_path, columns=columns, rows=rows, width=width)
        facts["beat_sampling"] = "no beats detected; fell back to uniform"
        return "video (beats, uniform fallback)", facts
    has_alpha = _pix_fmt_has_alpha(source)
    tiles = _tiles_at_times(source, chosen, width, sheet_path, has_alpha)
    sheet = _tile_grid(tiles, columns, rows)
    sheet.save(sheet_path)
    return "video (beats)", facts


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


def _rendered_deliverable(root: Path, deliverable_id: str) -> Path | None:
    """The rendered file for a deliverable, if one exists on disk."""

    video_dir = root / "out" / "video"
    if not video_dir.is_dir():
        return None
    for candidate in sorted(video_dir.glob(f"{deliverable_id}.*")):
        if not candidate.stem.endswith("-sheet") and candidate.is_file():
            return candidate
    return None


def peek_project(
    source: Path,
    destination: Path | None = None,
    *,
    columns: int = 4,
    rows: int = 3,
    width: int = 320,
) -> PeekResult:
    """Look at a project directory: deliverables, assets, and a render sheet.

    Raises:
        SeeError: the directory has no readable project.yaml, or the sheet
            could not be written.
    """

    from .manifest import ManifestError, load_project

    try:
        project = load_project(source)
    except ManifestError as error:
        raise SeeError(f"cannot read project: {error}") from error

    if destination is None:
        sheet_path = project.root / "project.see.png"
    elif destination.is_dir() or not destination.suffix:
        destination.mkdir(parents=True, exist_ok=True)
        sheet_path = destination / "project.see.png"
    else:
        sheet_path = destination

    facts = {"project": project.slug, "title": project.title, "status": project.status}
    first_render: Path | None = None
    for deliverable in project.deliverables:
        rendered = _rendered_deliverable(project.root, deliverable.id)
        if rendered is not None and first_render is None:
            first_render = rendered
        state = f"not rendered ({deliverable.kind})"
        if rendered is not None:
            state = f"{rendered.name} ({deliverable.kind})"
        facts[deliverable.id] = state

    missing = 0
    for asset_id, relative in sorted(project.assets.items()):
        if not (project.root / relative).is_file():
            missing += 1
            facts[f"asset {asset_id}"] = f"missing: {relative}"
    facts["assets"] = f"{len(project.assets)} declared, {missing} missing"

    sheet: Path | None = sheet_path
    if first_render is not None:
        from .media import contact_sheet

        contact_sheet(first_render, sheet_path, columns=columns, rows=rows, width=width)
    else:
        sheet = None

    return PeekResult(source=source, kind="project", sheet=sheet, facts=facts)


def peek(
    source: Path,
    destination: Path | None = None,
    *,
    columns: int = 4,
    rows: int = 3,
    width: int = 320,
    mode: str = "uniform",
    scene_threshold: float = 0.3,
) -> PeekResult:
    """Build a fact sheet plus a visual sheet for ``source``.

    A directory containing ``project.yaml`` reports project state instead:
    deliverables versus renders on disk, missing assets, and a contact sheet
    of the first rendered video.

    ``mode`` only applies to plain video files: ``uniform`` (the default,
    unchanged behaviour), ``scene`` samples on ffmpeg scene changes, and
    ``beats`` samples on the audio rhythm grid. For a project directory,
    image, or audio the flag is ignored and behaviour is unchanged.

    Raises:
        SeeError: the file is missing, unreadable, or rendering failed.
    """

    if source.is_dir():
        return peek_project(source, destination, columns=columns, rows=rows, width=width)

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
        kind, facts = _peek_video(
            source,
            info,
            columns,
            rows,
            width,
            sheet_path,
            mode=mode,
            scene_threshold=scene_threshold,
        )
    elif video_like:
        kind, facts = _peek_image(source, width, sheet_path)
    else:
        kind, facts = _peek_audio(source, sheet_path)

    return PeekResult(source=source, kind=kind, sheet=sheet_path, facts=facts)
