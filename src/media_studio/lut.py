"""Color LUT grading for media-studio.

Bundles a small set of minimal but syntactically valid ``.cube`` LUTs and
applies them to video through ffmpeg's ``lut3d`` filter. Also searches bundled
LUTs alongside library preset entries tagged ``lut``.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .ingest import IngestError, probe_media
from .library import LIBRARY_ROOT, LibraryError, load_catalog

BUNDLED_LUTS_DIR: Path = Path(__file__).parent / "data" / "luts"


class LutError(RuntimeError):
    """Raised when a LUT cannot be located, validated, or applied."""


def _resolve_lut(lut: str | Path, *, library_root: Path = LIBRARY_ROOT) -> Path:
    """Resolve a LUT argument to a ``.cube`` path.

    A UUID that matches a library catalog entry is resolved to its
    ``file_path``; anything else is treated as a direct file path.
    """

    lut = Path(lut) if not isinstance(lut, Path) else lut
    # A direct .cube path takes precedence.
    if lut.suffix.lower() == ".cube" and lut.is_file():
        return lut

    # Otherwise try to resolve a library ID against the catalog.
    try:
        entries = load_catalog(root=library_root)
    except LibraryError:
        entries = []
    for entry in entries:
        if entry.id == str(lut):
            return Path(entry.file_path)
    return lut


def _run_ffmpeg(argv: list[str]) -> None:
    """Run an ffmpeg command, raising LutError on failure."""

    try:
        subprocess.run(argv, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise LutError("ffmpeg is not installed") from error
    except subprocess.CalledProcessError as error:
        raise LutError(
            f"ffmpeg failed ({error.returncode}):\n{error.stderr.strip()[-2000:]}"
        ) from error


def apply_lut(
    source: Path,
    lut: Path,
    destination: Path,
    *,
    codec: str = "h264",
) -> Path:
    """Apply a ``.cube`` LUT to ``source`` and write ``destination``.

    The source is converted to ``yuv420p`` before the ``lut3d`` filter (which
    only accepts 8-bit 4:2:0), then converted back to the target pixel format
    for the encoder. For ProRes the picture is promoted to ``yuv444p10le``
    after grading before encoding with ``prores_ks``.

    Raises:
        LutError: the LUT is not a ``.cube`` file, the source is missing, or
            ffmpeg fails.
    """

    source = Path(source)
    lut = Path(lut)
    destination = Path(destination)

    if lut.suffix.lower() != ".cube":
        raise LutError(f"LUT must be a .cube file, got {lut!r}")

    if not source.is_file():
        raise LutError(f"no such file: {source}")

    # Probe the source so a missing/unreadable file surfaces clearly.
    try:
        probe_media(source)
    except IngestError as error:
        raise LutError(str(error)) from error

    destination.parent.mkdir(parents=True, exist_ok=True)

    if codec == "prores":
        vf = f"format=yuv420p,lut3d={lut},format=yuv444p10le"
        encoder = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le"]
    else:
        vf = f"format=yuv420p,lut3d={lut}"
        encoder = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]

    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            vf,
            *encoder,
            str(destination),
        ]
    )

    if not destination.is_file():
        raise LutError(f"ffmpeg reported success but wrote no file: {destination}")
    return destination


def preview_lut(lut: Path, reference: Path, out: Path) -> Path:
    """Extract frame 0 of ``reference``, grade it with ``lut``, and hstack.

    Writes a side-by-side PNG (original | graded) to ``out`` and returns it.
    The output PNG width is double the reference frame width.
    """

    lut = Path(lut)
    reference = Path(reference)
    out = Path(out)

    if not reference.is_file():
        raise LutError(f"no such file: {reference}")
    if lut.suffix.lower() != ".cube":
        raise LutError(f"LUT must be a .cube file, got {lut!r}")

    with tempfile.TemporaryDirectory(prefix="media-studio-lut-preview-") as staging:
        staging_path = Path(staging)
        tmp_frame = staging_path / "frame.png"
        tmp_graded = staging_path / "graded.png"

        _run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(reference),
                "-vframes",
                "1",
                "-ss",
                "0",
                str(tmp_frame),
            ]
        )

        _run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(tmp_frame),
                "-vf",
                f"lut3d={lut}",
                str(tmp_graded),
            ]
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(tmp_frame),
                "-i",
                str(tmp_graded),
                "-filter_complex",
                "hstack",
                str(out),
            ]
        )

    if not out.is_file():
        raise LutError(f"ffmpeg reported success but wrote no file: {out}")
    return out


def search_luts(
    query: str = "",
    *,
    library_root: Path = LIBRARY_ROOT,
) -> list[Path]:
    """Return bundled LUTs plus library preset entries tagged ``lut``.

    Bundled LUTs come first, then library matches. When ``query`` is given,
    only entries whose filename contains it (case-insensitive) are returned.
    """

    results: list[Path] = sorted(BUNDLED_LUTS_DIR.glob("*.cube"))

    catalog_path = library_root / "catalog.json"
    if catalog_path.is_file():
        try:
            entries = load_catalog(root=library_root)
        except LibraryError:
            entries = []
        for entry in entries:
            if entry.type == "preset" and "lut" in set(entry.tags):
                file_path = Path(entry.file_path)
                if file_path.is_file():
                    results.append(file_path)

    if query:
        q = query.lower()
        results = [p for p in results if q in p.name.lower()]

    return results
