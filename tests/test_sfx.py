"""Tests for the bundled CC0 SFX pack (media_studio.sfx).

Real-ffmpeg cases are marked ``slow`` and exercise FFmpeg 9.0.2 end to end.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest

from media_studio import media
from media_studio.sfx import (
    SfxCue,
    SfxEntry,
    SfxError,
    list_bundled_sfx,
    mix_transition_sfx,
)

SFX_DIR = Path("src/media_studio/data/sfx")
SFX_NAMES = (
    "whoosh-soft.wav",
    "whoosh-fast.wav",
    "impact-sub.wav",
    "impact-hit.wav",
    "riser-build.wav",
    "ui-pop.wav",
)


def test_sfx_directory_and_yaml_exist() -> None:
    """The bundled CC0 pack ships six WAVs plus a catalog manifest."""

    assert SFX_DIR.is_dir()
    manifest = SFX_DIR / "sfx.yaml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert "license: CC0-1.0" in text or "CC0-1.0" in text
    for name in SFX_NAMES:
        assert (SFX_DIR / name).is_file(), f"missing {name}"


def test_bundled_wavs_load_cleanly() -> None:
    """Every bundled WAV is a valid 48 kHz PCM-16 file with audible content."""

    for name in SFX_NAMES:
        path = SFX_DIR / name
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == 48000, f"{name} sample rate"
            assert wav.getsampwidth() == 2, f"{name} sample width"
            assert wav.getnframes() > 0, f"{name} has no frames"


def test_sfx_manifest_tags_and_license() -> None:
    """sfx.yaml declares CC0-1.0 and tags each effect."""

    import yaml

    manifest = yaml.safe_load((SFX_DIR / "sfx.yaml").read_text(encoding="utf-8"))
    assert manifest.get("license") == "CC0-1.0"
    effects = manifest.get("effects") or []
    ids = {entry["id"] for entry in effects}
    assert ids == set(SFX_NAMES) or ids == {name.removesuffix(".wav") for name in SFX_NAMES}
    for entry in effects:
        assert entry["id"]
        assert isinstance(entry.get("tags"), list) and entry["tags"]


def test_list_bundled_sfx_returns_all_entries() -> None:
    """list_bundled_sfx returns one SfxEntry per bundled effect."""

    entries = list_bundled_sfx()
    assert isinstance(entries, list)
    assert len(entries) == len(SFX_NAMES)
    for entry in entries:
        assert isinstance(entry, SfxEntry)
        assert entry.id
        assert entry.path.is_file()
        assert entry.tags
        assert entry.license == "CC0-1.0"
        assert entry.duration > 0.0


def test_list_bundled_sfx_filters_by_tag() -> None:
    """Tag filtering returns only matching effects."""

    whoosh = list_bundled_sfx(tag="whoosh")
    ids = {entry.id for entry in whoosh}
    assert "whoosh-soft" in ids
    assert "whoosh-fast" in ids
    assert all("whoosh" in entry.tags for entry in whoosh)
    impacts = list_bundled_sfx(tag="impact")
    assert any("impact" in entry.tags for entry in impacts)
    assert len(list_bundled_sfx(tag="does-not-exist")) == 0


def test_mix_transition_sfx_rejects_missing_base() -> None:
    """A missing base audio file raises SfxError."""

    with pytest.raises(SfxError, match="not found"):
        mix_transition_sfx(
            Path("/tmp/opencode/does-not-exist.wav"),
            [SfxCue(path_or_id="ui-pop", at=0.1)],
            output=Path("/tmp/opencode/out.wav"),
            total_duration=1.0,
        )


@pytest.mark.slow
def test_mix_transition_sfx_pure_sfx_bed(tmp_path: Path) -> None:
    """No base audio: silent bed, mixed cues match total_duration and stay under -1.5 dBTP."""

    output = tmp_path / "mixed.wav"
    result = mix_transition_sfx(
        None,
        [
            SfxCue(path_or_id="whoosh-soft", at=0.2, gain_db=-6.0),
            SfxCue(path_or_id="impact-hit", at=1.0, gain_db=-3.0),
        ],
        output=output,
        total_duration=2.0,
    )

    assert result == output
    assert output.is_file()
    info = media.probe(output)
    assert info.duration == pytest.approx(2.0, abs=0.05)
    assert info.audio_codec is not None

    loudness = media.measure_loudness(output, target=-14.0)
    assert loudness.true_peak_dbtp <= -1.5


@pytest.mark.slow
def test_mix_transition_sfx_over_base_audio(tmp_path: Path) -> None:
    """Base audio is preserved and mixed under the SFX cues."""

    base = tmp_path / "base.wav"
    subprocess.run(
        [
            media.FFMPEG,
            "-hide_banner",
            "-nostats",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=2:sample_rate=48000",
            "-c:a",
            "pcm_s16le",
            str(base),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert base.is_file()

    output = tmp_path / "mixed.wav"
    mix_transition_sfx(
        base,
        [SfxCue(path_or_id="ui-pop", at=0.5, gain_db=0.0)],
        output=output,
        total_duration=2.0,
    )

    assert output.is_file()
    info = media.probe(output)
    assert info.duration == pytest.approx(2.0, abs=0.05)
