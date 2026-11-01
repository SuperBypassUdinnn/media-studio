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
from .filmcraft import CommandResult, FilmCraftError, FilmCraftRunner


class AIEditError(FilmCraftError):
    """Raised when an AI editing operation fails."""


def import_transcript(
    project_path: Path,
    transcript: Path | dict[str, Any] | list[dict[str, Any]],
    runner: FilmCraftRunner | None = None,
) -> CommandResult:
    """
    Import word-level transcript into a FilmCraft project.

    Args:
        project_path: Path to .fcproj
        transcript: Path to JSON file, or dict/list containing words/segments
        runner: Optional FilmCraftRunner instance
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

    return runner.exec(
        "transcript.set",
        params=data,
        project_path=project_path,
        save=True,
    )


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
