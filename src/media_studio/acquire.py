"""Asset acquisition pipeline and brief consistency checks.

The acquisition pipeline, in order, is:

1. Library check - search the catalog; on a hit, link the best-ranked entry
   into the project assets dir and return ``source="library"``.
2. External search - stubbed in this task (actual API integrations are out of
   scope); returns ``None`` so we fall through to manual fallback.
3. Manual fallback - return ``None`` and let the caller emit
   ``manual_request_message``.

Also provides ``check_brief_consistency`` (frames + BGM versus a brief) and the
``inspect_thumbnail`` / ``inspect_audio_preview`` inspection helpers.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import audio, search
from .library import (
    LIBRARY_ROOT,
    LibraryEntry,
    link_asset_to_project,
    search_assets,
)

#: The ``ms`` console script, resolved next to the running interpreter so the
#: consistency check can invoke ``ms see`` / ``ms video beats`` via subprocess.
MS_BIN = Path(sys.executable).with_name("ms")
if not MS_BIN.is_file():
    MS_BIN = Path("ms")


class AcquireError(RuntimeError):
    """Raised when the acquisition pipeline cannot complete an operation."""


@dataclass
class AcquireResult:
    """Outcome of one :func:`acquire_asset` call."""

    entry: LibraryEntry
    symlink: Path
    source: str


def _search_external(*, type: str, tags: list[str]) -> list[dict[str, Any]] | None:
    """Delegate search to external API providers via search.run_search when tags are present.

    Returns ranked candidate dicts (preferring CC0 / non-quarantined licenses) when non-empty,
    or None when empty, offline, or unmapped type.
    """

    if not tags or type not in ("image", "video", "audio", "music"):
        return None
    query = " ".join(tags)
    try:
        outcome = search.run_search(query, type)
    except search.SearchError:
        return None
    if not outcome or not outcome.candidates:
        return None
    candidates = search.rank_candidates(list(outcome.candidates), query)
    return [candidate.to_dict() for candidate in candidates]


def acquire_asset(
    *,
    type: str,
    tags: list[str],
    project_assets_dir: Path,
    project_slug: str,
    library_root: Path = LIBRARY_ROOT,
) -> AcquireResult | None:
    """Acquire one asset for a project.

    Returns an :class:`AcquireResult` when an asset is found in the library,
    or ``None`` when manual fallback is needed (the caller emits
    :func:`manual_request_message`).
    """

    project_assets_dir = Path(project_assets_dir)

    # (1) Library check - rank by how often the asset has been used.
    hits = search_assets(type=type, tags=tags, root=library_root)
    if hits:
        hits = sorted(hits, key=lambda entry: len(entry.used_in), reverse=True)
        entry = hits[0]
        symlink = link_asset_to_project(entry, project_assets_dir)
        return AcquireResult(entry=entry, symlink=symlink, source="library")

    # (2) External search - stubbed; returns None, so we fall through.
    _search_external(type=type, tags=tags)

    # (3) Manual fallback.
    return None


def manual_request_message(
    *,
    name: str,
    type: str,
    source_url: str,
    license: str,
    destination: Path,
) -> str:
    """Return a chat-ready checklist asking a human to acquire the asset."""

    return (
        "- [ ] Acquire asset manually\n"
        f"  name:    {name}\n"
        f"  type:    {type}\n"
        f"  source:  {source_url}\n"
        f"  license: {license}\n"
        f"  dest:    {destination}\n"
    )


def check_brief_consistency(
    shots: list[Path],
    bgm: Path,
    brief_text: str,
) -> list[str]:
    """Return inconsistency descriptions between shots/BGM and a brief.

    Empty list means consistent. Extracts the first frame of each shot with
    ffmpeg, inspects each frame with ``ms see``, analyses the BGM with
    ``ms video beats``, and flags simple keyword conflicts between the brief
    and the observed palette/tempo.
    """

    problems: list[str] = []
    brief_lower = brief_text.lower()

    palette_parts: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for shot in shots:
            frame = tmp_dir / f"{Path(shot).stem}-first.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    "0",
                    "-i",
                    str(shot),
                    "-vframes",
                    "1",
                    str(frame),
                ],
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [str(MS_BIN), "see", str(frame)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                palette_parts.append(result.stdout)

    beats = subprocess.run(
        [str(MS_BIN), "video", "beats", str(bgm)],
        capture_output=True,
        text=True,
    )
    bpm: float | None = None
    if beats.returncode == 0:
        match = re.search(r"bpm\s+([\d.]+)", beats.stdout)
        if match:
            bpm = float(match.group(1))

    palette = " ".join(palette_parts).lower()

    dark_brief = any(k in brief_lower for k in ("dark", "horror", "gloom", "night", "moody"))
    bright_brief = any(k in brief_lower for k in ("bright", "light", "sunny", "day", "brilliant"))
    bright_palette = any(
        k in palette for k in ("bright", "saturated", "vivid", "neon", "brilliant")
    )
    dark_palette = any(k in palette for k in ("dark", "dim", "gloomy", "murky"))
    if dark_brief and bright_palette:
        problems.append(
            "brief suggests a dark/moody tone but sampled frames are bright or saturated"
        )
    if bright_brief and dark_palette:
        problems.append("brief suggests a bright tone but sampled frames are dark or dim")

    if bpm is not None:
        fast_brief = any(k in brief_lower for k in ("fast", "energetic", "upbeat", "rapid"))
        slow_brief = any(k in brief_lower for k in ("slow", "chill", "relaxed", "sleepy"))
        if fast_brief and bpm < 80:
            problems.append(f"brief suggests a fast pace but BGM BPM is {bpm:.0f}")
        if slow_brief and bpm > 100:
            problems.append(f"brief suggests a slow pace but BGM BPM is {bpm:.0f}")

    return problems


def inspect_thumbnail(url: str, out_dir: Path) -> Path:
    """Download ``url`` into ``out_dir`` and return the local file path."""

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(url).path).name or "thumbnail.png"
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        name = "thumbnail.png"
    destination = out_dir / name
    urllib.request.urlretrieve(url, str(destination))
    return destination


def inspect_audio_preview(
    file: Path,
    out_dir: Path,
    duration: float = 10.0,
) -> dict:
    """Trim ``file`` to ``duration`` seconds and return ffprobe metadata.

    Returns ``{"duration", "sample_rate", "channels", "bpm_estimate"}``.
    ``bpm_estimate`` is ``None`` when librosa is unavailable.
    """

    file = Path(file)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    preview = out_dir / f"{file.stem}-preview.wav"

    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(file),
            "-t",
            str(duration),
            "-c:a",
            "pcm_s16le",
            str(preview),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(preview),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(probe.stdout)
    audio_stream = next(
        stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"
    )
    duration_value = float(data.get("format", {}).get("duration", 0.0))

    try:
        bpm_estimate = audio.analyze_rhythm(preview)["bpm"]
    except Exception:  # librosa missing or analysis failed
        bpm_estimate = None

    return {
        "duration": duration_value,
        "sample_rate": int(audio_stream.get("sample_rate", 0)),
        "channels": int(audio_stream.get("channels", 0)),
        "bpm_estimate": bpm_estimate,
    }
