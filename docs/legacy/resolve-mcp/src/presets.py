"""
LUT finder, installer, and Fusion macro generator for the Resolve MCP server.

LUTs are discovered from local library directories and installed to the Resolve
user LUT folder. Fusion macros are generated as .setting files from a template
that the LLM fills in, then loaded into Resolve via the Python API.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

# Resolve's user LUT directory on Linux
_RESOLVE_LUT_DIR = Path.home() / ".local/share/DaVinciResolve/Fusion/LUTs"
# Resolve's user Macro directory on Linux
_RESOLVE_MACRO_DIR = Path.home() / ".local/share/DaVinciResolve/Fusion/Macros"

# Optional local LUT library (additional search path, e.g. ~/LUTs or ~/luts)
_LOCAL_LUT_SEARCH = [
    Path.home() / "LUTs",
    Path.home() / "luts",
    Path.home() / "Documents/LUTs",
]


class PresetError(Exception):
    """Raised when a LUT or macro operation fails."""


# ---------------------------------------------------------------------------
# LUT management
# ---------------------------------------------------------------------------


def list_installed_luts() -> list[dict]:
    """
    Return all .cube LUT files installed in the Resolve user LUT directory.

    Each entry is a dict with keys: name, path, size_kb.
    """
    if not _RESOLVE_LUT_DIR.exists():
        return []
    result = []
    for f in sorted(_RESOLVE_LUT_DIR.rglob("*.cube")):
        result.append(
            {
                "name": f.stem,
                "path": str(f),
                "size_kb": round(f.stat().st_size / 1024, 1),
            }
        )
    return result


def find_lut(name: str) -> Path | None:
    """
    Search for a .cube LUT by name (case-insensitive, partial match) in:
    1. Installed Resolve user LUT directory
    2. Local LUT search paths

    Returns the first matching Path, or None.
    """
    needle = name.lower()
    search_dirs: list[Path] = [_RESOLVE_LUT_DIR, *_LOCAL_LUT_SEARCH]
    for directory in search_dirs:
        if not directory.exists():
            continue
        for f in directory.rglob("*.cube"):
            if needle in f.stem.lower():
                return f
    return None


def install_lut(source: Path, subfolder: str = "") -> Path:
    """
    Copy a .cube LUT file into the Resolve user LUT directory (optionally into a subfolder).

    Returns the destination path.
    Raises PresetError if the source file does not exist or is not a .cube file.
    """
    if not source.exists():
        raise PresetError(f"LUT file not found: {source}")
    if source.suffix.lower() != ".cube":
        raise PresetError(f"Expected a .cube file, got: {source.suffix}")

    dest_dir = _RESOLVE_LUT_DIR / subfolder if subfolder else _RESOLVE_LUT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    shutil.copy2(source, dest)
    return dest


def apply_lut_to_clip(clip, lut_path: Path) -> None:
    """
    Apply a LUT to a MediaPoolItem clip via the Resolve Python API.

    The clip must be a DaVinci Resolve MediaPoolItem object obtained from the bridge.
    Raises PresetError if the operation fails.
    """
    result = clip.SetClipProperty("LUT", str(lut_path))
    if not result:
        raise PresetError(
            f"Failed to apply LUT {lut_path.name} to clip. "
            "Ensure Resolve is open and the LUT path is accessible."
        )


# ---------------------------------------------------------------------------
# Fusion macro generator
# ---------------------------------------------------------------------------

# Minimal valid Fusion macro .setting template.
# Macro type: "Tool" (a reusable node sub-graph).
# The generated .setting is text-based and can be loaded via comp:AddTool().
_MACRO_TEMPLATE = textwrap.dedent(
    """\
    {
        Tools = ordered() {
            %(name)s = MacroOperator {
                Inputs = ordered() {
    %(inputs)s
                },
                Outputs = ordered() {
    %(outputs)s
                },
                ViewInfo = OperatorInfo { Pos = { 0, 0 } },
                Tools = ordered() {
    %(tools)s
                },
            }
        }
    }
    """
)


def generate_fusion_macro(
    name: str,
    description: str,
    nodes: list[dict],
    inputs: list[dict] | None = None,
    outputs: list[dict] | None = None,
) -> str:
    """
    Generate a Fusion macro .setting file as a string.

    Parameters
    ----------
    name:
        Macro name (no spaces; used as the Fusion tool name).
    description:
        Human-readable description (stored as a comment in the file).
    nodes:
        List of node dicts, each with keys:
          - id: unique tool ID string (e.g. "ColorCorrector1")
          - type: Fusion tool type (e.g. "ColorCorrector", "Blur", "Brightness")
          - params: dict of parameter name -> value pairs
          - pos: optional [x, y] position for the node graph
    inputs:
        List of macro input dicts, each with keys:
          - id: input name as seen from outside the macro
          - source: "ToolID.InputName" inside the macro
    outputs:
        List of macro output dicts, each with keys:
          - id: output name as seen from outside the macro
          - source: "ToolID.OutputName" inside the macro

    Returns the .setting file content as a string.
    """
    inputs = inputs or []
    outputs = outputs or []

    inputs_str = ""
    for inp in inputs:
        src_parts = inp["source"].split(".")
        src_op, src_name = src_parts[0], src_parts[-1]
        inputs_str += (
            f"                    {inp['id']} = Input {{"
            f' SourceOp = "{src_op}", Source = "{src_name}", }},\n'
        )

    outputs_str = ""
    for out in outputs:
        src_parts = out["source"].split(".")
        src_op, src_name = src_parts[0], src_parts[-1]
        outputs_str += (
            f"                    {out['id']} = Output {{"
            f' SourceOp = "{src_op}", Source = "{src_name}", }},\n'
        )

    tools_str = ""
    for node in nodes:
        pos = node.get("pos", [0, 0])
        params_str = ""
        for k, v in node.get("params", {}).items():
            if isinstance(v, str):
                params_str += f'                    {k} = "{v}",\n'
            elif isinstance(v, bool):
                params_str += f"                    {k} = {'true' if v else 'false'},\n"
            else:
                params_str += f"                    {k} = {v},\n"
        tools_str += (
            f"                {node['id']} = {node['type']} {{\n"
            f"                    Inputs = {{\n"
            f"{params_str}"
            f"                    }},\n"
            f"                    ViewInfo = OperatorInfo {{ Pos = {{ {pos[0]}, {pos[1]} }} }},\n"
            f"                }},\n"
        )

    header = f"-- Fusion macro: {name}\n-- {description}\n"
    body = _MACRO_TEMPLATE % {
        "name": name,
        "inputs": inputs_str.rstrip(",\n"),
        "outputs": outputs_str.rstrip(",\n"),
        "tools": tools_str.rstrip(",\n"),
    }
    return header + body


def save_macro(name: str, content: str, subfolder: str = "MediaStudio") -> Path:
    """
    Save a Fusion macro .setting file into the Resolve user Macros directory.

    Returns the saved path.
    """
    dest_dir = _RESOLVE_MACRO_DIR / subfolder if subfolder else _RESOLVE_MACRO_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.setting"
    dest.write_text(content, encoding="utf-8")
    return dest


def load_macro_into_comp(comp, macro_path: Path) -> None:
    """
    Load a saved Fusion macro into an open Fusion composition.

    `comp` must be a live Fusion composition object obtained via the Resolve Python API.
    Raises PresetError on failure.
    """
    result = comp.LoadTool(str(macro_path))
    if result is None:
        raise PresetError(
            f"Failed to load macro from {macro_path}. "
            "Ensure the composition is open on the Fusion page."
        )


# ---------------------------------------------------------------------------
# Built-in neon/cyberpunk color grade preset
# ---------------------------------------------------------------------------

NEON_CYBERPUNK_MACRO = {
    "name": "NeonCyberpunkGrade",
    "description": "Neon/cyberpunk color grade: high saturation, blue-cyan push, vignette",
    "nodes": [
        {
            "id": "ColorCorrector1",
            "type": "ColorCorrector",
            "params": {
                "Saturation": 1.3,
                "Contrast": 1.1,
            },
            "pos": [0, 0],
        },
        {
            "id": "ColorCurves1",
            "type": "ColorCurves",
            "params": {},
            "pos": [1, 0],
        },
    ],
    "inputs": [
        {"id": "Input", "source": "ColorCorrector1.Input"},
    ],
    "outputs": [
        {"id": "Output", "source": "ColorCurves1.Output"},
    ],
}


def create_neon_cyberpunk_macro() -> Path:
    """Generate and install the built-in neon/cyberpunk grade macro. Returns saved path."""
    content = generate_fusion_macro(**NEON_CYBERPUNK_MACRO)
    return save_macro(NEON_CYBERPUNK_MACRO["name"], content)
