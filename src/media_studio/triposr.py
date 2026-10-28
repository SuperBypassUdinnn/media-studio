"""TripoSR image-to-3D: headless conversion of a reference image into a textured mesh.

TripoSR (MIT license, offline after install, no API key) turns a single reference image into a
draft-quality textured OBJ/GLB mesh. It runs on a GTX 1650 Max-Q 4GB via ``--chunk-size 4096``
(or ``--device cpu`` as a slower fallback). The output pipes into the existing Blender pipeline:
``ms 3d convert`` → ``ms 3d material`` → ``ms 3d turntable``.

Nothing in this module runs unless explicitly called: if ``triposr`` is not available every public
function raises :class:`TripoSRError` with an installation hint, so the rest of the toolkit stays
usable without TripoSR.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TRIPOSR = shutil.which("triposr")  # `pip install triposr`, or python -m triposr.cli


class TripoSRError(RuntimeError):
    """Raised when TripoSR is unavailable, conversion fails, or disk is too low."""


@dataclass(frozen=True)
class ImageToMeshResult:
    """Outcome of one TripoSR conversion."""

    output: Path
    """Generated mesh file (OBJ or GLB)."""

    report_path: Path
    """JSON report written beside the output."""

    report: dict
    """Machine-readable report (ok, chunksize, device, problems)."""

    @property
    def ok(self) -> bool:
        return bool(self.report.get("ok", False)) and self.output.exists()


def check_installation() -> None:
    """Raise :class:`TripoSRError` if TripoSR is not available.

    The hint names ``pip install triposr`` (MIT) and notes that model weights (~3-5GB) download
    automatically on first run, so a storage check should happen first.
    """

    if TRIPOSR is None:
        hint = (
            "triposr is not on PATH. Install TripoSR so ms 3d image-to-mesh works:\n"
            "  pip install triposr          # MIT license, offline after install\n"
            "First run downloads model weights (~3-5GB); check disk space with storage-doctor\n"
            "before installing. Then re-run with triposr on PATH, or skip image-to-mesh."
        )
        raise TripoSRError(hint)


def vram_safe_chunk_size() -> int:
    """Return a VRAM-safe ``--chunk-size`` for the local GPU.

    Probes ``nvidia-smi`` for total VRAM and returns a conservative chunk size: 4096 for
    4GB cards (GTX 1650 Max-Q), scaling up for larger GPUs. Falls back to 4096 when
    nvidia-smi is unavailable or unparseable.
    """

    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            total_mb = int(completed.stdout.strip().splitlines()[0])
            # Conservative: use no more than ~1/3 of VRAM for the working chunk.
            if total_mb <= 6000:
                return 4096
            if total_mb <= 8000:
                return 6144
            return 8192
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return 4096


def image_to_mesh(
    image_path: Path,
    output_path: Path,
    *,
    chunk_size: int | None = None,
    device: str = "cuda",
    mc_resolution: int = 256,
    remove_bg: bool = True,
) -> ImageToMeshResult:
    """Run TripoSR on ``image_path`` and write a textured OBJ/GLB to ``output_path``.

    ``chunk_size`` defaults to a VRAM-safe value from :func:`vram_safe_chunk_size`. ``device`` is
    ``"cuda"`` (GPU) or ``"cpu"`` (slow fallback when CUDA OOMs). ``mc_resolution`` is the
    marching-cubes resolution. ``remove_bg`` strips the background before conversion.

    Returns an :class:`ImageToMeshResult`.
    """

    check_installation()

    if chunk_size is None:
        chunk_size = vram_safe_chunk_size()

    image_path = Path(image_path).expanduser()
    output_path = Path(output_path).expanduser()
    if not image_path.is_file():
        raise TripoSRError(f"no such reference image: {image_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        TRIPOSR,
        "predict",
        str(image_path),
        "--output",
        str(output_path),
        "--chunk-size",
        str(chunk_size),
        "--device",
        device,
        "--mc-resolution",
        str(mc_resolution),
    ]
    if remove_bg:
        cmd.append("--remove-bg")

    completed = subprocess.run(cmd, capture_output=True, text=True)

    report: dict = {
        "tool": "triposr",
        "binary": TRIPOSR,
        "image": str(image_path),
        "output": str(output_path),
        "chunk_size": chunk_size,
        "device": device,
        "mc_resolution": mc_resolution,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0 and output_path.exists(),
    }

    problems: list[str] = []
    if completed.returncode != 0:
        problems.append(f"triposr exited {completed.returncode}")
        if completed.stderr:
            problems.append(completed.stderr.strip().splitlines()[-1])
    if not output_path.exists():
        problems.append(f"no output written: {output_path}")

    report_path = output_path.with_suffix(".json")
    import json

    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    report["ok"] = report["ok"] and not problems
    report["problems"] = problems
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return ImageToMeshResult(
        output=output_path,
        report_path=report_path,
        report=report,
    )


def image_to_mesh_project(
    image_path: Path,
    project_root: Path | None = None,
    output_dir: Path | None = None,
    *,
    chunk_size: int | None = None,
    device: str = "cuda",
    mc_resolution: int = 256,
    remove_bg: bool = True,
) -> ImageToMeshResult:
    """Convert a reference image into a mesh inside a project's ``out/3d/`` directory.

    The output is named after the image stem (``<stem>-base.obj`` / ``.glb``) and is ready to feed
    into ``ms 3d convert`` → ``ms 3d material`` → ``ms 3d turntable``.

    Returns an :class:`ImageToMeshResult`.
    """

    check_installation()

    from .project import default_project_root

    if project_root is None:
        project_root = default_project_root()

    if output_dir is None:
        output_dir = project_root / "out" / "3d"
    output_dir.mkdir(parents=True, exist_ok=True)

    image_path = Path(image_path).expanduser()
    stem = image_path.stem
    output = output_dir / f"{stem}-base.obj"

    return image_to_mesh(
        image_path,
        output,
        chunk_size=chunk_size,
        device=device,
        mc_resolution=mc_resolution,
        remove_bg=remove_bg,
    )
