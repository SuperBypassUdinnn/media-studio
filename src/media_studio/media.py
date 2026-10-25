"""ffmpeg and ffprobe helpers: probing, loudness, speech, black frames, and contact sheets.

Every function shells out once and returns parsed data. stderr is captured and shown only in
errors, and `melt`'s duplicate-registration chatter is ignored because it is harmless noise.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
MELT = "melt"
TIMEOUT_SECONDS = 3600
OUTPUT_TAIL = 1200
LOUDNESS_JSON = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)


class MediaError(RuntimeError):
    """Raised when ffmpeg, ffprobe, or melt fails."""


@dataclass(frozen=True)
class MediaInfo:
    """What ffprobe reports about a file."""

    path: Path
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    frames: int | None
    video_codec: str | None
    audio_codec: str | None
    audio_channels: int | None


@dataclass(frozen=True)
class Loudness:
    """Integrated loudness and true peak, in LUFS and dBTP."""

    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range: float
    threshold: float
    offset: float = 0.0

    def distance_from(self, target: float) -> float:
        return abs(self.integrated_lufs - target)

    def as_loudnorm_arguments(self) -> str:
        return (
            f"measured_I={self.integrated_lufs:.2f}:measured_LRA={self.loudness_range:.2f}:"
            f"measured_TP={self.true_peak_dbtp:.2f}:measured_thresh={self.threshold:.2f}:"
            f"offset={self.offset:.2f}:linear=true"
        )


def triage_scratch() -> Path:
    """Where triage keeps throwaway measurement files."""

    directory = Path("/tmp/opencode/triage")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def run_command(
    command: list[str], *, label: str, expect_stdout: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MediaError(f"{label} could not run: {error}") from error
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-OUTPUT_TAIL:]
        raise MediaError(f"{label} failed with exit {completed.returncode}:\n{tail}")
    if expect_stdout and not completed.stdout.strip():
        raise MediaError(f"{label} produced no output")
    return completed


def probe(path: Path) -> MediaInfo:
    """Read duration, geometry, frame rate, and codecs."""

    completed = run_command(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        label=f"ffprobe {path.name}",
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise MediaError(f"ffprobe returned invalid JSON for {path}: {error.msg}") from error

    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    duration = payload.get("format", {}).get("duration")
    if duration is None and video.get("duration") is not None:
        duration = video["duration"]

    return MediaInfo(
        path=path,
        duration=float(duration or 0.0),
        width=video.get("width"),
        height=video.get("height"),
        fps=_parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        frames=_as_int(video.get("nb_frames")),
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name"),
        audio_channels=audio.get("channels"),
    )


def _parse_rate(value: object) -> float | None:
    if not value or not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        denominator_value = float(denominator)
        return round(float(numerator) / denominator_value, 4) if denominator_value else None
    except ValueError:
        return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def measure_loudness(path: Path, target: float = -14.0) -> Loudness:
    """Measure with loudnorm's first pass, which is what the second pass needs."""

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        label=f"measure loudness of {path.name}",
        expect_stdout=False,
    )
    match = LOUDNESS_JSON.search(completed.stderr or "")
    if not match:
        tail = (completed.stderr or "")[-OUTPUT_TAIL:]
        raise MediaError(f"loudnorm printed no measurement for {path.name}:\n{tail}")
    payload = json.loads(match.group(0))
    return Loudness(
        integrated_lufs=float(payload["input_i"]),
        true_peak_dbtp=float(payload["input_tp"]),
        loudness_range=float(payload["input_lra"]),
        threshold=float(payload["input_thresh"]),
        offset=float(payload.get("target_offset", 0.0)),
    )


def speech_segments(
    path: Path, *, noise_db: float = -35.0, min_silence: float = 0.4
) -> list[tuple[float, float]]:
    """Return the spans where the voice track is actually speaking."""

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence}",
            "-f",
            "null",
            "-",
        ],
        label=f"detect speech in {path.name}",
        expect_stdout=False,
    )
    text = completed.stderr or ""
    silence_starts = [float(value) for value in re.findall(r"silence_start: ([\d.]+)", text)]
    silence_ends = [float(value) for value in re.findall(r"silence_end: ([\d.]+)", text)]
    duration = probe(path).duration
    return _invert(silence_starts, silence_ends, duration)


