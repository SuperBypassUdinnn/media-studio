"""Tests for the Playwright frame-stepped HTML/SVG-to-video engine (Task 4).

Unit tests mock the Playwright page/browser/launcher so they run offline without
Chromium or network access. The ``@pytest.mark.slow`` E2E test renders a real
template through Chromium + FFmpeg and verifies alpha preservation via ffprobe
and a decoded corner pixel.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

# Top-level import: in RED state (module not yet implemented) this raises
# ModuleNotFoundError: No module named 'media_studio.html_motion'.
from media_studio import html_motion
from media_studio.manifest import Brand, Project


def _cached_chromium() -> bool:
    """Return True when a cached Chromium binary exists under ms-playwright."""
    cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.is_dir():
        return False
    patterns = (
        "chromium-*/chrome-linux64/chrome",
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    )
    return any(cache.glob(pattern) for pattern in patterns)


@pytest.fixture
def _mock_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests do not encode real frames; stub out ffmpeg."""
    monkeypatch.setattr(
        html_motion.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=[], returncode=0),
    )


try:  # pragma: no cover - import probe for the slow marker decision
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAS_PLAYWRIGHT = False


NEEDS_CHROMIUM = pytest.mark.skipif(
    not HAS_PLAYWRIGHT or not _cached_chromium(),
    reason="playwright or cached Chromium unavailable",
)


def _make_project(tmp_path: Path, palette: dict[str, str] | None = None) -> Project:
    return Project(
        root=tmp_path,
        slug="demo",
        title="Demo",
        status="draft",
        deliverables=(),
        brand=Brand(palette=palette or {}, fonts={}),
        budget=None,
        assets={},
        timeline=(),
        video=None,
        design=None,
        effects=(),
        raw={},
    )


def _fake_page() -> SimpleNamespace:
    page = SimpleNamespace()
    page.screenshot = mock.MagicMock()
    page.evaluate = mock.MagicMock(return_value=None)
    page.goto = mock.MagicMock(return_value=None)
    page.add_style_tag = mock.MagicMock(return_value=None)
    page.add_script_tag = mock.MagicMock(return_value=None)
    page.close = mock.MagicMock()
    return page


def _fake_browser(page: SimpleNamespace) -> SimpleNamespace:
    browser = SimpleNamespace()
    context = SimpleNamespace()
    context.new_page = mock.MagicMock(return_value=page)
    context.close = mock.MagicMock()
    browser.new_context = mock.MagicMock(return_value=context)
    browser.close = mock.MagicMock()
    return browser


class _FakePlaywrightCtx:
    """Context-manager wrapper so ``with sync_playwright() as pw`` works in tests."""

    def __init__(self, pw: SimpleNamespace) -> None:
        self.pw = pw

    def __enter__(self) -> SimpleNamespace:
        return self.pw

    def __exit__(self, *args: object) -> bool:
        return False


def _fake_playwright(page: SimpleNamespace) -> _FakePlaywrightCtx:
    pw = SimpleNamespace()
    browser = _fake_browser(page)
    pw.chromium = SimpleNamespace()
    pw.chromium.launch = mock.MagicMock(return_value=browser)
    pw.chromium.executable_path = "/no/such/chrome"
    pw.stop = mock.MagicMock()
    return _FakePlaywrightCtx(pw)


# ---------------------------------------------------------------------------
# RED unit tests (offline, mock Playwright)
# ---------------------------------------------------------------------------


def test_render_html_motion_resolves_bundled_template(tmp_path: Path, _mock_ffmpeg) -> None:
    """A bundled template name resolves to templates/motion-html/<name>.html."""

    page = _fake_page()
    with mock.patch(
        "media_studio.html_motion.sync_playwright", return_value=_fake_playwright(page)
    ):
        result = html_motion.render_html_motion(
            "saas-feature-card",
            output=tmp_path / "out.mov",
            params={"headline": "Hello"},
            project=_make_project(tmp_path),
            width=320,
            height=240,
            fps=10,
            duration=0.5,
            transparent=True,
        )
    assert result.template.endswith("saas-feature-card.html")
    assert result.frames == 5  # round(0.5 * 10)


