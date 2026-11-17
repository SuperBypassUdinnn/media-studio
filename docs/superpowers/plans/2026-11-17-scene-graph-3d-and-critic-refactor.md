# Media-Studio v3 Scene-Graph, 3D Cinematography, & Dual-Gate Critic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy rigid `ms promo` 5-beat pipeline with a skill-governed, multi-engine 3D/Motion Scene-Graph (`ms storyboard` -> `ms scene` -> `ms critique`), a 24-asset 4-layer CC0 SFX/Foley baseline library, and a mandatory Dual-Gate quality critic backed by `submodules/media-mcp` and a 7-axis Multimodal Vision Rubric.

**Architecture:** Six repository-local skills under `.agents/skills/` and `.agents/rules/architecture.md` establish the creative, 3D, typography, and discovery contracts first. `scene_graph.py` orchestrates arbitrary sequences of `blender-3d`, `effectcraft`, `flow-gen`, and `composite` scenes with world-class montage grammar (`ECU/CU/MS/WS` shot scale contrast, non-linear speed ramping, Z-depth sandwiched 3D typography, Chiaroscuro lighting, and optical finishing), while `storyboard.py` renders full-resolution visual keyframes per scene and `critique.py` enforces 6 quantitative gates via `submodules/media-mcp` plus a 7-axis Vision Rubric.

**Tech Stack:** Python 3.12 (`.venv/bin/python`), Blender 5.3 Headless (`--gpu-backend vulkan` with game-aware CPU fallback), EffectCraft 0.24.1 (`effectcraft-cli` + SAM 2.1 `sam2.1-hiera-base-plus`), FilmCraft 0.15.0 (`filmcraft-cli`), FFmpeg 9.0 (`h264_nvenc` dGPU-first with `libx264` fallback), `submodules/media-mcp`, `assets-3d` MCP, Freesound CC0 API.

**Spec:** `docs/superpowers/specs/2026-11-17-scene-graph-3d-and-critic-refactor-design.md`

## Global Constraints

- Python interpreter: `/home/superbypassudin/.local/share/opencode/worktree/711d91/promo-generative-pipeline/.venv/bin/python` (CPython 3.12.16; never use Python 3.14).
- Linter & formatter gate: `~/.local/bin/ruff check src tests && ~/.local/bin/ruff format --check src tests` must pass with 0 errors.
- File size limit: Every created or modified Python module in `src/media_studio/` must stay strictly under 600 lines.
- Subagent / Model Tier Rule: Never use `nemotron-3.5-super-vl` for visual layout, 3D choreography, EffectCraft composition design, or multimodal visual critique; use native execution in the main session or `google/antigravity-gemini-3.8-flash`.
- Asset Priority SOP: Tier 1 local/library SVGs, PNGs, and `assets-3d` 3D models first (never hallucinate recognized brand logos with AI); Tier 2 Google Flow (`Nano Banana Pro` @ 0 credits, `Veo 3.1 - Lite` @ 10 credits); Tier 3 zero silent fallback to Pollinations (`allow_keyless=False`).
- Every staged audio/3D/image asset must have a companion `<filename>.license` REUSE sidecar.
- Clean up all scratch directories under `/tmp/opencode/...` inside `finally:` blocks.

## Review Focus

- Adjacent scenes in `scene-graph.json` sharing both `shot_scale` and `camera_angle` must be rejected by `critique_montage` as a visual jump-cut error (`severity="error"`).
- A scene graph where every single scene contains non-empty `typography_3d` (0 sensory breathing shots) when `len(scenes) >= 4` must be flagged by `critique_montage` (`severity="error"`).
- A rendered frame containing unstyled off-palette surfaces (minimum CIELAB $\Delta E > 38.0$ against `brand.palette`) or forbidden boilerplate strings (`"New feature"`, `"Try it free"`, `"Up from last period"`) must fail `ms critique` with exit code `1`.
- Missing, stale, or sub-threshold (`score < 4` on any of the 7 axes) `out/reports/vision-critique.json` must block `ms critique` (`ok: False`, exit code `1`).
- Negative `lead_in_sec` (J-Cut) on the first scene (`scene_index == 0`) must clamp cleanly to `0.0s` without raising an FFmpeg negative-delay error.

---

## File Map

