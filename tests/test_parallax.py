"""Tests for the 2.5D multi-plane parallax compositor engine.

These run the real ffmpeg toolchain on small generated plates, so they prove the
compositor rather than mocking it. Resolution is kept small and durations short
to keep the suite fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from media_studio.parallax import (
    PARALLAX_MOTIONS,
    LayerTransform,
    ParallaxError,
    ParallaxResult,
    calculate_layer_transforms,
    check_alpha_channel,
    compose_parallax,
)

WIDTH = 1920
HEIGHT = 1080


def _plate(destination: Path, size: tuple[int, int], mode: str = "RGB") -> Path:
    Image.new(mode, size, color=(40, 50, 60, 255) if mode == "RGBA" else (40, 50, 60)).save(
        destination
    )
    return destination


def test_parallax_motions_available():
    assert "push_in" in PARALLAX_MOTIONS
    assert "pull_out" in PARALLAX_MOTIONS
    assert "whip_left" in PARALLAX_MOTIONS
    assert "whip_right" in PARALLAX_MOTIONS
    assert "dramatic_dolly" in PARALLAX_MOTIONS


def test_calculate_layer_transforms_push_in():
    bg_t, subj_t, fg_t = calculate_layer_transforms(
        motion="push_in",
        duration=3.0,
        fps=60,
        width=WIDTH,
        height=HEIGHT,
    )
    # Background (depth 0.3) moves slower than Subject (depth 1.0)
    assert bg_t.zoom_start < subj_t.zoom_start or bg_t.zoom_end < subj_t.zoom_end
    assert subj_t.zoom_end > 1.10
    assert fg_t.zoom_end > subj_t.zoom_end
    assert isinstance(bg_t, LayerTransform)


def test_calculate_layer_transforms_pull_out():
    bg_t, subj_t, fg_t = calculate_layer_transforms(
        motion="pull_out", duration=2.0, fps=60, width=WIDTH, height=HEIGHT
    )
    assert bg_t.zoom_start > bg_t.zoom_end == 1.0
    assert subj_t.zoom_start > subj_t.zoom_end == 1.0
    assert fg_t.zoom_start > subj_t.zoom_start
    assert fg_t.zoom_end == 1.0


def test_calculate_layer_transforms_whip_directions():
    left_bg, _, _ = calculate_layer_transforms(
        motion="whip_left", duration=1.0, fps=60, width=WIDTH, height=HEIGHT
    )
    right_bg, _, _ = calculate_layer_transforms(
        motion="whip_right", duration=1.0, fps=60, width=WIDTH, height=HEIGHT
    )
    assert left_bg.pan_x_end > left_bg.pan_x_start
    assert right_bg.pan_x_end < right_bg.pan_x_start


def test_calculate_layer_transforms_dramatic_dolly():
    _, subj_t, _ = calculate_layer_transforms(
        motion="dramatic_dolly", duration=2.0, fps=30, width=WIDTH, height=HEIGHT
    )
    assert subj_t.zoom_end > 1.20


def test_calculate_layer_transforms_rejects_unknown_motion():
    with pytest.raises(ParallaxError, match="unknown motion"):
        calculate_layer_transforms(motion="nope", duration=1.0, fps=30, width=WIDTH, height=HEIGHT)


def test_check_alpha_channel_detects_rgba_and_rgb(tmp_path: Path):
    rgba = tmp_path / "subject.png"
    Image.new("RGBA", (100, 100), color=(255, 0, 0, 180)).save(rgba)
    assert check_alpha_channel(rgba) is True

    rgb = tmp_path / "plate.png"
    Image.new("RGB", (100, 100), color=(50, 50, 50)).save(rgb)
    assert check_alpha_channel(rgb) is False

    missing = tmp_path / "not-here.png"
    assert check_alpha_channel(missing) is False


def test_compose_parallax_rejects_missing_background(tmp_path: Path):
    subject = tmp_path / "subject.png"
    Image.new("RGBA", (100, 100), color=(255, 0, 0, 180)).save(subject)

    with pytest.raises(ParallaxError, match="background"):
        compose_parallax(
            background=tmp_path / "does-not-exist.png",
            subject=subject,
            duration=1.0,
            destination=tmp_path / "out.mov",
        )


def test_compose_parallax_requires_alpha_on_subject(tmp_path: Path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), color=(50, 50, 50)).save(bg)

    subject_no_alpha = tmp_path / "subject_no_alpha.png"
    Image.new("RGB", (1000, 1000), color=(255, 0, 0)).save(subject_no_alpha)

    with pytest.raises(ParallaxError, match="subject layer requires an alpha channel"):
        compose_parallax(
            background=bg,
            subject=subject_no_alpha,
            duration=2.0,
            destination=tmp_path / "out.mov",
        )


def test_compose_parallax_scales_non_16_9_background(tmp_path: Path):
    bg_square = tmp_path / "bg_square.png"
    Image.new("RGB", (1080, 1080), color=(30, 40, 50)).save(bg_square)

    subject_rgba = tmp_path / "subject.png"
    Image.new("RGBA", (1000, 1000), color=(255, 0, 0, 180)).save(subject_rgba)

    out = tmp_path / "out.mov"
    res = compose_parallax(
        background=bg_square,
        subject=subject_rgba,
        duration=1.0,
        destination=out,
        fps=30,
    )
    assert isinstance(res, ParallaxResult)
    assert res.destination.is_file()
    assert res.resolution == "1920x1080"
    assert res.fps == 30
    assert res.layers_composited == 2
    assert res.motion == "push_in"
    assert res.size_bytes > 0


def test_compose_parallax_with_foreground(tmp_path: Path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), color=(30, 40, 50)).save(bg)
    subject = tmp_path / "subject.png"
    Image.new("RGBA", (1000, 1000), color=(255, 0, 0, 180)).save(subject)
    fg = tmp_path / "fg.png"
    Image.new("RGBA", (800, 800), color=(0, 255, 0, 120)).save(fg)

    out = tmp_path / "out.mov"
    res = compose_parallax(
        background=bg,
        subject=subject,
        foreground=fg,
        duration=1.0,
        destination=out,
        fps=30,
        motion="dramatic_dolly",
    )
    assert res.destination.is_file()
    assert res.layers_composited == 3
    assert res.motion == "dramatic_dolly"


def test_compose_parallax_output_matches_duration(tmp_path: Path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), color=(30, 40, 50)).save(bg)
    subject = tmp_path / "subject.png"
    Image.new("RGBA", (1000, 1000), color=(255, 0, 0, 180)).save(subject)

    out = tmp_path / "out.mov"
    res = compose_parallax(background=bg, subject=subject, duration=1.5, destination=out, fps=30)
    assert res.duration == 1.5
    assert res.resolution == "1920x1080"

    from media_studio.media import probe

    info = probe(out)
    assert info.width == 1920
    assert info.height == 1080


def test_compose_parallax_rejects_below_floor_resolution(tmp_path: Path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), color=(30, 40, 50)).save(bg)
    subject = tmp_path / "subject.png"
    Image.new("RGBA", (1000, 1000), color=(255, 0, 0, 180)).save(subject)

    with pytest.raises(ParallaxError, match="is below the 1920x1080 minimum floor"):
        compose_parallax(
            background=bg,
            subject=subject,
            duration=1.0,
            destination=tmp_path / "out.mov",
            resolution="1280x720",
        )


def test_compose_parallax_rejects_malformed_resolution(tmp_path: Path):
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1920, 1080), color=(30, 40, 50)).save(bg)
    subject = tmp_path / "subject.png"
    Image.new("RGBA", (1000, 1000), color=(255, 0, 0, 180)).save(subject)

    with pytest.raises(ParallaxError, match="must be WxH"):
        compose_parallax(
            background=bg,
            subject=subject,
            duration=1.0,
            destination=tmp_path / "out.mov",
            resolution="not-a-resolution",
        )
