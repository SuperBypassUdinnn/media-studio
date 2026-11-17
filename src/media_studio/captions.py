"""Word-highlight captions: media-mcp Whisper loader -> styled .ass burn-in.

Reuses media-mcp's existing Whisper pipeline via `transcribe.py` for word-level
timestamps, groups words into readable chunks, and burns them into the source
with `.ass` active-word highlight tags positioned inside the platform safe zone.
Supports `engine="html"` via `html_motion.render_html_motion("kinetic-captions", ...)`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from media_studio import media, video
from media_studio.reframe import OUTPUT_SIZE, safe_zone_for
from media_studio.tts import WordTiming

DEFAULT_MEDIA_MCP_ROOT = Path.home() / ".clone/Github/SuperBypassUdinnn/media-mcp"

STYLES = ("promo-pop", "hormozi-bold", "clean-saas", "amv-lyric")
ENGINES = ("ass", "html")


class CaptionsError(RuntimeError):
    """Raised when captions cannot be extracted or rendered."""


@dataclass(frozen=True)
class CaptionWord:
    word: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionChunk:
    text: str
    start: float
    end: float
    words: list[CaptionWord]


def _media_mcp_root() -> Path:
    return Path(os.environ.get("MEDIA_MCP_ROOT", str(DEFAULT_MEDIA_MCP_ROOT)))


def _transcribe_script(media_mcp_root: Path) -> Path:
    return media_mcp_root / "transcribe" / ".venv" / "bin" / "python"


def _transcribe_py(media_mcp_root: Path) -> Path:
    return media_mcp_root / "transcribe" / "transcribe.py"


def _words_sidecar(media_path: Path) -> Path | None:
    """Return a transcript sidecar if it looks like word timings.

    ``.words.json`` is always accepted. A bare ``.json`` sidecar is accepted
    only when it contains ``words`` or ``segments`` keys (an arbitrary
    ``<stem>.json`` config file must not be mistaken for a transcript).
    """

    for suffix in (".words.json", ".json"):
        candidate = media_path.with_suffix(suffix)
        if not candidate.is_file():
            continue
        if suffix == ".words.json":
            return candidate
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and ("words" in payload or "segments" in payload):
            return candidate
    if media_path.with_suffix(".srt").is_file():
        return media_path.with_suffix(".srt")
    return None


def _temp_dir() -> Path:
    """Scratch directory under /tmp/opencode for captions intermediates."""

    base = Path("/tmp/opencode")
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="captions-", dir=str(base)))


def _parse_words_json(payload: object) -> list[CaptionWord]:
    if isinstance(payload, dict):
        words = payload.get("words")
        if isinstance(words, list):
            return [_word_from_dict(item) for item in words]
        segments = payload.get("segments")
        if isinstance(segments, list):
            out: list[CaptionWord] = []
            for seg in segments:
                seg_words = seg.get("words")
                if isinstance(seg_words, list):
                    out.extend(_word_from_dict(item) for item in seg_words)
            return out
    return []


def _word_from_dict(item: object) -> CaptionWord:
    if not isinstance(item, dict):
        return CaptionWord(word=str(item), start=0.0, end=0.0)
    return CaptionWord(
        word=str(item.get("text", item.get("word", ""))),
        start=float(item.get("start", 0.0)),
        end=float(item.get("end", 0.0)),
    )


_TIMESTAMP_RE = re.compile(r"(\d+):(\d+):(\d+)[.,](\d+)")


def _srt_timestamp(text: str) -> float:
    match = _TIMESTAMP_RE.search(text)
    if not match:
        return 0.0
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def _srt_to_words(srt_text: str) -> list[CaptionWord]:
    words: list[CaptionWord] = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        times = lines[1]
        start_match = _TIMESTAMP_RE.search(times.split("-->")[0])
        end_match = _TIMESTAMP_RE.search(times.split("-->")[1]) if "-->" in times else None
        start = _srt_timestamp(start_match.group(0)) if start_match else 0.0
        end = _srt_timestamp(end_match.group(0)) if end_match else start
        text = " ".join(lines[2:])
        offset = 0.0
        for word in text.split():
            duration = max(0.1, (end - start) / max(1, len(text.split())))
            words.append(
                CaptionWord(word=word, start=start + offset, end=start + offset + duration)
            )
            offset += duration
    return words


def _group_punctuation(words: list[CaptionWord]) -> list[list[CaptionWord]]:
    groups: list[list[CaptionWord]] = []
    current: list[CaptionWord] = []
    for word in words:
        current.append(word)
        if word.word.rstrip(".,;:!?") != word.word or word.word.endswith((".", "?", "!")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def group_words(
    words: list[CaptionWord] | list[WordTiming],
    *,
    max_words: int = 3,
    max_chars: int = 24,
) -> list[CaptionChunk]:
    """Group word timings into readable caption chunks."""

    normalized: list[CaptionWord] = []
    for item in words:
        if isinstance(item, CaptionWord):
            normalized.append(item)
        else:
            normalized.append(CaptionWord(word=item.word, start=item.start, end=item.end))

    if not normalized:
        return []

    chunks: list[CaptionChunk] = []
    for group in _group_punctuation(normalized):
        buffer: list[CaptionWord] = []
        buffer_chars = 0
        for word in group:
            tentative = (buffer_chars + 1 if buffer else 0) + len(word.word)
            if buffer and (len(buffer) >= max_words or tentative > max_chars):
                chunks.append(_emit(buffer))
                buffer = [word]
                buffer_chars = len(word.word)
            else:
                buffer.append(word)
                buffer_chars = tentative
        if buffer:
            chunks.append(_emit(buffer))
    return chunks


def _emit(words: list[CaptionWord]) -> CaptionChunk:
    return CaptionChunk(
        text=" ".join(w.word for w in words),
        start=words[0].start,
        end=words[-1].end,
        words=list(words),
    )


def _ass_style_for(
    style: str,
    zone: dict[str, int],
    accent_color: str = "#38BDF8",
) -> dict[str, str]:
    """Build an .ass style dict placing the caption inside the safe zone."""

    if style not in STYLES:
        raise CaptionsError(f"unknown style {style!r}; expected one of {STYLES}")

    margin_v = int(zone.get("bottom", 72))
    margin_l = int(zone.get("left", 60))
    margin_r = int(zone.get("right", 140))
    h = accent_color.lstrip("#")
    accent_bgr = f"{h[4:6]}{h[2:4]}{h[0:2]}"

    if style == "promo-pop":
        font, size, bold, italic = "Arial", 56, 1, 0
    elif style == "hormozi-bold":
        font, size, bold, italic = "Arial Black", 64, 1, 0
    elif style == "clean-saas":
        font, size, bold, italic = "Helvetica", 48, 0, 0
    else:  # amv-lyric
        font, size, bold, italic = "Impact", 44, 1, 1

    return {
        "Name": style,
        "Fontname": font,
        "Fontsize": str(size),
        "PrimaryColour": "&H00FFFFFF&",
        "SecondaryColour": f"&H00{accent_bgr}&",
        "TertiaryColour": "&H000000FF&",
        "BackColour": "&H80000000&",
        "Bold": str(bold),
        "Italic": str(italic),
        "Alignment": "2",
        "MarginL": str(margin_l),
        "MarginR": str(margin_r),
        "MarginV": str(margin_v),
        "Outline": "2",
        "Shadow": "1",
        "Spacing": "0",
    }


def _write_ass(
    chunks: list[CaptionChunk],
    style: str,
    zone: dict[str, int],
    accent_color: str,
    path: Path,
    *,
    width: int,
    height: int,
) -> None:
    style_dict = _ass_style_for(style, zone, accent_color)
    lines = [
        "[Script Info]",
        "Title: media-studio captions",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "TertiaryColour, BackColour, Bold, Italic, Alignment, MarginL, MarginR, "
        "MarginV, Outline, Shadow, Spacing",
        "Style: "
        + ", ".join(
            style_dict[key]
            for key in (
                "Name",
                "Fontname",
                "Fontsize",
                "PrimaryColour",
                "SecondaryColour",
                "TertiaryColour",
                "BackColour",
                "Bold",
                "Italic",
                "Alignment",
                "MarginL",
                "MarginR",
                "MarginV",
                "Outline",
                "Shadow",
                "Spacing",
            )
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for chunk in chunks:
        # Active-word highlight: animated \\c/\\fscx per word via \\t, relative to chunk.start.
        text_parts: list[str] = []
        accent_bgr = style_dict["SecondaryColour"][4:-1]  # strip &H00 ... &
        for word in chunk.words:
            t0 = round((word.start - chunk.start) * 1000)
            t1 = round((word.end - chunk.start) * 1000)
            t1_end = max(t0 + 30, t1)
            white = "\\c&H00FFFFFF&\\fscx100"
            accent = f"\\c&H00{accent_bgr}&\\fscx110"
            override = (
                f"{{{white}\\t({t0},{min(t0 + 30, t1_end)},{accent})"
                f"\\t({t1_end},{t1_end + 30},{white})}}"
            )
            text_parts.append(f"{override}{word.word}")
        text = " ".join(text_parts)
        lines.append(
            f"Dialogue: 0,{_ass_ts(chunk.start)},{_ass_ts(chunk.end)},"
            f"{style_dict['Name']},,0,0,0,,{text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_TIMESTAMP_RE_MS = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})")


def _ass_ts(seconds: float) -> str:
    total_ms = round(max(0.0, seconds) * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    cs = min(99, millis // 10)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _media_mcp_available(media_mcp_root: Path) -> bool:
    return _transcribe_script(media_mcp_root).is_file() and _transcribe_py(media_mcp_root).is_file()


def extract_word_timestamps(media_path: Path) -> list[CaptionWord]:
    """Extract word-level timestamps from a sidecar or media-mcp Whisper."""

    media_path = Path(media_path)
    if not media_path.is_file():
        raise CaptionsError(f"no such file: {media_path}")

    sidecar = _words_sidecar(media_path)
    if sidecar is not None:
        text = sidecar.read_text(encoding="utf-8")
        if sidecar.suffix.lower() == ".srt":
            return _srt_to_words(text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise CaptionsError(f"{sidecar} is not valid JSON: {error}") from error
        return _parse_words_json(payload)

    media_mcp_root = _media_mcp_root()
    if not _media_mcp_available(media_mcp_root):
        raise CaptionsError(
            "No words.json/SRT sidecar found and media-mcp Whisper is unavailable: "
            f"set MEDIA_MCP_ROOT or install {_transcribe_py(media_mcp_root)} "
            f"with {_transcribe_script(media_mcp_root)}."
        )

    # Invoke media-mcp transcribe.py: --num-speakers 1 --no-diarize --out <dir>.
    # All scratch lives under /tmp/opencode/captions-<pid> and is removed in finally.
    scratch = _temp_dir()
    words_out: list[CaptionWord] | None = None
    try:
        subprocess.run(
            [
                str(_transcribe_script(media_mcp_root)),
                str(_transcribe_py(media_mcp_root)),
                str(media_path),
                "--num-speakers",
                "1",
                "--no-diarize",
                "--out",
                str(scratch),
            ],
            capture_output=True,
            check=True,
        )
        stem = media_path.stem
        json_path = scratch / f"{stem}.json"
        if not json_path.is_file():
            raise CaptionsError(f"media-mcp did not write {json_path}")
        words_out = _parse_words_json(json.loads(json_path.read_text(encoding="utf-8")))
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptionsError(f"media-mcp transcribe could not run: {error}") from error
    except subprocess.CalledProcessError as error:
        raise CaptionsError(
            f"media-mcp transcribe failed: {(error.stderr or b'').decode(errors='replace')[-500:]}"
        ) from error
    finally:
        # Clean scratch even on success; ignore errors (best-effort).
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)

    if words_out is None:
        raise CaptionsError("media-mcp transcribe produced no words")
    return words_out


def render_captions(
    source: Path,
    *,
    words: list[CaptionWord] | list[WordTiming] | Path,
    style: str = "promo-pop",
    aspect: str = "9:16",
    accent_color: str = "#38BDF8",
    output: Path,
    engine: str = "ass",
    fps: int = 30,
) -> Path:
    """Render captions onto ``source`` and write ``output``.

    ``fps`` (default 30) is threaded to the HTML engine's frame math and to the
    ASS filter's ``fps=`` normalization so caption output honors the deliverable
    frame rate.
    """

    source = Path(source)
    output = Path(output)
    if engine not in ENGINES:
        raise CaptionsError(f"unknown engine {engine!r}; expected one of {ENGINES}")
    width, height = OUTPUT_SIZE[aspect]
    zone = safe_zone_for(aspect, width, height)

    resolved_words = extract_word_timestamps(words) if isinstance(words, Path) else list(words)
    chunks = group_words(resolved_words)

    if engine == "html":
        from media_studio.html_motion import HtmlMotionError, render_html_motion

        try:
            render_html_motion(
                "kinetic-captions",
                output=output,
                params={
                    "chunks": [{"text": c.text, "start": c.start, "end": c.end} for c in chunks],
                    "style": style,
                    "accent_color": accent_color,
                    "zone": zone,
                },
                width=width,
                height=height,
                fps=fps,
                duration=max((c.end for c in chunks), default=3.0),
                transparent=True,
            )
        except HtmlMotionError as error:
            raise CaptionsError(str(error)) from error
        return output

    # Write the .ass intermediate under /tmp/opencode scratch (cleaned in finally).
    scratch = _temp_dir()
    ass_path = scratch / f"{output.stem}.ass"
    try:
        _write_ass(chunks, style, zone, accent_color, ass_path, width=width, height=height)

        def build(encoder_flags: list[str]) -> list[str]:
            return [
                media.FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                f"ass={ass_path},fps={fps},format=yuv420p",
                *encoder_flags,
                "-color_range",
                "tv",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-c:a",
                "copy",
                str(output),
            ]

        try:
            video.run_ffmpeg_encode_with_fallback(
                build, output=output, label=f"ffmpeg burn captions {engine}"
            )
        except (media.MediaError, video.VideoError) as error:
            raise CaptionsError(str(error)) from error
    finally:
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)

    if not output.is_file():
        raise CaptionsError(f"ffmpeg did not write captioned output: {output}")
    return output
