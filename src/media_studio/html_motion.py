"""Playwright frame-stepped HTML/SVG/CSS-to-video renderer.

Renders bundled (or custom) HTML/SVG templates through a headless Chromium page,
stepping ``window.__msSeek(timeSec, frameIndex, totalFrames, params)`` one frame
at a time, capturing RGBA PNGs, and encoding them with FFmpeg into a ProRes 4444
``.mov`` (alpha) or an H.264 ``.mp4`` (opaque).

Playwright and Chromium are optional: when the ``render-html`` extra is not
installed, or no Chromium binary can be located, :class:`HtmlMotionError` is
raised with an install hint. Nothing here touches the network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # playwright is an optional extra ([render-html])
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional extra absent
    sync_playwright = None

from .manifest import Project

TEMPLATE_SUBDIR = "templates/motion-html"
SCRATCH_ROOT = Path("/tmp/opencode")


class HtmlMotionError(RuntimeError):
    """Raised when HTML motion rendering cannot proceed."""


@dataclass(frozen=True)
class HtmlMotionResult:
    """What one render produced."""

    output: Path
    template: str
    frames: int
    fps: int
    width: int
    height: int
    transparent: bool
    duration: float


HTML_MOTION_TEMPLATES: tuple[str, ...] = (
    "saas-feature-card.html",
    "device-mockup.html",
    "metric-counter.html",
    "kinetic-captions.html",
    "cinematic-product-hero.html",
)


def _template_directory() -> Path:
    """Locate the ``templates/motion-html`` directory inside the checkout."""

    here = Path(__file__).resolve()
    for candidate in here.parents:
        directory = candidate / TEMPLATE_SUBDIR
        if directory.is_dir():
            return directory
    raise HtmlMotionError(
        f"no {TEMPLATE_SUBDIR} templates found; run media-studio from its git checkout "
        f"(expected <repo>/{TEMPLATE_SUBDIR}/*.html)"
    )


def resolve_template(template: str | Path) -> Path:
    """Resolve a bundled template name or a direct template path to a file."""

    path = Path(template)
    if not path.is_absolute() and not path.suffix:
        directory = _template_directory()
        for candidate in (directory / f"{path}.html", directory / path):
            if candidate.is_file():
                return candidate
        raise HtmlMotionError(
            f"no bundled motion template {template!r}; expected one of: "
            f"{', '.join(HTML_MOTION_TEMPLATES)}"
        )
    if not path.is_file():
        raise HtmlMotionError(f"motion template not found: {path}")
    return path


def _resolve_chromium_executable(playwright_obj) -> str | None:
    """Resolve a Chromium executable per the Task 4 ruling.

    (1) ``playwright_obj.chromium.executable_path`` when it exists,
    (2) ``PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`` env var,
    (3) the newest cached Chromium / headless shell under
        ``~/.cache/ms-playwright/``.
    """

    executable = getattr(playwright_obj.chromium, "executable_path", None)
    if executable and Path(executable).is_file():
        return executable

    env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    cache = Path.home() / ".cache" / "ms-playwright"
    best: Path | None = None
    if cache.is_dir():
        patterns = (
            "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
            "chromium-*/chrome-linux64/chrome",
        )
        for pattern in patterns:
            for match in cache.glob(pattern):
                if best is None or match.parent.parent.name > best.parent.parent.name:
                    best = match
    return str(best) if best else None


def _brand_params(project: Project | None) -> dict[str, Any]:
    """Merge project brand palette/font tokens into the seek params."""

    params: dict[str, Any] = {}
    if project is not None:
        brand = getattr(project, "brand", None)
        if brand is not None:
            palette = getattr(brand, "palette", None) or {}
            fonts = getattr(brand, "fonts", None) or {}
            params.update({str(k): v for k, v in palette.items()})
            params.update({str(k): v for k, v in fonts.items()})
    return params


def _encode_frames(
    scratch: Path,
    output: Path,
    total_frames: int,
    fps: float,
    width: int,
    height: int,
    transparent: bool,
) -> None:
    """Encode the captured PNG sequence into the output video."""

    input_glob = str(scratch / "frame_%06d.png")
    if transparent:
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-framerate",
            str(fps),
            "-i",
            input_glob,
            "-frames:v",
            str(total_frames),
            "-c:v",
            "png",
            "-pix_fmt",
            "rgba",
            str(output),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-framerate",
            str(fps),
            "-i",
            input_glob,
            "-frames:v",
            str(total_frames),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "main",
            str(output),
        ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise HtmlMotionError(
            "ffmpeg encode failed for html motion render: "
            + (completed.stderr or completed.stdout or "").strip()
        )


def render_html_motion(
    template: str | Path,
    *,
    output: Path,
    params: dict[str, Any] | None = None,
    project: Project | None = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    duration: float = 3.0,
    transparent: bool = True,
) -> HtmlMotionResult:
    """Render ``template`` frame-by-frame into ``output``.

    Each frame calls ``window.__msSeek(time_sec, frame_index, total_frames,
    params)`` in the page, then screenshots with ``omit_background`` so a
    transparent template yields an alpha channel. The PNG sequence is encoded
    with FFmpeg (PNG ``rgba`` for alpha, else
    ``libx264`` ``yuv420p``).
    """

    if sync_playwright is None:
        raise HtmlMotionError(
            "playwright is not installed; install the render-html extra and a "
            "Chromium binary: pip install 'media-studio[render-html]' && "
            "playwright install chromium"
        )

    template_path = resolve_template(template)
    output_path = Path(output)
    if transparent and output_path.suffix.lower() != ".mov":
        output_path = output_path.with_suffix(".mov")

    seek_params = _brand_params(project)
    seek_params.update(params or {})
    seek_params.update(
        {
            "width": width,
            "height": height,
            "fps": fps,
            "duration": duration,
            "transparent": transparent,
        }
    )

    total_frames = max(round(duration * fps), 1)
    scratch = Path(tempfile.mkdtemp(prefix="html-motion-", dir=str(SCRATCH_ROOT)))

    try:
        with sync_playwright() as playwright_obj:
            chromium = _resolve_chromium_executable(playwright_obj)
            if chromium is None:
                raise HtmlMotionError(
                    "no Chromium executable found; install one or cache a binary under "
                    "~/.cache/ms-playwright/ (playwright install chromium)"
                )

            browser = playwright_obj.chromium.launch(
                executable_path=chromium,
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            )
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )
                try:
                    page = context.new_page()
                    try:
                        page.goto(template_path.as_uri(), wait_until="networkidle")
                        if transparent:
                            page.add_style_tag(
                                content="html, body { background: transparent !important; }"
                            )
                        page.add_script_tag(
                            content=(
                                "window.__msSpring = function ({frame, fps = 30,"
                                " damping = 12, stiffness = 100, mass = 1} = {}) {"
                                "const t = Math.max(0, frame / fps);"
                                "const omegaN = Math.sqrt(stiffness / mass);"
                                "const zeta = damping / (2 * Math.sqrt(stiffness * mass));"
                                "if (zeta >= 1) {"
                                "return 1 - Math.exp(-omegaN * t) * (1 + omegaN * t);"
                                "}"
                                "const omegaD = omegaN * Math.sqrt(1 - zeta * zeta);"
                                "const envelope = Math.exp(-zeta * omegaN * t);"
                                "return 1 - envelope * (Math.cos(omegaD * t)"
                                " + (zeta * omegaN / omegaD) * Math.sin(omegaD * t));"
                                "};"
                            ),
                        )
                        for frame in range(total_frames):
                            time_sec = frame / fps
                            page.evaluate(
                                "(p) => window.__msSeek(p.t, p.f, p.n, p.p)",
                                {
                                    "t": time_sec,
                                    "f": frame,
                                    "n": total_frames,
                                    "p": seek_params,
                                },
                            )
                            # Let the frame paint (requestAnimationFrame) and fonts settle.
                            page.evaluate("() => new Promise((r) => requestAnimationFrame(r))")
                            page.evaluate("() => document.fonts.ready")
                            page.screenshot(
                                path=scratch / f"frame_{frame:06d}.png",
                                omit_background=transparent,
                            )
                    finally:
                        page.close()
                finally:
                    context.close()
            finally:
                browser.close()

        # Encode while the scratch frames still exist.
        _encode_frames(scratch, output_path, total_frames, fps, width, height, transparent)
    except HtmlMotionError:
        raise
    except Exception as error:
        raise HtmlMotionError(f"html motion render failed: {error}") from error
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return HtmlMotionResult(
        output=output_path,
        template=str(template_path),
        frames=total_frames,
        fps=fps,
        width=width,
        height=height,
        transparent=transparent,
        duration=duration,
    )
