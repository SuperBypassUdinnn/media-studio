# Promotional Video & Hybrid Generative Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end hybrid promotional video and free-quota generative pipeline (`9:16` Reels/Shorts + `16:9` Masters) on top of `media-studio`, closing existing CLI/design/video friction and expanding motion, audio/TTS/SFX, reframe, captions, generative providers, and promo orchestration.

**Architecture:** First decompose the 4,049-line `cli.py` monolith into `src/media_studio/cli/` and resolve Tier 1-3 friction plus `acquire._search_external`. Next layer brand reference packs, EffectCraft promo presets, Playwright frame-stepped HTML/SVG-to-video (`html_motion.py`), FFmpeg transitions (`transitions.py`), CC0 SFX (`sfx.py`), free TTS/cleanup (`tts.py`), auto-reframe (`reframe.py`), and word-highlight captions (`captions.py`). Finally add the pluggable free-quota generative package (`gen/`), the `ms promo` orchestrator (`promo.py`), `media-create` skill updates, and verify against a 30-second dual-aspect proving ground.

**Tech Stack:** Python 3.12+, FFmpeg 9.0.2, EffectCraft 0.19.0, FilmCraft 0.15.0, Inkscape 1.4.4, ImageMagick 7.1.2, `espeak-ng`, optional extras `edge-tts`, `playwright` (reusing `~/.cache/ms-playwright/`), `httpx`, `google-genai`, and existing `media-mcp` Whisper pipeline.

**Spec:** `docs/superpowers/specs/2026-11-12-promo-generative-pipeline-design.md`

## Global Constraints

- Dual resolution floor for non-preview video deliverables: `min(width, height) >= 1080` (`1080x1920` for `9:16` Reels/Shorts and `1920x1080` for `16:9` Masters), `>= 30 fps`, `-14.0 LUFS ± 1.0 LU`, `<= -1.5 dBTP`.
- Preserve `from media_studio.cli import main, build_parser` and every existing CLI flag/subcommand behavior across the `cli/` package split.
- Custom `RuntimeError` subclass per module (`HtmlMotionError`, `TransitionError`, `SfxError`, `TtsError`, `ReframeError`, `CaptionsError`, `GenError`, `PromoError`); CLI handlers catch domain exceptions, print `error: <msg>` to stderr, and return exit code `1`.
- Never execute real generative API calls, Playwright renders requiring network, or external downloads in default `pytest` unit runs; mock HTTP/SDK transports and guard heavy real-binary E2E tests with `@pytest.mark.slow`.
- Temporary scratch files live under `/tmp/opencode/<module>-<pid>/` and are cleaned in `finally`.
- Quota store (`~/.local/state/media-studio/gen-quota.json`) and catalog writes use `fcntl.flock` and are overridable via argument or env var in tests.
- Every generated media and TTS asset produces a REUSE `.license` sidecar and updates `assets/index.json` + `CREDITS.md`.
- `ruff check src tests`, `ruff format --check src tests`, and `codegraph sync` run cleanly before every task commit; commit messages are plain-text Conventional Commits without emojis.

## Review Focus

- **CLI backward compatibility across package split:** Moving `src/media_studio/cli.py` to `src/media_studio/cli/__init__.py` can break `import media_studio.cli as cli_module` patch points if internal helper attributes (for example `cli_module.apply_speed_ramp` or monkeypatched symbols in `tests/test_ramp.py`) move out of `cli` namespace. Task 1 tests must inspect existing `cli` monkeypatches across `tests/` and re-export patched symbols or update cleanly while keeping all 747 existing tests green.
- **AudioCue duration clamping against shorter or offset beds:** Setting `in_point=30.0` on a 45 s track for a 15 s video must clamp total audio span to 15 s without cutting short when `duration` exceeds remaining source length. Task 2 test must cover `in_point > 0` plus `clamp_to_video=True`.
- **Playwright `html_motion` alpha channel preservation:** Headless Chromium screenshots default to opaque white backgrounds unless both `omit_background=True` AND transparent CSS `body { background: transparent }` are set, and FFmpeg must encode `yuva444p10le` (`prores_ks -profile:v 4444`) rather than `yuv420p`. Task 4 `@pytest.mark.slow` E2E test must assert `pix_fmt == "yuva444p10le"` and non-zero transparent corners.
- **Reframe crop bounds and jitter on rapid subject cuts:** Smoothing focal center `(cx, cy)` across a hard shot cut would pan slowly across the cut boundary and slice a subject in half; `reframe.py` must reset the smoother at scene boundaries (`scene.detect_scenes`) and clamp `x` strictly inside `[0, source_w - crop_w]`. Task 7 test must assert scene-boundary reset and out-of-bounds clamping.
- **Generative cascade & quota lock safety:** When `aistudio` returns HTTP 429 or hits daily cap while `max_gen_calls=1`, cascade must advance to `huggingface` / `keyless` / deterministic still parallax without double-counting failed attempts against the user's `--max-gen-calls` cap unless an asset was generated, and `gen-quota.json` must survive concurrent writes via `fcntl.flock`. Task 8 test must verify both quota-cap accounting and lock acquisition.

