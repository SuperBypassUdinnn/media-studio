"""Local toolchain detection used by ``ms doctor``.

The probes are deliberately shallow: presence plus one version line. Deep checks that cost
seconds (torch imports, GPU device files) only run behind ``--deep``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROBE_TIMEOUT_SECONDS = 30
VERSION_WIDTH = 96
SYSTEM_BIN = "/usr/bin"
LOCAL_BIN = "~/.local/bin"
NVIDIA_VULKAN_ICD = Path("/usr/share/vulkan/icd.d/nvidia_icd.json")
TORCH_VENVS = (
    ("DeepFilterNet", Path("~/.clone/Github/DeepFilterNet/.venv")),
    ("demucs (UVR)", Path("~/.clone/Github/ultimatevocalremovergui/.venv")),
)
EFFECTCRAFT_UPDATE_CACHE = Path("~/.cache/media-studio/effectcraft-update.json")


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
    ToolSpec("melt", ("--version",), True, "MLT timeline rendering (legacy fallback)"),
    ToolSpec("inkscape", ("--version",), True, "SVG to PNG and PDF"),
    ToolSpec("magick", ("-version",), True, "raster post-processing"),
    ToolSpec("node", ("--version",), True, "3d-asset-server and gltf-transform"),
    ToolSpec("npm", ("--version",), True, "Node package management"),
    ToolSpec("uv", ("--version",), True, "Python environments"),
    ToolSpec("filmcraft-cli", ("--version",), True, "timeline and NLE engine"),
    ToolSpec("effectcraft-cli", ("--version",), True, "motion graphics engine"),
    ToolSpec("kdenlive", ("--version",), False, "manual timeline handoff (legacy)"),
    ToolSpec("git", ("--version",), False, "project versioning"),
    ToolSpec(
        "vtracer",
        (sys.executable, "-c", "import vtracer"),
        False,
        "colour tracing (optional extra 'vector')",
    ),
    ToolSpec(
        "playwright",
        (sys.executable, "-c", "import playwright"),
        False,
        "HTML/SVG-to-video rendering (optional extra 'render-html')",
    ),
    ToolSpec(
        "edge-tts",
        (sys.executable, "-c", "import edge_tts"),
        False,
        "free neural TTS (optional extra 'tts')",
    ),
    ToolSpec(
        "httpx",
        (sys.executable, "-c", "import httpx"),
        False,
        "generative provider HTTP client (optional extra 'gen')",
    ),
    ToolSpec(
        "google-genai",
        (sys.executable, "-c", "import google.genai"),
        False,
        "Google AI Studio generative client (optional extra 'gen')",
    ),
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
        # A probe whose first element is sys.executable checks a Python module
        # (for example ``vtracer``); run it directly rather than resolving
        # spec.name as a binary, since a same-named executable may exist.
        if spec.probe and spec.probe[0] == sys.executable:
            probe_cmd = [sys.executable, *spec.probe[1:]]
            try:
                completed = run(
                    probe_cmd,
                    capture_output=True,
                    text=True,
                    timeout=PROBE_TIMEOUT_SECONDS,
                    check=False,
                )
                if completed.returncode == 0:
                    version = first_line(completed.stdout or "") or None
                else:
                    version = None
            except (OSError, subprocess.SubprocessError):
                completed = None
                version = None
            statuses.append(
                ToolStatus(
                    spec=spec,
                    path=(
                        sys.executable
                        if (completed is not None and completed.returncode == 0)
                        else None
                    ),
                    version=version,
                )
            )
            continue
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
            if completed.returncode == 0:
                version = first_line(completed.stdout or "") or None
            else:
                version = None
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
            results.append(
                CheckResult(
                    f"torch {label}",
                    False,
                    f"optional: missing venv {expanded}; ffmpeg vocal cleanup fallback active",
                )
            )
            continue
        version = python_probe(expanded)
        if version is None:
            results.append(
                CheckResult(
                    f"torch {label}",
                    False,
                    "optional: torch not importable in "
                    f"{expanded}; ffmpeg vocal cleanup fallback active",
                )
            )
        else:
            results.append(CheckResult(f"torch {label}", True, version))

    # Blender can be present and still refuse to start (a library upgrade mismatch did exactly
    # that once), so the version is read through the same environment a render uses.
    try:
        from .blender import BlenderError, environment, version_string

        built = environment(create_shim=False)
        detail = version_string()
        if built.notes:
            detail = f"{detail} ({built.describe()})"
        results.append(CheckResult("Blender headless", True, detail))
    except BlenderError as error:
        results.append(CheckResult("Blender headless", False, str(error)))
    except Exception as error:
        results.append(CheckResult("Blender headless", False, f"cannot run: {error}"))

    usage = shutil.disk_usage(Path.home())
    free_gib = usage.free / (1024**3)
    results.append(
        CheckResult(
            "disk free (home)",
            ok=free_gib >= 20,
            detail=f"{free_gib:.1f} GiB free",
        )
    )

    # Playwright plus a cached Chromium binary (optional render-html extra).
    try:  # pragma: no cover - environment probe
        import playwright  # noqa: F401

        cache = Path.home() / ".cache" / "ms-playwright"
        patterns = (
            "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
            "chromium-*/chrome-linux64/chrome",
        )
        has_chromium = cache.is_dir() and any(cache.glob(pattern) for pattern in patterns)
        results.append(
            CheckResult(
                "Playwright Chromium",
                has_chromium,
                "cached Chromium present under ~/.cache/ms-playwright/"
                if has_chromium
                else "playwright installed but no cached Chromium binary",
            )
        )
    except ImportError:
        results.append(
            CheckResult(
                "Playwright Chromium",
                False,
                "playwright not installed (install media-studio[render-html] + a Chromium binary)",
            )
        )

    # Optional Python dependencies reported as checks (never fatal).
    for module_name, detail, label in (
        (
            "edge_tts",
            "edge-tts not installed (install media-studio[tts])",
            "edge-tts",
        ),
        (
            "httpx",
            "httpx not installed (install media-studio[gen])",
            "httpx",
        ),
        (
            "google.genai",
            "google-genai not installed (install media-studio[gen])",
            "google-genai",
        ),
    ):
        try:  # pragma: no cover - environment probe
            __import__(module_name)
            results.append(CheckResult(label, True, "installed"))
        except ImportError:
            results.append(CheckResult(label, False, detail))

    engines_cfg = Path("~/.config/opencode/media-engines.json").expanduser()
    if engines_cfg.is_file():
        try:
            with engines_cfg.open("r", encoding="utf-8") as f:
                data = json.load(f)
            engines = data.get("engines", {})
            for name, meta in engines.items():
                cli_path = Path(meta.get("cli", ""))
                version = meta.get("version", "unknown")
                if cli_path.is_file() and os.access(cli_path, os.X_OK):
                    results.append(CheckResult(f"engine {name}", True, f"v{version} at {cli_path}"))
                else:
                    results.append(
                        CheckResult(f"engine {name}", False, f"pinned binary missing: {cli_path}")
                    )
        except Exception as err:
            results.append(CheckResult("media-engines.json", False, f"parse error: {err}"))
    else:
        results.append(CheckResult("media-engines.json", False, f"missing: {engines_cfg}"))

    # Automated EffectCraft update check (uses cached metadata to avoid slowing down checks)
    ec_update = check_effectcraft_update()
    if ec_update.get("error"):
        results.append(CheckResult("EffectCraft update", True, f"skipped ({ec_update['error']})"))
    elif ec_update.get("update_available"):
        cur = ec_update["installed"]
        lat = ec_update["latest"]
        results.append(
            CheckResult(
                "EffectCraft update",
                False,
                f"update available: v{cur} -> v{lat} ({ec_update['url']})",
            )
        )
    else:
        results.append(
            CheckResult(
                "EffectCraft update",
                True,
                f"up to date (v{ec_update.get('installed', 'unknown')})",
            )
        )

    return results


def _is_newer(latest: str | None, current: str | None) -> bool:
    if not latest or not current or current == "unknown":
        return False

    def nums(s: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", s))

    return nums(latest) > nums(current)


def check_effectcraft_update(
    *,
    installed_version: str | None = None,
    cache_path: Path | None = None,
    cache_ttl_seconds: int = 86400,
    timeout: float = 2.0,
    force: bool = False,
    opener: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Check whether a newer release of EffectCraft is available on GitHub."""
    if installed_version is None:
        cli = shutil.which("effectcraft-cli")
        if cli:
            try:
                proc = subprocess.run(
                    [cli, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if proc.returncode == 0:
                    line = first_line(proc.stdout or proc.stderr)
                    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", line)
                    if m:
                        installed_version = m.group(1)
            except Exception:
                pass

    current_ver = installed_version or "unknown"
    release_url = "https://github.com/storytold/effectcraft/releases/latest"
    api_url = "https://api.github.com/repos/storytold/effectcraft/releases/latest"

    target_cache = (cache_path or EFFECTCRAFT_UPDATE_CACHE).expanduser()
    now_ts = time.time()

    if not force and target_cache.is_file():
        try:
            with target_cache.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_ts = cached.get("checked_at", 0)
            if now_ts - cached_ts < cache_ttl_seconds:
                latest_ver = cached.get("latest")
                return {
                    "installed": current_ver,
                    "latest": latest_ver,
                    "update_available": _is_newer(latest_ver, current_ver),
                    "error": cached.get("error"),
                    "url": release_url,
                }
        except Exception:
            pass

    latest_ver = None
    fetch_error = None
    if opener is not None:
        try:
            latest_ver = opener()
        except Exception as err:
            fetch_error = str(err)
    else:
        try:
            import urllib.request

            req = urllib.request.Request(api_url, headers={"User-Agent": "media-studio"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tag = data.get("tag_name", "").lstrip("v")
                m = re.search(r"(\d+\.\d+(?:\.\d+)?)", tag)
                if m:
                    latest_ver = m.group(1)
        except Exception as err:
            fetch_error = f"network error: {err}"

    try:
        target_cache.parent.mkdir(parents=True, exist_ok=True)
        with target_cache.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "checked_at": now_ts,
                    "latest": latest_ver,
                    "error": fetch_error,
                },
                f,
            )
    except Exception:
        pass

    return {
        "installed": current_ver,
        "latest": latest_ver,
        "update_available": _is_newer(latest_ver, current_ver) if latest_ver else False,
        "error": fetch_error,
        "url": release_url,
    }


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
