"""Natron headless compositing for AMV production.

Generates Natron ``.ntp`` project files and renders them through
``NatronRenderer`` (headless, CPU-based, no VRAM). Natron (GPLv2) is a
node-based compositor equivalent to Nuke/After Effects: OFX Glow, Blur,
ChromaKey, Roto, Tracker, Grade, OpenColorIO LUTs, plus keyframe-able node
parameters driven by Python API.

Nothing in this module runs unless explicitly called: if ``NatronRenderer`` is
not on ``PATH`` every public function raises :class:`NatronError` with an
installation hint, so the rest of the toolkit stays usable on a machine
without Natron.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from xml.dom import minidom

from .project import default_project_root
from .video import qc_report

NATRON_RENDERER = shutil.which("NatronRenderer")  # /usr/bin or config path
NATRON = shutil.which("Natron")  # GUI binary, verification only


class NatronError(RuntimeError):
    """Raised when Natron is unavailable, a project is malformed, or a render fails."""


@dataclass(frozen=True)
class CompositeResult:
    """Outcome of one Natron composite pass."""

    output: Path
    """Composited video file."""

    ntp_path: Path
    """Generated Natron project file (``.ntp``)."""

    report_path: Path
    """JSON report written beside the output."""

    report: dict
    """Machine-readable report (duration, loudness, checks, problems)."""

    @property
    def ok(self) -> bool:
        return bool(self.report.get("ok", False)) and self.output.exists()


def check_installation() -> None:
    """Raise :class:`NatronError` if ``NatronRenderer`` is not on ``PATH``.

    The hint names the package (pacman/Arch, flatpak/Flathub) so a person can
    install it without searching docs.
    """

    if NATRON_RENDERER is None:
        hint = (
            "NatronRenderer is not on PATH. Install Natron so ms video composite works:\n"
            "  pacman -S natron          # Arch / CachyOS community package\n"
            "  flatpak install flathub fr.natron.Natron  # Flatpak (binary may be sandboxed)\n"
            "Then re-run with NatronRenderer on PATH, or run without compositing."
        )
        raise NatronError(hint)


def build_ntp(nodes: list[dict], output_path: Path) -> Path:
    """Serialise a Natron node graph as a ``.ntp`` XML document.

    Each entry in ``nodes`` is a dict with:

    * ``pluginID`` — Natron plugin identifier, e.g. ``"fr.inria.Builtin-Grade"``.
    * ``params`` — list of ``{"name": ..., "value": ...}`` parameter pairs.
    * ``inputs`` — optional list of upstream node names (connection order).

    The output is valid XML that ``ElementTree`` can parse back, so unit tests
    can round-trip without Natron installed. The same schema opens in the
    Natron GUI for human inspection.

    Returns the path written.
    """

    root = ElementTree.Element("natron", {"version": "2.5"})
    project = ElementTree.SubElement(root, "project")
    project.set("name", output_path.stem)
    nodes_el = ElementTree.SubElement(project, "nodes")

    for node in nodes:
        _validate_node(node)
        el = ElementTree.SubElement(nodes_el, "node")
        el.set("name", node.get("name", node["pluginID"]))
        el.set("pluginID", node["pluginID"])
        params_el = ElementTree.SubElement(el, "params")
        for param in node.get("params", []):
            p = ElementTree.SubElement(params_el, "param")
            p.set("name", param["name"])
            p.text = str(param["value"])
        inputs = node.get("inputs", [])
        if inputs:
            inputs_el = ElementTree.SubElement(el, "inputs")
            for i, upstream in enumerate(inputs):
                inp = ElementTree.SubElement(inputs_el, "input")
                inp.set("index", str(i))
                inp.text = upstream

    # Find every Write node and declare it as a render target.
    writes_el = ElementTree.SubElement(project, "writes")
    for node in nodes:
        if node["pluginID"] == "fr.inria.Builtin-Write":
            w = ElementTree.SubElement(writes_el, "write")
            w.set("node", node.get("name", node["pluginID"]))
            for param in node.get("params", []):
                if param["name"] == "filename":
                    w.set("file", str(param["value"]))

    pretty = minidom.parseString(ElementTree.tostring(root, encoding="unicode")).toprettyxml(
        indent="  "
    )
    output_path.write_text(pretty, encoding="utf-8")
    return output_path


def _validate_node(node: dict) -> None:
    if "pluginID" not in node:
        raise NatronError("node missing pluginID")
    if not isinstance(node.get("params", []), list):
        raise NatronError("node params must be a list")
    for param in node.get("params", []):
        if "name" not in param or "value" not in param:
            raise NatronError("each param needs name and value")


def simple_grade(
    source: Path,
    output: Path,
    ntp_path: Path,
    *,
    lut: Path | None = None,
    gain: float = 1.0,
    gamma: float = 1.0,
    saturation: float = 1.0,
    glow_size: float = 0.0,
    glow_mix: float = 0.5,
    vignette: float = 0.0,
) -> CompositeResult:
    """Build a minimal Natron project applying grade + glow + vignette and render it.

    Parameters mirror the CLI flags in :mod:`media_studio.cli`. All defaults are
    pass-through (no colour shift), so a default render is an identity except
    for the encode re-generation.

    Returns a :class:`CompositeResult`.
    """

    check_installation()

    nodes: list[dict] = [
        {
            "name": "Read1",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [
                {"name": "filename", "value": str(source)},
            ],
        },
    ]

    # Grade node.
    grade_params = [
        {"name": "gain", "value": gain},
        {"name": "gamma", "value": gamma},
        {"name": "saturation", "value": saturation},
    ]
    if lut is not None:
        grade_params.append({"name": "ocioFileTransform", "value": str(lut)})
    nodes.append(
        {
            "name": "Grade1",
            "pluginID": "fr.inria.Builtin-Grade",
            "inputs": ["Read1"],
            "params": grade_params,
        }
    )

    # Optional Glow node.
    if glow_size > 0:
        nodes.append(
            {
                "name": "Glow1",
                "pluginID": "fr.inria.Ofx-Glow",
                "inputs": ["Grade1"],
                "params": [
                    {"name": "size", "value": glow_size},
                    {"name": "mix", "value": glow_mix},
                ],
            }
        )

    # Optional Vignette node.
    if vignette > 0:
        nodes.append(
            {
                "name": "Vignette1",
                "pluginID": "fr.inria.Ofx-Vignette",
                "inputs": ["Glow1" if glow_size > 0 else "Grade1"],
                "params": [{"name": "strength", "value": vignette}],
            }
        )

    # Write node — target output file.
    last = "Vignette1" if vignette > 0 else ("Glow1" if glow_size > 0 else "Grade1")
    nodes.append(
        {
            "name": "Write1",
            "pluginID": "fr.inria.Builtin-Write",
            "inputs": [last],
            "params": [{"name": "filename", "value": str(output)}],
        }
    )

    for node in nodes:
        _validate_node(node)

    ntp_path = build_ntp(nodes, ntp_path)
    return render(ntp_path, output)


def composite_layers(
    background: Path,
    foreground: Path,
    output: Path,
    ntp_path: Path,
    *,
    merge_op: str = "over",
    mask: Path | None = None,
) -> CompositeResult:
    """Merge ``foreground`` over ``background`` using the given Merge operator.

    Returns a :class:`CompositeResult`.
    """

    merge_ops = {"over", "screen", "add", "multiply", "difference", "min", "max"}
    if merge_op not in merge_ops:
        raise NatronError(
            f"unknown merge operator {merge_op!r}; expected one of {sorted(merge_ops)}"
        )

    check_installation()

    inputs = [background]
    if mask is not None:
        inputs.append(mask)
    inputs.append(foreground)

    nodes: list[dict] = [
        {
            "name": "ReadBg",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [{"name": "filename", "value": str(background)}],
        },
        {
            "name": "ReadFg",
            "pluginID": "fr.inria.Builtin-Read",
            "params": [{"name": "filename", "value": str(foreground)}],
        },
    ]
    for i, path in enumerate(inputs[1:], start=1):
        nodes.append(
            {
                "name": f"ReadIn{i}",
                "pluginID": "fr.inria.Builtin-Read",
                "params": [{"name": "filename", "value": str(path)}],
            }
        )

    nodes.append(
        {
            "name": "Merge1",
            "pluginID": "fr.inria.Builtin-Merge2",
            "inputs": [n["name"] for n in nodes[:2]]
            + [f"ReadIn{i}" for i in range(1, len(inputs) - 1)],
            "params": [{"name": "operator", "value": merge_op}],
        }
    )
    nodes.append(
        {
            "name": "Write1",
            "pluginID": "fr.inria.Builtin-Write",
            "inputs": ["Merge1"],
            "params": [{"name": "filename", "value": str(output)}],
        }
    )

    for node in nodes:
        _validate_node(node)

    ntp_path = build_ntp(nodes, ntp_path)
    return render(ntp_path, output)


def render(
    project_path: Path, output: Path, *, extra_args: list[str] | None = None
) -> CompositeResult:
    """Run ``NatronRenderer`` on a ``.ntp`` file and return the output path.

    Writes a JSON report beside the output with duration, checks, and problems.
    """

    check_installation()

    cmd = [NATRON_RENDERER, str(project_path), "-W", str(output)]
    if extra_args:
        cmd.extend(extra_args)

    completed = subprocess.run(cmd, capture_output=True, text=True)

    report: dict = {
        "tool": "natron",
        "renderer": NATRON_RENDERER,
        "project": str(project_path),
        "output": str(output),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0 and output.exists(),
    }

    problems: list[str] = []
    if completed.returncode != 0:
        problems.append(f"NatronRenderer exited {completed.returncode}")
        if completed.stderr:
            problems.append(completed.stderr.strip().splitlines()[-1])
    if not output.exists():
        problems.append(f"no output written: {output}")

    report_path = output.with_suffix(".json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Re-run QC on the composited output so callers can trust duration/loudness.
    try:
        qc = qc_report(output, None, None)
        report["qc"] = qc
        if not qc.get("ok", True):
            problems.extend(qc.get("problems", []))
    except Exception as error:
        problems.append(f"QC unavailable: {error}")

    report["ok"] = report["ok"] and not problems
    report["problems"] = problems
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return CompositeResult(
        output=output,
        ntp_path=project_path,
        report_path=report_path,
        report=report,
    )


def composite_project(
    deliverable: str,
    compositing: dict,
    project_root: Path | None = None,
    output_dir: Path | None = None,
) -> CompositeResult:
    """Composite one deliverable using ``compositing`` settings from ``project.yaml``.

    ``compositing`` is expected to look like::

        compositing:
          grade:
            lut: assets/luts/film_emulation.cube   # optional
            gain: 1.05
            gamma: 0.96
            saturation: 1.1
          effects:
            - glow:
                size: 3.0
                mix: 0.35
                threshold: 0.7
            - vignette:
                strength: 0.25

    The source is the last rendered deliverable (``out/video/<deliverable>.mp4``),
    and the composited output is written next to it as
    ``out/video/<deliverable>-comp.mp4``. Audio is muxed back from the source
    with FFmpeg ``-c:a copy`` because NatronRenderer is video-only.

    Returns a :class:`CompositeResult`.
    """

    check_installation()

    if project_root is None:
        project_root = default_project_root()

    # Source: the previously rendered deliverable (default container is mp4).
    source = project_root / "out" / "video" / f"{deliverable}.mp4"
    if not source.exists():
        raise NatronError(f"no rendered deliverable to composite: {source}")

    if output_dir is None:
        output_dir = project_root / "out" / "video"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{deliverable}-comp.mp4"
    ntp_path = output_dir / f"{deliverable}.ntp"

    grade = compositing.get("grade", {})
    lut = None
    if grade.get("lut"):
        lut = project_root / grade["lut"]
        if not lut.exists():
            raise NatronError(f"LUT not found: {lut}")

    glow_size = 0.0
    glow_mix = 0.5
    vignette = 0.0
    for effect in compositing.get("effects", []):
        for name, params in effect.items():
            if name == "glow":
                glow_size = float(params.get("size", 3.0))
                glow_mix = float(params.get("mix", 0.5))
            elif name == "vignette":
                vignette = float(params.get("strength", 0.25))

    result = simple_grade(
        source,
        output,
        ntp_path,
        lut=lut,
        gain=float(grade.get("gain", 1.0)),
        gamma=float(grade.get("gamma", 1.0)),
        saturation=float(grade.get("saturation", 1.0)),
        glow_size=glow_size,
        glow_mix=glow_mix,
        vignette=vignette,
    )

    # NatronRenderer is video-only: mux the source audio back onto the comp.
    if result.ok:
        audio_out = output.with_suffix(".with-audio.mp4")
        mux = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(output),
                "-i",
                str(source),
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0?",
                str(audio_out),
            ],
            capture_output=True,
            text=True,
        )
        if mux.returncode == 0:
            audio_out.replace(output)
            result.report["audio"] = "muxed from source"
        else:
            result.report["audio"] = (
                f"mux failed: {mux.stderr.strip().splitlines()[-1] if mux.stderr else 'unknown'}"
            )
            result.report["ok"] = False

    result.report_path.write_text(json.dumps(result.report, indent=2), encoding="utf-8")
    return result
