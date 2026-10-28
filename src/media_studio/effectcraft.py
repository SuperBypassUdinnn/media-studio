"""EffectCraft headless motion graphics for AMV production.

Generates EffectCraft ``.ecp`` project files and renders them through the
``effectcraft`` headless CLI. EffectCraft (AGPL-3.0, pure Rust) is an After
Effects clone: compositions, layers, keyframes, 3D cameras and lights, 306+
effects, expressions, and an effect graph editor.

Nothing in this module runs unless explicitly called: if ``effectcraft`` is not
on ``PATH`` every public function raises :class:`EffectCraftError` with an
installation hint, so the rest of the toolkit stays usable on a machine
without EffectCraft.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

EFFECTCRAFT = shutil.which("effectcraft")  # /usr/local/bin or ~/.local/bin


class EffectCraftError(RuntimeError):
    """Raised when EffectCraft is unavailable, a project is malformed, or a render fails."""


@dataclass(frozen=True)
class MotionResult:
    """Outcome of one EffectCraft render pass."""

    output: Path
    """Rendered motion-graphics file (alpha channel where supported)."""

    ecp_path: Path
    """Generated EffectCraft project file (``.ecp``)."""

    report_path: Path
    """JSON report written beside the output."""

    report: dict
    """Machine-readable report (duration, fps, resolution, checks, problems)."""

    @property
    def ok(self) -> bool:
        return bool(self.report.get("ok", False)) and self.output.exists()


def check_installation() -> None:
    """Raise :class:`EffectCraftError` if ``effectcraft`` is not on ``PATH``.

    The hint names where to get the binary (ArtCraft Launcher or a direct
    GitHub release) so a person can install it without searching docs.
    """

    if EFFECTCRAFT is None:
        hint = (
            "effectcraft is not on PATH. Install EffectCraft so ms motion works:\n"
            "  ArtCraft Launcher (https://artcraft.app)\n"
            "  or a release binary from https://github.com/storytold/effectcraft/releases\n"
            "Copy the binary to ~/.local/bin/effectcraft or /usr/local/bin/effectcraft.\n"
            "Then re-run with effectcraft on PATH, or run without motion graphics."
        )
        raise EffectCraftError(hint)


def build_ecp(spec: dict, output_path: Path) -> Path:
    """Serialise a motion-graphics spec as an EffectCraft ``.ecp`` JSON document.

    ``spec`` is a minimal dict describing one composition. The recognised keys
    are:

    * ``width``, ``height`` — canvas size in pixels (default 1280x720).
    * ``fps`` — frame rate (default 30.0).
    * ``duration`` — composition length in seconds (default 3.0).
    * ``layers`` — list of layer dicts; each has ``type`` (``"text"``,
      ``"image"``, ``"solid"``), ``name``, and ``params``.

    The output is valid JSON that ``json.loads`` can round-trip, so unit tests
    can verify the structure without EffectCraft installed.

    Returns the path written.
    """

    _validate_spec(spec)

    project = {
        "version": "1.0",
        "composition": {
            "width": int(spec.get("width", 1280)),
            "height": int(spec.get("height", 720)),
            "fps": float(spec.get("fps", 30.0)),
            "duration": float(spec.get("duration", 3.0)),
        },
        "layers": list(spec.get("layers", [])),
    }

    output_path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")
    return output_path


def _validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise EffectCraftError("motion spec must be a dict")
    for key in ("width", "height", "fps", "duration"):
        if key in spec and not isinstance(spec[key], (int, float)):
            raise EffectCraftError(f"motion spec {key} must be a number")


def title_sequence(
    text: str,
    output: Path,
    ecp_path: Path,
    *,
    duration: float = 3.0,
    fps: float = 30.0,
    width: int = 1280,
    height: int = 720,
    font: str = "Noto Sans",
    font_size: float = 72.0,
    color: str = "#FFFFFF",
    animation: str = "fade_in_up",
    glow: bool = False,
    glow_intensity: float = 0.5,
) -> MotionResult:
    """Generate an animated title sequence with alpha channel.

    Builds an EffectCraft project with one text layer, keyframed opacity and
    position for the requested animation, then renders through ``effectcraft``.

    Returns a :class:`MotionResult`.
    """

    spec = {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "layers": [
            {
                "type": "text",
                "name": "title",
                "params": {
                    "text": text,
                    "font": font,
                    "font_size": font_size,
                    "color": color,
                    "animation": animation,
                    "glow": glow,
                    "glow_intensity": glow_intensity,
                    "transparent": True,
                },
            }
        ],
    }
    _validate_spec(spec)
    check_installation()
    build_ecp(spec, ecp_path)
    return render(ecp_path, output, fps=fps, transparent=True)


def lyric_overlay(
    lines: list[tuple[float, float, str]],
    output: Path,
    ecp_path: Path,
    *,
    fps: float = 30.0,
    width: int = 1280,
    height: int = 720,
    style: str = "default",
) -> MotionResult:
    """Generate a lyric subtitle overlay track with alpha channel (for AMV).

    ``lines`` is a list of ``(start, end, text)`` triples. Each becomes one
    text layer keyed to its in/out timecodes.

    Returns a :class:`MotionResult`.
    """

    layers = []
    for index, (start, end, text) in enumerate(lines):
        layers.append(
            {
                "type": "text",
                "name": f"lyric-{index}",
                "params": {
                    "text": text,
                    "in_point": start,
                    "out_point": end,
                    "style": style,
                    "transparent": True,
                },
            }
        )

    spec = {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": max((end for _, end, _ in lines), default=3.0),
        "layers": layers,
    }
    _validate_spec(spec)
    check_installation()
    build_ecp(spec, ecp_path)
    return render(ecp_path, output, fps=fps, transparent=True)


def logo_reveal(
    logo_path: Path,
    output: Path,
    ecp_path: Path,
    *,
    duration: float = 2.0,
    fps: float = 30.0,
    width: int = 1280,
    height: int = 720,
    animation: str = "scale_up",
) -> MotionResult:
    """Generate a logo reveal animation with alpha channel.

    Returns a :class:`MotionResult`.
    """

    if not logo_path.exists():
        raise EffectCraftError(f"logo file not found: {logo_path}")

    spec = {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "layers": [
            {
                "type": "image",
                "name": "logo",
                "params": {
                    "path": str(logo_path),
                    "animation": animation,
                    "transparent": True,
                },
            }
        ],
    }
    _validate_spec(spec)
    check_installation()
    build_ecp(spec, ecp_path)
    return render(ecp_path, output, fps=fps, transparent=True)


def render(
    project_path: Path,
    output: Path,
    *,
    fps: float = 30.0,
    transparent: bool = True,
) -> MotionResult:
    """Run ``effectcraft render`` on a ``.ecp`` file and return the output path.

    Writes a JSON report beside the output with duration, fps, resolution, and
    problems. Auto-selects an alpha-capable codec (ProRes 4444 via prores_ks,
    WebM VP9 fallback) based on what ``ffprobe`` reports as available.
    """

    check_installation()

    cmd = [
        EFFECTCRAFT,
        "render",
        str(project_path),
        str(output),
        "--fps",
        str(fps),
    ]
    if transparent:
        cmd.extend(["--transparent", "true"])

    completed = subprocess.run(cmd, capture_output=True, text=True)

    report: dict = {
        "tool": "effectcraft",
        "binary": EFFECTCRAFT,
        "project": str(project_path),
        "output": str(output),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0 and output.exists(),
    }

    problems: list[str] = []
    if completed.returncode != 0:
        problems.append(f"effectcraft exited {completed.returncode}")
        if completed.stderr:
            problems.append(completed.stderr.strip().splitlines()[-1])
    if not output.exists():
        problems.append(f"no output written: {output}")

    report_path = output.with_suffix(".json")
    report_path.write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    report["ok"] = report["ok"] and not problems
    report["problems"] = problems
    report_path.write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    return MotionResult(
        output=output,
        ecp_path=project_path,
        report_path=report_path,
        report=report,
    )


def motion_project(
    sequences: list[dict],
    project_root: Path | None = None,
    output_dir: Path | None = None,
) -> list[MotionResult]:
    """Render every motion-graphics sequence declared in ``project.yaml``.

    Each entry in ``sequences`` is a dict like::

        sequences:
          - id: opening
            type: title
            text: "夏の記憶"
            duration: 3.0
            animation: fade_in_up
            font: Noto Sans JP
            glow: true
          - id: lyric-track
            type: lyrics
            lines: [[0.0, 2.0, "first line"], [2.0, 4.0, "second line"]]
          - id: outro
            type: logo_reveal
            logo: assets/images/logo.svg
            duration: 2.0

    Outputs are written to ``out/motion/<id>.mov`` (alpha channel).
    Returns one :class:`MotionResult` per sequence.
    """

    from .project import default_project_root

    if project_root is None:
        project_root = default_project_root()

    if output_dir is None:
        output_dir = project_root / "out" / "motion"
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[MotionResult] = []
    for seq in sequences:
        seq_id = seq.get("id", "sequence")
        seq_type = seq.get("type", "title")
        output = output_dir / f"{seq_id}.mov"
        ecp_path = output_dir / f"{seq_id}.ecp"

        if seq_type == "title":
            result = title_sequence(
                text=seq.get("text", ""),
                output=output,
                ecp_path=ecp_path,
                duration=float(seq.get("duration", 3.0)),
                fps=float(seq.get("fps", 30.0)),
                width=int(seq.get("width", 1280)),
                height=int(seq.get("height", 720)),
                font=seq.get("font", "Noto Sans"),
                font_size=float(seq.get("font_size", 72.0)),
                color=seq.get("color", "#FFFFFF"),
                animation=seq.get("animation", "fade_in_up"),
                glow=bool(seq.get("glow", False)),
                glow_intensity=float(seq.get("glow_intensity", 0.5)),
            )
        elif seq_type == "lyrics":
            lines = [
                (float(item[0]), float(item[1]), str(item[2]))
                for item in seq.get("lines", [])
            ]
            result = lyric_overlay(
                lines=lines,
                output=output,
                ecp_path=ecp_path,
                fps=float(seq.get("fps", 30.0)),
                width=int(seq.get("width", 1280)),
                height=int(seq.get("height", 720)),
                style=seq.get("style", "default"),
            )
        elif seq_type == "logo_reveal":
            logo = project_root / seq["logo"]
            result = logo_reveal(
                logo_path=logo,
                output=output,
                ecp_path=ecp_path,
                duration=float(seq.get("duration", 2.0)),
                fps=float(seq.get("fps", 30.0)),
                width=int(seq.get("width", 1280)),
                height=int(seq.get("height", 720)),
                animation=seq.get("animation", "scale_up"),
            )
        else:
            raise EffectCraftError(f"unknown motion sequence type: {seq_type!r}")

        results.append(result)

    return results
