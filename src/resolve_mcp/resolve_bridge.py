"""
Bridge to connect to a running DaVinci Resolve instance via DaVinciResolveScript.

DaVinci Resolve must already be running (it's a GUI app; no headless mode on free tier).
This module is imported by the MCP server tools to get a live Resolve handle.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Standard install path on Linux.
_RESOLVE_SCRIPT_PATHS = [
    Path("/opt/resolve/Developer/Scripting/Modules"),
    Path("/opt/DaVinci Resolve/Developer/Scripting/Modules"),
]

# Environment variable override (set RESOLVE_SCRIPT_PATH if installed elsewhere).
if env_path := os.environ.get("RESOLVE_SCRIPT_PATH"):
    _RESOLVE_SCRIPT_PATHS.insert(0, Path(env_path))


def _ensure_resolve_on_path() -> None:
    """Add the Resolve scripting module to sys.path if not already present."""
    for p in _RESOLVE_SCRIPT_PATHS:
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
            return
    raise RuntimeError(
        "DaVinci Resolve scripting module not found. "
        "Install Resolve or set RESOLVE_SCRIPT_PATH to its Modules directory."
    )


def get_resolve():
    """
    Return the live Resolve application object.

    Raises RuntimeError if Resolve is not running or the module is not found.
    """
    _ensure_resolve_on_path()
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "Could not import DaVinciResolveScript. "
            "Is DaVinci Resolve running and the scripting module accessible?"
        ) from exc

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError(
            "DaVinci Resolve is not running. Please open Resolve before using this MCP server."
        )
    return resolve


def get_project_manager():
    """Return the active ProjectManager."""
    return get_resolve().GetProjectManager()


def get_current_project():
    """Return the currently open project, or raise if none is open."""
    pm = get_project_manager()
    project = pm.GetCurrentProject()
    if project is None:
        raise RuntimeError(
            "No project is currently open in DaVinci Resolve. Open or create a project first."
        )
    return project


def get_current_timeline():
    """Return the current timeline of the open project, or raise if none exists."""
    project = get_current_project()
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError(
            "No timeline is currently active. Create or select a timeline in DaVinci Resolve."
        )
    return timeline


def get_media_pool():
    """Return the media pool of the currently open project."""
    return get_current_project().GetMediaPool()
