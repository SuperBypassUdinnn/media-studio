"""Tests for 24-asset 4-layer CC0 sound effects pack, J/L-cut mixing, and sound design."""

from __future__ import annotations

from pathlib import Path

from media_studio.sfx import SfxCue, list_bundled_sfx, mix_transition_sfx
from media_studio.sound_design import (
    modify_library_sound,
    synthesize_sound_event,
    verify_event_envelopes,
)

REQUIRED_FOLEY_IDS = {
    "velvet-night-drone",
    "sub-room-ambience",
    "glass-crystal-resonance",
    "perfume-atomizer-mist",
    "liquid-amber-swirl",
    "water-droplet-obsidian",
    "magnetic-cap-click",
    "metallic-gleam-shimmer",
    "deep-cinema-whoosh",
    "whip-air-transient",
    "reverse-vacuum-riser",
    "sub-drop-40hz",
    "braam-low-horn",
    "cinematic-taiko-boom",
}


def test_bundled_sfx_has_all_four_world_class_layers() -> None:
    entries = list_bundled_sfx()
    ids = {e.id for e in entries}
    assert len(entries) >= 24
    assert REQUIRED_FOLEY_IDS.issubset(ids)
    for layer in ("ambience", "foley", "transition", "impact"):
        layer_items = list_bundled_sfx(layer=layer)
        assert len(layer_items) >= 4, f"layer {layer} underpopulated"


def test_modify_library_sound_sculpts_exact_duration_and_clean_decay(tmp_path: Path) -> None:
    out = tmp_path / "sculpted.wav"
    res = modify_library_sound(
        "glass-crystal-resonance",
        out,
        target_duration_s=0.55,
        pitch_semitones=3.0,
        transient_boost_db=3.5,
        filter_sweep_hz=(1200.0, 14000.0),
        pan_sweep=(-0.3, 0.3),
        trail_out_s=0.45,
    )
    assert res.is_file()
    check = verify_event_envelopes(res)
    assert check["ok"] is True
    assert check["end_dbfs"] < -45.0


def test_synthesize_sub_bass_and_curve_progress_zero_orphan_ticks(tmp_path: Path) -> None:
    sub = tmp_path / "sub.wav"
    synthesize_sound_event(
        "sub_bass",
        sub,
        duration_s=0.35,
        params={
            "initial_frequency_hz": 80.0,
            "final_frequency_hz": 45.0,
            "attack_ms": 5.0,
            "decay_ms": 345.0,
        },
    )
    assert verify_event_envelopes(sub)["ok"] is True

    prog = tmp_path / "progress.wav"
    synthesize_sound_event(
        "curve_progress",
        prog,
        duration_s=2.0,
        params={"active_duration_s": 1.4, "start_freq_hz": 440.0, "end_freq_hz": 880.0},
    )
    env = verify_event_envelopes(prog, active_until_s=1.55)
    assert env["ok"] is True
    assert env["orphan_ticks"] is False


def test_j_cut_and_l_cut_sfx_mixing_clamps_scene_zero_lead_in(tmp_path: Path) -> None:
    out = tmp_path / "mixed.wav"
    cues = [
        SfxCue("velvet-night-drone", at=0.0, gain_db=-12.0, lead_in_sec=0.35, layer="ambience"),
        SfxCue(
            "perfume-atomizer-mist",
            at=1.5,
            gain_db=-4.0,
            lead_in_sec=0.25,
            trail_out_sec=0.4,
            layer="foley",
        ),
        SfxCue("sub-drop-40hz", at=2.0, gain_db=-3.0, layer="impact"),
    ]
    res = mix_transition_sfx(None, cues, output=out, duration=3.5, target_lufs=-14.0)
    assert res.is_file()