- Create: `.agents/rules/architecture.md`
- Create: `.agents/skills/effectcraft-discovery/SKILL.md`
- Create: `.agents/skills/3d-product-cinematography/SKILL.md`
- Create: `.agents/skills/kinetic-3d-typography/SKILL.md`
- Create: `.agents/skills/visual-storyboarding/SKILL.md`
- Create: `.agents/skills/multimodal-critic-gate/SKILL.md`
- Create: `.agents/skills/feature-dev-workflow/SKILL.md`
- Modify: `src/media_studio/cli/motion.py` (add `ms motion catalog` subcommand)
- Modify: `src/media_studio/sfx.py` and `src/media_studio/data/sfx/sfx.yaml` (expand to 24+ 4-layer CC0 SFX/Foley pack + J/L-cut mixing)
- Create: `submodules/media-mcp` (git submodule pointing to `https://github.com/SuperBypassUdinnn/media-mcp.git`)
- Create: `src/media_studio/critique.py` and `src/media_studio/cli/critique.py` (Dual-Gate Critic: 6 quantitative gates + 7-axis Vision Rubric)
- Create: `src/media_studio/stage_3d.py` (Blender 5.3 3D product stage, PBR glass/liquid/metal, Chiaroscuro + volumetric lighting, `ECU/CU/MS/WS` speed-ramped camera rigs, alpha matte output)
- Create: `src/media_studio/typography_3d.py` (EffectCraft 0.24.1 Z-depth sandwiched 3D typography, per-character 3D animators, `cclightsweep`/`glow`/`cameralens`, and Master LUT + 35mm grain optical finishing)
- Create: `src/media_studio/scene_graph.py` and `src/media_studio/cli/scene.py` (`ms scene validate|render`)
- Create: `src/media_studio/storyboard.py` and `src/media_studio/cli/storyboard.py` (`ms storyboard build`)
- Modify: `src/media_studio/cli/__init__.py` (register `storyboard`, `scene`, `critique` CLI subcommands)
- Test: `tests/test_repo_skills_and_catalog.py`
- Test: `tests/test_sfx_world_class.py`
- Test: `tests/test_critique_dual_gate.py`
- Test: `tests/test_stage_and_typography_3d.py`
- Test: `tests/test_scene_graph_and_storyboard.py`

---

### Task 1: Repository Skill Contracts, Architectural Rules, & EffectCraft Catalog CLI

**Files:**
- Create: `.agents/rules/architecture.md`
- Create: `.agents/skills/effectcraft-discovery/SKILL.md`
- Create: `.agents/skills/3d-product-cinematography/SKILL.md`
- Create: `.agents/skills/kinetic-3d-typography/SKILL.md`
- Create: `.agents/skills/visual-storyboarding/SKILL.md`
- Create: `.agents/skills/multimodal-critic-gate/SKILL.md`
- Create: `.agents/skills/feature-dev-workflow/SKILL.md`
- Modify: `src/media_studio/cli/motion.py`
- Test: `tests/test_repo_skills_and_catalog.py`

**Interfaces:**
- Consumes: `media_studio.effectcraft_catalog.list_effects(category: str | None = None) -> list[dict[str, Any]]`, `media_studio.effectcraft_catalog.list_templates() -> list[dict[str, Any]]`
- Produces:
  - CLI command `ms motion catalog [--category <cat>] [--templates] [--json]` in `src/media_studio/cli/motion.py` (`cmd_motion_catalog(args: argparse.Namespace) -> int`)
  - Repository rules and skills in `.agents/rules/architecture.md` and `.agents/skills/<id>/SKILL.md`, symlinked into `~/.agents/skills/<id>` and `~/.config/opencode/skills/<id>`

- [ ] **Step 1: Write the failing test in `tests/test_repo_skills_and_catalog.py`**

```python
from pathlib import Path
from media_studio.cli import main

REQUIRED_SKILLS = (
    "effectcraft-discovery",
    "3d-product-cinematography",
    "kinetic-3d-typography",
    "visual-storyboarding",
    "multimodal-critic-gate",
    "feature-dev-workflow",
)

def test_repo_rules_and_skills_exist_with_valid_frontmatter() -> None:
    repo = Path(__file__).resolve().parents[1]
    arch_rule = repo / ".agents" / "rules" / "architecture.md"
    assert arch_rule.is_file()
    text = arch_rule.read_text(encoding="utf-8")
    assert "Asset Priority SOP" in text
    assert "60-30-10" in text

    for skill_id in REQUIRED_SKILLS:
        skill_md = repo / ".agents" / "skills" / skill_id / "SKILL.md"
        assert skill_md.is_file(), f"missing repo skill {skill_id}"
        content = skill_md.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert f"name: {skill_id}" in content

def test_cli_motion_catalog_outputs_effects_and_templates(capsys) -> None:
    rc = main(["motion", "catalog", "--category", "stylize", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ec.stylize.cclightsweep" in out

    rc_tpl = main(["motion", "catalog", "--templates", "--json"])
    assert rc_tpl == 0
    out_tpl = capsys.readouterr().out
    assert "text-orbit-3d" in out_tpl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_repo_skills_and_catalog.py -v -p no:cacheprovider`
