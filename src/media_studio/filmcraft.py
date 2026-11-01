"""FilmCraft headless NLE runner for OpenCode agent.

Drives FilmCraft (0.15.0+, pure Rust, MIT/Apache-2.0) through its headless CLI
(``filmcraft-cli``). Exposes typed command execution across 711 engine commands,
sequence inspection, GPU rendering with automatic CPU fallback, frame rendering,
and project lifecycle management.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Binary resolution order: MS_FILMCRAFT_BIN override, PATH, then the pinned path
# recorded by the Stage 0 installer in ~/.config/opencode/media-engines.json.
PINNED_BIN = Path.home() / ".local" / "opt" / "filmcraft-0.15.0" / "bin" / "filmcraft-cli"
ENGINES_CONFIG = Path.home() / ".config" / "opencode" / "media-engines.json"
CATALOG_CACHE_DIR = Path.home() / ".cache" / "media-studio"


def resolve_binary(cli_path: str | Path | None = None) -> Path:
    """Locate the filmcraft CLI: explicit arg, env override, PATH, then pinned install."""
    candidates: list[str | Path] = []
    if cli_path:
        candidates.append(cli_path)
    env_override = os.environ.get("MS_FILMCRAFT_BIN")
    if env_override:
        candidates.append(env_override)
    on_path = shutil.which("filmcraft-cli") or shutil.which("filmcraft")
    if on_path:
        candidates.append(on_path)
    if ENGINES_CONFIG.is_file():
        try:
            pinned = json.loads(ENGINES_CONFIG.read_text(encoding="utf-8"))
            filmcraft = pinned.get("filmcraft") or {}
            configured = filmcraft.get("cli") or filmcraft.get("binary")
            if configured:
                candidates.append(configured)
        except (json.JSONDecodeError, OSError):
            pass
    candidates.append(PINNED_BIN)

    for candidate in candidates:
        resolved = shutil.which(str(candidate))
        if resolved is None and str(candidate).startswith("/"):
            resolved = str(candidate)
        if resolved and Path(resolved).is_file():
            return Path(resolved)

    raise FilmCraftError(
        "filmcraft-cli is not on PATH. Please install FilmCraft:\n"
        "  Binary tarball: https://github.com/storytold/filmcraft/releases\n"
        "  Extract to ~/.local/opt/filmcraft-0.15.0/bin/ and symlink into "
        "~/.local/bin/filmcraft-cli.\n"
        "Run 'ms doctor' to verify."
    )


def command_exists() -> bool:
    """Return True when a usable filmcraft CLI binary can be resolved."""
    try:
        resolve_binary()
        return True
    except FilmCraftError:
        return False


# Regex to detect and strip harmless DRI2/Mesa warnings on Linux
DRI2_WARNING_RE = re.compile(r"^MESA-EGL: warning: DRI2: failed to create screen\s*$", re.MULTILINE)

# GPU failure signatures that justify a CPU retry (device/driver/adapter problems),
# as opposed to bad parameters or a missing project, which would fail on CPU too.
GPU_FAILURE_RE = re.compile(
    r"\bgpu\b|wgpu|vulkan|adapter|device lost|out of memory|hardware|dri2",
    re.IGNORECASE,
)


class FilmCraftError(RuntimeError):
    """Raised when FilmCraft fails, project is malformed, or binary is unavailable."""


class FilmCraftCommandError(FilmCraftError):
    """Raised when a specific engine command returns non-zero status."""

    def __init__(self, command: str, returncode: int, stderr: str, result: Any = None):
        super().__init__(f"command '{command}' failed with code {returncode}: {stderr.strip()}")
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        self.result = result


class FilmCraftOfflineMediaError(FilmCraftError):
    """Raised when an operation encounters offline media without --allow-offline."""


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one FilmCraft engine command."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    result: Any = None
    ok: bool = True


@dataclass(frozen=True)
class ExportResult:
    """Outcome of a sequence export."""

    output: Path
    returncode: int
    stdout: str
    stderr: str
    gpu_attempted: bool
    gpu_fallback_to_cpu: bool
    render_path: str  # "gpu" or "cpu"
    frames: int | None = None
    duration_sec: float | None = None
    fps: float | None = None
    ok: bool = True
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderResult:
    """Outcome of a single-frame preview render."""

    output: Path
    returncode: int
    stdout: str
    stderr: str
    render_time_ms: float | None = None
    seconds: float = 0.0
    scale: float = 0.5
    ok: bool = True


def _filter_stderr(raw_stderr: str) -> str:
    """Remove known harmless driver/environment warnings from stderr."""
    cleaned = DRI2_WARNING_RE.sub("", raw_stderr)
    return cleaned.strip()


def check_installation(cli_path: str | Path | None = None) -> Path:
    """Validate that the FilmCraft CLI can be resolved and return its path."""
    return resolve_binary(cli_path)


class FilmCraftRunner:
    """Headless driver for FilmCraft 0.15.0+."""

    def __init__(
        self,
        cli_path: str | Path | None = None,
        default_timeout: int = 60,
    ):
        self.cli_path = check_installation(cli_path)
        self.default_timeout = default_timeout
        self._commands_cache: list[dict[str, Any]] | None = None
        self.allowed_root: Path | None = None
        """When set, exec/run_batch refuse a project_path outside this root."""

    def _guard_project_path(self, project_path: Path | None) -> None:
        """Reject project paths outside the allowed root (Stage 2 project guard)."""
        if project_path is None or self.allowed_root is None:
            return
        root = self.allowed_root.resolve()
        target = project_path.resolve()
        if root not in target.parents and target != root:
            raise FilmCraftError(
                f"project path {target} is outside the allowed project root {root}"
            )

    def get_commands_catalog(self, cached: bool = True) -> list[dict[str, Any]]:
        """Retrieve the catalog of all 711+ commands supported by FilmCraft.

        Results are cached in memory for the runner lifetime and on disk under
        ``~/.cache/media-studio/filmcraft-<version>-commands.json`` keyed by the
        engine version, so repeated sessions skip the catalogue round trip.
        """
        if cached and self._commands_cache is not None:
            return self._commands_cache

        version = self.engine_version()
        cache_file = CATALOG_CACHE_DIR / f"filmcraft-{version}-commands.json"
        if cached and cache_file.is_file():
            try:
                self._commands_cache = json.loads(cache_file.read_text(encoding="utf-8"))
                return self._commands_cache
            except (json.JSONDecodeError, OSError):
                pass  # corrupt cache: refetch below

        cmd = [str(self.cli_path), "commands", "--json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        catalog = json.loads(proc.stdout)
        self._commands_cache = catalog
        try:
            CATALOG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(catalog), encoding="utf-8")
        except OSError:
            pass  # cache write is best-effort
        return catalog

    def engine_version(self) -> str:
        """Report the engine version string (used for on-disk cache keys)."""
        proc = subprocess.run(
            [str(self.cli_path), "--version"], capture_output=True, text=True, check=False
        )
        line = (proc.stdout.strip() or proc.stderr.strip() or "unknown").splitlines()[0]
        return re.sub(r"[^A-Za-z0-9._-]", "-", line)[:60] or "unknown"

    def describe_command(self, command_id: str) -> dict[str, Any]:
        """Describe a single command (menu, shortcut, params schema, enabled status)."""
        cmd = [str(self.cli_path), "describe", command_id]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def exec(
        self,
        command_id: str,
        params: dict[str, Any] | None = None,
        project_path: Path | None = None,
        save: bool = True,
        save_as: Path | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> CommandResult:
        """Execute one FilmCraft command headlessly via 'exec'.

        Args:
            command_id: Command identifier, e.g. 'timeline.place', 'file.newSequence'.
            params: Dict of typed parameters.
            project_path: Existing .fcproj file to load.
            save: Whether to save changes back to project_path upon completion.
            save_as: Save project to a different path upon completion.
            timeout: Subprocess timeout in seconds.
            check: If True, raises FilmCraftCommandError on non-zero exit code.
        """
        self._guard_project_path(project_path)
        argv = [str(self.cli_path)]
        if project_path:
            argv.extend(["--project", str(project_path)])
            if save:
                argv.append("--save")
        if save_as:
            argv.extend(["--save-as", str(save_as)])

        argv.extend(["exec", command_id])
        if params is not None:
            argv.append(json.dumps(params))

        effective_timeout = timeout or self.default_timeout
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=effective_timeout)
        cleaned_stderr = _filter_stderr(proc.stderr)

        parsed_result = None
        if proc.stdout.strip():
            try:
                parsed_result = json.loads(proc.stdout)
            except json.JSONDecodeError:
                parsed_result = proc.stdout.strip()

        ok = proc.returncode == 0
        if not ok and check:
            if "offline media" in cleaned_stderr.lower():
                raise FilmCraftOfflineMediaError(f"Command '{command_id}' drew offline media")
            raise FilmCraftCommandError(command_id, proc.returncode, cleaned_stderr, parsed_result)

        return CommandResult(
            command=command_id,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=cleaned_stderr,
            result=parsed_result,
            ok=ok,
        )

    def run_batch(
        self,
        commands: list[dict[str, Any]],
        project_path: Path | None = None,
        save: bool = True,
        save_as: Path | None = None,
        keep_going: bool = False,
        timeout: int | None = None,
    ) -> list[CommandResult]:
        """Execute multiple commands in a single session via 'run -' (JSON Lines).

        Args:
            commands: List of dicts, each containing {"id": str, "params": dict}.
            project_path: Project to load.
            save: Save back to project_path when done.
            save_as: Save to another path when done.
            keep_going: Continue on errors.
            timeout: Subprocess timeout in seconds.
        """
        self._guard_project_path(project_path)
        argv = [str(self.cli_path)]
        if project_path:
            argv.extend(["--project", str(project_path)])
            if save:
                argv.append("--save")
        if save_as:
            argv.extend(["--save-as", str(save_as)])
        if keep_going:
            argv.append("--keep-going")

        argv.extend(["run", "-"])

        # Format commands as JSON Lines
        lines = []
        for cmd in commands:
            cmd_id = cmd.get("id") or cmd.get("command")
            if not cmd_id:
                raise FilmCraftError(f"Command missing 'id' or 'command': {cmd}")
            payload = {"id": cmd_id, "params": cmd.get("params", {})}
            lines.append(json.dumps(payload))
        stdin_content = "\n".join(lines) + "\n"

        effective_timeout = timeout or (self.default_timeout * max(1, len(commands)))
        proc = subprocess.run(
            argv,
            input=stdin_content,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
        cleaned_stderr = _filter_stderr(proc.stderr)

        results: list[CommandResult] = []
        lines_out = [item.strip() for item in proc.stdout.strip().splitlines() if item.strip()]
        for idx, line in enumerate(lines_out):
            default_cmd = (
                (commands[idx].get("id") or commands[idx].get("command"))
                if idx < len(commands)
                else "unknown"
            )
            try:
                data = json.loads(line)
                cmd_id = data.get("id") or default_cmd
                is_ok = bool(data.get("ok", True))
                res = data.get("result", data)
                results.append(
                    CommandResult(
                        command=cmd_id,
                        returncode=0 if is_ok else 1,
                        stdout=line,
                        stderr=cleaned_stderr,
                        result=res,
                        ok=is_ok,
                    )
                )
            except json.JSONDecodeError:
                results.append(
                    CommandResult(
                        command=default_cmd,
                        returncode=proc.returncode,
                        stdout=line,
                        stderr=cleaned_stderr,
                        result=line,
                        ok=proc.returncode == 0,
                    )
                )

        return results

    def inspect(self, project_path: Path, target: str = "both") -> dict[str, Any]:
        """Inspect project tree, active sequence, or both as a parsed dict.

        Args:
            project_path: Path to the .fcproj project file.
            target: 'project', 'sequence', or 'both'.
        """
        if not project_path.exists():
            raise FilmCraftError(f"Project not found: {project_path}")

        argv = [str(self.cli_path), "--project", str(project_path), "inspect"]
        if target in ("project", "sequence"):
            argv.append(target)

        proc = subprocess.run(argv, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def render_frame(
        self,
        project_path: Path,
        output_png: Path,
        seconds: float = 0.0,
        scale: float = 0.5,
        allow_offline: bool = False,
        timeout: int = 20,
    ) -> RenderResult:
        """Render a single frame of the active sequence to PNG."""
        if not project_path.exists():
            raise FilmCraftError(f"Project not found: {project_path}")

        output_png.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            str(self.cli_path),
            "--project",
            str(project_path),
            "render",
            "--seconds",
            str(seconds),
            "--out",
            str(output_png),
            "--scale",
            str(scale),
        ]
        if allow_offline:
            argv.append("--allow-offline")

        start_time = time.monotonic()
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        cleaned_stderr = _filter_stderr(proc.stderr)

        ok = proc.returncode == 0 and output_png.exists()
        return RenderResult(
            output=output_png,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=cleaned_stderr,
            render_time_ms=elapsed_ms,
            seconds=seconds,
            scale=scale,
            ok=ok,
        )

    def export(
        self,
        project_path: Path,
        output_file: Path,
        format: str = "h264",
        scale: float = 1.0,
        quality: int = 70,
        loudness_lufs: float | None = -14.0,
        gpu: str = "auto",  # "auto" or "off"
        allow_offline: bool = False,
        burn_captions: bool = False,
        timeout: int = 1800,
    ) -> ExportResult:
        """Export the active sequence to disk with GPU default and automatic CPU fallback.

        Implements D41: tries --gpu-rendering auto first. If it fails with a GPU/driver
        error or crash, automatically falls back to --gpu-rendering off and records
        gpu_fallback_to_cpu in the ExportResult.
        """
        if not project_path.exists():
            raise FilmCraftError(f"Project not found: {project_path}")

        output_file.parent.mkdir(parents=True, exist_ok=True)

        # `loudnessLufs` is a parameter of `file.exportMedia`, not an ExportSettings key,
        # so the export goes through the engine command instead of the CLI wrapper.
        params: dict[str, Any] = {
            "path": str(output_file),
            "format": format,
            "scale": float(scale),
            "quality": int(quality),
            "wait": True,
        }
        if loudness_lufs is not None:
            params["loudnessLufs"] = float(loudness_lufs)
        if burn_captions:
            params["burnCaptions"] = True

        base_argv = [str(self.cli_path), "--project", str(project_path)]

        gpu_attempted = gpu == "auto"
        gpu_fallback = False
        render_path = "gpu" if gpu_attempted else "cpu"

        # Attempt 1: primary render path
        argv1 = [*base_argv, "--gpu-rendering", gpu, "exec", "file.exportMedia", json.dumps(params)]

        proc = subprocess.run(argv1, capture_output=True, text=True, timeout=timeout)
        cleaned_stderr = _filter_stderr(proc.stderr)
        gpu_failure_text: str | None = None

        # CPU retry only for genuine GPU/driver failures (D41): a bad parameter or a
        # missing project would fail identically on CPU and retrying would mask the
        # real error.
        if proc.returncode != 0 and gpu_attempted and GPU_FAILURE_RE.search(cleaned_stderr):
            gpu_failure_text = cleaned_stderr[-2000:]
            logger.warning("FilmCraft GPU render failed; retrying on CPU: %s", gpu_failure_text)
            argv2 = [
                *base_argv,
                "--gpu-rendering",
                "off",
                "exec",
                "file.exportMedia",
                json.dumps(params),
            ]
            proc = subprocess.run(argv2, capture_output=True, text=True, timeout=timeout)
            cleaned_stderr = _filter_stderr(proc.stderr)
            gpu_fallback = True
            render_path = "cpu"

        ok = proc.returncode == 0 and output_file.exists()

        # Parse export statistics from the engine's JSON result, or the CLI summary line
        frames, duration, fps = None, None, None
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            result = payload.get("result") or {}
            if isinstance(result, dict):
                frames = result.get("frames")
                duration = result.get("seconds")
                fps = result.get("render_fps")
        if frames is None:
            match = re.search(
                r"exported\s+(\d+)\s+frames\s+in\s+([\d\.]+)s\s+\(([\d\.]+)\s+fps\)", proc.stdout
            )
            if match:
                frames = int(match.group(1))
                duration = float(match.group(2))
                fps = float(match.group(3))

        return ExportResult(
            output=output_file,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=cleaned_stderr,
            gpu_attempted=gpu_attempted,
            gpu_fallback_to_cpu=gpu_fallback,
            render_path=render_path,
            frames=frames,
            duration_sec=duration,
            fps=fps,
            ok=ok,
            details={
                "loudnessLufs": loudness_lufs,
                "scale": scale,
                "quality": quality,
                "gpu_failure_text": gpu_failure_text,
            },
        )
