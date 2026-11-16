"""EffectCraft headless motion graphics runner for OpenCode agent.

Drives EffectCraft (0.19.0+, pure Rust, dual MIT/Apache-2.0) through its headless
CLI (``effectcraft-cli``). Supports composition creation, 308+ effects, text layers,
keyframed properties, frame previews, and headless alpha ProRes 4444 / WebM VP9 rendering.
"""

from __future__ import annotations

import copy
import itertools
import json
import logging
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_studio.manifest import Project

logger = logging.getLogger(__name__)

# Prefer effectcraft-cli (headless engine) over the GUI binary
EFFECTCRAFT = shutil.which("effectcraft-cli") or shutil.which("effectcraft")


class EffectCraftError(RuntimeError):
    """Raised when EffectCraft is unavailable, a project is malformed, or a render fails."""


@dataclass(frozen=True)
class MotionResult:
    """Outcome of one EffectCraft render pass."""

    output: Path
    """Rendered motion-graphics file (alpha channel where supported)."""

    ecp_path: Path
    """Generated or target EffectCraft project file (``.ecproj`` or legacy ``.ecp``)."""

    report_path: Path
    """JSON report written beside the output."""

    report: dict[str, Any]
    """Machine-readable report (duration, fps, resolution, checks, problems)."""

    @property
    def ok(self) -> bool:
        return bool(self.report.get("ok", False)) and self.output.exists()


@dataclass(frozen=True)
class CompRenderResult:
    """Outcome of a :meth:`EffectCraftRunner.render_comp` run (design 5.9)."""

    output: Path
    """Rendered composition file."""

    render_path: str
    """``gpu`` or ``cpu``: the path that actually produced the output."""

    gpu_requested: bool
    """Whether a GPU render was attempted (``auto`` or ``on`` mode)."""

    render_seconds: float
    """Wall-clock time of the successful render attempt (including fallback)."""

    duration_seconds: float | None
    """Composition duration from the engine info, or None when unreadable."""

    alpha_coverage: float | None
    """Mean alpha 0..1 for RGBA renders, None for RGB or unmeasurable output."""

    report_path: Path
    """JSON report written beside the output (``<output>.render.json``)."""

    @property
    def ok(self) -> bool:
        return self.output.exists()


def check_installation(cli_path: str | Path | None = None) -> Path:
    """Raise :class:`EffectCraftError` if ``effectcraft-cli`` is not on ``PATH``."""
    resolved = shutil.which(str(cli_path)) if cli_path else EFFECTCRAFT
    if resolved is None:
        hint = (
            "effectcraft is not on PATH (effectcraft-cli missing). Please install EffectCraft:\n"
            "  Binary tarball: https://github.com/storytold/effectcraft/releases\n"
            "  Extract to ~/.local/opt/effectcraft-0.19.0/bin/ and symlink into "
            "~/.local/bin/effectcraft-cli.\n"
            "Run 'ms doctor' to verify."
        )
        raise EffectCraftError(hint)
    return Path(resolved)