Expected: FAIL (`AssertionError: assert False` on missing `.agents/rules/architecture.md` and unrecognized `catalog` subcommand).

- [ ] **Step 3: Author `.agents/rules/architecture.md`, the 6 `.agents/skills/<id>/SKILL.md` files, and `cmd_motion_catalog` in `src/media_studio/cli/motion.py`**

Include exact command tables, 60-30-10 color theory, the 8 world-class cinematography pillars (`ECU/CU/MS/WS` shot scale progression, speed ramping, asymmetrical Rule-of-Thirds & eye-trace, sensory vs typography alternation, Chiaroscuro lighting, Z-depth sandwiched 3D typography, 180-degree shutter optical pipeline, and 4-layer J/L-cut audio), and add the `catalog` subparser to `register_motion_parser` in `src/media_studio/cli/motion.py`. Symlink the 5 new domain skills into `~/.agents/skills/` and `~/.config/opencode/skills/`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_repo_skills_and_catalog.py -v -p no:cacheprovider && ~/.local/bin/ruff check src tests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .agents/ src/media_studio/cli/motion.py tests/test_repo_skills_and_catalog.py
git commit -m "feat(skills,motion): add repo-level creative skill contracts and ms motion catalog cli"
```

---

### Task 2: Context Hygiene (`~/Projects` Cleanup), 4-Layer CC0 SFX Library, & Scene-Adaptive Sound Design Engine

**Files:**
- Modify: `src/media_studio/sfx.py`
- Modify: `src/media_studio/data/sfx/sfx.yaml`
- Create: `src/media_studio/data/sfx/<18_new_48khz_wav_and_license_files>`
- Create: `src/media_studio/sound_design.py`
- Test: `tests/test_sfx_world_class.py`

**Interfaces:**
- Consumes: `media_studio.media.run_command`, `numpy`, `scipy.signal`
- Produces:
  - `SfxCue(path_or_id: str, at: float, gain_db: float = 0.0, lead_in_sec: float = 0.0, trail_out_sec: float = 0.0, layer: str = "transition")`
  - `list_bundled_sfx(tag: str | None = None, layer: str | None = None) -> list[SfxEntry]` returning 24 curated CC0 48kHz WAV entries across 4 layers (`"ambience"`, `"foley"`, `"transition"`, `"impact"`)
  - `modify_library_sound(source_id_or_path: str | Path, output: Path, *, target_duration_s: float, pitch_semitones: float = 0.0, transient_boost_db: float = 0.0, filter_sweep_hz: tuple[float, float] | None = None, pan_sweep: tuple[float, float] = (0.0, 0.0), trail_out_s: float = 0.0) -> Path` in `src/media_studio/sound_design.py`
  - `synthesize_sound_event(synth_type: str, output: Path, *, duration_s: float, params: dict[str, Any] | None = None) -> Path` supporting `"sub_bass"`, `"curve_progress"`, and `"velocity_whoosh"` at `48000 Hz`
  - `verify_event_envelopes(wav_path: Path, *, active_until_s: float | None = None) -> dict[str, Any]` checking end decay `< -45 dBFS` and zero orphan ticks after `active_until_s`

- [ ] **Step 1: Purge polluted legacy projects in `~/Projects/*` and verify empty state**

Run: `rm -rf ~/Projects/amv-demonslayer-3d ~/Projects/aura-noir-perfume ~/Projects/lumina-motion-v2 ~/Projects/scr2026-* && ls -A ~/Projects`
Expected: Empty output (zero legacy directories in `~/Projects`).

- [ ] **Step 2: Write the failing test in `tests/test_sfx_world_class.py`**

```python
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
        layer_items = list_bundled_sfx(tag=layer)
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
        params={"initial_frequency_hz": 80.0, "final_frequency_hz": 45.0, "attack_ms": 5.0, "decay_ms": 345.0},
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
        SfxCue("perfume-atomizer-mist", at=1.5, gain_db=-4.0, lead_in_sec=0.25, trail_out_sec=0.4, layer="foley"),
        SfxCue("sub-drop-40hz", at=2.0, gain_db=-3.0, layer="impact"),
    ]
    res = mix_transition_sfx(None, cues, output=out, duration=3.5, target_lufs=-14.0)
    assert res.is_file()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sfx_world_class.py -v -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError: No module named 'media_studio.sound_design'`).

- [ ] **Step 4: Stage the 24 CC0 48kHz multi-layer acoustic SFX/Foley WAVs + `.license` sidecars, update `sfx.yaml`, upgrade `sfx.py`, and implement `src/media_studio/sound_design.py`**

Implement STFT phase-vocoder time-stretching, ADSR exponential decay shaping, biquad filter sweep (`scipy.signal.sosfilt`), stereo panning, `sub_bass` / `curve_progress` / `velocity_whoosh` synthesizers, and `verify_event_envelopes`.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sfx_world_class.py tests/test_sfx.py -v -p no:cacheprovider && ~/.local/bin/ruff check src tests`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/media_studio/sfx.py src/media_studio/sound_design.py src/media_studio/data/sfx/ tests/test_sfx_world_class.py
git commit -m "feat(sound): add 24-asset CC0 foley library, scene-adaptive DSP modifier, and 48kHz synthesizer"
```

---

### Task 3: Git Submodule `media-mcp` & Mandatory Dual-Gate Critic (`ms critique`)

**Files:**
- Create: `submodules/media-mcp` (git submodule)
- Create: `src/media_studio/critique.py`
- Create: `src/media_studio/cli/critique.py`
- Modify: `src/media_studio/cli/__init__.py`
- Test: `tests/test_critique_dual_gate.py`

**Interfaces:**
- Consumes: `submodules/media-mcp/media` (`media_probe`, `media_motion`, `media_ocr`, `media_image_quality`, `media_beats`, `media_avsync`), `media_studio.sound_design.verify_event_envelopes`, `media_studio.video.qc_report`
- Produces:
  - `critique_palette(project: Project, frame_paths: Sequence[Path]) -> list[dict[str, Any]]` (WCAG contrast `>= 4.5:1`, anti-default `#38BDF8` check, CIELAB $\Delta E \le 38.0$ dominant cluster check, 60-30-10 accent area `<= 25%` check)
  - `critique_montage(scene_specs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]` (`shot_scale` + `camera_angle` anti-jump-cut check, sensory vs typography pacing contrast check, `focal_point` eye-trace delta `<= 0.45` check)
  - `validate_vision_rubric(report_path: Path, *, stage: str, min_mtime: float = 0.0) -> list[dict[str, Any]]` (enforces all 7 rubric axes `>= 4`, `passed is True`, `defects == []`, non-stale timestamp)
  - `critique_project(project: Project, *, stage: str = "video", scene_graph_path: Path | None = None, deliverables: tuple[str, ...] = ("reel-9x16", "master-16x9")) -> dict[str, Any]`
  - CLI `ms critique <project> [--stage storyboard|video] [--numbered] [--out <json>]`

- [ ] **Step 1: Add `submodules/media-mcp` git submodule**

Run: `git submodule add https://github.com/SuperBypassUdinnn/media-mcp.git submodules/media-mcp || git submodule update --init --recursive submodules/media-mcp`
Expected: `submodules/media-mcp/media/server.py` exists on disk.

- [ ] **Step 2: Write the failing test in `tests/test_critique_dual_gate.py`**

```python
import json
from pathlib import Path
from PIL import Image
from media_studio.critique import critique_montage, critique_palette, validate_vision_rubric
from media_studio.manifest import load_project

def test_critique_montage_rejects_adjacent_same_scale_and_all_text_clutter() -> None:
    scenes = [
        {"id": "s1", "duration": 4.0, "shot_scale": "MS", "camera_angle": "eye-level", "focal_point": [0.33, 0.5], "typography_3d": {"text": "ONE"}},
        {"id": "s2", "duration": 4.0, "shot_scale": "MS", "camera_angle": "eye-level", "focal_point": [0.90, 0.9], "typography_3d": {"text": "TWO"}},
        {"id": "s3", "duration": 4.0, "shot_scale": "CU", "camera_angle": "low-hero", "focal_point": [0.5, 0.5], "typography_3d": {"text": "THREE"}},
        {"id": "s4", "duration": 4.0, "shot_scale": "WS", "camera_angle": "high-zenith", "focal_point": [0.5, 0.5], "typography_3d": {"text": "FOUR"}},
    ]
    defects = critique_montage(scenes)
    categories = {d["defect"] for d in defects}
    assert any("jump-cut" in d.lower() for d in categories)
    assert any("eye-trace" in d.lower() for d in categories)
    assert any("sensory" in d.lower() for d in categories)

def test_critique_palette_rejects_off_palette_white_card_and_low_wcag(tmp_path: Path) -> None:
    proj_dir = tmp_path / "lux"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(
        "slug: lux\ntitle: Lux\nstatus: draft\ndeliverables: []\nbrand:\n  palette:\n    primary: '#080B11'\n    accent: '#F59E0B'\n    secondary: '#1E293B'\n    ink: '#F8FAFC'\nassets: {}\n",
        encoding="utf-8",
    )
    project = load_project(proj_dir)
    bad_frame = tmp_path / "white_card.png"
    Image.new("RGB", (320, 180), (255, 255, 255)).save(bad_frame)
    defects = critique_palette(project, [bad_frame])
    assert any(d["severity"] == "error" and "palette" in d["category"] for d in defects)

def test_validate_vision_rubric_blocks_missing_or_low_score(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    assert validate_vision_rubric(missing, stage="storyboard")

    low_report = tmp_path / "vision-critique.json"
    low_report.write_text(
        json.dumps({
            "stage": "storyboard",
            "evaluated_by": "google/antigravity-gemini-3.8-flash",
            "keyframes_inspected": ["s1.png"],
            "scores": {
                "three_d_object_and_environment_depth": 3,
                "three_d_and_kinetic_typography_z_depth": 5,
                "camera_choreography_speed_ramp_and_motion": 5,
                "shot_scale_montage_and_eye_trace": 5,
                "color_palette_selection_and_harmony": 5,
                "chiaroscuro_lighting_and_optical_finish": 5,
                "conceptual_fit_to_brief": 5,
            },
            "defects": [],
            "passed": True,
        }),
        encoding="utf-8",
    )
    defects = validate_vision_rubric(low_report, stage="storyboard")
    assert any("three_d_object_and_environment_depth" in d["defect"] for d in defects)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_critique_dual_gate.py -v -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError: No module named 'media_studio.critique'`).

- [ ] **Step 4: Implement `src/media_studio/critique.py`, `src/media_studio/cli/critique.py`, and wire into `src/media_studio/cli/__init__.py`**

Implement the 6 quantitative gates (`media_motion` hold check, `media_ocr` anti-boilerplate check, `media_image_quality` exposure/contrast check, `critique_palette` WCAG + CIELAB $\Delta E$ + 60-30-10 check, `verify_event_envelopes` + EBU R128 check, and `critique_montage`) plus Gate 2 (`validate_vision_rubric`).

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_critique_dual_gate.py -v -p no:cacheprovider && ~/.local/bin/ruff check src tests`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .gitmodules submodules/media-mcp src/media_studio/critique.py src/media_studio/cli/critique.py src/media_studio/cli/__init__.py tests/test_critique_dual_gate.py
git commit -m "feat(critique): integrate media-mcp submodule and implement dual-gate 7-axis visual critic"
```

---

### Task 4: 3D Product Stage & Chiaroscuro Camera Engine (`stage_3d.py`) + Z-Depth 3D Typography & Optical Finishing (`typography_3d.py`)

**Files:**
- Create: `src/media_studio/stage_3d.py`
- Create: `src/media_studio/typography_3d.py`
- Test: `tests/test_stage_and_typography_3d.py`

**Interfaces:**
- Consumes: `media_studio.blender.run_script`, `media_studio.effectcraft.EffectCraftRunner`, `media_studio.media.run_command`
- Produces:
  - `Stage3DSpec(model_path: Path | None, preset_geometry: str, shot_scale: str, camera_angle: str, camera_move: str, velocity_curve: str, focal_length_mm: float, dof_fstop: float, key_to_fill_ratio: float, primary_color: str, accent_color: str, volumetric_fog: bool = True, motion_blur: bool = True)`
  - `render_3d_stage_still(spec: Stage3DSpec, output_png: Path, *, matte_png: Path | None = None, width: int = 1920, height: int = 1080) -> Path`
  - `render_3d_stage_clip(spec: Stage3DSpec, output_mp4: Path, *, matte_mov: Path | None = None, duration: float = 4.0, fps: int = 60, width: int = 1920, height: int = 1080) -> Path`
  - `Typography3DSpec(text: str, subtext: str = "", style: str = "per-char-3d-reveal", font: str = "Noto Sans", color: str = "#F8FAFC", accent: str = "#F59E0B", z_layer: str = "behind-subject", focal_point: tuple[float, float] = (0.66, 0.42), effects: tuple[str, ...] = ("ec.stylize.cclightsweep", "ec.stylize.glow"))`
  - `render_3d_typography_still(spec: Typography3DSpec, output_png: Path, *, width: int = 1920, height: int = 1080) -> Path`
  - `render_3d_typography_clip(spec: Typography3DSpec, output_mov: Path, *, duration: float = 4.0, fps: int = 60, width: int = 1920, height: int = 1080) -> Path`
  - `composite_z_depth_sandwich(background_path: Path, typography_rgba: Path, subject_matte: Path | None, output_path: Path, *, z_layer: str = "behind-subject", apply_optical_grade: bool = True) -> Path`

- [ ] **Step 1: Write the failing test in `tests/test_stage_and_typography_3d.py`**

```python
from pathlib import Path
from PIL import Image
from media_studio.stage_3d import Stage3DSpec, build_blender_stage_script
from media_studio.typography_3d import (
    Typography3DSpec,
    build_effectcraft_3d_text_commands,
    composite_z_depth_sandwich,
)

def test_build_blender_stage_script_configures_chiaroscuro_and_speed_ramp() -> None:
    spec = Stage3DSpec(
        model_path=None,
        preset_geometry="luxury-perfume-flacon",
        shot_scale="ECU",
        camera_angle="low-hero",
        camera_move="orbital-reveal",
        velocity_curve="slow-fast-slow",
        focal_length_mm=105.0,
        dof_fstop=1.8,
        key_to_fill_ratio=8.0,
        primary_color="#080B11",
        accent_color="#F59E0B",
        volumetric_fog=True,
        motion_blur=True,
    )
    script = build_blender_stage_script(spec)
    assert "use_motion_blur = True" in script
    assert "lens = 105.0" in script
    assert "aperture_fstop = 1.8" in script
    assert "IOR" in script

def test_effectcraft_3d_text_commands_include_camera_lights_and_sweeps() -> None:
    spec = Typography3DSpec(
        text="AURA NOIR",
        subtext="EXTRAIT DE PARFUM",
        style="text-orbit-3d",
        color="#F8FAFC",
        accent="#F59E0B",
        z_layer="behind-subject",
        focal_point=(0.66, 0.42),
    )
    cmds = build_effectcraft_3d_text_commands(spec, width=1920, height=1080, duration=4.0, fps=60)
    cmd_ids = [c["id"] for c in cmds]
    assert "camera.newCamera" in cmd_ids
    assert "light.newLight" in cmd_ids
    assert "effect.apply" in cmd_ids

def test_composite_z_depth_sandwich_occludes_text_behind_subject_matte(tmp_path: Path) -> None:
    bg = tmp_path / "bg.png"
    txt = tmp_path / "txt.png"
    matte = tmp_path / "matte.png"
    out = tmp_path / "comp.png"

    Image.new("RGBA", (320, 180), (10, 14, 24, 255)).save(bg)
    Image.new("RGBA", (320, 180), (245, 158, 11, 255)).save(txt)
    # Center 80x80 subject matte (white=subject, black=background)
    m_img = Image.new("RGBA", (320, 180), (10, 14, 24, 0))
    for x in range(120, 200):
        for y in range(50, 130):
            m_img.putpixel((x, y), (20, 30, 50, 255))
    m_img.save(matte)

    res = composite_z_depth_sandwich(bg, txt, matte, out, z_layer="behind-subject", apply_optical_grade=False)
    assert res.is_file()
    out_img = Image.open(res)
    # Center pixel is occluded by subject matte (20, 30, 50), while outer pixel shows gold text (245, 158, 11)
    assert out_img.getpixel((160, 90))[:3] == (20, 30, 50)
    assert out_img.getpixel((40, 40))[:3] == (245, 158, 11)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stage_and_typography_3d.py -v -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError: No module named 'media_studio.stage_3d'`).

- [ ] **Step 3: Implement `src/media_studio/stage_3d.py` and `src/media_studio/typography_3d.py`**

Implement the Blender 5.3 procedural/GLB 3D stage builder (multi-part glass flacon with liquid amber core, gold collar, wet obsidian stone pedestal, condensation droplets, Chiaroscuro key/fill/rim lights, volumetric haze, speed-ramped camera trajectories, and foreground RGBA subject matte output) and EffectCraft 0.24.1 3D typography + Z-depth sandwich compositor + optical finishing pass (180-degree shutter motion blur, `ec.stylize.cclightsweep`, `ec.stylize.glow`, halation, and fine 35mm film grain).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_stage_and_typography_3d.py -v -p no:cacheprovider && ~/.local/bin/ruff check src tests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/media_studio/stage_3d.py src/media_studio/typography_3d.py tests/test_stage_and_typography_3d.py
git commit -m "feat(3d,motion): add 3D product stage engine, Z-depth sandwiched 3D typography, and optical finish"
```

---

### Task 5: Composable Scene-Graph Orchestrator (`ms scene`) & Visual Storyboard Engine (`ms storyboard`)

**Files:**
- Create: `src/media_studio/scene_graph.py`
- Create: `src/media_studio/storyboard.py`
- Create: `src/media_studio/cli/scene.py`
- Create: `src/media_studio/cli/storyboard.py`
- Modify: `src/media_studio/cli/__init__.py`
- Test: `tests/test_scene_graph_and_storyboard.py`

**Interfaces:**
- Consumes: `media_studio.stage_3d`, `media_studio.typography_3d`, `media_studio.sound_design`, `media_studio.critique`, `media_studio.gen.pipeline.generate_asset`
- Produces:
  - `SceneGraph` and `SceneNode` dataclasses in `src/media_studio/scene_graph.py` (`load_scene_graph(path: Path) -> SceneGraph`, `render_scene_graph(project: Project, graph: SceneGraph, *, scenes: Sequence[str] | None = None, deliverables: tuple[str, ...] = ("reel-9x16", "master-16x9"), force: bool = False) -> dict[str, Path]`)
  - `build_visual_storyboard(project: Project, graph: SceneGraph, *, scenes: Sequence[str] | None = None, output: Path | None = None) -> Path` in `src/media_studio/storyboard.py` rendering full-resolution 1080p keyframes per scene into `out/storyboard/<scene_id>.png` and assembling a visual-first bento contact sheet (`>= 85%` visual image area per panel) at `out/storyboard/storyboard.png`
  - CLI `ms storyboard build <project> [--scene <id>]` and `ms scene render <project> [--scene <id>] [--force]`

- [ ] **Step 1: Write the failing test in `tests/test_scene_graph_and_storyboard.py`**

```python
import json
from pathlib import Path
from PIL import Image
from media_studio.manifest import load_project
from media_studio.scene_graph import load_scene_graph, render_scene_graph
from media_studio.storyboard import build_visual_storyboard

def test_build_visual_storyboard_renders_high_res_bento_panels(tmp_path: Path, monkeypatch) -> None:
    proj_dir = tmp_path / "noir"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(
        "slug: noir\ntitle: Aura Noir\nstatus: draft\ndeliverables:\n- id: master-16x9\n  kind: video\n  container: mp4\n  resolution: 1920x1080\n  fps: 60\n  duration: 8.0\n  loudness_lufs: -14.0\nbrand:\n  palette:\n    primary: '#080B11'\n    accent: '#F59E0B'\n    secondary: '#1E293B'\n    ink: '#F8FAFC'\nassets: {}\n",
        encoding="utf-8",
    )
    sg_path = proj_dir / "scene-graph.json"
    sg_path.write_text(
        json.dumps({
            "project_slug": "noir",
            "aspects": ["16:9"],
            "fps": 60,
            "subtitles": False,
            "scenes": [
                {
                    "id": "scene-01-macro",
                    "duration": 2.5,
                    "engine": "blender-3d",
                    "shot_scale": "ECU",
                    "camera_angle": "low-hero",
                    "focal_point": [0.35, 0.48],
                    "typography_3d": None,
                },
                {
                    "id": "scene-02-hero",
                    "duration": 5.5,
                    "engine": "composite",
                    "shot_scale": "MS",
                    "camera_angle": "eye-level",
                    "focal_point": [0.40, 0.50],
                    "typography_3d": {"text": "AURA NOIR", "z_layer": "behind-subject"},
                },
            ],
        }),
        encoding="utf-8",
    )
    project = load_project(proj_dir)
    graph = load_scene_graph(sg_path)

    def _fake_scene_still(_project, scene, out_png, **_kw):
        img = Image.new("RGB", (1920, 1080), (14, 18, 28))
        img.save(out_png)
        return out_png

    monkeypatch.setattr("media_studio.storyboard.render_scene_keyframe", _fake_scene_still)
    sheet = build_visual_storyboard(project, graph)
    assert sheet.is_file()
    assert (proj_dir / "out" / "storyboard" / "scene-01-macro.png").is_file()
    assert (proj_dir / "out" / "storyboard" / "scene-02-hero.png").is_file()
    with Image.open(sheet) as im:
        assert im.width >= 1280
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scene_graph_and_storyboard.py -v -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError: No module named 'media_studio.scene_graph'`).

- [ ] **Step 3: Implement `src/media_studio/scene_graph.py`, `src/media_studio/storyboard.py`, `src/media_studio/cli/scene.py`, and `src/media_studio/cli/storyboard.py`**

Wire content-addressed scene caching (`out/cache/scenes/<sha256>.mp4`), 48kHz sound event synthesis/DSP assembly (`sound_design.py`), Z-depth 3D compositing, optical finishing pass, and `.fcproj` / `.otio.json` / `.mlt` timeline export.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scene_graph_and_storyboard.py -v -p no:cacheprovider && ~/.local/bin/ruff check src tests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/media_studio/scene_graph.py src/media_studio/storyboard.py src/media_studio/cli/scene.py src/media_studio/cli/storyboard.py src/media_studio/cli/__init__.py tests/test_scene_graph_and_storyboard.py
git commit -m "feat(scene,storyboard): add composable scene-graph orchestrator and visual-first storyboard engine"
```

---

### Task 6: Benchmark Verification — 3D Luxury Perfume Commercial (`~/Projects/aura-noir-3d`) at Storyboard Gate

**Files:**
- Create: `/home/superbypassudin/Projects/aura-noir-3d/project.yaml`
- Create: `/home/superbypassudin/Projects/aura-noir-3d/scene-graph.json`
- Create: `/home/superbypassudin/Projects/aura-noir-3d/out/storyboard/storyboard.png`
- Create: `/home/superbypassudin/Projects/aura-noir-3d/out/reports/vision-critique.json`

**Interfaces:**
- Consumes: `ms storyboard build`, `ms critique --stage storyboard`, `.agents/skills/*`
- Produces:
  - Verified 6-scene 3D Luxury Perfume Commercial storyboard (`~/Projects/aura-noir-3d/out/storyboard/storyboard.png` + `scene-01..06.png`) with true 3D glass/gold/liquid flacon, wet obsidian 3D environment, Chiaroscuro lighting, `ECU -> MS -> CU -> WS -> MS -> CU` shot scale progression, Z-depth sandwiched 3D typography, and 60-30-10 color harmony, passing `ms critique ~/Projects/aura-noir-3d --stage storyboard` (`ok: true`, 0 errors) and presented to the user for approval before full video rendering.

- [ ] **Step 1: Scaffold `~/Projects/aura-noir-3d` and author the 6-scene `scene-graph.json` following `.agents/skills/3d-product-cinematography/SKILL.md` and `.agents/skills/kinetic-3d-typography/SKILL.md`**

- [ ] **Step 2: Run `ms storyboard build ~/Projects/aura-noir-3d` to render the 6 full-resolution 3D + 3D Typography keyframes**

Run: `.venv/bin/python -m media_studio.cli storyboard build ~/Projects/aura-noir-3d`
Expected: `out/storyboard/scene-01..06.png` and `out/storyboard/storyboard.png` generated.

- [ ] **Step 3: Inspect every keyframe via `read`, iterate on any lighting/camera/3D-text defects, write `out/reports/vision-critique.json`, and run `ms critique ~/Projects/aura-noir-3d --stage storyboard`**

Run: `.venv/bin/python -m media_studio.cli critique ~/Projects/aura-noir-3d --stage storyboard --numbered`
Expected: `No defects found — critique passed.` (exit code 0).

- [ ] **Step 4: Present the visual `storyboard.png` and keyframe stills to the user for Gate 1 approval before running `ms scene render ~/Projects/aura-noir-3d`**



