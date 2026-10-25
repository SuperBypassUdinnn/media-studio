#!/usr/bin/env python3
"""Probe which MLT services this build actually applies, so a recipe is written from evidence.

A recipe claims a mechanism; this script is how that claim is checked before the recipe exists. It
renders one short clip once through each candidate filter and reports the frame's mean level plus a
digest of its decoded pixels, so a filter that does nothing is visible as `same` even when the
picture looks plausible in a recipe file.

Run from the repository root with the project virtualenv:

    .venv/bin/python scripts/probe-preset-filters.py

It writes its scratch renders into /tmp/opencode and prints one line per candidate. Copy the ones
worth keeping into `presets/`, then let the snapshot test record a baseline.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from media_studio import media
from media_studio.mlt import Clip, Timeline

# Candidates are MLT service names with the properties this stack would pass. Values are strings,
# because MLT properties are strings everywhere else in the pipeline.
CANDIDATES: dict[str, dict[str, str]] = {
    "saturation": {"saturation": "1.6"},
    "gamma": {"gamma": "2.0"},
    "lift_gamma_gain": {"gain_r": "1.4", "gain_g": "1.0", "gain_b": "0.8"},
    "wave": {"wave": "10"},
    "boxblur": {"hori": "8", "vert": "8"},
    "crop": {"left": "20", "right": "20", "top": "0", "bottom": "0"},
    "affine": {"transition.rotate": "8"},
    "hue": {"hue": "120"},
    "colorbalance": {"r": "1.4"},
    "rgba": {"r": "140"},
    "brightness": {"level": "1.2"},
    "charcoal": {},
    "sepia": {"u": "0.2", "v": "0.1"},
    "mirror": {"mirror": "horizontal"},
    "invert": {},
    "threshold": {"threshold": "0.5"},
}


def main() -> int:
    scratch = Path(os.environ.get("MS_PROBE_DIR", "/tmp/opencode"))
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="preset-probe-", dir=scratch))
    clip = work / "source.mp4"
    subprocess.run(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(clip),
        ],
        check=True,
    )

    def render(name: str, filters: tuple) -> tuple[float, str]:
        timeline = Timeline(
            width=160,
            height=90,
            fps=24.0,
            clips=(Clip(resource=clip, duration=1.0, filters=filters),),
        )
        path = work / f"{name}.mlt"
        path.write_text(timeline.to_xml(work), encoding="utf-8")
        frame = media.render_timeline(path, work / f"{name}.mkv")
        return media.mean_luminance_at(frame, 0.5), media.pixel_digest(frame)

    plain_luma, plain_digest = render("plain", ())
    print(f"{'plain':18} luma={plain_luma:7.2f} sig={plain_digest[:12]}")
    failures = 0
    for name, properties in CANDIDATES.items():
        try:
            luma, digest = render(name, ((name, properties),))
        except Exception as error:  # any failure here is itself a result worth printing
            failures += 1
            print(f"{name:18} FAILED {str(error)[:60]}")
            continue
        verdict = "CHANGED" if digest != plain_digest else "no effect"
        print(f"{name:18} luma={luma:7.2f} {verdict} sig={digest[:12]}")
    print(f"\nscratch: {work}")
    print(f"{len(CANDIDATES) - failures} of {len(CANDIDATES)} candidates rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