def _invert(starts: list[float], ends: list[float], duration: float) -> list[tuple[float, float]]:
    """Turn silence spans into sounding spans."""

    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for index, start in enumerate(starts):
        if start > cursor:
            segments.append((round(cursor, 3), round(start, 3)))
        cursor = ends[index] if index < len(ends) else duration
    if cursor < duration:
        segments.append((round(cursor, 3), round(duration, 3)))
    return [(begin, end) for begin, end in segments if end - begin > 0.05]


def black_segments(
    path: Path, *, threshold: float = 0.10, minimum: float = 0.2
) -> list[tuple[float, float]]:
    """Return spans that are (nearly) black."""

    return _segments(
        path,
        f"blackdetect=d={minimum}:pix_th={threshold}",
        r"black_start:([\d.]+)",
        r"black_end:([\d.]+)",
        "detect black frames in",
    )


def frozen_segments(path: Path, *, minimum: float = 0.5) -> list[tuple[float, float]]:
    """Return spans where the picture does not move."""

    return _segments(
        path,
        f"freezedetect=n=-60dB:d={minimum}",
        r"freeze_start: ([\d.]+)",
        r"freeze_end: ([\d.]+)",
        "detect frozen frames in",
    )


def _segments(
    path: Path, filter_spec: str, start_pattern: str, end_pattern: str, label: str
) -> list[tuple[float, float]]:
    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            filter_spec,
            "-f",
            "null",
            "-",
        ],
        label=f"{label} {path.name}",
        expect_stdout=False,
    )
    text = completed.stderr or ""
    starts = [float(value) for value in re.findall(start_pattern, text)]
    if not starts:
        return []
    ends = [float(value) for value in re.findall(end_pattern, text)]
    # `freezedetect` prints a start but no end when the picture stays frozen until the file ends,
    # which is the worst case; closing the run at the media duration keeps it visible.
    duration = probe(path).duration
    return [
        (start, ends[index] if index < len(ends) else duration)
        for index, start in enumerate(starts)
    ]


def mean_luminance_at(path: Path, at: float, crop: str | None = None) -> float:
    """Average luma of a single frame, optionally of one region.

    `crop` is `W:H:X:Y`, which is how a small feature such as a title can be measured without
    drowning it in the rest of the picture.
    """

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            (f"crop={crop}," if crop else "") + "signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        label=f"read luminance at {at:.3f}s in {path.name}",
    )
    values = re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", completed.stdout or "")
    if not values:
        raise MediaError(f"no luminance reading for {path.name} at {at:.3f}s")
    return float(values[0])


def brightest_luminance_at(path: Path, at: float, crop: str | None = None) -> float:
    """Brightest pixel in a frame or region.

    A categorical signal for something small and bright, such as white text, where a mean would
    barely move.
    """

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            (f"crop={crop}," if crop else "") + "signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        label=f"read peak luminance at {at:.3f}s in {path.name}",
    )
    values = re.findall(r"lavfi\.signalstats\.YMAX=([\d.]+)", completed.stdout or "")
    if not values:
        raise MediaError(f"no peak luminance reading for {path.name} at {at:.3f}s")
    return float(values[0])


def contact_sheet(
    source: Path, destination: Path, *, columns: int = 4, rows: int = 3, width: int = 320
) -> Path:
    """Sample frames across the file into one review image."""

    info = probe(source)
    count = columns * rows
    interval = max(info.duration / (count + 1), 0.1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"fps=1/{interval:.3f},scale={width}:-1,tile={columns}x{rows}",
            "-frames:v",
            "1",
            str(destination),
        ],
        label=f"build a contact sheet for {source.name}",
        expect_stdout=False,
    )
    if not destination.is_file():
        raise MediaError(f"contact sheet was not written to {destination}")
    return destination


def mean_volume_window(path: Path, start: float, duration: float) -> float:
    """Mean volume of one window in dB, used to prove a duck actually lowered the music."""

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        label=f"measure {path.name} from {start:.3f}s",
        expect_stdout=False,
    )
    match = re.search(r"mean_volume: (-?[\d.]+) dB", completed.stderr or "")
    if not match:
        raise MediaError(f"no mean volume reported for {path.name}")
    return float(match.group(1))


def render_timeline(mlt_path: Path, intermediate: Path) -> Path:
    """Render MLT XML to an intermediate with melt, keeping it lossless for the encoder."""

    intermediate.parent.mkdir(parents=True, exist_ok=True)
    command = [
        MELT,
        str(mlt_path),
        "-consumer",
        f"avformat:{intermediate}",
        "vcodec=ffv1",
        "acodec=pcm_s16le",
        "ar=48000",
    ]
    run_command(command, label=f"melt render of {mlt_path.name}", expect_stdout=False)
    if not intermediate.is_file():
        raise MediaError(f"melt did not write {intermediate}")
    return intermediate


