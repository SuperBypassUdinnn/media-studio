"""Remotion motion-graphics renderer wrapper.

Thin shell around ``npx remotion render`` for the Remotion pilot:
``is_remotion_available()`` probes the CLI, and ``render_remotion_clip()``
builds and runs the ``npx remotion render`` command line, injecting composition
props as a JSON string through ``--props``. The module never renders frames
itself; it only constructs and executes the command.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NPM = "npx"
REMOTION_CLI = "remotion"
DEFAULT_DURATION = 3.0
DEFAULT_FPS = 60
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
RENDERER_SUBPATH = Path("renderers") / "remotion" / "src" / "Root.tsx"


class RemotionError(RuntimeError):
    """Raised when Remotion is unavailable or a render command fails."""


@dataclass(frozen=True)
class RemotionResult:
    """What one Remotion render produced."""

    output: Path
    composition: str
    props: dict[str, Any]
    duration: float
    fps: int
    width: int
    height: int


def is_remotion_available() -> bool:
    """Whether the ``npx remotion`` CLI can be invoked right now.

    ``npx`` must be on ``PATH`` and ``npx remotion --version`` must exit 0.
    No network install is attempted: a missing Remotion installation reports
    unavailable rather than triggering an interactive ``npx`` install.
    """

    if shutil.which(NPM) is None:
        return False
    completed = subprocess.run(
        [NPM, REMOTION_CLI, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _resolve_entry_file(entry_file: str | Path | None = None) -> Path:
    """Locate the Remotion entry file (Root.tsx) inside the checkout."""

    if entry_file is not None:
        path = Path(entry_file)
        if not path.is_file():
            raise RemotionError(f"Remotion entry file not found: {path}")
        return path
    here = Path(__file__).resolve()
    for candidate in here.parents:
        entry = candidate / RENDERER_SUBPATH
        if entry.is_file():
            return entry
    raise RemotionError(
        f"no {RENDERER_SUBPATH} found; run media-studio from its git checkout "
        f"(expected <repo>/{RENDERER_SUBPATH})"
    )


def render_remotion_clip(
    composition_id: str,
    output_path: str | Path,
    props_dict: dict[str, Any] | None = None,
    *,
    duration: float = DEFAULT_DURATION,
    fps: int = DEFAULT_FPS,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    entry_file: str | Path | None = None,
) -> RemotionResult:
    """Render ``composition_id`` to ``output_path`` with ``props_dict``.

    Builds and runs::

        npx remotion render <entry> <output> --composition <id>
            --props <json> --fps <fps> --frames <frames> --silent

    Props are serialized to JSON and passed through ``--props`` so the
    composition receives them at render time. ``width`` and ``height`` record
    the intended canvas; the composition size itself is defined in
    ``renderers/remotion/src/Root.tsx``.
    """

    if not is_remotion_available():
        raise RemotionError(
            "remotion is not available; ensure Node.js and the remotion package "
            "are installed so that 'npx remotion --version' succeeds"
        )

    entry = _resolve_entry_file(entry_file)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    frames = max(round(duration * fps), 1)
    props = props_dict or {}
    command = [
        NPM,
        REMOTION_CLI,
        "render",
        str(entry),
        str(output),
        "--composition",
        composition_id,
        "--props",
        json.dumps(props),
        "--fps",
        str(fps),
        "--frames",
        str(frames),
        "--silent",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RemotionError(
            f"remotion render failed (exit {completed.returncode}): "
            f"{(completed.stderr or completed.stdout or '').strip()}"
        )
    return RemotionResult(
        output=output,
        composition=composition_id,
        props=props,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
    )
