"""Tests for media_studio.impact: transient AMV impact frame generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_studio.impact import (
    IMPACT_MODES,
    ImpactError,
    build_filter_chain,
    generate_impact_frames,
    parse_at_timestamp,
)


def test_impact_modes_contain_all_five():
    assert set(IMPACT_MODES.keys()) == {"flash", "invert", "sketch", "red-sketch", "chromatic"}


def test_parse_at_timestamp_seconds_and_frames():
    assert parse_at_timestamp(2.5, fps=60) == (2.5, 150)
    assert parse_at_timestamp("f120", fps=60) == (2.0, 120)
    assert parse_at_timestamp("3.0", fps=30) == (3.0, 90)


def test_parse_at_timestamp_invalid():
    with pytest.raises(ImpactError, match="negative"):
        parse_at_timestamp(-1.0, fps=60)
    with pytest.raises(ImpactError, match="invalid timestamp format"):
        parse_at_timestamp("invalid", fps=60)


def test_build_filter_chain_modes():
    filters = build_filter_chain(mode="invert", resolution="1920x1080", fps=60)
    assert "negate" in filters
    assert "scale=1920:1080" in filters
    assert "fps=60" in filters

    flash_filter = build_filter_chain(mode="flash", resolution="1920x1080", fps=60)
    assert "lutyuv" in flash_filter

    sketch_filter = build_filter_chain(mode="sketch", resolution="1920x1080", fps=60)
    assert "edgedetect" in sketch_filter

    chromatic_filter = build_filter_chain(mode="chromatic", resolution="1920x1080", fps=60)
    assert "overlay=x=4" in chromatic_filter
    assert "overlay=x=-4" in chromatic_filter


def test_chromatic_filter_produces_channel_displacement():
    filter_chain = build_filter_chain(mode="chromatic", resolution="1920x1080", fps=60)
    # Red shifted right/down (+4,0), blue shifted left (-4,0) relative to green.
    assert "split=3" in filter_chain
    assert "[r]crop=iw-8:ih:4:0[rp]" in filter_chain
    assert "[b]crop=iw-8:ih:4:0[bp]" in filter_chain
    assert "[rp][gp]overlay=x=4:y=0:shortest=1[rg]" in filter_chain
    assert "[rg][bp]overlay=x=-4:y=0:shortest=1" in filter_chain


def test_generate_impact_frames_bounds_validation(tmp_path: Path):
    dummy = tmp_path / "dummy.mp4"
    dummy.write_bytes(b"empty")
    with pytest.raises(ImpactError, match="frames must be between 1 and 10"):
        generate_impact_frames(dummy, at=1.0, frames=0)
    with pytest.raises(ImpactError, match="frames must be between 1 and 10"):
        generate_impact_frames(dummy, at=1.0, frames=15)
    with pytest.raises(ImpactError, match="unknown impact mode"):
        generate_impact_frames(dummy, at=1.0, mode="supernova")