@dataclass(frozen=True)
class FrameStats:
    """Luma statistics of a sampled frame set: exposure, contrast, and clipping."""

    mean: float
    minimum: float
    maximum: float
    low_clip: float
    high_clip: float


def frame_stats(path: Path, *, frames: int = 12) -> FrameStats:
    """Sample frames and report exposure, contrast, and how much is clipped."""

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            f"select='not(mod(n\\,{max(frames, 1)}))',signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        label=f"read frame statistics of {path.name}",
    )
    values = re.findall(r"lavfi\.signalstats\.(YAVG|YMIN|YMAX)=([\d.]+)", completed.stdout or "")
    if not values:
        raise MediaError(f"no frame statistics for {path.name}")
    series: dict[str, list[float]] = {"YAVG": [], "YMIN": [], "YMAX": []}
    for key, value in values:
        series[key].append(float(value))
    lows = sum(1 for value in series["YMIN"] if value <= 16.5)
    highs = sum(1 for value in series["YMAX"] if value >= 234.5)
    count = max(len(series["YAVG"]), 1)
    return FrameStats(
        mean=sum(series["YAVG"]) / count,
        minimum=min(series["YMIN"]),
        maximum=max(series["YMAX"]),
        low_clip=lows / count,
        high_clip=highs / count,
    )


def edge_strength(path: Path, *, frames: int = 8) -> float:
    """Mean edge energy, a sharpness proxy that needs no Python imaging stack."""

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            f"select='not(mod(n\\,{max(frames, 1)}))',sobel,signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        label=f"measure edge strength of {path.name}",
    )
    values = [
        float(value)
        for value in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", completed.stdout or "")
    ]
    if not values:
        raise MediaError(f"no edge strength for {path.name}")
    return sum(values) / len(values)


def motion_level(path: Path, *, frames: int = 12) -> float:
    """Mean frame-to-frame difference: near zero means a held shot."""

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            f"select='not(mod(n\\,{max(frames, 1)}))',"
            "tblend=all_mode=difference,signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        label=f"measure motion in {path.name}",
    )
    values = [
        float(value)
        for value in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", completed.stdout or "")
    ]
    if not values:
        raise MediaError(f"no motion reading for {path.name}")
    return sum(values) / len(values)


def scene_cuts(path: Path, *, threshold: float = 0.3) -> list[float]:
    """Timestamps where the picture changes scene."""

    completed = run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            f"select='gt(scene\\,{threshold})',metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        label=f"detect scene cuts in {path.name}",
        expect_stdout=False,
    )
    # `metadata=print:file=-` writes to stdout, not stderr.
    shown = re.findall(r"pts_time:([\d.]+)", completed.stdout or "")
    return sorted({round(float(value), 3) for value in shown})


def perceptual_hash(path: Path, *, size: int = 8) -> str:
    """Difference hash of one frame: identical pictures hash identically."""

    # Raw pixels need bytes, not decoded text.
    completed = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-ss",
            "1",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={size + 1}:{size},format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise MediaError(f"could not hash {path.name}: {completed.stderr[-200:]!r}")
    pixels = completed.stdout
    if len(pixels) < (size + 1) * size:
        raise MediaError(f"could not read pixels from {path.name}")
    bits: list[str] = []
    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            bits.append("1" if pixels[offset + column] > pixels[offset + column + 1] else "0")
    return f"{int(''.join(bits), 2):0{size * size // 4}x}"


def shake_level(path: Path, *, scratch: Path) -> float:
    """Standard deviation of per-frame translations, a shake proxy from vidstab."""

    scratch.mkdir(parents=True, exist_ok=True)
    transforms = scratch / f"{path.stem}-transforms.trf"
    run_command(
        [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            f"vidstabdetect=shakiness=5:result={transforms}",
            "-f",
            "null",
            "-",
        ],
        label=f"measure shake in {path.name}",
        expect_stdout=False,
    )
    if not transforms.is_file():
        return 0.0
    xs: list[float] = []
    ys: list[float] = []
    for line in transforms.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 6:
            try:
                xs.append(float(parts[4]))
                ys.append(float(parts[5]))
            except ValueError:
                continue
    if len(xs) < 2:
        return 0.0

    def spread(values: list[float]) -> float:
        mean = sum(values) / len(values)
        return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5

    return round((spread(xs) + spread(ys)) / 2, 4)
