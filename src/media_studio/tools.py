"""Local toolchain detection used by ``ms doctor``.

The probes are deliberately shallow: presence plus one version line. Deep checks that cost
seconds (torch imports, GPU device files) only run behind ``--deep``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROBE_TIMEOUT_SECONDS = 30
VERSION_WIDTH = 96
SYSTEM_BIN = "/usr/bin"
LOCAL_BIN = "~/.local/bin"
NVIDIA_VULKAN_ICD = Path("/usr/share/vulkan/icd.d/nvidia_icd.json")
TORCH_VENVS = (
    ("DeepFilterNet", Path("~/.clone/Github/DeepFilterNet/.venv")),
    ("demucs (UVR)", Path("~/.clone/Github/ultimatevocalremovergui/.venv")),
)


@dataclass(frozen=True)
class ToolSpec:
    """One external program the toolkit depends on."""

    name: str
    probe: tuple[str, ...]
    required: bool
    purpose: str


@dataclass(frozen=True)
class ToolStatus:
    """Presence and version of one tool."""

    spec: ToolSpec
    path: str | None
    version: str | None

    @property
    def ok(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one deep or environmental check."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    """One complete doctor run, passed around as a single object."""

    tools: list[ToolStatus]
    checks: list[CheckResult]

    @property
    def missing_required(self) -> list[str]:
        """Required tools that were not found; the exit-code signal."""

        return [status.spec.name for status in self.tools if status.spec.required and not status.ok]

    @property
    def warnings(self) -> list[str]:
        """Failed checks that do not fail the run, such as the PATH ordering hint."""

        return [check.name for check in self.checks if not check.ok]

    def to_dict(self) -> dict[str, object]:
        """Machine-readable form, consumed by skills and the selftest."""

        return {
            "tools": [
                {
                    "name": status.spec.name,
                    "path": status.path,
                    "version": status.version,
                    "required": status.spec.required,
                    "purpose": status.spec.purpose,
                }
                for status in self.tools
            ],
            "checks": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
            "missing_required": self.missing_required,
            "warnings": self.warnings,
        }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("blender", ("--version",), True, "3D authoring and headless rendering"),
    ToolSpec("ffmpeg", ("-version",), True, "encoding, filters, loudness, QC"),
    ToolSpec("ffprobe", ("-version",), True, "media measurement"),
    ToolSpec("melt", ("--version",), True, "MLT timeline rendering"),
    ToolSpec("inkscape", ("--version",), True, "SVG to PNG and PDF"),
    ToolSpec("magick", ("-version",), True, "raster post-processing"),
    ToolSpec("node", ("--version",), True, "3d-asset-server and gltf-transform"),
    ToolSpec("npm", ("--version",), True, "Node package management"),
    ToolSpec("uv", ("--version",), True, "Python environments"),
    ToolSpec("kdenlive", ("--version",), False, "manual timeline handoff"),
    ToolSpec("git", ("--version",), False, "project versioning"),
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def first_line(text: str) -> str:
    """Return the first non-empty line, trimmed to a readable width."""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:VERSION_WIDTH]
    return ""


def collect_toolchain(
    specs: Sequence[ToolSpec] = TOOL_SPECS,
    which: Callable[[str], str | None] = shutil.which,
    run: Runner = subprocess.run,
) -> list[ToolStatus]:
    """Probe every tool spec without raising on missing or failing binaries."""

    statuses: list[ToolStatus] = []
    for spec in specs:
        path = which(spec.name)
        if path is None:
            statuses.append(ToolStatus(spec=spec, path=None, version=None))
            continue
        version: str | None = None
        try:
            completed = run(
                [path, *spec.probe],
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
            version = first_line(completed.stdout or completed.stderr or "") or None
        except (OSError, subprocess.SubprocessError):
            version = None
        statuses.append(ToolStatus(spec=spec, path=path, version=version))
    return statuses


def first_index_of(entries: Sequence[str], target: Path) -> int | None:
    """Return the position of ``target`` in a PATH-style list, or None."""

    wanted = os.path.normpath(str(target.expanduser()))
    for index, entry in enumerate(entries):
        if os.path.normpath(os.path.expanduser(entry)) == wanted:
            return index
    return None


def path_order_problem(path_value: str) -> str | None:
    """Report the Blender PATH pitfall when ``~/.local/bin`` shadows ``/usr/bin``.

    Blender resolves its embedded Python stdlib through ``python3.14`` on ``PATH``; a
    uv-managed shim earlier in the list breaks startup with ``No module named 'math'``.
    """

    entries = [entry for entry in path_value.split(os.pathsep) if entry]
    system_index = first_index_of(entries, Path(SYSTEM_BIN))
    local_index = first_index_of(entries, Path(LOCAL_BIN))
    if system_index is not None and local_index is not None and local_index < system_index:
        return (
            f"{LOCAL_BIN} precedes {SYSTEM_BIN} on PATH; Blender may lose its embedded "
            "Python stdlib. Put /usr/bin first, as bin/blender-run does"
        )
    return None


def deep_checks(
    python_probe: Callable[[Path], str | None] = lambda venv: _venv_torch_version(venv),
) -> list[CheckResult]:
    """Run the checks that are too slow or too specific for the default report."""

    results: list[CheckResult] = []

    icd = NVIDIA_VULKAN_ICD
    results.append(
        CheckResult(
            name="Vulkan NVIDIA ICD",
            ok=icd.is_file(),
            detail=str(icd) if icd.is_file() else f"missing: {icd}",
        )
    )

    for label, venv in TORCH_VENVS:
        expanded = venv.expanduser()
        if not (expanded / "bin" / "python").is_file():
            results.append(CheckResult(f"torch {label}", False, f"missing venv: {expanded}"))
            continue
        version = python_probe(expanded)
        if version is None:
            results.append(
                CheckResult(f"torch {label}", False, f"torch not importable in {expanded}")
            )
        else:
            results.append(CheckResult(f"torch {label}", True, version))

    usage = shutil.disk_usage(Path.home())
    free_gib = usage.free / (1024**3)
    results.append(
        CheckResult(
            "disk free (home)",
            ok=free_gib >= 20,
            detail=f"{free_gib:.1f} GiB free",
        )
    )
    return results


def _venv_torch_version(venv: Path) -> str | None:
    python = venv / "bin" / "python"
    try:
        completed = subprocess.run(
            [str(python), "-c", "import torch; print(torch.__version__)"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = first_line(completed.stdout)
    return version or None
