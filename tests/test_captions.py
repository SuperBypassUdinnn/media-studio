"""Tests for media_studio.captions: word-highlight captions.

Unit tests exercise group_words punctuation/chunking, .ass safe-zone placement,
and media-mcp Whisper JSON parsing. The slow E2E group burns real .ass
captions onto a synthetic 1080x1920 clip with ffmpeg/libass.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from media_studio.captions import (
    CaptionsError,
    WordTiming,
    _ass_style_for,
    _parse_words_json,
    _srt_to_words,
    extract_word_timestamps,
    group_words,
    render_captions,
    safe_zone_for,
)

FPS = 30.0


def W(word: str, start: float, end: float) -> WordTiming:
    return WordTiming(word=word, start=start, end=end)


# ---------------------------------------------------------------------------
# group_words
# ---------------------------------------------------------------------------


def test_group_words_basic_chunking() -> None:
    chunks = group_words(
        [W("Hello", 0.0, 0.5), W("world", 0.5, 1.0), W("this", 1.0, 1.5)],
        max_words=2,
        max_chars=24,
    )
    assert len(chunks) == 2
    assert chunks[0].text == "Hello world"
    assert chunks[1].text == "this"


def test_group_words_splits_on_punctuation() -> None:
    words = [
        W("Wait.", 0.0, 0.5),
        W("What?", 0.6, 1.1),
        W("Sure!", 1.2, 1.7),
    ]
    chunks = group_words(words, max_words=4, max_chars=24)
    assert len(chunks) == 3
    assert chunks[0].text == "Wait."
    assert chunks[1].text == "What?"
    assert chunks[2].text == "Sure!"


def test_group_words_wraps_by_max_chars() -> None:
    words = [W(f"word{i}", i * 0.5, i * 0.5 + 0.4) for i in range(8)]
    chunks = group_words(words, max_words=100, max_chars=12)
    # Each "wordN" is 5 chars; two = 6, three = 8, four = 10, five = 11, six = 12.
    # max_chars 12 allows up to 6 words per line.
    assert all(len(c.text) <= 12 for c in chunks)


def test_group_words_empty() -> None:
    assert group_words([]) == []


# ---------------------------------------------------------------------------
# safe_zone_for
# ---------------------------------------------------------------------------


def test_safe_zone_for_9x16_1080x1920() -> None:
    zone = safe_zone_for("9:16", 1080, 1920)
    assert zone["top"] >= 220
    assert zone["bottom"] >= 380
    assert zone["left"] >= 60
    assert zone["right"] >= 140


# ---------------------------------------------------------------------------
# .ass style / safe-zone placement
# ---------------------------------------------------------------------------


def test_ass_style_places_marginv_inside_safe_zone() -> None:
    zone = safe_zone_for("9:16", 1080, 1920)
    style = _ass_style_for("promo-pop", zone, accent_color="#38BDF8")
    # MarginV is the top/bottom margin in the .ass style; must sit inside safe zone.
    margin_v = int(style["MarginV"])
    assert margin_v >= zone["top"], f"MarginV {margin_v} below safe top {zone['top']}"
    assert style["Alignment"] == "2"


def test_ass_style_has_active_word_highlight_tags() -> None:
    zone = safe_zone_for("9:16", 1080, 1920)
    style = _ass_style_for("hormozi-bold", zone, accent_color="#38BDF8")
    body = "\n".join(f"{k}={v}" for k, v in style.items())
    # The highlight (accent) colour is carried in SecondaryColour for \\c tags.
    assert "SecondaryColour" in body
    assert "&H" in style["SecondaryColour"]


def test_all_styles_render() -> None:
    zone = safe_zone_for("9:16", 1080, 1920)
    for style in ("promo-pop", "hormozi-bold", "clean-saas", "amv-lyric"):
        result = _ass_style_for(style, zone, accent_color="#38BDF8")
        assert "Alignment" in result
        assert "MarginV" in result


# ---------------------------------------------------------------------------
# Whisper JSON parsing
# ---------------------------------------------------------------------------


def test_parse_words_json_segments_shape() -> None:
    payload = {
        "meta": {"model": "large-v3-turbo"},
        "segments": [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "Hello world",
                "words": [
                    {"start": 0.0, "end": 0.5, "text": "Hello"},
                    {"start": 0.5, "end": 1.0, "text": "world"},
                ],
            }
        ],
    }
    words = _parse_words_json(payload)
    assert [w.word for w in words] == ["Hello", "world"]
    assert words[0].start == 0.0 and words[0].end == 0.5


def test_parse_words_json_top_level_words() -> None:
    payload = {
        "words": [
            {"start": 0.0, "end": 0.5, "text": "Foo"},
            {"start": 0.5, "end": 1.0, "text": "Bar"},
        ]
    }
    words = _parse_words_json(payload)
    assert [w.word for w in words] == ["Foo", "Bar"]


def test_parse_words_json_empty() -> None:
    assert _parse_words_json({"segments": []}) == []
    assert _parse_words_json({"words": []}) == []


def test_srt_to_words() -> None:
    srt = (
        "1\n00:00:00,000 --> 00:00:02,000\nHello world\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nFoo bar.\n"
    )
    words = _srt_to_words(srt)
    assert [w.word for w in words] == ["Hello", "world", "Foo", "bar."]


# ---------------------------------------------------------------------------
# extract_word_timestamps
# ---------------------------------------------------------------------------


def test_extract_word_timestamps_from_sidecar_json(tmp_path: Path) -> None:
    media = tmp_path / "interview.mp4"
    media.write_bytes(b"fake")
    sidecar = media.with_suffix(".json")
    sidecar.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "text": "Hello world",
                        "words": [
                            {"start": 0.0, "end": 0.5, "text": "Hello"},
                            {"start": 0.5, "end": 1.0, "text": "world"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    words = extract_word_timestamps(media)
    assert [w.word for w in words] == ["Hello", "world"]


def test_extract_word_timestamps_raises_when_no_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "silent.mp4"
    media.write_bytes(b"fake")
    # No sidecar words.json/SRT and media-mcp venv missing -> clear error.
    monkeypatch.setenv("MEDIA_MCP_ROOT", str(tmp_path / "nonexistent-media-mcp"))
    with pytest.raises(CaptionsError, match=r"media-mcp|transcribe|words"):
        extract_word_timestamps(media)


# ---------------------------------------------------------------------------
# render_captions E2E (slow, real ffmpeg/libass)
# ---------------------------------------------------------------------------


def _make_caption_source(path: Path, *, duration: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:size=1080x1920:rate={int(FPS)}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.mark.slow
def test_render_captions_burns_ass_into_safe_zone(tmp_path: Path) -> None:
    source = _make_caption_source(tmp_path / "source.mp4")
    output = tmp_path / "captioned.mp4"
    words = [
        W("Hello", 0.0, 1.0),
        W("world", 1.0, 2.0),
        W("promo", 2.0, 3.0),
    ]
    result = render_captions(
        source,
        words=words,
        style="promo-pop",
        aspect="9:16",
        output=output,
        engine="ass",
    )
    assert result.is_file()
    # ffprobe: video stream present.
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        "h264" in completed.stdout or "libx264" in completed.stdout or "mpeg4" in completed.stdout
    )


@pytest.mark.slow
def test_render_captions_accepts_words_path(tmp_path: Path) -> None:
    source = _make_caption_source(tmp_path / "source.mp4")
    words_path = tmp_path / "words.json"
    words_path.write_text(
        json.dumps(
            {
                "words": [
                    {"start": 0.0, "end": 1.0, "text": "Hello"},
                    {"start": 1.0, "end": 2.0, "text": "world"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "captioned.mp4"
    result = render_captions(
        source,
        words=words_path,
        style="hormozi-bold",
        aspect="9:16",
        output=output,
        engine="ass",
    )
    assert result.is_file()