def test_render_html_motion_accepts_template_path(tmp_path: Path, _mock_ffmpeg) -> None:
    """A direct template path is used as-is."""

    custom = tmp_path / "custom.html"
    custom.write_text("<html><body>hi</body></html>", encoding="utf-8")
    page = _fake_page()
    with mock.patch(
        "media_studio.html_motion.sync_playwright", return_value=_fake_playwright(page)
    ):
        result = html_motion.render_html_motion(
            tmp_path / "custom.html",
            output=tmp_path / "out.mov",
            project=_make_project(tmp_path),
        )
    assert result.template == str(tmp_path / "custom.html")


def test_render_html_motion_merges_brand_tokens_into_params(tmp_path: Path, _mock_ffmpeg) -> None:
    """project.brand palette/fonts merge into the __msSeek params dict."""

    project = _make_project(tmp_path, {"primary": "#0F172A", "accent": "#38BDF8"})
    seen: dict[str, object] = {}

    def fake_seek(_js_expr, arg=None):
        # The __msSeek call passes (js_expr, {"t":..., "f":..., "n":..., "p": params}).
        if isinstance(arg, dict) and "p" in arg:
            seen["params"] = arg["p"]
        return None

    page = _fake_page()
    page.evaluate.side_effect = fake_seek
    with mock.patch(
        "media_studio.html_motion.sync_playwright", return_value=_fake_playwright(page)
    ):
        html_motion.render_html_motion(
            "saas-feature-card",
            output=tmp_path / "out.mov",
            params={"headline": "Hello"},
            project=project,
        )
    assert seen["params"]["primary"] == "#0F172A"
    assert seen["params"]["accent"] == "#38BDF8"
    assert seen["params"]["headline"] == "Hello"


def test_render_html_motion_coerces_mov_when_transparent(tmp_path: Path, _mock_ffmpeg) -> None:
    """transparent=True forces the .mov extension regardless of --out."""

    page = _fake_page()
    with mock.patch(
        "media_studio.html_motion.sync_playwright", return_value=_fake_playwright(page)
    ):
        result = html_motion.render_html_motion(
            "saas-feature-card",
            output=tmp_path / "out.mp4",
            project=_make_project(tmp_path),
            transparent=True,
        )
    assert result.output.suffix == ".mov"


def test_render_html_motion_keeps_mp4_when_opaque(tmp_path: Path, _mock_ffmpeg) -> None:
    """transparent=False keeps the requested extension."""

    page = _fake_page()
    with mock.patch(
        "media_studio.html_motion.sync_playwright", return_value=_fake_playwright(page)
    ):
        result = html_motion.render_html_motion(
            "saas-feature-card",
            output=tmp_path / "out.mp4",
            project=_make_project(tmp_path),
            transparent=False,
        )
    assert result.output.suffix == ".mp4"


def test_render_html_motion_cleans_temp_dir_in_finally(tmp_path: Path, _mock_ffmpeg) -> None:
    """The scratch dir under /tmp/opencode/html-motion-<pid> is removed in finally."""

    page = _fake_page()
    with mock.patch(
        "media_studio.html_motion.sync_playwright", return_value=_fake_playwright(page)
    ):
        html_motion.render_html_motion(
            "saas-feature-card",
            output=tmp_path / "out.mov",
            project=_make_project(tmp_path),
        )
    leftover = (
        list(Path("/tmp/opencode").glob("html-motion-*")) if Path("/tmp/opencode").is_dir() else []
    )
    assert not leftover, f"scratch dirs left behind: {leftover}"


def test_render_html_motion_raises_when_playwright_missing(tmp_path: Path, monkeypatch) -> None:
    """Absent playwright raises HtmlMotionError with an install hint."""

    monkeypatch.setattr(html_motion, "sync_playwright", None, raising=False)
    with (
        mock.patch.dict(sys.modules, {"playwright": None}),
        pytest.raises(html_motion.HtmlMotionError, match="playwright"),
    ):
        html_motion.render_html_motion(
            "saas-feature-card",
            output=tmp_path / "out.mov",
            project=_make_project(tmp_path),
        )


def test_html_motion_templates_contains_bundled_names() -> None:
    """HTML_MOTION_TEMPLATES lists the four bundled templates."""

    assert "saas-feature-card.html" in html_motion.HTML_MOTION_TEMPLATES
    assert "device-mockup.html" in html_motion.HTML_MOTION_TEMPLATES
    assert "metric-counter.html" in html_motion.HTML_MOTION_TEMPLATES
    assert "kinetic-captions.html" in html_motion.HTML_MOTION_TEMPLATES
    assert len(html_motion.HTML_MOTION_TEMPLATES) == 4