---

### Task 1: Modular CLI Package Split (`src/media_studio/cli/`)

**Files:**
- Delete/Replace: `src/media_studio/cli.py` -> package `src/media_studio/cli/`
- Create: `src/media_studio/cli/__init__.py`, `common.py`, `project.py`, `assets.py`, `video.py`, `audio.py`, `design.py`, `motion.py`, `gen.py`, `promo.py`, `three_d.py`, `triage_see.py`
- Test: `tests/test_cli.py`, `tests/test_cli_package.py`

**Interfaces:**
- Consumes: existing domain modules imported previously in `src/media_studio/cli.py`.
- Produces: `media_studio.cli.build_parser() -> argparse.ArgumentParser`, `media_studio.cli.main(argv: list[str] | None = None) -> int`, `register_*_parser(subparsers)` per domain module.

- [ ] **Step 1: Audit monkeypatch targets and write parity test in `tests/test_cli_package.py`**

  Check `grep -rn "cli\." tests/` first to list any symbols patched on `media_studio.cli` (for example `tests/test_ramp.py`). Write `tests/test_cli_package.py` asserting:
  - `from media_studio.cli import main, build_parser` works.
  - `build_parser().parse_args([...])` succeeds for all existing top-level commands (`doctor`, `project`, `assets`, `video`, `library`, `acquire`, `3d`, `design`, `image`, `fonts`, `presets`, `triage`, `motion`, `see`) plus placeholder registration hooks for `audio`, `gen`, `promo`.
  - Every submodule stays under 600 lines.

- [ ] **Step 2: Run test to verify it fails / identifies parity requirements**

  Run: `.venv/bin/python -m pytest tests/test_cli_package.py -q`
  Expected: FAIL (`tests/test_cli_package.py` missing or submodule imports missing).

- [ ] **Step 3: Decompose `src/media_studio/cli.py` into `src/media_studio/cli/`**

  Move shared argument/output/error helpers into `common.py`. Extract each parser+handler group into its domain module (`project.py`, `assets.py`, `video.py`, `design.py`, `motion.py`, `three_d.py`, `triage_see.py`), add empty/stub `register_audio_parser`, `register_gen_parser`, `register_promo_parser` in `audio.py`, `gen.py`, `promo.py` wired into `__init__.py`, and re-export any symbol monkeypatched by existing tests on `media_studio.cli`. Remove monolithic `src/media_studio/cli.py`.

