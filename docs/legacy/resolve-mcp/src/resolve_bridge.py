"""
Bridge to connect to a running DaVinci Resolve instance via DaVinciResolveScript.

DaVinci Resolve must already be running. This module is imported by the MCP
server tools to get a live Resolve handle.

``scriptapp()`` needs three things before it can work, and only one of them is
about ``sys.path``:

* ``RESOLVE_SCRIPT_API`` — the ``Developer/Scripting`` directory, which holds
  ``Modules/DaVinciResolveScript.py`` and the ``.pyi`` stub.
* ``RESOLVE_SCRIPT_LIB`` — the ``fusionscript`` shared library, which lives in
  ``libs/Fusion`` rather than at the install root.
* the ``Modules`` directory on ``sys.path``.

Missing either variable makes ``scriptapp()`` return ``None``, which looks
exactly like "Resolve is not running" and sends the caller chasing the wrong
problem. The values are therefore resolved here, and an existing non-empty
environment variable always wins.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Install roots that hold ``Developer/Scripting`` and ``libs/Fusion``.
_RESOLVE_ROOTS = [
    Path("/opt/resolve"),
    Path("/opt/DaVinci Resolve"),
    Path("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents"),
]

#: Environment variable override (set RESOLVE_SCRIPT_PATH to a Modules directory
#: when Resolve is installed somewhere unusual).
if env_path := os.environ.get("RESOLVE_SCRIPT_PATH"):
    _RESOLVE_ROOTS.insert(0, Path(env_path).parent.parent)

#: Library file names to look for, per platform.
_LIBRARY_NAMES = ["fusionscript.so", "fusionscript.dll", "fusionscript.dylib"]


class ResolveNotRunning(RuntimeError):
    """Raised when the scripting API cannot reach a running Resolve."""


def _scripting_root() -> Path:
    """Return the ``Developer/Scripting`` directory, or raise."""
    for root in _RESOLVE_ROOTS:
        candidate = root / "Developer" / "Scripting"
        if (candidate / "Modules" / "DaVinciResolveScript.py").is_file():
            return candidate
    raise ResolveNotRunning(
        "DaVinci Resolve scripting module not found. Install Resolve or set "
        "RESOLVE_SCRIPT_PATH to its Developer/Scripting/Modules directory."
    )


def _library_path(root: Path) -> Path:
    """Return the ``fusionscript`` shared library, or raise."""
    for name in _LIBRARY_NAMES:
        for candidate in (root / "libs" / "Fusion" / name, root / name):
            if candidate.is_file():
                return candidate
    raise ResolveNotRunning(
        f"fusionscript library not found under {root}; set RESOLVE_SCRIPT_LIB to its full path."
    )


def configure_environment() -> dict[str, str]:
    """
    Populate the environment variables and ``sys.path`` the API needs.

    Existing non-empty values are preserved, so a caller that points at a
    different Resolve install keeps control.

    Returns the variables that were set, for diagnostics.
    """
    scripting = _scripting_root()
    modules = scripting / "Modules"
    if str(modules) not in sys.path:
        sys.path.insert(0, str(modules))

    applied: dict[str, str] = {}
    if not os.environ.get("RESOLVE_SCRIPT_API"):
        os.environ["RESOLVE_SCRIPT_API"] = str(scripting)
        applied["RESOLVE_SCRIPT_API"] = str(scripting)
    if not os.environ.get("RESOLVE_SCRIPT_LIB"):
        library = _library_path(scripting.parent.parent)
        os.environ["RESOLVE_SCRIPT_LIB"] = str(library)
        applied["RESOLVE_SCRIPT_LIB"] = str(library)
    return applied


def get_resolve():
    """
    Return the live Resolve application object.

    Raises:
        ResolveNotRunning: the module is missing, or Resolve is not running.
    """
    configure_environment()
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import]
    except ImportError as exc:
        raise ResolveNotRunning(
            "Could not import DaVinciResolveScript. Is DaVinci Resolve installed?"
        ) from exc

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise ResolveNotRunning(
            "DaVinci Resolve is not running, or it does not accept scripting "
            "connections. Open Resolve with a project loaded and try again."
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
        raise ResolveNotRunning(
            "No project is currently open in DaVinci Resolve. Open or create a project first."
        )
    return project


def get_current_timeline():
    """Return the current timeline of the open project, or raise if none exists."""
    project = get_current_project()
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveNotRunning(
            "No timeline is currently active. Create or select a timeline in DaVinci Resolve."
        )
    return timeline


def get_media_pool():
    """Return the media pool of the currently open project."""
    return get_current_project().GetMediaPool()
