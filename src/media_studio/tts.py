"""Multi-engine free TTS and vocal cleanup sidecars.

Engines (in ladder order): ``edge-tts`` (primary, neural, exact word-boundary
timing), ``piper`` / ``kokoro`` (local ONNX fallback), ``espeak-ng``
(last-resort, always on system ``PATH``). Output is WAV normalized to
``-16 LUFS`` plus ``words.json`` and a REUSE ``.license`` sidecar.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import audio
from .media import FFMPEG, run_command
from .sidecar import sidecar_path


class TtsError(RuntimeError):
    """Raised when speech synthesis or vocal cleanup fails."""


@dataclass(frozen=True)
class WordTiming:
    """One word with its synthesized start/end time in seconds."""

    word: str
    start: float
    end: float


@dataclass(frozen=True)
class TtsResult:
    """Result of one ``synthesize_speech`` call."""

    audio_path: Path
    words_path: Path
    sidecar_path: Path
    engine: str
    voice: str
    duration: float
    words: tuple[WordTiming, ...]


# ---------------------------------------------------------------------------
# engine availability probes (patchable by tests)
# ---------------------------------------------------------------------------


def _edge_available() -> bool:
    try:  # pragma: no cover - import probe
        import edge_tts  # noqa: F401

        return True
    except ImportError:
        return False


def _piper_available() -> bool:
    return shutil.which("piper") is not None


def _kokoro_available() -> bool:
    try:  # pragma: no cover - import probe
        import kokoro  # noqa: F401

        return True
    except ImportError:
        return False


def _espeak_available() -> bool:
    return shutil.which("espeak-ng") is not None


def _engine_ladder(engine: str) -> list[str]:
    if engine == "auto":
        return ["edge", "piper", "kokoro", "espeak-ng"]
    return [engine]


# ---------------------------------------------------------------------------
# edge-tts
# ---------------------------------------------------------------------------


async def _edge_stream(text: str, voice: str, rate: str):
    """Stream audio chunks + WordBoundary metadata from edge-tts."""

    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    chunks: list[bytes] = []
    words: list[tuple[str, float, float]] = []
    async for message in communicate.stream():
        if message["type"] == "audio":
            chunks.append(message["data"])
        elif message["type"] == "WordBoundary":
            start = message["offset"] / 1e7
            end = (message["offset"] + message["duration"]) / 1e7
            words.append((message["text"], start, end))
    return b"".join(chunks), words


def _run_edge(text: str, voice: str, rate: str) -> tuple[bytes, list[tuple[str, float, float]]]:
    return asyncio.run(_edge_stream(text, voice, rate))


# ---------------------------------------------------------------------------
# espeak-ng last resort
# ---------------------------------------------------------------------------


def _run_espeak(text: str, output_wav: Path) -> list[WordTiming]:
    """Synthesize with espeak-ng and estimate a uniform word timing grid."""

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        ["espeak-ng", "-w", str(output_wav), "-s", "160", text],
        label=f"espeak-ng {output_wav.name}",
    )
    duration = _probe_duration(output_wav)
    words = text.split()
    if not words:
        return []
    slot = duration / len(words)
    timings = []
    t = 0.0
    for word in words:
        timings.append(
            WordTiming(word=word, start=round(t, 4), end=round(min(t + slot, duration), 4))
        )
        t += slot
    return timings


def _probe_duration(path: Path) -> float:
    import json

    completed = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            str(path),
        ],
        label=f"ffprobe {path.name}",
        expect_stdout=True,
    )
    payload = json.loads(completed.stdout)
    return float(payload.get("format", {}).get("duration") or 0.0)


# ---------------------------------------------------------------------------
# sidecar
# ---------------------------------------------------------------------------


def _write_sidecar(audio_path: Path, *, provider: str, model: str, prompt: str) -> Path:
    path = sidecar_path(audio_path)
    sha = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    lines = [
        "SPDX-License-Identifier: LicenseRef-TTS-Generated",
        f"Provider: {provider}",
        f"Model: {model}",
        f"Prompt: {_escape_single_line(prompt)}",
        f"Generated-At: {datetime.now(UTC).replace(microsecond=0).isoformat()}",
        f"SHA256: {sha}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _escape_single_line(text: str) -> str:
    return " ".join(text.split()).replace("\\", "\\\\")


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def synthesize_speech(
    text: str,
    *,
    output: Path,
    voice: str = "id-ID-ArdiNeural",
    engine: str = "auto",
    rate: str = "+0%",
    words_out: Path | None = None,
    target_lufs: float = -16.0,
) -> TtsResult:
    """Synthesize ``text`` to WAV, normalized to ``target_lufs``.

    Writes ``words.json`` (default ``<output>.json``) and a REUSE ``.license``
    sidecar next to ``output``. Falls through the engine ladder on failure.
    """

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = text.strip()
    if not text:
        raise TtsError("text is empty")
    if words_out is None:
        words_out = output.with_suffix(".json")

    last_error: Exception | None = None
    for candidate in _engine_ladder(engine):
        try:
            if candidate == "edge":
                if not _edge_available():
                    raise TtsError("edge-tts not installed")
                return _synthesize_edge(text, output, voice, rate, words_out, target_lufs)
            if candidate in ("piper", "kokoro"):
                if candidate == "piper" and not _piper_available():
                    raise TtsError("piper not installed")
                if candidate == "kokoro" and not _kokoro_available():
                    raise TtsError("kokoro not installed")
                return _synthesize_piper_kokoro(
                    text, output, voice, rate, words_out, target_lufs, candidate
                )
            if candidate == "espeak-ng":
                if not _espeak_available():
                    raise TtsError("espeak-ng not on PATH")
                return _synthesize_espeak(text, output, voice, words_out, target_lufs)
            raise TtsError(f"unknown engine {candidate!r}")
        except TtsError as exc:
            last_error = exc
            continue
    raise TtsError(f"no TTS engine available ({last_error})") from last_error


def _synthesize_edge(text, output, voice, rate, words_out, target_lufs) -> TtsResult:
    raw_mp3, words = _run_edge(text, voice, rate)
    mp3_path = output.with_suffix(".edge.mp3")
    normalized = output.with_suffix(".edge.norm.wav")
    try:
        mp3_path.write_bytes(raw_mp3)
        run_command(
            [FFMPEG, "-hide_banner", "-y", "-i", str(mp3_path), "-c:a", "pcm_s16le", str(output)],
            label=f"ffmpeg mp3->wav {output.name}",
        )
        audio.normalize_loudness(output, normalized, target_lufs=target_lufs)
        normalized.replace(output)
        duration = _probe_duration(output)
        timing = [WordTiming(word=w, start=round(s, 4), end=round(e, 4)) for w, s, e in words]
        words_out.write_text(
            json.dumps(
                [{"word": t.word, "start": t.start, "end": t.end} for t in timing],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        sidecar = _write_sidecar(output, provider="edge-tts", model=voice, prompt=text)
        return TtsResult(
            audio_path=output,
            words_path=words_out,
            sidecar_path=sidecar,
            engine="edge",
            voice=voice,
            duration=duration,
            words=tuple(timing),
        )
    finally:
        mp3_path.unlink(missing_ok=True)
        normalized.unlink(missing_ok=True)


def _synthesize_piper_kokoro(
    text, output, voice, rate, words_out, target_lufs, engine
) -> TtsResult:
    # Placeholder hook: real piper/kokoro CLI invocation goes here. They are not
    # installed in the base environment, so this path raises and falls back.
    raise TtsError(f"{engine} not implemented in this build")


def _synthesize_espeak(text, output, voice, words_out, target_lufs) -> TtsResult:
    del voice  # espeak-ng uses its own default voice
    timings = _run_espeak(text, output)
    audio.normalize_loudness(output, output, target_lufs=target_lufs)
    duration = _probe_duration(output)
    words_out.write_text(
        json.dumps([{"word": t.word, "start": t.start, "end": t.end} for t in timings], indent=2)
        + "\n",
        encoding="utf-8",
    )
    sidecar = _write_sidecar(output, provider="espeak-ng", model="default", prompt=text)
    return TtsResult(
        audio_path=output,
        words_path=words_out,
        sidecar_path=sidecar,
        engine="espeak-ng",
        voice="default",
        duration=duration,
        words=tuple(timings),
    )
