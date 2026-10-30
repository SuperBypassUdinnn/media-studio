"""Transcode footage into a codec DaVinci Resolve can import on Linux.

The free Linux build of DaVinci Resolve ships without an H.264/H.265 decode
licence, so an MP4 straight out of a camera, a download, or our own renderer
cannot be dropped on the timeline. Resolve's own DNxHR family is always
importable, so ingest rewrites the picture into DNxHR and the sound into
uncompressed PCM inside a QuickTime container.

Two jobs share this module:

* ``probe_media`` reads what a file actually is.
* ``ingest`` rewrites one file (or a folder) into a Resolve-safe intermediate.

Planning is separated from execution so the decision about what to transcode
can be tested without running ffmpeg.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Container extensions ingest treats as footage worth probing.
FOOTAGE_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mts", ".m2ts", ".wmv", ".flv"}
)

#: Video codecs the free Linux build of Resolve cannot decode.
#:
#: H.264, H.265 and AV1 are the licensed consumer codecs Blackmagic ships only
#: in Studio on Linux. The rest are legacy or web codecs that Resolve never
#: accepted on this platform.
LINUX_UNSUPPORTED_CODECS = frozenset(
    {
        "h264",
        "hevc",
        "av1",
        "vp8",
        "vp9",
        "mpeg4",
        "mpeg2video",
        "mpeg1video",
        "wmv2",
        "wmv3",
        "vc1",
        "flv1",
    }
)

#: Video codecs Resolve imports on Linux without transcoding.
LINUX_SUPPORTED_CODECS = frozenset({"dnxhd", "prores", "cfhd", "rawvideo", "v210", "ffv1"})


class IngestError(RuntimeError):
    """Raised when footage cannot be probed or transcoded."""


@dataclass(frozen=True)
class MediaInfo:
    """What a media file actually contains."""

    path: Path
    width: int
    height: int
    fps: float
    duration: float
    video_codec: str
    audio_codec: str | None
    audio_channels: int
    sample_rate: int
    avg_fps: float = 0.0

    @property
    def has_audio(self) -> bool:
        return self.audio_codec is not None

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def is_variable_rate(self) -> bool:
        """Whether the real rate and the average rate disagree.

        Phone captures and screen recordings mix rates, which an intra-only
        codec cannot store faithfully. ``avg_fps`` is 0 when ffprobe reported
        nothing usable, in which case the source is trusted as constant.
        """

        if not self.avg_fps or not self.fps:
            return False
        return abs(self.fps - self.avg_fps) > 0.01


@dataclass(frozen=True)
class IngestProfile:
    """One target format for the intermediate."""

    name: str
    dnxhr_profile: str
    max_width: int
    max_height: int
    pix_fmt: str
    description: str


#: The three intermediates worth having. ``proxy`` is what a rough cut wants;
#: ``hqx`` is what a grade wants.
PROFILES: dict[str, IngestProfile] = {
    "proxy": IngestProfile(
        name="proxy",
        dnxhr_profile="dnxhr_lb",
        max_width=1920,
        max_height=1080,
        pix_fmt="yuv422p",
        description="DNxHR LB, at most 1080p; small and fast to scrub",
    ),
    "hq": IngestProfile(
        name="hq",
        dnxhr_profile="dnxhr_hq",
        max_width=3840,
        max_height=2160,
        pix_fmt="yuv422p",
        description="DNxHR HQ, source resolution up to UHD, 8-bit",
    ),
    "hqx": IngestProfile(
        name="hqx",
        dnxhr_profile="dnxhr_hqx",
        max_width=3840,
        max_height=2160,
        pix_fmt="yuv422p10le",
        description="DNxHR HQX, source resolution up to UHD, 10-bit for grading",
    ),
}


@dataclass(frozen=True)
class IngestPlan:
    """What ingest will do to one file, decided without running ffmpeg."""

    source: Path
    destination: Path
    profile: IngestProfile
    scale_width: int
    scale_height: int
    reasons: tuple[str, ...]
    fps: float = 0.0

    @property
    def resized(self) -> bool:
        return self.scale_width > 0

    @property
    def normalized_fps(self) -> bool:
        return self.fps > 0

    def command(self, *, overwrite: bool = True) -> list[str]:
        """The ffmpeg invocation this plan describes."""

        filters: list[str] = []
        if self.resized:
            filters.append(f"scale={self.scale_width}:{self.scale_height}")
        # Only a variable-rate source needs its rate pinned. Forcing a rate on
        # constant-rate footage would silently drop or duplicate frames, so the
        # source rate is otherwise passed through untouched.
        if self.normalized_fps:
            filters.append(f"fps={self.fps:g}")

        argv = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        if overwrite:
            argv.append("-y")
        argv += [
            "-i",
            str(self.source),
            "-map",
            "0:v:0",
            "-c:v",
            "dnxhd",
            "-profile:v",
            self.profile.dnxhr_profile,
            "-pix_fmt",
            self.profile.pix_fmt,
        ]
        if filters:
            argv += ["-vf", ",".join(filters)]
        # Audio is optional; a silent clip must still ingest.
        argv += ["-map", "0:a:0?", "-c:a", "pcm_s24le", "-ar", "48000"]
        argv += ["-movflags", "+write_colr", str(self.destination)]
        return argv


@dataclass(frozen=True)
class IngestResult:
    """The outcome of one ingest."""

    source: Path
    destination: Path
    profile: str
    skipped: bool
    size_bytes: int


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as error:  # pragma: no cover - ffmpeg is a dependency
        raise IngestError("ffmpeg is not installed") from error
    if result.returncode != 0:
        raise IngestError(f"{argv[0]} failed for {argv[-1]}:\n{result.stderr.strip()[-2000:]}")
    return result


def probe_media(path: Path) -> MediaInfo:
    """Read the stream layout of ``path``.

    Raises:
        IngestError: the file is missing, or ffprobe cannot read a video stream.
    """

    if not path.is_file():
        raise IngestError(f"no such file: {path}")

    result = _run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
    )
    try:
        payload = json.loads(result.stdout)
    except ValueError as error:
        raise IngestError(f"{path} is not readable by ffprobe: {error}") from error

    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise IngestError(f"{path} has no video stream")

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    return MediaInfo(
        path=path,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=_parse_rate(video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/1"),
        duration=float(
            (payload.get("format") or {}).get("duration") or video.get("duration") or 0.0
        ),
        video_codec=str(video.get("codec_name") or "unknown"),
        audio_codec=str(audio["codec_name"]) if audio else None,
        audio_channels=int((audio or {}).get("channels") or 0),
        sample_rate=int((audio or {}).get("sample_rate") or 0),
        avg_fps=_parse_rate(video.get("avg_frame_rate") or "0/1"),
    )


def _parse_rate(text: str) -> float:
    """Turn ``30000/1001`` into ``29.97``."""

    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            divisor = float(denominator)
            return round(float(numerator) / divisor, 3) if divisor else 0.0
        except ValueError:
            return 0.0
    try:
        return round(float(text), 3)
    except ValueError:
        return 0.0


def is_resolve_safe(info: MediaInfo) -> bool:
    """Whether Resolve on Linux imports this file untouched.

    Audio never blocks an import: Resolve reads the common audio codecs on
    every platform, and ingest always rewrites sound to PCM anyway.
    """

    return info.video_codec in LINUX_SUPPORTED_CODECS


def _scaled(info: MediaInfo, profile: IngestProfile) -> tuple[int, int]:
    """Fit the picture inside the profile's box, keeping the aspect ratio.

    Returns ``(0, 0)`` when the source already fits, which lets the plan skip
    the scale filter and avoid a needless resample.
    """

    if info.width <= profile.max_width and info.height <= profile.max_height:
        return 0, 0

    ratio = min(profile.max_width / info.width, profile.max_height / info.height)
    # DNxHR works on even dimensions; an odd edge is rejected by the encoder.
    width = int(info.width * ratio) // 2 * 2
    height = int(info.height * ratio) // 2 * 2
    return max(width, 2), max(height, 2)


def plan_ingest(
    source: Path,
    destination: Path | None = None,
    profile: str = "proxy",
) -> IngestPlan:
    """Decide what ingest will do, without touching the filesystem beyond a probe.

    Raises:
        IngestError: the profile is unknown or the source cannot be probed.
    """

    if profile not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise IngestError(f"unknown ingest profile {profile!r}; available: {known}")

    info = probe_media(source)
    target = PROFILES[profile]
    width, height = _scaled(info, target)
    reasons: list[str] = []

    if info.video_codec in LINUX_UNSUPPORTED_CODECS:
        reasons.append(f"{info.video_codec} cannot be decoded by Resolve on Linux")
    elif not is_resolve_safe(info):
        reasons.append(f"{info.video_codec} is not a codec Resolve imports on Linux")

    if info.audio_codec and info.audio_codec != "pcm_s24le":
        reasons.append(f"audio {info.audio_codec} rewritten to 24-bit PCM")

    if width:
        reasons.append(f"scaled {info.resolution} to {width}x{height} for {target.name}")

    fps = 0.0
    if info.is_variable_rate:
        fps = info.fps
        reasons.append(f"variable frame rate ({info.avg_fps:g} fps average) pinned to {fps:g} fps")

    if destination is None:
        destination = source.with_name(f"{source.stem}.{profile}.mov")

    return IngestPlan(
        source=source,
        destination=destination,
        profile=target,
        scale_width=width,
        scale_height=height,
        reasons=tuple(reasons),
        fps=fps,
    )


def ingest(
    source: Path,
    destination: Path | None = None,
    profile: str = "proxy",
    *,
    force: bool = False,
) -> IngestResult:
    """Transcode one file into a Resolve-safe intermediate.

    Args:
        source: the footage to read.
        destination: where to write; defaults to ``<stem>.<profile>.mov`` beside
            the source.
        profile: one of ``proxy``, ``hq``, ``hqx``.
        force: transcode even when the source is already Resolve-safe and no
            destination exists yet.

    Raises:
        IngestError: probing or transcoding failed.
    """

    info = probe_media(source)
    plan = plan_ingest(source, destination, profile)

    if is_resolve_safe(info) and not force:
        return IngestResult(
            source=source,
            destination=source,
            profile=profile,
            skipped=True,
            size_bytes=source.stat().st_size,
        )

    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    _run(plan.command())
    if not plan.destination.is_file():
        raise IngestError(f"ffmpeg reported success but wrote no file: {plan.destination}")

    return IngestResult(
        source=source,
        destination=plan.destination,
        profile=profile,
        skipped=False,
        size_bytes=plan.destination.stat().st_size,
    )


def ingest_folder(
    source_dir: Path,
    destination_dir: Path | None = None,
    profile: str = "proxy",
    *,
    force: bool = False,
    recursive: bool = True,
) -> list[IngestResult]:
    """Ingest every footage file under ``source_dir``.

    Non-footage files are ignored, so a mixed folder of stills, audio and
    sidecars is safe to point at.

    Raises:
        IngestError: ``source_dir`` is not a directory.
    """

    if not source_dir.is_dir():
        raise IngestError(f"not a directory: {source_dir}")

    pattern = "**/*" if recursive else "*"
    results: list[IngestResult] = []
    for candidate in sorted(source_dir.glob(pattern)):
        if not candidate.is_file() or candidate.suffix.lower() not in FOOTAGE_SUFFIXES:
            continue
        if destination_dir is None:
            destination = None
        else:
            relative = candidate.relative_to(source_dir).parent
            destination = destination_dir / relative / f"{candidate.stem}.{profile}.mov"
        results.append(ingest(candidate, destination, profile, force=force))
    return results
