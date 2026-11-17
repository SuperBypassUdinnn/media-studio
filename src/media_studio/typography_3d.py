"""Z-Depth Sandwiched 3D Typography, Kinetic Text Animators, and Optical Finishing."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image


@dataclass(frozen=True)
class Typography3DSpec:
    """Specification for 3D kinetic typography authored in EffectCraft."""

    text: str
    subtext: str = ""
    style: str = "per-char-3d-reveal"
    font: str = "Noto Sans"
    color: str = "#F8FAFC"
    accent: str = "#F59E0B"
    z_layer: str = "behind-subject"
    focal_point: tuple[float, float] = (0.66, 0.42)
    effects: tuple[str, ...] = field(
        default_factory=lambda: ("ec.stylize.cclightsweep", "ec.stylize.glow")
    )


def build_effectcraft_3d_text_commands(
    spec: Typography3DSpec,
    *,
    width: int = 1920,
    height: int = 1080,
    duration: float = 4.0,
    fps: int = 60,
) -> list[dict[str, Any]]:
    """Generate EffectCraft engine commands for 3D kinetic typography."""
    cmds: list[dict[str, Any]] = [
        {
            "id": "comp.new",
            "params": {
                "name": "Typography3D",
                "width": width,
                "height": height,
                "duration": duration,
                "frameRate": float(fps),
            },
        },
        {
            "id": "camera.newCamera",
            "params": {
                "name": "TypoCamera",
                "preset": "50mm",
                "type": "twoNode",
            },
        },
        {
            "id": "light.newLight",
            "params": {
                "name": "GoldRim",
                "type": "spot",
                "color": spec.accent,
                "intensity": 220.0,
            },
        },
        {
            "id": "layer.newText",
            "params": {
                "name": "Title3D",
                "text": spec.text,
                "font": spec.font,
                "size": round(height * 0.08),
                "fill": spec.color,
                "justify": "center",
                "position": [
                    round(spec.focal_point[0] * width),
                    round(spec.focal_point[1] * height),
                ],
            },
        },
    ]

    for eff in spec.effects:
        cmds.append(
            {
                "id": "effect.apply",
                "params": {"effect": eff, "layers": ["Title3D"]},
            }
        )

    return cmds


def composite_z_depth_sandwich(
    background_path: Path,
    typography_rgba: Path,
    subject_matte: Path | None,
    output_path: Path,
    *,
    z_layer: str = "behind-subject",
    apply_optical_grade: bool = True,
) -> Path:
    """Composite 3D typography sandwiched between 3D environment and subject matte."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg_p = Path(background_path)
    txt_p = Path(typography_rgba)
    matte_p = Path(subject_matte) if subject_matte else None

    # Stills composite via PIL
    if bg_p.suffix.lower() in (".png", ".jpg", ".jpeg"):
        bg_im = Image.open(bg_p).convert("RGBA")
        txt_im = Image.open(txt_p).convert("RGBA")

        # Layer 1 + 2: Background + 3D Typography
        composite = Image.alpha_composite(bg_im, txt_im)

        # Layer 3: Subject Matte occlusion if behind-subject
        if z_layer == "behind-subject" and matte_p and matte_p.is_file():
            sub_im = Image.open(matte_p).convert("RGBA")
            composite = Image.alpha_composite(composite, sub_im)

        composite.save(output_path)
        return output_path

    # Video composite via FFmpeg filter_complex
    inputs = ["-i", str(bg_p)]
    if txt_p.suffix.lower() in (".png", ".jpg", ".jpeg"):
        inputs.extend(["-loop", "1", "-i", str(txt_p)])
    else:
        inputs.extend(["-i", str(txt_p)])

    if z_layer == "behind-subject" and matte_p and matte_p.is_file():
        if matte_p.suffix.lower() in (".png", ".jpg", ".jpeg"):
            inputs.extend(["-loop", "1", "-i", str(matte_p)])
        else:
            inputs.extend(["-i", str(matte_p)])
        filter_spec = (
            "[0:v][1:v]overlay=0:0:shortest=1[bg_txt];[bg_txt][2:v]overlay=0:0:shortest=1[outv]"
        )
    else:
        filter_spec = "[0:v][1:v]overlay=0:0:shortest=1[outv]"

    if apply_optical_grade:
        filter_spec += ";[outv]noise=alls=3:allf=t+u,eq=contrast=1.04:saturation=1.02[final]"
        out_map = "[final]"
    else:
        out_map = "[outv]"

    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        *inputs,
        "-filter_complex",
        filter_spec,
        "-map",
        out_map,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path
