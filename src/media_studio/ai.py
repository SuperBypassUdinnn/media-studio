"""
AI editing workflows for FilmCraft timelines.

Implements transcript-driven cuts, captions generation, filler-word removal,
and beat-synchronized timeline markers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audio import detect_beats
from .fcproj import snapshot_timeline
from .filmcraft import CommandResult, FilmCraftError, FilmCraftRunner


class AIEditError(FilmCraftError):
    """Raised when an AI editing operation fails."""


def import_transcript(
    project_path: Path,
    transcript: Path | dict[str, Any] | list[dict[str, Any]],
    runner: FilmCraftRunner | None = None,
    item_id: int | None = None,
) -> CommandResult:
    """
    Import word-level transcript into a FilmCraft project.

    Args:
        project_path: Path to .fcproj
        transcript: Path to JSON file, or dict/list containing words/segments
        runner: Optional FilmCraftRunner instance
        item_id: Media-pool item the transcript attaches to; resolved
            automatically from the first audio item when omitted.
    """
    if runner is None:
        runner = FilmCraftRunner()

    if isinstance(transcript, Path):
        if not transcript.is_file():
            raise AIEditError(f"Transcript file not found: {transcript}")
        with open(transcript, encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(transcript, (dict, list)):
        data = transcript
    else:
        raise AIEditError(f"Unsupported transcript type: {type(transcript)}")

    if isinstance(data, dict) and "transcript" not in data:
        data = {"transcript": data}
    elif isinstance(data, list):
        data = {"transcript": {"words": data}}

    if item_id is None:
        item_id = _first_audio_item(project_path, runner)
    params = dict(data)
    params["item"] = item_id

    return runner.exec(
        "transcript.set",
        params=params,
        project_path=project_path,
        save=True,
    )


def _first_audio_item(project_path: Path, runner: FilmCraftRunner) -> int:
    """Find the first audio media-pool item for transcript attachment."""
    try:
        info = runner.inspect(project_path, target="project")
    except FilmCraftError as error:
        raise AIEditError(f"cannot inspect project to resolve transcript item: {error}") from error
    children = (info.get("root") or {}).get("children") or []
    for child in children:
        if isinstance(child, dict) and child.get("type") == "Audio":
            item = child.get("item")
            if item is not None:
                return int(item)
    raise AIEditError(
        "no audio item in the media pool to attach the transcript to; import the voiceover first"
    )


def _normalize_transcript_times(
    transcript: Path | dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any] | list[dict[str, Any]]:
    """Convert word times that look like seconds into engine ticks.

    Engine ticks are ~2.5e11 per second, so any word end below 1e8 is
    unambiguously a second value; ticks pass through untouched.
    """
    from .fcproj import ticks_from_seconds

    data: Any = transcript
    if isinstance(transcript, Path):
        if not transcript.is_file():
            raise AIEditError(f"Transcript file not found: {transcript}")
        data = json.loads(transcript.read_text(encoding="utf-8"))

    container = data.get("transcript", data) if isinstance(data, dict) else {"words": data}
    words = container.get("words") if isinstance(container, dict) else None
    if not isinstance(words, list) or not words:
        return data

    scaled = False
    for word in words:
        if not isinstance(word, dict):
            continue
        end = word.get("end")
        if isinstance(end, (int, float)) and end < 1e8:
            word["start"] = ticks_from_seconds(float(word.get("start", 0.0)))
            word["end"] = ticks_from_seconds(float(end))
            scaled = True
    if scaled and isinstance(data, dict):
        data["transcript"] = container
    return data


def generate_captions(
    project_path: Path,
    runner: FilmCraftRunner | None = None,
) -> CommandResult:
    """Generate captions track on the timeline from the imported transcript."""
    if runner is None:
        runner = FilmCraftRunner()

    return runner.exec(
        "transcript.createCaptions",
        project_path=project_path,
        save=True,
    )


def remove_fillers(
    project_path: Path,
    fillers: list[str] | None = None,
    runner: FilmCraftRunner | None = None,
) -> CommandResult:
    """
    Remove filler words (e.g. "um", "uh") from the timeline and ripple subsequent clips.

    Args:
        project_path: Path to .fcproj
        fillers: List of words to cut (default: ["um", "uh", "like"])
        runner: Optional FilmCraftRunner instance
    """
    if runner is None:
        runner = FilmCraftRunner()

    if fillers is None:
        fillers = ["um", "uh", "like"]

    return runner.exec(
        "transcript.removeFillers",
        params={"fillers": fillers},
        project_path=project_path,
        save=True,
    )


def snap_cuts_to_beats(
    project_path: Path,
    audio_path: Path,
    marker_name: str = "Beat",
    runner: FilmCraftRunner | None = None,
) -> list[CommandResult]:
    """
    Detect beats in the music track and inject beat markers onto the FilmCraft timeline.

    Args:
        project_path: Path to .fcproj
        audio_path: Path to music file to analyze
        marker_name: Label for created markers (default: "Beat")
        runner: Optional FilmCraftRunner instance
    """
    if runner is None:
        runner = FilmCraftRunner()

    beat_info = detect_beats(audio_path)
    beat_times: list[float] = beat_info.get("beats", [])  # type: ignore[assignment]

    commands: list[dict[str, Any]] = []
    for t in beat_times:
        commands.append(
            {
                "command": "markers.add",
                "params": {
                    "seconds": t,
                    "name": marker_name,
                },
            }
        )

    if not commands:
        return []

    return runner.run_batch(commands, project_path=project_path, save=True)


def cut_by_transcript(
    project_path: Path,
    transcript: Path | dict[str, Any] | list[dict[str, Any]] | None = None,
    *,
    remove_fillers_words: bool = False,
    fillers: list[str] | None = None,
    remove_pauses: bool = False,
    min_pause: float = 0.6,
    runner: FilmCraftRunner | None = None,
) -> list[CommandResult]:
    """Import a transcript and apply AI cuts in one snapshot-guarded pass.

    D46: the current timeline is archived as a snapshot before the first
    change, so the whole chain is reversible. Passes run in a fixed order:
    import, filler removal, pause compression.

    Args:
        project_path: Path to .fcproj.
        transcript: Transcript file or data to import; None reuses what is
            already in the project.
        remove_fillers_words: Cut filler words ("um", "uh", ...) with ripple.
        fillers: Word list for filler removal (default um/uh/like).
        remove_pauses: Compress silent gaps longer than ``min_pause``.
        min_pause: Minimum pause length in seconds to compress.
        runner: Optional FilmCraftRunner instance.
    """
    if runner is None:
        runner = FilmCraftRunner()

    # Archive before the first destructive change (D46).
    snapshot_timeline(project_path)

    # Friendly input: word times in seconds are converted to engine ticks.
    transcript = _normalize_transcript_times(transcript)

    results: list[CommandResult] = []
    if transcript is not None:
        results.append(import_transcript(project_path, transcript, runner))
    if remove_fillers_words:
        results.append(remove_fillers(project_path, fillers, runner))
    if remove_pauses:
        results.append(
            runner.exec(
                "transcript.removePauses",
                params={"minSeconds": float(min_pause)},
                project_path=project_path,
                save=True,
            )
        )
    return results
