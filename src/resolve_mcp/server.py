"""
MCP server for DaVinci Resolve integration.

Exposes tools for:
  - Project and timeline inspection
  - Media pool management
  - LUT/preset installation
  - Fusion macro generation and loading
  - Audio pre-processing (loudness, beats, ducking) via FFmpeg

Resolve must already be running (free tier requires a GUI session).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]
    except ImportError:
        print(
            "ERROR: 'mcp' package not installed. Run: pip install mcp",
            file=sys.stderr,
        )
        sys.exit(1)

from resolve_mcp import audio, presets
from resolve_mcp.resolve_bridge import (
    get_current_project,
    get_media_pool,
)

mcp = FastMCP(
    "resolve-mcp",
    instructions=(
        "Tools for controlling DaVinci Resolve 21. "
        "Resolve must be running on the same machine with the scripting API enabled. "
        "Use get_project_info first to confirm a project is open."
    ),
)


# ---------------------------------------------------------------------------
# Project / timeline inspection
# ---------------------------------------------------------------------------


@mcp.tool()
def get_project_info() -> dict:
    """
    Return name, frame rate, and timeline count of the currently open Resolve project.

    Raises RuntimeError if Resolve is not running or no project is open.
    """
    project = get_current_project()
    return {
        "name": project.GetName(),
        "frame_rate": project.GetSetting("timelineFrameRate"),
        "timeline_count": project.GetTimelineCount(),
        "current_timeline": (
            project.GetCurrentTimeline().GetName() if project.GetCurrentTimeline() else None
        ),
    }


@mcp.tool()
def list_timelines() -> list[dict]:
    """List all timelines in the current project with their name and duration."""
    project = get_current_project()
    timelines = []
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        timelines.append(
            {
                "index": i,
                "name": tl.GetName(),
                "start_frame": tl.GetStartFrame(),
                "end_frame": tl.GetEndFrame(),
                "frame_rate": tl.GetSetting("timelineFrameRate"),
            }
        )
    return timelines


@mcp.tool()
def list_media_pool_clips(folder: str = "") -> list[dict]:
    """
    List clips in the media pool root (or a named subfolder).

    Parameters
    ----------
    folder:
        Subfolder name to list. Leave empty for the root folder.

    Returns a list of clip info dicts (name, path, duration, type).
    """
    pool = get_media_pool()
    root = pool.GetRootFolder()
    target = root
    if folder:
        for sub in root.GetSubFolderList():
            if sub.GetName() == folder:
                target = sub
                break
    clips = []
    for clip in target.GetClipList():
        props = clip.GetClipProperty()
        clips.append(
            {
                "name": clip.GetName(),
                "path": props.get("File Path", ""),
                "duration": props.get("Duration", ""),
                "type": props.get("Type", ""),
            }
        )
    return clips


@mcp.tool()
def import_media(file_paths: list[str]) -> dict:
    """
    Import media files into the root of the current project's media pool.

    Parameters
    ----------
    file_paths:
        Absolute paths to media files to import.

    Returns a summary of imported and failed clips.
    """
    pool = get_media_pool()
    clips = pool.ImportMedia(file_paths)
    imported = [c.GetName() for c in clips] if clips else []
    imported_paths = {c.GetClipProperty().get("File Path", "") for c in (clips or [])}
    failed = [p for p in file_paths if p not in imported_paths]
    return {"imported": imported, "failed": failed}


# ---------------------------------------------------------------------------
# LUT / preset tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_luts() -> list[dict]:
    """List all LUT (.cube) files installed in the Resolve user LUT directory."""
    return presets.list_installed_luts()


@mcp.tool()
def find_lut(name: str) -> dict:
    """
    Search for a LUT by name (case-insensitive, partial match).

    Parameters
    ----------
    name:
        Partial or full LUT name to search for (e.g. 'cinematic', 'cyberpunk').

    Returns the first matching LUT's info, or an error if none found.
    """
    result = presets.find_lut(name)
    if result is None:
        return {"found": False, "name": name}
    return {"found": True, "name": result.stem, "path": str(result)}


@mcp.tool()
def install_lut(source_path: str, subfolder: str = "MediaStudio") -> dict:
    """
    Install a .cube LUT file into the Resolve user LUT directory.

    Parameters
    ----------
    source_path:
        Absolute path to the .cube file to install.
    subfolder:
        Optional subfolder under the Resolve LUT directory (default: 'MediaStudio').

    Returns the installed path.
    """
    dest = presets.install_lut(Path(source_path), subfolder)
    return {"installed": str(dest)}


@mcp.tool()
def install_neon_cyberpunk_macro() -> dict:
    """
    Generate and install the built-in neon/cyberpunk color grade Fusion macro.

    The macro is saved to ~/.local/share/DaVinciResolve/Fusion/Macros/MediaStudio/.
    Returns the saved path.
    """
    dest = presets.create_neon_cyberpunk_macro()
    return {"saved": str(dest)}


@mcp.tool()
def generate_fusion_macro(
    name: str,
    description: str,
    nodes_json: str,
    inputs_json: str = "[]",
    outputs_json: str = "[]",
    subfolder: str = "MediaStudio",
) -> dict:
    """
    Generate a Fusion macro from a node description and save it as a .setting file.

    Parameters
    ----------
    name:
        Macro name (no spaces), e.g. 'GlowEffect' or 'ChromaticAberration'.
    description:
        Human-readable description of what the macro does.
    nodes_json:
        JSON array of node objects, each with keys:
          id (str), type (str), params (object), pos ([x,y] optional).
        Example: [{"id":"Blur1","type":"Blur","params":{"XBlurSize":3},"pos":[0,0]}]
    inputs_json:
        JSON array of {id, source} objects mapping macro inputs to internal tool inputs.
        Example: [{"id":"Input","source":"Blur1.Input"}]
    outputs_json:
        JSON array of {id, source} objects mapping macro outputs to internal tool outputs.
        Example: [{"id":"Output","source":"Blur1.Output"}]
    subfolder:
        Subfolder under the Resolve Macros directory (default: 'MediaStudio').

    Returns the saved path and the generated .setting content.
    """
    nodes = json.loads(nodes_json)
    inputs = json.loads(inputs_json)
    outputs = json.loads(outputs_json)
    content = presets.generate_fusion_macro(name, description, nodes, inputs, outputs)
    dest = presets.save_macro(name, content, subfolder)
    return {"saved": str(dest), "content": content}


# ---------------------------------------------------------------------------
# Audio processing tools
# ---------------------------------------------------------------------------


@mcp.tool()
def measure_audio_loudness(audio_path: str) -> dict:
    """
    Measure integrated loudness (LUFS), true peak (dBTP), and LRA of an audio file.

    Parameters
    ----------
    audio_path:
        Absolute path to the audio or video file.

    Returns {integrated, true_peak, lra, threshold}.
    """
    return audio.measure_loudness(Path(audio_path))


@mcp.tool()
def normalize_audio(
    input_path: str,
    output_path: str,
    target_lufs: float = -14.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> dict:
    """
    Two-pass EBU R128 loudness normalization of an audio file.

    Parameters
    ----------
    input_path:
        Absolute path to the source audio file.
    output_path:
        Absolute path to write the normalized audio file.
    target_lufs:
        Target integrated loudness in LUFS (default: -14.0).
    target_tp:
        Target true peak in dBTP (default: -1.0).
    target_lra:
        Target loudness range in LU (default: 11.0).

    Returns {output_path, measured} with measured loudness of the result.
    """
    result = audio.normalize_loudness(
        Path(input_path), Path(output_path), target_lufs, target_tp, target_lra
    )
    measured = audio.measure_loudness(result)
    return {"output_path": str(result), "measured": measured}


@mcp.tool()
def detect_beats(audio_path: str, min_bpm: float = 60.0, max_bpm: float = 200.0) -> dict:
    """
    Detect beat timestamps and BPM in an audio file.

    Uses librosa if available; falls back to a fixed 128BPM grid via FFmpeg.

    Parameters
    ----------
    audio_path:
        Absolute path to the audio file.
    min_bpm:
        Minimum BPM to consider (default: 60).
    max_bpm:
        Maximum BPM to consider (default: 200).

    Returns {bpm, beats} where beats is a list of timestamps in seconds.
    """
    return audio.detect_beats(Path(audio_path), min_bpm, max_bpm)


@mcp.tool()
def duck_music(
    music_path: str,
    voice_path: str,
    output_path: str,
    duck_db: float = -12.0,
    attack_ms: float = 50.0,
    release_ms: float = 300.0,
) -> dict:
    """
    Apply sidechain ducking: lower music volume when voice is present.

    Parameters
    ----------
    music_path:
        Absolute path to the music bed audio file.
    voice_path:
        Absolute path to the voice/narration audio file (sidechain trigger).
    output_path:
        Absolute path to write the ducked music file.
    duck_db:
        Amount to duck music below its natural level when voice is present (default: -12 dB).
    attack_ms:
        Compressor attack time in milliseconds (default: 50).
    release_ms:
        Compressor release time in milliseconds (default: 300).

    Returns {output_path}.
    """
    result = audio.duck_music(
        Path(music_path), Path(voice_path), Path(output_path), duck_db, attack_ms, release_ms
    )
    return {"output_path": str(result)}


@mcp.tool()
def remove_silence(
    input_path: str,
    output_path: str,
    silence_threshold_db: float = -40.0,
    min_silence_duration: float = 0.5,
) -> dict:
    """
    Remove silent sections from an audio file.

    Parameters
    ----------
    input_path:
        Absolute path to the source audio file.
    output_path:
        Absolute path to write the trimmed audio file.
    silence_threshold_db:
        Sections below this level (dBFS) are considered silent (default: -40 dB).
    min_silence_duration:
        Minimum duration of silence to remove in seconds (default: 0.5).

    Returns {output_path}.
    """
    result = audio.remove_silence(
        Path(input_path), Path(output_path), silence_threshold_db, min_silence_duration
    )
    return {"output_path": str(result)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