class EffectCraftRunner:
    """Headless driver for EffectCraft 0.19.0+."""

    def __init__(self, cli_path: str | Path | None = None, default_timeout: int = 60):
        self.cli_path = check_installation(cli_path)
        self.default_timeout = default_timeout
        self._commands_cache: list[dict[str, Any]] | None = None

    def get_commands_catalog(self, cached: bool = True) -> list[dict[str, Any]]:
        """Retrieve the catalog of engine commands supported by EffectCraft."""
        if cached and self._commands_cache is not None:
            return self._commands_cache

        cmd = [str(self.cli_path), "commands", "--json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        catalog = json.loads(proc.stdout)
        if cached:
            self._commands_cache = catalog
        return catalog

    def inspect(self, project_path: Path) -> dict[str, Any]:
        """Inspect project structure and active composition via 'info --json'."""
        if not project_path.exists():
            raise EffectCraftError(f"Project not found: {project_path}")

        argv = [str(self.cli_path), "info", "--project", str(project_path), "--json"]
        proc = subprocess.run(argv, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def exec(
        self,
        command_id: str,
        params: dict[str, Any] | None = None,
        project_path: Path | None = None,
        save: bool = True,
        save_as: Path | None = None,
        empty: bool = False,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute one engine command via 'exec'."""
        argv = [str(self.cli_path)]
        if project_path:
            argv.extend(["--project", str(project_path)])
            if save:
                argv.append("--save")
        elif empty:
            argv.append("--empty")

        if save_as:
            argv.extend(["--save-as", str(save_as)])

        argv.extend(["exec", command_id])
        if params is not None:
            argv.extend(["--params", json.dumps(params)])

        effective_timeout = timeout or self.default_timeout
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=effective_timeout)
        if proc.returncode != 0:
            raise EffectCraftError(
                f"Command '{command_id}' failed ({proc.returncode}): {proc.stderr.strip()}"
            )

        try:
            return json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            return {"raw": proc.stdout.strip()}

    def run_commands(
        self,
        commands: list[dict[str, Any]],
        project_path: Path | None = None,
        save: bool = True,
        save_as: Path | None = None,
        empty: bool = False,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run multiple commands sequentially using 'run --commands -'."""
        argv = [str(self.cli_path)]
        if project_path:
            argv.extend(["--project", str(project_path)])
            if save:
                argv.append("--save")
        elif empty:
            argv.append("--empty")

        if save_as:
            argv.extend(["--save-as", str(save_as)])

        argv.extend(["run", "--commands", "-"])

        lines = [json.dumps(cmd) for cmd in commands]
        stdin_content = "\n".join(lines) + "\n"

        effective_timeout = timeout or (self.default_timeout * max(1, len(commands)))
        proc = subprocess.run(
            argv,
            input=stdin_content,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        if proc.returncode != 0:
            raise EffectCraftError(
                f"run_commands failed ({proc.returncode}): {proc.stderr.strip()}"
            )

        results: list[dict[str, Any]] = []
        for line in proc.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                results.append({"raw": line})
        return results

    def render_comp(
        self,
        project_path: Path,
        output_file: Path,
        comp: str | None = None,
        format: str = "prores",
        channels: str = "rgba",
        prores_profile: str = "4444",
        fps: float = 30.0,
        timeout: int = 180,
        gpu: str = "auto",
    ) -> CompRenderResult:
        """Render a composition to disk, defaulting to transparent ProRes 4444.

        ``gpu`` selects the render path (design 5.9): ``auto`` tries the GPU
        first and falls back to CPU, ``on`` is GPU-only, ``off`` is CPU-only.
        The returned result carries the render path, render time, composition
        duration, and (for RGBA) mean alpha coverage; a sidecar report is
        written next to the output.
        """
        if gpu not in ("auto", "on", "off"):
            raise EffectCraftError(f"invalid gpu mode {gpu!r}; use auto, on, or off")
        if not project_path.exists():
            raise EffectCraftError(f"Project not found: {project_path}")

        output_file.parent.mkdir(parents=True, exist_ok=True)
        base = [
            str(self.cli_path),
            "--project",
            str(project_path),
            "render",
            "--out",
            str(output_file),
            "--format",
            format,
            "--channels",
            channels,
            "--fps",
            str(fps),
        ]
        if comp:
            base.extend(["--comp", comp])
        if format == "prores":
            base.extend(["--prores", prores_profile])

        # `--gpu` is a global option, so it goes before the subcommand.
        attempts: list[tuple[str, list[str]]] = []
        if gpu in ("auto", "on"):
            attempts.append(("gpu", [*base[:3], "--gpu", *base[3:]]))
        if gpu in ("auto", "off"):
            attempts.append(("cpu", base))

        started = time.monotonic()
        last_error: EffectCraftError | None = None
        render_path = "cpu"
        for label, argv in attempts:
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                last_error = EffectCraftError(f"render timed out after {timeout}s ({label})")
                continue
            if proc.returncode == 0 and output_file.exists():
                render_path = label
                break
            detail = proc.stderr.strip() or f"no output written to {output_file}"
            last_error = EffectCraftError(
                f"render failed ({label}, exit {proc.returncode}): {detail}"
            )
        else:
            assert last_error is not None
            raise last_error
        render_seconds = round(time.monotonic() - started, 3)

        duration = self._comp_duration(project_path)
        alpha = self._alpha_coverage(output_file) if channels == "rgba" else None
        result = CompRenderResult(
            output=output_file,
            render_path=render_path,
            gpu_requested=gpu in ("auto", "on"),
            render_seconds=render_seconds,
            duration_seconds=duration,
            alpha_coverage=alpha,
            report_path=output_file.with_suffix(".render.json"),
        )
        result.report_path.write_text(
            json.dumps(
                {
                    "tool": "effectcraft",
                    "project": str(project_path),
                    "comp": comp,
                    "output": str(output_file),
                    "format": format,
                    "channels": channels,
                    "fps": fps,
                    "gpuMode": gpu,
                    "renderPath": render_path,
                    "gpuRequested": result.gpu_requested,
                    "renderSeconds": render_seconds,
                    "durationSeconds": duration,
                    "alphaCoverage": alpha,
                    "ok": result.ok,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return result

    def _comp_duration(self, project_path: Path) -> float | None:
        """Composition duration from the engine's info, or None when unreadable."""
        try:
            data = self.inspect(project_path)
        except Exception:
            return None
        duration = (data.get("activeComp") or {}).get("duration")
        try:
            return float(duration) if duration is not None else None
        except (TypeError, ValueError):
            return None

    def _alpha_coverage(self, output: Path, timeout: int = 120) -> float | None:
        """Mean alpha of a rendered file as 0..1, via alphaextract + signalstats.

        Normalised per frame by YMAX so 8- and 12-bit alpha decode identically.
        Returns None when ffmpeg cannot measure the file.
        """
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(output),
            "-vf",
            "alphaextract,signalstats,metadata=print",
            "-f",
            "null",
            "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        stderr = proc.stderr
        yavgs = [float(v) for v in re.findall(r"YAVG[=:]\s*([0-9.]+)", stderr)]
        ymaxs = [float(v) for v in re.findall(r"YMAX[=:]\s*([0-9.]+)", stderr)]
        if not yavgs:
            return None
        ratios: list[float] = []
        for index, yavg in enumerate(yavgs):
            ymax = ymaxs[index] if index < len(ymaxs) else 255.0
            if ymax <= 0:
                continue
            ratios.append(yavg / ymax)
        if not ratios:
            return None
        return round(min(sum(ratios) / len(ratios), 1.0), 4)

    def render_frame(
        self,
        project_path: Path,
        output_png: Path,
        seconds: float = 0.0,
        comp: str | None = None,
        transparent: bool = True,
        scale: float = 1.0,
        timeout: int = 15,
    ) -> Path:
        """Render a single frame to PNG with transparent alpha background."""
        if not project_path.exists():
            raise EffectCraftError(f"Project not found: {project_path}")

        output_png.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            str(self.cli_path),
            "--project",
            str(project_path),
            "render-frame",
            "--time",
            str(seconds),
            "--out",
            str(output_png),
            "--scale",
            str(scale),
        ]
        if comp:
            argv.extend(["--comp", comp])
        if transparent:
            argv.append("--transparent")

        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 or not output_png.exists():
            raise EffectCraftError(
                f"Frame render failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return output_png


# =============================================================================
# Legacy Spec & Helper Functions (Backward Compatibility)
# =============================================================================


def _validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise EffectCraftError("motion spec must be a dict")
    for key in ("width", "height", "fps", "duration"):
        if key in spec and not isinstance(spec[key], (int, float)):
            raise EffectCraftError(f"motion spec {key} must be a number")


def build_ecp(spec: dict, output_path: Path) -> Path:
    """Serialise a motion-graphics spec as an EffectCraft JSON document."""
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")
    return output_path


def _compile_spec_to_ecproj(spec_path: Path, ecproj_path: Path) -> None:
    """Compile a high-level spec dictionary into a native .ecproj project."""
    ecproj_path.parent.mkdir(parents=True, exist_ok=True)
    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
    comp_info = spec_data.get("composition", {})

    width = int(comp_info.get("width", 1280))
    height = int(comp_info.get("height", 720))
    fps = float(comp_info.get("fps", 30.0))
    duration = float(comp_info.get("duration", 3.0))

    cmds: list[dict[str, Any]] = [
        {
            "id": "comp.new",
            "params": {
                "name": "Main",
                "width": width,
                "height": height,
                "fps": fps,
                "duration": duration,
            },
        }
    ]

    for layer in spec_data.get("layers", []):
        ltype = layer.get("type")
        params = layer.get("params", {})
        if ltype == "text":
            text_params: dict[str, Any] = {
                "text": params.get("text", ""),
                "size": float(params.get("font_size", 64.0)),
            }
            if layer.get("name"):
                text_params["name"] = layer["name"]
            # Comp-space anchor: [x, y] in pixels. Without it EffectCraft centres the layer.
            if params.get("position") is not None:
                text_params["position"] = [float(v) for v in params["position"]]
            for optional in (
                "fill",
                "font",
                "justify",
                "tracking",
                "stroke",
                "strokeWidth",
                "box",
            ):
                if params.get(optional) is not None:
                    text_params[optional] = params[optional]
            cmds.append({"id": "layer.newText", "params": text_params})
            layer_name = text_params.get("name")
            if layer_name and params.get("keyframes"):
                cmds.extend(_keyframe_commands(layer_name, params["keyframes"]))
            # Apply outer glow when the layer requests it (AMV cyber titles,
            # etc.). glow_intensity is recorded in the spec; the engine's
            # outerGlow command carries no intensity parameter, so it is
            # applied at the default strength.
            if params.get("glow"):
                layer_name = text_params.get("name")
                if layer_name:
                    cmds.append({"id": "layer.style.outerGlow", "params": {"layers": [layer_name]}})
        elif ltype == "image":
            image_path = params.get("path")
            if image_path:
                cmds.append(
                    {
                        "id": "file.import",
                        "params": {
                            "paths": [image_path],
                            "addToComp": True,
                        },
                    }
                )
        elif ltype == "solid":
            solid_params: dict[str, Any] = {"name": layer.get("name", "Solid")}
            for optional in ("color", "width", "height"):
                if params.get(optional) is not None:
                    solid_params[optional] = params[optional]
            cmds.append({"id": "layer.newSolid", "params": solid_params})
            layer_name = solid_params.get("name", "Solid")
            if params.get("keyframes"):
                cmds.extend(_keyframe_commands(layer_name, params["keyframes"]))
            if params.get("opacity") is not None and not params.get("keyframes", {}).get("opacity"):
                layer_name = solid_params.get("name", "Solid")
                raw_op = float(params["opacity"])
                opacity_val = raw_op * 100.0 if raw_op <= 1.0 else raw_op
                cmds.append(
                    {
                        "id": "layer.setTransform",
                        "params": {"layers": [layer_name], "prop": "opacity", "value": opacity_val},
                    }
                )

    runner = EffectCraftRunner()
    runner.run_commands(cmds, empty=True, save_as=ecproj_path)


def render(
    project_path: Path,
    output: Path,
    *,
    fps: float = 30.0,
    transparent: bool = True,
) -> MotionResult:
    """Run ``effectcraft render`` on a project file and return the output path.

    Handles both native ``.ecproj`` projects and spec JSON files automatically.
    """
    check_installation()

    # If project_path is a legacy spec (.ecp or json missing schema), compile to native .ecproj
    actual_project = project_path
    is_spec = project_path.suffix in (".ecp", ".json")
    if not is_spec and project_path.exists():
        try:
            content = project_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict) and "schema" not in data:
                is_spec = True
        except Exception:
            pass

    if is_spec:
        compiled_proj = project_path.with_name(project_path.stem + "_compiled.ecproj")
        try:
            _compile_spec_to_ecproj(project_path, compiled_proj)
            actual_project = compiled_proj
        except Exception as exc:
            logger.warning("Failed to compile spec to ecproj: %s; trying raw file", exc)

    cmd = [
        str(EFFECTCRAFT),
        "--project",
        str(actual_project),
        "render",
        "--out",
        str(output),
        "--fps",
        str(fps),
    ]

    if transparent:
        cmd.extend(["--format", "prores", "--channels", "rgba", "--prores", "4444"])
    else:
        cmd.extend(["--format", "h264", "--channels", "rgb"])

    start_time = time.monotonic()
    completed = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - start_time

    ok = completed.returncode == 0 and output.exists()
    report: dict[str, Any] = {
        "tool": "effectcraft",
        "binary": str(EFFECTCRAFT),
        "project": str(project_path),
        "output": str(output),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "duration_sec": elapsed,
        "ok": ok,
        "problems": [],
    }

    if completed.returncode != 0:
        report["problems"].append(f"effectcraft exited {completed.returncode}")
        if completed.stderr:
            report["problems"].append(completed.stderr.strip().splitlines()[-1])
    if not output.exists():
        report["problems"].append(f"no output written: {output}")

    report_path = output.with_suffix(".json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return MotionResult(
        output=output,
        ecp_path=project_path,
        report_path=report_path,
        report=report,
    )


# ---------------------------------------------------------------------------
# AMV motion & typography presets (Task 5)
# ---------------------------------------------------------------------------

#: AMV-tailored motion graphics presets, all 1920x1080 at 60 fps.
#: Each entry is a motion spec understood by :func:`build_ecp` / :func:`render`
#: (composition geometry plus a list of text/solid layers). render_amv_preset
#: deep-copies the chosen preset and injects text, duration, fps, width and
#: height before rendering it as transparent ProRes 4444 RGBA.
AMV_MOTION_PRESETS: dict[str, dict[str, Any]] = {
    "amv-title-cyber": {
        "fps": 60.0,
        "width": 1920,
        "height": 1080,
        "duration": 3.0,
        "layers": [
            {
                "type": "text",
                "name": "title",
                "params": {
                    "text": "",
                    "font": "Noto Sans",
                    "font_size": 120.0,
                    "color": "#00FFFF",
                    "glow": True,
                    "glow_intensity": 0.8,
                    "tracking": 6.0,
                    "position": [960.0, 540.0],
                    "transparent": True,
                },
            },
        ],
    },
    "amv-hud-accent": {
        "fps": 60.0,
        "width": 1920,
        "height": 1080,
        "duration": 3.0,
        "layers": [
            {
                "type": "solid",
                "name": "grid-horizontal-1",
                "params": {
                    "color": "#00FFFF",
                    "width": 1920,
                    "height": 1,
                    "opacity": 0.25,
                    "position": [0.0, 270.0],
                },
            },
            {
                "type": "solid",
                "name": "grid-horizontal-2",
                "params": {
                    "color": "#00FFFF",
                    "width": 1920,
                    "height": 1,
                    "opacity": 0.25,
                    "position": [0.0, 810.0],
                },
            },
            {
                "type": "solid",
                "name": "grid-vertical-1",
                "params": {
                    "color": "#00FFFF",
                    "width": 1,
                    "height": 1080,
                    "opacity": 0.25,
                    "position": [640.0, 0.0],
                },
            },
            {
                "type": "solid",
                "name": "grid-vertical-2",
                "params": {
                    "color": "#00FFFF",
                    "width": 1,
                    "height": 1080,
                    "opacity": 0.25,
                    "position": [1280.0, 0.0],
                },
            },
            {
                "type": "text",
                "name": "crosshair-top-left",
                "params": {
                    "text": "[ + ]",
                    "font": "Noto Sans",
                    "font_size": 24.0,
                    "color": "#00FFFF",
                    "position": [40.0, 40.0],
                    "transparent": True,
                },
            },
            {
                "type": "text",
                "name": "crosshair-top-right",
                "params": {
                    "text": "[ + ]",
                    "font": "Noto Sans",
                    "font_size": 24.0,
                    "color": "#00FFFF",
                    "position": [1840.0, 40.0],
                    "transparent": True,
                },
            },
            {
                "type": "text",
                "name": "crosshair-bottom-left",
                "params": {
                    "text": "[ + ]",
                    "font": "Noto Sans",
                    "font_size": 24.0,
                    "color": "#00FFFF",
                    "position": [40.0, 1020.0],
                    "transparent": True,
                },
            },
            {
                "type": "text",
                "name": "crosshair-bottom-right",
                "params": {
                    "text": "[ + ]",
                    "font": "Noto Sans",
                    "font_size": 24.0,
                    "color": "#00FFFF",
                    "position": [1840.0, 1020.0],
                    "transparent": True,
                },
            },
        ],
    },
    "amv-drop-shutter": {
        "fps": 60.0,
        "width": 1920,
        "height": 1080,
        "duration": 3.0,
        "layers": [
            {
                "type": "solid",
                "name": "shutter-left",
                "params": {
                    "color": "#000000",
                    "width": 960,
                    "height": 1080,
                    "position": [0.0, 0.0],
                    "animation": "shutter_open",
                },
            },
            {
                "type": "solid",
                "name": "shutter-right",
                "params": {
                    "color": "#000000",
                    "width": 960,
                    "height": 1080,
                    "position": [960.0, 0.0],
                    "animation": "shutter_open",
                },
            },
            {
                "type": "text",
                "name": "title",
                "params": {
                    "text": "",
                    "font": "Noto Sans",
                    "font_size": 140.0,
                    "color": "#FFFFFF",
                    "position": [960.0, 540.0],
                    "transparent": True,
                },
            },
        ],
    },
}


#: Default colour tokens used when no project brand is supplied.
PROMO_DEFAULT_PALETTE: dict[str, str] = {
    "primary": "#3b82f6",
    "accent": "#22d3ee",
    "ink": "#f8fafc",
}
PROMO_DEFAULT_FONT = "Noto Sans"

#: Promo-tailored motion graphics presets. Geometry is expressed as fractions of
#: the canvas (positions, widths, heights and font sizes in ``[0, 1]``) so every
#: preset scales cleanly from ``width`` and ``height`` for both 1080x1920 (9:16)
#: and 1920x1080 (16:9). Colours and fonts reference brand tokens
#: (``{primary}``, ``{accent}``, ``{ink}``, ``{heading}``, ``{body}``) which
#: :func:`render_promo_preset` resolves from ``project.brand`` when a project is
#: supplied, falling back to the defaults above otherwise.
PROMO_MOTION_PRESETS: dict[str, dict[str, Any]] = {
    "promo-hook-slam": {
        "fps": 30.0,
        "width": 1080,
        "height": 1920,
        "duration": 3.0,
        "layers": [
            {
                "type": "solid",
                "name": "hook-pill",
                "params": {
                    "color": "{accent}",
                    "opacity": 0.10,
                    "width": 0.92,
                    "height": 0.06,
                    "position": [0.5, 0.28],
                    "keyframes": {
                        "scale": [
                            {"t": 0.0, "v": 0.5, "ease": "back-out"},
                            {"t": 0.30, "v": 1.0, "ease": "back-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.10, "v": 0.10, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "title",
                "text_role": "title",
                "params": {
                    "text": "{text}",
                    "font": "{heading}",
                    "font_size": 0.075,
                    "color": "{ink}",
                    "position": [0.5, 0.42],
                    "keyframes": {
                        "scale": [
                            {"t": 0.0, "v": 1.5, "ease": "back-out"},
                            {"t": 0.15, "v": 0.96, "ease": "back-out"},
                            {"t": 0.35, "v": 1.0, "ease": "back-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "linear"},
                            {"t": 0.10, "v": 1.0, "ease": "linear"},
                            {"t": 2.80, "v": 1.0, "ease": "linear"},
                            {"t": 3.00, "v": 0.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
        ],
    },
    "promo-kinetic-callout": {
        "fps": 30.0,
        "width": 1080,
        "height": 1920,
        "duration": 3.0,
        "layers": [
            {
                "type": "solid",
                "name": "accent-rule",
                "params": {
                    "color": "{accent}",
                    "width": 0.005,
                    "height": 0.35,
                    "position": [0.12, 0.42],
                    "keyframes": {
                        "scale": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.25, "v": 1.0, "ease": "expo-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.25, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "title",
                "text_role": "title",
                "params": {
                    "text": "{text}",
                    "font": "{heading}",
                    "font_size": 0.050,
                    "color": "{ink}",
                    "position": [0.15, 0.40],
                    "keyframes": {
                        "position": [
                            {"t": 0.0, "v": [0.10, 0.40], "ease": "expo-out"},
                            {"t": 0.30, "v": [0.15, 0.40], "ease": "expo-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.30, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "subhead",
                "text_role": "subtext",
                "params": {
                    "text": "{subtext}",
                    "font": "{body}",
                    "font_size": 0.030,
                    "color": "{primary}",
                    "position": [0.25, 0.49],
                    "keyframes": {
                        "opacity": [
                            {"t": 0.38, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.60, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
        ],
    },
    "promo-metric-badge": {
        "fps": 30.0,
        "width": 1080,
        "height": 1920,
        "duration": 3.0,
        "layers": [
            {
                "type": "solid",
                "name": "badge-pill",
                "params": {
                    "color": "{primary}",
                    "opacity": 0.92,
                    "width": 0.70,
                    "height": 0.17,
                    "position": [0.5, 0.40],
                    "keyframes": {
                        "scale": [
                            {"t": 0.0, "v": 0.0, "ease": "back-out"},
                            {"t": 0.30, "v": 1.0, "ease": "back-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.30, "v": 0.92, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "counter",
                "text_role": "title",
                "params": {
                    "text": "{text}",
                    "font": "{heading}",
                    "font_size": 0.130,
                    "color": "{ink}",
                    "position": [0.5, 0.40],
                    "keyframes": {
                        "scale": [
                            {"t": 0.0, "v": 0.0, "ease": "back-out"},
                            {"t": 0.30, "v": 1.0, "ease": "back-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.35, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "unit",
                "text_role": "subtext",
                "params": {
                    "text": "{subtext}",
                    "font": "{body}",
                    "font_size": 0.040,
                    "color": "{ink}",
                    "position": [0.5, 0.57],
                    "keyframes": {
                        "opacity": [
                            {"t": 0.05, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.45, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
        ],
    },
    "promo-lower-third-clean": {
        "fps": 30.0,
        "width": 1080,
        "height": 1920,
        "duration": 4.0,
        "layers": [
            {
                "type": "solid",
                "name": "accent-bar",
                "params": {
                    "color": "{accent}",
                    "width": 0.014,
                    "height": 0.15,
                    "position": [0.12, 0.78],
                    "keyframes": {
                        "position": [
                            {"t": 0.0, "v": [0.0, 0.78], "ease": "expo-out"},
                            {"t": 0.30, "v": [0.12, 0.78], "ease": "expo-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.30, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "name",
                "text_role": "title",
                "params": {
                    "text": "{text}",
                    "font": "{heading}",
                    "font_size": 0.050,
                    "color": "{ink}",
                    "position": [0.18, 0.74],
                    "keyframes": {
                        "opacity": [
                            {"t": 0.30, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.60, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "role",
                "text_role": "subtext",
                "params": {
                    "text": "{subtext}",
                    "font": "{body}",
                    "font_size": 0.030,
                    "color": "{primary}",
                    "position": [0.18, 0.81],
                    "keyframes": {
                        "opacity": [
                            {"t": 0.45, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.75, "v": 1.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
        ],
    },
    "promo-cta-endcard": {
        "fps": 30.0,
        "width": 1080,
        "height": 1920,
        "duration": 4.0,
        "layers": [
            {
                "type": "solid",
                "name": "cta-pill",
                "params": {
                    "color": "{primary}",
                    "opacity": 0.95,
                    "width": 0.60,
                    "height": 0.10,
                    "position": [0.5, 0.62],
                    "keyframes": {
                        "scale": [
                            {"t": 0.0, "v": 0.0, "ease": "back-out"},
                            {"t": 0.25, "v": 1.0, "ease": "back-out"},
                            {"t": 0.30, "v": 1.0, "ease": "linear"},
                            {"t": 0.80, "v": 1.02, "ease": "expo-out"},
                            {"t": 1.80, "v": 1.0, "ease": "expo-out"},
                            {"t": 2.80, "v": 1.02, "ease": "expo-out"},
                            {"t": 3.80, "v": 1.0, "ease": "expo-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.25, "v": 0.95, "ease": "expo-out"},
                            {"t": 3.80, "v": 0.95, "ease": "linear"},
                            {"t": 4.00, "v": 0.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "cta",
                "text_role": "title",
                "params": {
                    "text": "{text}",
                    "font": "{heading}",
                    "font_size": 0.060,
                    "color": "{ink}",
                    "position": [0.5, 0.62],
                    "keyframes": {
                        "scale": [
                            {"t": 0.0, "v": 0.0, "ease": "back-out"},
                            {"t": 0.25, "v": 1.0, "ease": "back-out"},
                            {"t": 0.30, "v": 1.0, "ease": "linear"},
                            {"t": 0.80, "v": 1.02, "ease": "expo-out"},
                            {"t": 1.30, "v": 1.0, "ease": "expo-out"},
                        ],
                        "opacity": [
                            {"t": 0.0, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.25, "v": 1.0, "ease": "expo-out"},
                            {"t": 3.80, "v": 1.0, "ease": "linear"},
                            {"t": 4.00, "v": 0.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
            {
                "type": "text",
                "name": "url",
                "text_role": "subtext",
                "params": {
                    "text": "{subtext}",
                    "font": "{body}",
                    "font_size": 0.035,
                    "color": "{ink}",
                    "position": [0.5, 0.72],
                    "keyframes": {
                        "opacity": [
                            {"t": 0.30, "v": 0.0, "ease": "expo-out"},
                            {"t": 0.60, "v": 1.0, "ease": "expo-out"},
                            {"t": 3.80, "v": 1.0, "ease": "linear"},
                            {"t": 4.00, "v": 0.0, "ease": "expo-out"},
                        ],
                    },
                },
            },
        ],
    },
}


def _promo_palette(project: Project | None) -> dict[str, str]:
    """Return the project brand palette, or an empty dict when unset."""

    if project is not None and project.brand.palette:
        return dict(project.brand.palette)
    return {}


def _promo_fonts(project: Project | None) -> dict[str, str]:
    """Return the project brand fonts, or an empty dict when unset."""

    if project is not None and project.brand.fonts:
        return dict(project.brand.fonts)
    return {}


def _resolve_token(value: str, palette: dict[str, str]) -> str:
    """Resolve a ``{token}`` colour reference against the brand palette."""

    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        token = value[1:-1]
        return palette.get(token, PROMO_DEFAULT_PALETTE.get(token, value))
    return value


def _resolve_font_role(value: str, fonts: dict[str, str]) -> str:
    """Resolve a ``{role}`` font reference against the brand fonts."""

    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        role = value[1:-1]
        return fonts.get(role, PROMO_DEFAULT_FONT)
    return value


def _scale_promo_params(params: dict[str, Any], width: int, height: int) -> None:
    """Scale fraction-based geometry to absolute pixels for the canvas.

    Any ``position`` component, ``width``/``height`` or ``font_size`` in
    ``[0, 1]`` is treated as a fraction of the matching canvas dimension.
    ``font_size`` scales against ``min(width, height)`` so portrait canvases
    do not oversize text, and long title text is shrunk (floored at 24px) or
    wrapped onto two lines so it always fits inside 84% of the canvas width.
    """

    position = params.get("position")
    if (
        isinstance(position, list)
        and len(position) == 2
        and all(isinstance(item, (int, float)) and 0 <= item <= 1 for item in position)
    ):
        params["position"] = [round(position[0] * width), round(position[1] * height)]
    for key in ("width", "height"):
        value = params.get(key)
        if isinstance(value, (int, float)) and 0 < value <= 1:
            params[key] = round(value * (width if key == "width" else height))
    size = params.get("font_size")
    if isinstance(size, (int, float)) and 0 < size <= 1:
        params["font_size"] = round(size * min(width, height))
    _fit_promo_text(params, width)


def _fit_promo_text(params: dict[str, Any], width: int) -> None:
    """Shrink and wrap a text layer's text so it fits inside safe horizontal margins."""
    import textwrap

    text = params.get("text")
    if not isinstance(text, str) or not text:
        return
    font_size = params.get("font_size")
    if not isinstance(font_size, (int, float)) or font_size <= 0:
        return

    pos = params.get("position")
    pos_x = 0.15
    if isinstance(pos, list) and len(pos) >= 1 and isinstance(pos[0], (int, float)):
        val = float(pos[0])
        pos_x = val / width if val > 1.0 else val

    avail_ratio = max(0.40, min(0.85, 0.90 - pos_x))
    max_width = width * avail_ratio

    words = text.split()
    if len(text) > 36 and len(words) > 3:
        chars_per_line = max(24, int(max_width / (font_size * 0.55)))
        wrapped = textwrap.fill(text, width=chars_per_line)
        params["text"] = wrapped
        longest_line = max(len(line) for line in wrapped.splitlines())
    else:
        longest_line = len(text)

    estimated_width = longest_line * font_size * 0.55
    if estimated_width > max_width:
        new_size = max(24, round(max_width / (longest_line * 0.55)))
        params["font_size"] = new_size


# ---------------------------------------------------------------------------
# Easing helpers (reused by the promo motion presets)
# ---------------------------------------------------------------------------


def _ease_expo_out(t: float) -> float:
    """Expo-out ease: fast start, gentle settle. ``t`` normalised to 0..1."""

    t = max(0.0, min(1.0, float(t)))
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 1.0 - 2.0 ** (-10.0 * t)


def _ease_back_out(t: float, overshoot: float = 1.70158) -> float:
    """Back-out ease: settles with an overshoot past the target. ``t`` in 0..1.

    ``overshoot`` controls how far past 1.0 the curve swings before settling.
    """

    t = max(0.0, min(1.0, float(t)))
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return (t - 1.0) ** 2 * ((overshoot + 1.0) * (t - 1.0) + overshoot) + 1.0


def _ease_linear(t: float) -> float:
    """Linear ease (identity). ``t`` normalised to 0..1."""

    return max(0.0, min(1.0, float(t)))


def calc_spring(
    t: float,
    *,
    duration: float = 1.0,
    damping: float = 12.0,
    stiffness: float = 100.0,
    mass: float = 1.0,
) -> float:
    """Damped harmonic oscillator spring value at time ``t``.

    Returns the normalised spring position: starts at 0, naturally overshoots
    past 1.0, then settles back to 1.0. Based on the analytical damped harmonic
    oscillator::

        omega_n = sqrt(k / m)
        zeta    = c / (2 * sqrt(k * m))
        omega_d = omega_n * sqrt(1 - zeta^2)
        val(t)  = 1 - e^(-zeta * omega_n * t)
                  * (cos(omega_d * t) + (zeta * omega_n / omega_d) * sin(omega_d * t))

    Parameters
    ----------
    t:
        Time in seconds from the start of the spring motion.
    duration:
        Total animation duration; values at or beyond this are treated as settled (1.0).
    damping:
        Damping coefficient ``c``. Higher values reduce oscillation.
    stiffness:
        Spring stiffness ``k``. Higher values oscillate faster.
    mass:
        Oscillating mass ``m``.
    """

    t = max(0.0, float(t))
    duration = max(float(duration), 1e-6)
    if t >= duration or stiffness <= 0.0 or mass <= 0.0:
        return 1.0

    omega_n = math.sqrt(stiffness / mass)
    zeta = damping / (2.0 * math.sqrt(stiffness * mass))

    if zeta >= 1.0:
        # Critically or over-damped: monotonic settle, no overshoot.
        return 1.0 - math.exp(-omega_n * t) * (1.0 + omega_n * t)

    omega_d = omega_n * math.sqrt(1.0 - zeta * zeta)
    envelope = math.exp(-zeta * omega_n * t)
    return 1.0 - envelope * (
        math.cos(omega_d * t) + (zeta * omega_n / omega_d) * math.sin(omega_d * t)
    )


def _promo_keyframe(t: float, v: Any, ease: str = "linear") -> dict[str, Any]:
    """Build one keyframe record: time (s), value, and easing curve name."""

    return {"t": float(t), "v": v, "ease": ease}


def _scale_keyframe_positions(
    keyframes: dict[str, list[dict[str, Any]]], width: int, height: int
) -> None:
    """Scale fraction-based ``position`` keyframes to absolute canvas pixels.

    Position keyframes carry ``v`` as ``[x, y]`` in ``[0, 1]`` canvas fractions,
    mirroring the layer-level ``position`` scaling in :func:`_scale_promo_params`.
    Scale/opacity keyframes are dimensionless and are left untouched.
    """

    for prop, frames in keyframes.items():
        if prop != "position":
            continue
        for frame in frames:
            value = frame.get("v")
            if (
                isinstance(value, list)
                and len(value) == 2
                and all(isinstance(item, (int, float)) and 0 <= item <= 1 for item in value)
            ):
                frame["v"] = [round(value[0] * width), round(value[1] * height)]


def _ease_value(v0: Any, v1: Any, e: float) -> Any:
    """Interpolate between ``v0`` and ``v1`` by eased factor ``e`` (0..1, may overshoot).

    Handles scalars and equal-length lists (e.g. position vec2) component-wise.
    """

    if isinstance(v0, list) and isinstance(v1, list) and len(v0) == len(v1):
        return [v0[i] + (v1[i] - v0[i]) * e for i in range(len(v0))]
    return v0 + (v1 - v0) * e


def _expand_eased_keyframes(frames: list[dict[str, Any]], samples: int = 8) -> list[dict[str, Any]]:
    """Insert intermediate keys per segment so linear playback approximates the ease curve.

    The engine interpolates linearly between keys, so each segment is resampled
    onto its easing curve (expo-out / back-out / linear) with ``samples``
    intermediate keys. Original endpoints are kept so start/end values are exact.
    """

    if len(frames) < 2:
        return [dict(f) for f in frames]
    ordered = sorted(frames, key=lambda f: f["t"])
    expanded: list[dict[str, Any]] = [dict(ordered[0])]
    for a, b in itertools.pairwise(ordered):
        t0, v0 = a["t"], a["v"]
        t1, v1 = b["t"], b["v"]
        ease_name = a.get("ease", "linear")
        span = t1 - t0
        if ease_name == "spring":

            def ease_fn(frac: float, _t0: float = t0, _span: float = span) -> float:
                return calc_spring(_span * frac, duration=_span)

        else:
            ease_fn = {
                "expo-out": _ease_expo_out,
                "back-out": _ease_back_out,
                "linear": _ease_linear,
            }.get(ease_name, _ease_linear)
        for k in range(1, samples + 1):
            frac = k / (samples + 1.0)
            expanded.append(
                {
                    "t": round(t0 + span * frac, 4),
                    "v": _ease_value(v0, v1, ease_fn(frac)),
                    "ease": ease_name,
                }
            )
        expanded.append(dict(b))
    return expanded


def _keyframe_path(prop: str) -> str:
    """Map a spec property name to the engine transform property path."""

    return {
        "scale": "transform/scale",
        "opacity": "transform/opacity",
        "position": "transform/position",
    }.get(prop, prop)


def _keyframe_value(prop: str, v: Any) -> Any:
    """Convert a spec keyframe value (factor for scale/opacity) to engine units.

    Scale and opacity are stored as 0..1 factors in the spec; the engine expects
    percent (0..100). Position is already in canvas pixels.
    """

    if prop in ("scale", "opacity") and isinstance(v, (int, float)):
        return round(float(v) * 100.0, 4)
    if prop == "position" and isinstance(v, list):
        return [round(float(x), 2) for x in v]
    return v


def _keyframe_commands(
    layer_name: str, keyframes: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Build engine ``prop.addKey`` commands for one layer's keyframes.

    Times are in composition seconds; values are engine units (percent for
    scale/opacity, pixels for position).
    """

    cmds: list[dict[str, Any]] = []
    for prop, frames in keyframes.items():
        path = _keyframe_path(prop)
        for frame in _expand_eased_keyframes(frames):
            cmds.append(
                {
                    "id": "prop.addKey",
                    "params": {
                        "layer": layer_name,
                        "path": path,
                        "time": round(float(frame["t"]), 4),
                        "value": _keyframe_value(prop, frame["v"]),
                    },
                }
            )
    return cmds


def render_promo_preset(
    preset_name: str,
    output: Path,
    *,
    text: str,
    subtext: str = "",
    project: Project | None = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    duration: float = 3.0,
) -> MotionResult:
    """Render one EffectCraft promo motion preset as transparent ProRes 4444 RGBA.

    Looks up ``preset_name`` in :data:`PROMO_MOTION_PRESETS`, deep-copies the
    preset spec, scales every layer position, dimension and font size from
    ``width`` and ``height``, injects ``text`` into ``text_role == "title"``
    layers and ``subtext`` into ``text_role == "subtext"`` layers, and resolves
    brand token colours (``{primary}``, ``{accent}``, ``{ink}``) and fonts
    (``{heading}``, ``{body}``) from ``project.brand`` when a project is
    supplied. The spec is written as an ``.ecp`` next to ``output`` and rendered
    through :func:`render`, producing ProRes 4444 with an alpha channel.

    Raises:
        EffectCraftError: ``preset_name`` is not a registered promo preset.
    """

    if preset_name not in PROMO_MOTION_PRESETS:
        known = ", ".join(sorted(PROMO_MOTION_PRESETS))
        raise EffectCraftError(
            f"unknown promo motion preset {preset_name!r}; choose one of: {known}"
        )

    preset = PROMO_MOTION_PRESETS[preset_name]
    spec = copy.deepcopy(dict(preset))
    spec["width"] = int(width)
    spec["height"] = int(height)
    spec["fps"] = float(fps)
    spec["duration"] = float(duration)

    palette = _promo_palette(project)
    fonts = _promo_fonts(project)

    for layer in spec.get("layers", []):
        params = layer.get("params", {})
        text_role = layer.get("text_role")
        if layer.get("type") == "text" and text_role == "title":
            params["text"] = text
        elif layer.get("type") == "text" and text_role == "subtext":
            params["text"] = subtext
        params.pop("text_role", None)
        _scale_promo_params(params, int(width), int(height))
        keyframes = params.get("keyframes")
        if isinstance(keyframes, dict):
            _scale_keyframe_positions(keyframes, int(width), int(height))
        for key in ("color", "fill", "stroke"):
            if isinstance(params.get(key), str):
                params[key] = _resolve_token(params[key], palette)
        if isinstance(params.get("font"), str):
            params["font"] = _resolve_font_role(params["font"], fonts)

    _validate_spec(spec)
    check_installation()

    output = Path(output)
    ecp_path = output.with_suffix(".ecp")
    build_ecp(spec, ecp_path)
    return render(ecp_path, output, fps=float(fps), transparent=True)


def render_amv_preset(
    preset_name: str,
    output: Path,
    *,
    text: str = "",
    duration: float = 3.0,
    fps: float = 60.0,
    width: int = 1920,
    height: int = 1080,
) -> MotionResult:
    """Render one AMV motion preset as transparent ProRes 4444 RGBA.

    Looks up ``preset_name`` in :data:`AMV_MOTION_PRESETS`, deep-copies the
    preset spec, injects ``text`` into the ``title`` text layer (leaving
    decorative text layers such as HUD crosshairs untouched) and overrides
    ``duration``, ``fps``, ``width`` and ``height`` on the composition. The
    spec is written as an ``.ecp`` next to ``output`` and rendered through
    :func:`render`, producing ProRes 4444 with an alpha channel.

    Raises:
        EffectCraftError: ``preset_name`` is not a registered AMV preset.
    """
    if preset_name not in AMV_MOTION_PRESETS:
        known = ", ".join(sorted(AMV_MOTION_PRESETS))
        raise EffectCraftError(f"unknown AMV motion preset {preset_name!r}; choose one of: {known}")

    preset = AMV_MOTION_PRESETS[preset_name]
    spec = copy.deepcopy(dict(preset))
    spec["width"] = int(width)
    spec["height"] = int(height)
    spec["fps"] = float(fps)
    spec["duration"] = float(duration)
    # Only inject --text into layers intended for custom text (the "title"
    # layer). Decorative text layers such as the HUD crosshairs ("[ + ]") must
    # keep their default text.
    for layer in spec.get("layers", []):
        if layer.get("type") == "text" and layer.get("name") == "title":
            layer.setdefault("params", {})["text"] = text

    _validate_spec(spec)
    check_installation()

    output = Path(output)
    ecp_path = output.with_suffix(".ecp")
    build_ecp(spec, ecp_path)
    return render(ecp_path, output, fps=float(fps), transparent=True)


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
    transparent: bool = True,
) -> MotionResult:
    """Generate an animated title sequence with alpha channel."""
    output = Path(output).resolve()
    if transparent:
        output = output.with_suffix(".mov")
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
    """Generate a lyric subtitle overlay track with alpha channel."""
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
    """Generate a logo reveal animation with alpha channel."""
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


def lower_third(
    title: str,
    subtitle: str = "",
    *,
    output: Path | None = None,
    ecp_path: Path | None = None,
    duration: float = 4.0,
    fps: float = 30.0,
    width: int = 1920,
    height: int = 1080,
    title_size: float = 48.0,
    subtitle_size: float = 28.0,
) -> MotionResult:
    """Generate a lower-third title and subtitle animation with alpha channel."""
    out_file = output or Path("out/motion/lower_third.mov")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    project_file = ecp_path or out_file.with_suffix(".ecproj")

    # Paragraph boxes anchor text left in the lower safe area; a bare position would
    # centre the layer on that point and clip wide titles off the left edge.
    margin_x = round(width * 0.0625)
    box_width = width - margin_x * 2
    title_box = [margin_x, round(height * 0.74), box_width, round(height * 0.11)]
    subtitle_box = [margin_x, round(height * 0.855), box_width, round(height * 0.09)]

    layers = [
        {
            "type": "text",
            "name": "title",
            "params": {
                "text": title,
                "font_size": title_size,
                "box": title_box,
                "justify": "left",
            },
        }
    ]
    if subtitle:
        layers.append(
            {
                "type": "text",
                "name": "subtitle",
                "params": {
                    "text": subtitle,
                    "font_size": subtitle_size,
                    "box": subtitle_box,
                    "justify": "left",
                },
            }
        )

    spec = {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "layers": layers,
    }
    _validate_spec(spec)
    check_installation()
    build_ecp(spec, project_file)
    return render(project_file, out_file, fps=fps, transparent=True)


def motion_project(
    sequences: list[dict],
    project_root: Path | None = None,
    output_dir: Path | None = None,
) -> list[MotionResult]:
    """Render every motion-graphics sequence declared in project spec."""
    check_installation()
    results: list[MotionResult] = []
    if not sequences:
        return results

    root = project_root or Path.cwd()
    target_dir = output_dir or (root / "out" / "motion")
    target_dir.mkdir(parents=True, exist_ok=True)

    for seq in sequences:
        seq_id = seq.get("id", "seq")
        seq_type = seq.get("type", "title")
        out_file = target_dir / f"{seq_id}.mov"
        ecp_file = target_dir / f"{seq_id}.ecproj"

        if seq_type == "title":
            res = title_sequence(
                seq.get("text", "Title"),
                out_file,
                ecp_file,
                duration=float(seq.get("duration", 3.0)),
            )
            results.append(res)
        elif seq_type == "lyrics":
            res = lyric_overlay(
                seq.get("lines", []),
                out_file,
                ecp_file,
            )
            results.append(res)
        elif seq_type == "logo":
            logo_p = Path(seq.get("path", ""))
            res = logo_reveal(
                logo_p,
                out_file,
                ecp_file,
                duration=float(seq.get("duration", 2.0)),
            )
            results.append(res)
        elif seq_type in ("lower_third", "lower-third", "lowerthird"):
            res = lower_third(
                seq.get("title", seq.get("text", "Name")),
                seq.get("subtitle", ""),
                output=out_file,
                ecp_path=ecp_file,
                duration=float(seq.get("duration", 4.0)),
            )
            results.append(res)

    return results


# ---------------------------------------------------------------------------
# Composition scaffolding (`ms motion new`, design 5.7)
# ---------------------------------------------------------------------------

MOTION_TEMPLATES: tuple[str, ...] = (
    "lower-third",
    "title-card",
    "logo-sting",
    "lyric-overlay",
    "explainer-beat",
)


def _text_layer(
    name: str,
    text: str,
    *,
    size: float,
    position: list[float],
    fill: str,
    font: str | None = None,
) -> dict[str, Any]:
    """A text layer spec with brand tokens already applied."""
    params: dict[str, Any] = {
        "text": text,
        "font_size": size,
        "position": position,
        "fill": fill,
    }
    if font:
        params["font"] = font
    return {"type": "text", "name": name, "params": params}


def scaffold_spec(
    template: str,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    palette: dict[str, str] | None = None,
    fonts: dict[str, str] | None = None,
    title: str = "Title",
    subtitle: str = "Subtitle",
) -> dict[str, Any]:
    """Build a motion spec for a named template with brand tokens injected.

    Templates are the five from design 5.7. Colours come from the project
    palette (``text`` for copy, ``accent`` then ``primary`` for decoration)
    and fonts from ``fonts.display`` so every scaffold matches the brand
    without manual styling.
    """
    palette = palette or {}
    fonts = fonts or {}
    text_colour = palette.get("text", "#f8fafc")
    accent_colour = palette.get("accent") or palette.get("primary", "#3b82f6")
    display_font = fonts.get("display")

    def text(name: str, body: str, size: float, position: list[float]) -> dict[str, Any]:
        return _text_layer(
            name, body, size=size, position=position, fill=text_colour, font=display_font
        )

    def bar(x: float, y: float, w: float, h: float) -> dict[str, Any]:
        return {
            "type": "solid",
            "name": "accent-bar",
            "params": {"color": accent_colour, "width": w, "height": h},
        }

    if template == "lower-third":
        layers = [
            bar(100, height - 240, 8, 160),
            text("title", title, 64, [128, height - 220]),
            text("subtitle", subtitle, 36, [128, height - 140]),
        ]
    elif template == "title-card":
        layers = [
            bar(width / 2 - 60, height / 2 - 150, 120, 8),
            text("headline", title, 96, [width / 2 - 240, height / 2 - 110]),
            text("subhead", subtitle, 40, [width / 2 - 180, height / 2 + 10]),
        ]
    elif template == "logo-sting":
        layers = [
            {
                "type": "image",
                "name": "logo",
                "params": {"path": "", "position": [width / 2 - 96, height / 2 - 96]},
            },
            text("brand", title, 72, [width / 2 - 180, height / 2 + 120]),
        ]
    elif template == "lyric-overlay":
        layers = [
            text("lyric", title, 56, [width / 2 - 320, height - 180]),
        ]
    elif template == "explainer-beat":
        layers = [
            text("heading", title, 64, [120, 120]),
            text("body", subtitle, 36, [120, 220]),
        ]
    else:
        raise EffectCraftError(
            f"unknown template {template!r}; choose one of: {', '.join(MOTION_TEMPLATES)}"
        )

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "layers": layers,
    }


def new_composition(
    project_root: Path,
    comp_id: str,
    template: str = "lower-third",
    *,
    duration: float | None = None,
    deliverable_resolution: str | None = None,
    deliverable_fps: float | None = None,
    palette: dict[str, str] | None = None,
    fonts: dict[str, str] | None = None,
    title: str = "Title",
    subtitle: str = "Subtitle",
    force: bool = False,
) -> Path:
    """Scaffold a composition file for ``ms motion new`` and return its path.

    The scaffold is a spec document (compiled to native form on first render)
    written to ``<project>/motion/<comp>.ecproj``. An existing composition is
    refused unless ``force`` is set, so agent scaffolds never clobber hand-edited
    work.
    """
    geometry = deliverable_resolution or "1920x1080"
    try:
        width_s, height_s = geometry.lower().split("x", 1)
        width, height = int(width_s), int(height_s)
    except ValueError as error:
        raise EffectCraftError(f"invalid resolution {geometry!r}") from error
    fps = float(deliverable_fps or 30.0)
    comp_duration = float(duration if duration is not None else 4.0)

    spec = scaffold_spec(
        template,
        width=width,
        height=height,
        fps=fps,
        duration=comp_duration,
        palette=palette,
        fonts=fonts,
        title=title,
        subtitle=subtitle,
    )

    target = (project_root / "motion" / f"{comp_id}.ecproj").resolve()
    if target.exists() and not force:
        raise EffectCraftError(
            f"{target.name} already exists; pick another --comp id or pass --force to overwrite"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    build_ecp(spec, target)
    return target