def test_motion_html_templates_use_vmin_sizing() -> None:
    """Templates must use viewport-relative vmin sizing (no fixed px/vw caps) so cards
    fill the frame proportionally at both 1080x1920 and 1920x1080."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    templates_dir = repo_root / "templates" / "motion-html"
    for name in html_motion.HTML_MOTION_TEMPLATES:
        source = (templates_dir / name).read_text(encoding="utf-8")
        # No fixed px width/height caps on the card/stage.
        assert "min(86vw," not in source, f"{name} still uses fixed vw cap"
        assert "min(82vw," not in source, f"{name} still uses fixed vw cap"
        assert "min(88vw," not in source, f"{name} still uses fixed vw cap"
        assert "640px" not in source, f"{name} still uses fixed 640px cap"
        assert "720px" not in source, f"{name} still uses fixed 720px cap"
        assert "960px" not in source, f"{name} still uses fixed 960px cap"
        # vmin-based sizing present for proportional fill.
        assert "vmin" in source, f"{name} has no vmin sizing"
        # Fonts scale with vmin (no fixed px font caps that break at 1080p).
        assert "clamp(" not in source or "vmin" in source, f"{name} fonts should scale with vmin"


def test_html_motion_fps_is_threaded_from_params(tmp_path: Path, _mock_ffmpeg) -> None:
    """render_html_motion must pass fps through to the encode and frame math."""
    page = _fake_page()
    seen: dict = {}

    def fake_seek(_js_expr, arg=None):
        if isinstance(arg, dict) and "p" in arg:
            seen["params"] = arg["p"]
        return None

    page.evaluate.side_effect = fake_seek
    with mock.patch(
        "media_studio.html_motion.sync_playwright", return_value=_fake_playwright(page)
    ):
        result = html_motion.render_html_motion(
            "saas-feature-card",
            output=tmp_path / "out.mov",
            params={"headline": "Hello"},
            project=_make_project(tmp_path),
            width=1080,
            height=1920,
            fps=60,
            duration=1.0,
            transparent=False,
        )
    assert result.fps == 60
    assert result.frames == 60  # round(1.0 * 60)
    assert seen["params"]["fps"] == 60


# ---------------------------------------------------------------------------
# Slow E2E test: real Chromium + FFmpeg, alpha pinned
# ---------------------------------------------------------------------------


def _ffprobe_json(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _video_stream(info: dict) -> dict:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    raise AssertionError("no video stream found")


def _decode_corner_png(path: Path, out_png: Path) -> None:
    """Decode the first frame of ``path`` to a PNG at the top-left corner."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-y",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "crop=40:40:0:0",
            str(out_png),
        ],
        capture_output=True,
        text=True,
        check=True,
    )


@NEEDS_CHROMIUM
@pytest.mark.slow
def test_e2e_saas_feature_card_preserves_alpha(tmp_path: Path) -> None:
    """Render saas-feature-card.html to a transparent ProRes 4444 .mov and pin alpha."""

    output = tmp_path / "card.mov"
    result = html_motion.render_html_motion(
        "saas-feature-card",
        output=output,
        params={"headline": "Hello world", "subhead": "Frame-stepped motion"},
        width=320,
        height=240,
        fps=10,
        duration=0.5,
        transparent=True,
    )

    assert result.frames == 5
    assert result.transparent is True
    assert result.output.suffix == ".mov"
    assert result.output.is_file() and result.output.stat().st_size > 0

    info = _ffprobe_json(result.output)
    stream = _video_stream(info)
    assert stream["codec_name"] in ("png", "prores")
    assert stream["pix_fmt"] in ("rgba", "yuva444p10le", "yuva444p12le"), stream["pix_fmt"]

    nb_read = stream.get("nb_read_frames") or stream.get("nb_frames")
    if nb_read is None:
        nb_read = info.get("format", {}).get("nb_read_frames")
    assert int(nb_read) == 5, f"expected 5 frames, got {nb_read!r}"

    corner = tmp_path / "corner.png"
    _decode_corner_png(result.output, corner)
    from PIL import Image

    with Image.open(corner) as im:
        im = im.convert("RGBA")
        alpha = im.getpixel((0, 0))[3]
    assert alpha == 0, f"corner alpha {alpha} != 0; background not transparent"