- [ ] **Step 4: Run full non-slow test suite & lint**

  Run: `.venv/bin/python -m pytest -q -p no:cacheprovider -k "not slow" && ~/.local/bin/ruff check src tests && ~/.local/bin/ruff format --check src tests`
  Expected: all tests pass with zero regressions.

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/cli tests/test_cli_package.py && git add -u src/media_studio/cli.py && git commit -m "refactor(cli): split monolithic cli.py into domain modules"`

---

### Task 2: Foundation Friction Fixes (Design Cutout/Safe-Area, Video Force/Audio Clamp, EffectCraft, Acquire)

**Files:**
- Modify: `src/media_studio/manifest.py`, `src/media_studio/design.py`, `templates/design/video-thumbnail.svg`, `templates/design/social-post.svg`, `templates/design/poster-a3.svg`
- Modify: `src/media_studio/video.py`, `src/media_studio/fcproj.py`, `src/media_studio/mlt.py`
- Modify: `src/media_studio/effectcraft.py`, `src/media_studio/acquire.py`, `src/media_studio/cli/design.py`, `src/media_studio/cli/video.py`, `src/media_studio/cli/motion.py`
- Test: `tests/test_design.py`, `tests/test_video.py`, `tests/test_fcproj.py`, `tests/test_effectcraft.py`, `tests/test_acquire.py`

**Interfaces:**
- Consumes: `search.run_search`, `ledger.read_sidecar`, `design.compose`.
- Produces:
  - `VariantSpec(..., image: str | None = None, image_fit: str = "contain", image_position: str = "right", headline_size: int | None = None, subhead_size: int | None = None)`
  - `AudioCue(..., duration: float | None = None, clamp_to_video: bool = True)`
  - `render_filmcraft(project, deliverable_id=None, *, force=False, ...)` and `--force` on `ms video render`
  - `ms design render` accepting both `--variant` and `--deliverable`
  - `effectcraft.title_sequence(..., output=Path(...))` normalizing relative paths and coercing `.mp4` -> `.mov` when `transparent=True`
  - `acquire._search_external(*, type: str, tags: list[str]) -> list[dict[str, Any]] | None` wired to `search.run_search`

- [ ] **Step 1: Write failing tests for all four friction blocks**

  - `tests/test_design.py`: `test_variant_image_cutout_composites_into_svg_and_png`, `test_safe_area_scales_with_1080p_canvas_no_overflow`, `test_design_render_accepts_deliverable_alias`.
  - `tests/test_video.py` & `tests/test_fcproj.py`: `test_audio_cue_clamps_long_music_to_video_duration_with_offset` (pins `in_point > 0` + `clamp_to_video=True`), `test_video_render_force_overwrites_existing_fcproj`.
  - `tests/test_effectcraft.py`: `test_relative_out_path_resolves_cleanly_and_coerces_mov_for_alpha`.
  - `tests/test_acquire.py`: `test_acquire_search_external_delegates_to_search_and_ranks_cc0`.

- [ ] **Step 2: Run tests to verify they fail**

  Run: `.venv/bin/python -m pytest tests/test_design.py tests/test_video.py tests/test_fcproj.py tests/test_effectcraft.py tests/test_acquire.py -k "cutout or safe_area or deliverable_alias or clamps_long_music or force_overwrites or relative_out_path or delegates_to_search" -q`
  Expected: FAIL.

- [ ] **Step 3: Implement friction fixes across `manifest.py`, `design.py`, `templates/design/*.svg`, `video.py`, `fcproj.py`, `effectcraft.py`, `acquire.py`, and CLI handlers**

  - Update SVG templates to use proportional `{{safe_x}}`, `{{safe_y}}`, `{{safe_w}}`, `{{safe_h}}`, `{{headline_size}}`, `{{subhead_size}}`, and `<image id="foreground" ... xlink:href="{{foreground_href}}"/>`; strip `#foreground` node when `variant.image` is absent.
  - Clamp `AudioCue` length in both `fcproj.py` (`out` frame clamped to `video_frames`) and `mlt.py`/`video.py` (`AudioPlan.duration` clamped to `timeline.duration`).
  - Wire `--force` in `video.render_filmcraft` and `cli/video.py`.
  - Resolve `output = Path(output).resolve()` and coerce `output = output.with_suffix(".mov")` when `transparent=True` in `effectcraft.py`.
  - Implement `_search_external` in `acquire.py` calling `search.run_search` for mapped classes (`video`, `audio`, `music`, `image`), returning ranked candidate dicts when non-empty or `None` when empty/offline.

- [ ] **Step 4: Run targeted tests + full suite + lint**

  Run: `.venv/bin/python -m pytest tests/test_design.py tests/test_fcproj.py tests/test_effectcraft.py tests/test_acquire.py tests/test_video.py -q && ~/.local/bin/ruff check src tests`
  Expected: PASS.

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/ templates/design/ tests/ && git commit -m "fix(core): resolve design cutout, video force/audio clamp, effectcraft, and acquire gaps"`

---

### Task 3: Brand Kit Reference Pack and EffectCraft Promo Motion Presets

**Files:**
- Modify: `src/media_studio/manifest.py`, `src/media_studio/effectcraft.py`, `src/media_studio/cli/motion.py`
- Test: `tests/test_manifest.py`, `tests/test_effectcraft.py`

**Interfaces:**
- Consumes: `manifest.Brand`, `effectcraft.build_ecp`, `effectcraft.render`.
- Produces:
  - `@dataclass(frozen=True) class ReferencePack`: `product: tuple[str, ...]`, `spokesperson: tuple[str, ...]`, `style_board: tuple[str, ...]`
  - `@dataclass(frozen=True) class BrandVoice`: `engine: str`, `voice_id: str`, `rate: str`
  - `Brand(..., references: ReferencePack, voice: BrandVoice | None, safe_zones: dict[str, dict[str, int]])`
  - `PROMO_MOTION_PRESETS: dict[str, dict[str, Any]]` (`promo-hook-slam`, `promo-kinetic-callout`, `promo-metric-badge`, `promo-lower-third-clean`, `promo-cta-endcard`)
  - `render_promo_preset(preset_name: str, *, text: str, subtext: str = "", output: Path, project: Project | None = None, width: int = 1080, height: int = 1920, fps: int = 30, duration: float = 3.0) -> MotionResult`

- [ ] **Step 1: Write failing tests in `tests/test_manifest.py` and `tests/test_effectcraft.py`**

  - `tests/test_manifest.py`: parse `brand.references`, `brand.voice`, `brand.safe_zones` cleanly; validate referenced asset IDs exist in `project.assets`; ensure existing manifests without those fields still pass.
  - `tests/test_effectcraft.py`: test `PROMO_MOTION_PRESETS` contains all 5 preset IDs, scales positions/font sizes proportionally for both `1080x1920` and `1920x1080`, injects `project.brand.palette` tokens, and validates ECP spec cleanly.

- [ ] **Step 2: Run tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_manifest.py tests/test_effectcraft.py -k "reference_pack or promo_preset" -q`
  Expected: FAIL.

- [ ] **Step 3: Implement `ReferencePack`, `BrandVoice`, and `PROMO_MOTION_PRESETS` + CLI `--preset promo-*` support**

  Update `manifest.py` dataclasses and validator; add `PROMO_MOTION_PRESETS` and `render_promo_preset()` in `effectcraft.py` and wire `--preset` in `ms motion title` to accept both `AMV_MOTION_PRESETS` and `PROMO_MOTION_PRESETS` with `--subtext` and `--project` brand token injection.

- [ ] **Step 4: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_manifest.py tests/test_effectcraft.py -q && ~/.local/bin/ruff check src tests`
  Expected: PASS.

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/manifest.py src/media_studio/effectcraft.py src/media_studio/cli/motion.py tests/test_manifest.py tests/test_effectcraft.py && git commit -m "feat(motion): add brand reference pack and 5 EffectCraft promo presets"`

---

### Task 4: Playwright Frame-Stepped HTML/SVG-to-Video Engine (`html_motion.py`)

**Files:**
- Create: `src/media_studio/html_motion.py`
- Create: `templates/motion-html/saas-feature-card.html`, `device-mockup.html`, `metric-counter.html`, `kinetic-captions.html`
- Modify: `src/media_studio/cli/motion.py`, `src/media_studio/tools.py`, `pyproject.toml`
- Test: `tests/test_html_motion.py`

**Interfaces:**
- Consumes: `manifest.Project`, `design.brand_tokens`, Playwright `sync_playwright`, FFmpeg binary.
- Produces:
  - `class HtmlMotionError(RuntimeError)`
  - `HTML_MOTION_TEMPLATES: tuple[str, ...]`
  - `@dataclass(frozen=True) class HtmlMotionResult`: `output: Path`, `template: str`, `frames: int`, `fps: int`, `width: int`, `height: int`, `transparent: bool`, `duration: float`
  - `render_html_motion(template: str | Path, *, output: Path, params: dict[str, Any] | None = None, project: Project | None = None, width: int = 1080, height: int = 1920, fps: int = 30, duration: float = 3.0, transparent: bool = True) -> HtmlMotionResult`

- [ ] **Step 1: Write failing unit and slow E2E tests in `tests/test_html_motion.py`**

  - Unit tests (mocking Playwright page object): validate template resolution, `project.brand` token merging into `__msSeek` params, frame count `round(duration * fps)`, `.mov` coercion when `transparent=True`, temp dir cleanup in `finally`, and clear `HtmlMotionError` with install hint if `playwright` is absent.
  - `@pytest.mark.slow` E2E test (skipped only if `playwright` or cached Chromium unavailable): render `saas-feature-card.html` at `320x240`, `fps=10`, `duration=0.5`, `transparent=True` -> assert `ffprobe` reports `pix_fmt == "yuva444p10le"`, `codec_name == "prores"`, `nb_read_frames == 5`, and corner alpha pixel == 0 (pins Review Focus transparency guarantee).

- [ ] **Step 2: Run unit tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_html_motion.py -k "not slow" -q`
  Expected: `ModuleNotFoundError: No module named 'media_studio.html_motion'`

- [ ] **Step 3: Implement `src/media_studio/html_motion.py`, 4 templates under `templates/motion-html/`, CLI `ms motion html`, and `[render-html]` extra**

  - Check Playwright + Chromium availability cleanly; capture `frame_%06d.png` via `page.screenshot(omit_background=transparent)` inside `/tmp/opencode/html-motion-<pid>/` cleaned in `finally`.
  - Encode PNGs with `prores_ks -profile:v 4444 -pix_fmt yuva444p10le` when `transparent=True`, else `libx264 -pix_fmt yuv420p`.
  - Wire `ms motion html` subcommand in `src/media_studio/cli/motion.py`.

- [ ] **Step 4: Run unit + slow E2E tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_html_motion.py -q && ~/.local/bin/ruff check src tests`
  Expected: unit tests pass (and E2E passes once `playwright` is installed in `.venv` or skipped gracefully when extra absent).

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/html_motion.py templates/motion-html/ src/media_studio/cli/motion.py src/media_studio/tools.py pyproject.toml tests/test_html_motion.py && git commit -m "feat(motion): add Playwright frame-stepped html-to-video renderer"`

---

### Task 5: FFmpeg Transitions (`transitions.py`) and Bundled CC0 SFX Pack (`sfx.py`)

**Files:**
- Create: `src/media_studio/transitions.py`, `src/media_studio/sfx.py`
- Create: `src/media_studio/data/sfx/whoosh-soft.wav`, `whoosh-fast.wav`, `impact-sub.wav`, `impact-hit.wav`, `riser-build.wav`, `ui-pop.wav`, `sfx.yaml`
- Modify: `src/media_studio/cli/video.py`, `src/media_studio/cli/audio.py`
- Test: `tests/test_transitions.py`, `tests/test_sfx.py`

**Interfaces:**
- Consumes: `ffmpeg`, `ffprobe`, `media.probe`, `library.search_assets`.
- Produces:
  - `class TransitionError(RuntimeError)`
  - `TRANSITION_KINDS: tuple[str, ...]` (`whip_pan_left`, `whip_pan_right`, `zoom_punch_in`, `zoom_punch_out`, `rgb_split_hit`, `flash_white`, `light_leak_warm`, `slide_up`)
  - `apply_transition(clip_a: Path, clip_b: Path, *, kind: str, output: Path, duration_ms: int = 250, width: int = 1080, height: int = 1920, fps: int = 30) -> TransitionResult`
  - `class SfxError(RuntimeError)`
  - `list_bundled_sfx(tag: str | None = None) -> list[SfxEntry]`
  - `mix_transition_sfx(base_audio: Path | None, events: list[SfxCue], *, output: Path, total_duration: float, target_lufs: float = -14.0) -> Path`

- [ ] **Step 1: Write failing tests in `tests/test_transitions.py` and `tests/test_sfx.py`**

  - `tests/test_transitions.py`: validate all 8 transition names, invalid kind/duration rejection, real FFmpeg filtergraph execution joining two 1 s synthetic clips with `whip_pan_left` and `rgb_split_hit` -> assert output duration, resolution, fps, and zero errors.
  - `tests/test_sfx.py`: verify all 6 bundled CC0 WAV files exist and load cleanly; verify `list_bundled_sfx(tag="whoosh")`; verify `mix_transition_sfx` with 2 cues over a base silent/tone WAV produces valid output matching `total_duration` and peak `<= -1.5 dBTP`.

- [ ] **Step 2: Run tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_transitions.py tests/test_sfx.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Generate deterministic CC0 starter WAVs under `src/media_studio/data/sfx/` and implement `transitions.py` + `sfx.py` + CLI commands**

  - Generate clean synthesized CC0 WAV effects via FFmpeg/NumPy (`whoosh-soft`, `whoosh-fast`, `impact-sub`, `impact-hit`, `riser-build`, `ui-pop`) and document them in `sfx.yaml` with `license: CC0-1.0`.
  - Implement `transitions.py` normalizing both inputs to `(width, height, fps, yuv420p)` before executing `xfade`/custom `geq`/`scale`/`rgbashift` filtergraphs.
  - Implement `sfx.py` placing each cue with `adelay`, `volume`, `amix`, and `alimiter=limit=0.84` (`-1.5 dBTP`).
  - Wire `ms video transition` and `ms audio sfx list|mix`.

- [ ] **Step 4: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_transitions.py tests/test_sfx.py -q && ~/.local/bin/ruff check src tests`
  Expected: PASS.

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/transitions.py src/media_studio/sfx.py src/media_studio/data/sfx/ src/media_studio/cli/ tests/test_transitions.py tests/test_sfx.py && git commit -m "feat(video,audio): add promo/AMV ffmpeg transitions and bundled CC0 SFX pack"`

---

### Task 6: Speech TTS (`tts.py`), Vocal Cleanup, and `ms doctor` Optional Checks

**Files:**
- Create: `src/media_studio/tts.py`
- Modify: `src/media_studio/audio.py`, `src/media_studio/tools.py`, `src/media_studio/cli/audio.py`, `pyproject.toml`
- Test: `tests/test_tts.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: `audio.normalize_loudness`, `ledger.write_index_and_credits`, `sidecar.sidecar_path`.
- Produces:
  - `class TtsError(RuntimeError)`
  - `@dataclass(frozen=True) class WordTiming`: `word: str`, `start: float`, `end: float`
  - `@dataclass(frozen=True) class TtsResult`: `audio_path: Path`, `words_path: Path`, `sidecar_path: Path`, `engine: str`, `voice: str`, `duration: float`, `words: tuple[WordTiming, ...]`
  - `synthesize_speech(text: str, *, output: Path, voice: str = "id-ID-ArdiNeural", engine: str = "auto", rate: str = "+0%", words_out: Path | None = None, target_lufs: float = -16.0) -> TtsResult`
  - `clean_voiceover(source: Path, *, output: Path, engine: str = "auto", target_lufs: float = -16.0) -> Path`

- [ ] **Step 1: Write failing tests in `tests/test_tts.py` and `tests/test_tools.py`**

  - `tests/test_tts.py`: mock `edge_tts` `Communicate.stream()` emitting audio chunks + `WordBoundary` events -> assert `synthesize_speech` writes WAV normalized to `-16 LUFS`, writes `words.json` with accurate `(word, start, end)`, writes `.license` sidecar with `Provider: edge-tts` + `Voice` + `Prompt`; test fallback ladder (`edge` unavailable -> `piper`/`kokoro` -> `espeak-ng` real synthesis smoke test on `espeak-ng`); test `clean_voiceover` FFmpeg chain on a noisy synthetic WAV -> verify output file, duration, and LUFS near `-16.0`.
  - `tests/test_tools.py`: verify missing DeepFilterNet/UVR venvs report as `optional` rather than fatal `FAIL`, and `edge-tts`/`playwright`/`httpx` appear as optional checks.

- [ ] **Step 2: Run tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_tts.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `tts.py`, `clean_voiceover` in `audio.py`, `tools.py` updates, `cli/audio.py`, and `[tts]` extra**

  - Implement `edge-tts` adapter capturing `WordBoundary` offsets/durations (`offset / 1e7`), `piper`/`kokoro` CLI/module hook, and `espeak-ng -w` fallback with estimated word timing grid.
  - Write REUSE `.license` sidecar (`SPDX-License-Identifier: LicenseRef-TTS-Generated`, `Provider`, `Model`, `Prompt`, `Generated-At`, `SHA256`).
  - Implement `clean_voiceover` supporting DeepFilterNet when present and deterministic FFmpeg voice chain otherwise.
  - Wire `ms audio tts` and `ms audio clean`.

- [ ] **Step 4: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_tts.py tests/test_tools.py -q && ~/.local/bin/ruff check src tests`
  Expected: PASS.

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/tts.py src/media_studio/audio.py src/media_studio/tools.py src/media_studio/cli/audio.py pyproject.toml tests/test_tts.py tests/test_tools.py && git commit -m "feat(audio): add multi-engine free TTS, word timings, and vocal cleanup"`

---

### Task 7: Subject-Tracked Auto-Reframe (`reframe.py`) and Word-Highlight Captions (`captions.py`)

**Files:**
- Create: `src/media_studio/reframe.py`, `src/media_studio/captions.py`
- Modify: `src/media_studio/cli/video.py`
- Test: `tests/test_reframe.py`, `tests/test_captions.py`

**Interfaces:**
- Consumes: `scene.detect_scenes`, `ingest.probe_media`, `tts.WordTiming`, `html_motion.render_html_motion`, `media-mcp/transcribe` (optional).
- Produces:
  - `class ReframeError(RuntimeError)`
  - `safe_zone_for(aspect: str, width: int, height: int) -> dict[str, int]`
  - `reframe_video(source: Path, *, aspect: str = "9:16", mode: str = "auto", output: Path, scene_threshold: float = 0.3) -> ReframeResult`
  - `class CaptionsError(RuntimeError)`
  - `extract_word_timestamps(media_path: Path) -> list[WordTiming]`
  - `group_words(words: list[WordTiming], *, max_words: int = 3, max_chars: int = 24) -> list[CaptionChunk]`
  - `render_captions(source: Path, *, words: list[WordTiming] | Path, style: str = "promo-pop", aspect: str = "9:16", accent_color: str = "#38BDF8", output: Path, engine: str = "ass") -> Path`

- [ ] **Step 1: Write failing tests in `tests/test_reframe.py` and `tests/test_captions.py`**

  - `tests/test_reframe.py`:
    - Pure unit tests for focal-center smoothing, clamping inside `[0, source_w - crop_w]`, and resetting smoother at scene boundaries (pins Review Focus scene-boundary check).
    - Real FFmpeg test reframing a synthetic `1920x1080` moving-box clip to `1080x1920` in `track` and `blur-pad` modes -> assert `ffprobe` returns `1080x1920`.
  - `tests/test_captions.py`:
    - Unit tests for `group_words` punctuation splitting & `max_chars` wrapping; `.ass` generation placing alignment/MarginV inside `safe_zone_for("9:16", 1080, 1920)` (`MarginV >= 380`); `extract_word_timestamps` parsing mocked `media-mcp` Whisper JSON output; clear error when neither `words.json` nor `media-mcp/transcribe/.venv` is available.
    - Real FFmpeg burn-in test placing `.ass` word-highlight captions onto a synthetic `1080x1920` clip -> assert non-empty caption pixels inside safe zone.

- [ ] **Step 2: Run tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_reframe.py tests/test_captions.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/media_studio/reframe.py`, `src/media_studio/captions.py`, and CLI `ms video reframe` / `ms video captions`**

  - In `reframe.py`, detect scenes via `scene.detect_scenes`, sample grayscale strips / motion centroid via FFmpeg rawvideo frames (`numpy` or `PIL` without forcing OpenCV), smooth per scene, clamp crop window, and generate FFmpeg `crop` expression or `blur-pad` composite.
  - In `captions.py`, resolve `words` from JSON/SRT or invoke `~/.clone/Github/SuperBypassUdinnn/media-mcp/transcribe/.venv/bin/python .../transcribe.py` (configurable via `MEDIA_MCP_ROOT`), group into chunks, generate `.ass` with active-word `\c`/`\fscx` tags (or invoke `html_motion` `kinetic-captions.html` when `engine="html"`), and burn/composite cleanly.

- [ ] **Step 4: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_reframe.py tests/test_captions.py -q && ~/.local/bin/ruff check src tests`
  Expected: PASS.

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/reframe.py src/media_studio/captions.py src/media_studio/cli/video.py tests/test_reframe.py tests/test_captions.py && git commit -m "feat(video): add subject-tracked auto-reframe and safe-zone word-highlight captions"`

---

### Task 8: Pluggable Free-Quota Generative Layer (`src/media_studio/gen/`) and Keys Wizard

**Files:**
- Create: `src/media_studio/gen/__init__.py`, `base.py`, `quota.py`, `pipeline.py`, `providers/__init__.py`, `providers/aistudio.py`, `providers/huggingface.py`, `providers/keyless.py`, `providers/plugins.py`
- Modify: `src/media_studio/cli/gen.py`, `src/media_studio/keys.py`, `src/media_studio/tools.py`, `scripts/keys-wizard.sh`, `pyproject.toml`
- Test: `tests/test_gen_quota.py`, `tests/test_gen_providers.py`, `tests/test_gen_pipeline.py`, `tests/test_keys_wizard.py`

**Interfaces:**
- Consumes: `keys.load_secrets`, `ledger.write_index_and_credits`, `sidecar.sidecar_path`, `acquire.acquire_asset`, `parallax.compose_parallax`.
- Produces:
  - `class GenError(RuntimeError)`
  - `GenRequest`, `GenResult`, `GenProvider` protocol (`base.py`)
  - `QuotaStore(path: Path = DEFAULT_QUOTA_PATH)` with `fcntl.flock`, `can_spend(provider, max_daily)`, `record_spend(provider)`, `set_cooldown(provider, seconds)`, `reset(provider=None)`
  - `generate_asset(request: GenRequest, *, project: Project | None = None, output: Path, max_gen_calls: int = 6, quota_store: QuotaStore | None = None) -> GenResult`
  - `generate_storyboard(scenes: list[str], *, project: Project | None = None, out_dir: Path, max_gen_calls: int = 6) -> list[GenResult]`

- [ ] **Step 1: Write failing tests across `tests/test_gen_quota.py`, `tests/test_gen_providers.py`, `tests/test_gen_pipeline.py`**

  - `tests/test_gen_quota.py`: test `QuotaStore` persistence, daily rollover, `set_cooldown` skipping rate-limited providers, and `fcntl.flock` acquisition (pins Review Focus lock test).
  - `tests/test_gen_providers.py`: test `aistudio`, `huggingface`, `keyless`, and plugin adapters (`fal`, `replicate`, `kling`, `seedance`) with mocked HTTP/SDK responses; verify skipped-without-key behavior; verify `references` attachment in `aistudio`.
  - `tests/test_gen_pipeline.py`:
    - Verify cascade `aistudio` (429 cooldown) -> `huggingface` -> `keyless` -> deterministic B-roll fallback without double-counting failed calls against `max_gen_calls` (pins Review Focus quota-cap test).
    - Verify image-only provider answering `kind="video"` triggers Ken Burns / 2.5D parallax motion conversion (`fallback_used=True`, `fallback_reason="motion-from-still"`).
    - Verify full REUSE `.license` sidecar (`Provider`, `Model`, `Prompt`, `Seed`, `Generated-At`, `SHA256`) and `CREDITS.md` update (`[AI Generated: ...]`).

- [ ] **Step 2: Run tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_gen_quota.py tests/test_gen_providers.py tests/test_gen_pipeline.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/media_studio/gen/*`, `cli/gen.py`, `keys.py`/`scripts/keys-wizard.sh`, and `[gen]`/`[promo]` extras**

  - Implement `QuotaStore` with `fcntl.flock` and `MEDIA_STUDIO_QUOTA_PATH` env override.
  - Implement provider adapters (`aistudio` via `GEMINI_API_KEY`, `huggingface` via `HF_TOKEN`/public Space endpoints, `keyless` via Pollinations image API, plus `fal`/`replicate`/`kling`/`seedance` slots).
  - Implement `generate_asset` cascade, still-to-motion fallback, deterministic fallback, and full sidecar + ledger registration.
  - Wire `ms gen image|video|storyboard|quota|providers` and extend `keys.py` / `scripts/keys-wizard.sh`.

- [ ] **Step 4: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_gen_quota.py tests/test_gen_providers.py tests/test_gen_pipeline.py tests/test_keys_wizard.py -q && ~/.local/bin/ruff check src tests`
  Expected: PASS.

- [ ] **Step 5: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/gen/ src/media_studio/cli/gen.py src/media_studio/keys.py src/media_studio/tools.py scripts/keys-wizard.sh pyproject.toml tests/test_gen_*.py tests/test_keys_wizard.py && git commit -m "feat(gen): add pluggable free-quota generative layer with cascade and provenance"`

---

### Task 9: Promo Orchestrator (`promo.py`), `media-create` Skill, and 30s Proving Ground

**Files:**
- Create: `src/media_studio/promo.py`
- Modify: `src/media_studio/cli/promo.py`
- Modify: `~/.agents/skills/media-create/SKILL.md` (in `~/.agents` scoped repo; use its worktree per repo rules)
- Test: `tests/test_promo.py`

**Interfaces:**
- Consumes: `manifest.load_project`, `tts.synthesize_speech`, `tts.clean_voiceover`, `sfx.mix_transition_sfx`, `transitions.apply_transition`, `effectcraft.render_promo_preset`, `html_motion.render_html_motion`, `reframe.reframe_video`, `captions.render_captions`, `gen.pipeline.generate_asset`, `video.qc_report`, `see.peek`.
- Produces:
  - `class PromoError(RuntimeError)`
  - `@dataclass(frozen=True) class PromoBeat`, `@dataclass(frozen=True) class PromoPlan`
  - `plan_promo(project: Project, *, script: str, aspects: tuple[str, ...] = ("9:16", "16:9"), out_path: Path | None = None) -> PromoPlan`
  - `render_promo(project: Project, plan: PromoPlan, *, auto: bool = False, max_gen_calls: int = 6, force: bool = False, deliverables: tuple[str, ...] = ("reel-9x16", "master-16x9")) -> PromoRenderResult`
  - `qc_promo(project: Project, *, deliverables: tuple[str, ...] = ("reel-9x16", "master-16x9"), use_media_mcp: bool = True) -> dict[str, Any]`

- [ ] **Step 1: Write failing tests in `tests/test_promo.py`**

  - Unit tests: `plan_promo` decomposes script into typed beats (`hook`, `problem`, `demo`, `proof`, `cta`), estimates `ms gen` calls, injects `brand.references`; `render_promo` enforces Storyboard/Quota Gate when `auto=False` and runs cleanly when `auto=True` with mocked `gen` + `html_motion` + `effectcraft` + real FFmpeg assembly; `qc_promo` verifies `1080x1920` and `1920x1080` dimensions, duration, LUFS, and `ms see` contact sheets.

- [ ] **Step 2: Run tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_promo.py -q`
  Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/media_studio/promo.py` and wire `ms promo plan|render|qc`**

  Implement beat decomposition, reference-pack injection, Storyboard/Quota Gate (writes `out/promo/storyboard.png` and stops with exit code `0` awaiting approval unless `--auto` is passed), dual-aspect render (`1080x1920` + `1920x1080`) combining TTS/cleaned VO, transition FX + SFX mix, EffectCraft CTA/hook titles, Playwright UI/metric card, AI/fallback B-roll, reframe, and safe-zone captions, plus `qc_promo` (`ms video qc`, `ms see`, optional `media-mcp` OCR/AV-sync check).

- [ ] **Step 4: Update `~/.agents/skills/media-create/SKILL.md` (in `~/.agents` worktree)**

  Add the **Promo Workflow Track** documenting `ms promo plan|render|qc`, `ms audio tts|clean|sfx`, `ms motion html`, `ms video reframe|captions|transition`, `ms gen`, Brand Reference Pack, Storyboard/Quota Gate, and dual `9:16` + `16:9` safe-zone QA.

- [ ] **Step 5: Run full test suite, lint, and 30-second proving ground benchmark (`scr2026-promo-baseline`)**

  - Run: `.venv/bin/python -m pytest -p no:cacheprovider && ~/.local/bin/ruff check src tests && ~/.local/bin/ruff format --check src tests`
  - Scaffold temporary proving ground `~/Projects/scr2026-promo-baseline` (outside git repo) and run `ms promo plan` + `ms promo render --auto` + `ms promo qc` + `ms see` to verify both `1080x1920` (`reel-9x16.mp4`) and `1920x1080` (`master-16x9.mp4`) pass all duration, loudness, resolution, caption legibility, and `CREDITS.md` provenance gates.

- [ ] **Step 6: Sync CodeGraph and commit**

  Run: `codegraph sync && git add src/media_studio/promo.py src/media_studio/cli/promo.py tests/test_promo.py && git commit -m "feat(promo): add dual-aspect promo orchestrator and proving ground QC"`


