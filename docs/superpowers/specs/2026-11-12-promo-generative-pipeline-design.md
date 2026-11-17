# Media-Studio Promotional Video & Hybrid Generative Pipeline — Design

**Date:** 2026-11-12
**Status:** Draft — pending user review
**Parent specs:** `docs/design.md`, `docs/superpowers/specs/2026-11-04-media-create-workflow-design.md`, `docs/superpowers/specs/2026-11-06-media-studio-phase2-design.md`

## 1. Purpose and Scope

`media-studio` currently excels at deterministic rhythm-driven video editing (AMV beat-sync, velocity ramps, LUT grading, EffectCraft 3D titles, MLT/FilmCraft assembly, SVG design kits, and measurable FFprobe/LUFS QC). However, compared to modern AI-native creative suites such as Higgsfield AI, it lacks:

1. A dedicated **promotional video production workflow** optimized for vertical short-form (`9:16` Reels/TikTok/Shorts) alongside landscape masters (`16:9`) from a single source timeline.
2. **Product and subject consistency** across shots (brand kits, cutout layering, reference packs).
3. **Modern promo motion graphics and transitions** (kinetic word-pop typography, SaaS UI/metric cards, callouts, CTA end-cards, whip-pans, zoom-punches, RGB splits, light leaks, and CC0 SFX impact mixes).
4. **Speech, voiceover, and styled captions** (free neural TTS, vocal cleanup, word-level highlighted captions in platform safe zones).
5. **Free-quota generative B-roll and hero shots** behind a pluggable multi-provider adapter with quota tracking and deterministic fallback.
6. Resolution of existing **foundation friction** (`cli.py` monolith, unimplemented `acquire._search_external` stub, and Tier 1-3 gaps in `docs/notes/prompt-to-result-friction.md`).

This specification defines the **Hybrid Asset + AI B-Roll Promotional Pipeline**: deterministic brand accuracy, frame-exact rhythm, and measurable QC combined with pluggable free-tier generative B-roll, EffectCraft motion presets, and frame-stepped HTML/SVG-to-video cards. AMV editing benefits directly from the shared transitions, SFX, and motion primitives as a secondary beneficiary.

---

## 2. Locked Architectural Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| P1 | Dual-aspect target: `9:16` (`1080x1920`) and `16:9` (`1920x1080`) from one timeline | Promotional campaigns require both vertical social cuts and landscape masters without duplicating edit logic. Both satisfy the 1080p short-side floor (`min(w, h) >= 1080`). |
| P2 | Hybrid Asset + AI B-roll primary workflow | Keeps real products, UI recordings, and brand assets deterministic while filling missing B-roll or hero atmospheres with AI generation. |
| P3 | Brand Kit + Reference Pack per project | Acts as the deterministic counterpart to Higgsfield Soul ID: logo kit, cutout PNGs, palette/fonts, and optional subject reference sheets feed both overlays and AI conditioning. |
| P4 | Dual Motion Engine: EffectCraft Promo Pack + Playwright Frame-Stepped HTML/SVG-to-Video; **no Remotion** | EffectCraft handles keyframed 3D/HUD/title motion (308+ effects); Playwright frame-stepped rendering reuses existing `~/.cache/ms-playwright` Chromium for HTML/CSS/UI/metric cards with alpha and zero `node_modules` bloat or Remotion's 3-seat Free License cap. |
| P5 | Single timeline + subject-tracked auto-reframe + safe-zone switching | One shot list drives both `16:9` and `9:16`; vertical crops track the subject and move titles/logos/captions out of Reels/TikTok UI dead zones. |
| P6 | Speech & SFX: `edge-tts` primary + Kokoro/Piper offline fallback + DeepFilterNet/FFmpeg vocal clean + bundled CC0 SFX | Covers both automated TTS voiceover (ID/EN) and user-recorded voice cleanup, paired with whoosh/impact/riser transition SFX and auto-ducking. |
| P7 | Reuse `media-mcp` Whisper for word-level timestamps -> styled word-highlight captions | Avoids duplicating heavy PyTorch/Whisper dependencies while enabling Hormozi/CapCut-style word-highlighted captions. |
| P8 | Pluggable multi-provider `ms gen` with free-quota adapters & Graceful Cascade | Batch 1 ships Google AI Studio/Gemini (existing key pool), HuggingFace Inference/ZeroGPU Spaces, and keyless fallback, plus plugin slots/wizard for fal/Replicate/Kling/Seedance. Exhausted/failed AI falls back to stock + 2.5D parallax/motion cards with QC warning. |
| P9 | Full `.license` sidecars with AI/TTS provenance in the asset ledger | Generated media and TTS files carry `Provider`, `Model`, `Prompt`, `Seed`, and `Generated-At` sidecar fields and enter `assets/index.json` + `CREDITS.md`. |
| P10 | Interactive Storyboard/Quota Gate + `--auto` mode with quota cap | Prevents burning limited daily AI video credits on misaligned briefs while supporting scriptable `--auto --max-gen-calls N` runs. |
| P11 | Foundation cleanup upfront: split `cli.py`, fix Tier 1-3 friction, wire `acquire._search_external` | Clears architectural debt and ensures every promo workflow composes over reliable primitives. |
| P12 | Optional extras in `pyproject.toml` (`[tts]`, `[render-html]`, `[gen]`, `[promo]`) | Preserves lightweight base install and keeps `ms doctor` honest with clear fallback reporting. |

---

## 3. Architecture Overview and Module Map

### 3.1 Modular CLI Decomposition (`src/media_studio/cli/`)

`src/media_studio/cli.py` (currently 4,049 lines) is refactored into a package `src/media_studio/cli/` while keeping `media_studio.cli.build_parser()` and `media_studio.cli.main(argv)` backward-compatible for every existing import and test:

```text
src/media_studio/cli/
  __init__.py        # top-level parser, build_parser(), main(), common error wrapper
  common.py          # shared path/format/output helpers and exit code formatting
  project.py         # ms doctor, ms project init|validate
  assets.py          # ms assets ..., ms library ..., ms acquire ...
  video.py           # ms video ... (including reframe, captions, transitions)
  audio.py           # ms audio tts|clean|sfx|mix
  design.py          # ms design ..., ms image ..., ms fonts ..., ms presets ...
  motion.py          # ms motion ... (including html)
  gen.py             # ms gen image|video|storyboard|quota|providers
  promo.py           # ms promo plan|render|qc
  three_d.py         # ms 3d ...
  triage_see.py      # ms triage ..., ms see ...
```

### 3.2 New and Modified Domain Modules

| Module | Responsibility |
| --- | --- |
| `src/media_studio/cli/` | Modular CLI entrypoints preserving `media_studio.cli:main` |
| `src/media_studio/design.py` | Native foreground cutout layering (`variant.image`), adaptive safe-area geometry, `--deliverable`/`--variant` parity |
| `src/media_studio/video.py` | `render --force` (`render_filmcraft` + `render`), `AudioCue.duration` sequence clamp, transition FX hooks |
| `src/media_studio/effectcraft.py` | Relative `--out` normalization, `.mov` coercion for ProRes RGBA, 5+ promo motion presets (`promo-*`) |
| `src/media_studio/acquire.py` | Replace `_search_external` stub with real dispatch to `search.py` and `library.py` |
| `src/media_studio/html_motion.py` | Playwright frame-stepped HTML/SVG/CSS/WAAPI renderer -> PNG RGBA sequence / ProRes 4444 `.mov` |
| `src/media_studio/reframe.py` | Subject-saliency/tracking crop-pan calculator + `9:16` / `16:9` safe-zone overlay placement |
| `src/media_studio/tts.py` | `edge-tts` + `piper`/`kokoro` + `espeak-ng` speech synthesis, DeepFilterNet/FFmpeg vocal cleanup, sidecar writing |
| `src/media_studio/captions.py` | Word-timestamp loader (`media-mcp` Whisper / JSON/SRT) -> styled word-highlight ASS & Playwright overlays |
| `src/media_studio/sfx.py` | Bundled CC0 promo/AMV SFX & overlay pack registry + beat/cut-aligned auto-mixing |
| `src/media_studio/transitions.py` | FFmpeg filtergraph transitions (`whip_pan`, `zoom_punch`, `rgb_split`, `light_leak`, `flash_cut`, `slide`) |
| `src/media_studio/gen/` | Pluggable generative adapters (`aistudio`, `huggingface`, `keyless`, `plugins`), local quota store, cascade, sidecars |
| `src/media_studio/promo.py` | Hybrid promo planner, storyboard/quota gate, brand reference pack injector, dual-aspect builder, QC hooks |

---

## 4. Phase B-0: Foundation Cleanup and Friction Resolution

Before adding promotional or generative workflows, all open items from `docs/notes/prompt-to-result-friction.md` and the `acquire._search_external` stub are resolved:

1. **`cli.py` Decomposition:** Split `src/media_studio/cli.py` into `src/media_studio/cli/` modules. Existing imports (`from media_studio.cli import main, build_parser`) continue to resolve identically.
2. **Design Cutout Layering & Adaptive Safe Area (`src/media_studio/design.py`, `manifest.py`, `templates/design/*.svg`):**
   - `VariantSpec` adds optional fields: `image: str | None = None` (staged cutout/foreground asset ID), `image_fit: str = "contain"`, `image_position: str = "right"`, `headline_size: int | None = None`, `subhead_size: int | None = None`.
   - `compose()` substitutes `{{foreground_href}}`, `{{safe_x}}`, `{{safe_y}}`, `{{safe_w}}`, `{{safe_h}}`, `{{headline_size}}`, and `{{subhead_size}}` calculated proportionally from `variant.width` and `variant.height` (e.g., 5% horizontal/vertical margin instead of hardcoded 720p `1160x600`). When `variant.image` is omitted, the `<image id="foreground">` node is stripped cleanly.
   - `ms design render` accepts both `--variant` and `--deliverable` as aliases.
3. **`ms video render --force` & `AudioCue` Clamping (`src/media_studio/video.py`, `manifest.py`, `fcproj.py`):**
   - `render_filmcraft()` and `ms video render` accept `--force` to overwrite an existing `.fcproj` when the manifest changed (eliminating manual `rm` around the D46 guard).
   - `AudioCue` gains optional `duration: float | None = None` and `clamp_to_video: bool = True`. Both MLT and FilmCraft compilers clamp the audio bed to `min(cue.duration or video_duration, video_duration)` so long music tracks never append black video tails.
4. **EffectCraft CLI Hygiene (`src/media_studio/effectcraft.py`):**
   - Normalize relative `--out` paths via `output.resolve()` before computing `relative_to()` in reporting.
   - Coerce output extension to `.mov` with a structured note whenever `transparent=True` renders ProRes 4444 RGBA, so `MotionResult.ok` matches the emitted container.
5. **Wire `acquire._search_external` (`src/media_studio/acquire.py`):**
   - Connect `_search_external` to `search.run_search()` across configured keyless and keyed providers (Pixabay, Freesound, Jamendo, Wikimedia, Internet Archive, Met, Openverse), ranking candidates by license policy and tags before falling back to `manual_request_message()`.

---

## 5. Promotional Motion, Transitions, Reframe, and Brand Consistency

### 5.1 Brand Kit and Reference Pack (`brand` + `references` in `project.yaml`)

Equivalent to Higgsfield Soul ID for commercial brand and product consistency:

```yaml
brand:
  palette:
    primary: "#0F172A"
    accent: "#38BDF8"
    ink: "#F8FAFC"
  fonts:
    heading: "font-heading-asset"
    body: "font-body-asset"
  logo: "logo-main"
  references:
    product: ["product-cutout", "product-angle-hero"]
    spokesperson: ["host-front"]      # optional portrait/character sheet
    style_board: ["ref-mood-01"]      # optional visual look reference
  voice:
    engine: "edge-tts"
    voice_id: "id-ID-ArdiNeural"      # or en-US-AndrewMultilingualNeural
    rate: "+5%"
  safe_zones:
    reel_9x16: { top: 220, bottom: 380, left: 60, right: 140 }
```

- **Deterministic injection:** EffectCraft templates, SVG thumbnails/end-cards, and Playwright HTML cards automatically receive `brand.palette`, `brand.fonts`, `brand.logo`, and `brand.references.product` PNGs without per-shot restatement.
- **Generative injection:** `ms gen` calls tied to a project automatically attach `brand.references` as conditioning images (when the provider supports image-to-image / image-to-video / reference input) and append brand color/style descriptors to the prompt.

### 5.2 EffectCraft Promo Pack (`src/media_studio/effectcraft.py`)

Extend `PROMO_MOTION_PRESETS` alongside `AMV_MOTION_PRESETS` (1920x1080 and 1080x1920 aware via `width`/`height` scaling):

| Preset ID | Purpose | Key Primitives |
| --- | --- | --- |
| `promo-hook-slam` | 0-3 s scroll-stopper headline | Scale 1.35 -> 1.0 ease-out + outer glow + subtle backdrop pill |
| `promo-kinetic-callout` | Feature highlight pointer | Accent rule draw-on + title/subhead staggered `fade_in_up` |
| `promo-metric-badge` | KPI / stat highlight | High-contrast pill card + large counter label + unit caption |
| `promo-lower-third-clean` | Speaker / product tag | Slide-in brand accent bar + stacked dual-weight typography |
| `promo-cta-endcard` | Closing call-to-action | Centered logo reveal + CTA pill + URL/handle line |

All presets render cleanly to ProRes 4444 RGBA (`.mov`) and accept `--width`, `--height`, `--fps`, `--duration`, and `--project` brand token overrides.

### 5.3 Playwright Frame-Stepped HTML/SVG-to-Video (`src/media_studio/html_motion.py`)

For complex UI demos, animated SaaS cards, device mockups, dynamic metric counters, and kinetic word-highlight cards without adding Remotion:

- **Runtime:** Uses Python `playwright` (`[render-html]` optional extra) pointed at the already-present headless Chromium under `~/.cache/ms-playwright/`. Zero `node_modules` or React bundler required.
- **Frame-stepping contract:** Every template HTML page exposes `window.__msSeek(timeSec, frameIndex, totalFrames, params)` (or drives CSS animations/WAAPI via `document.getAnimations().forEach(a => a.currentTime = timeSec * 1000)`).
- **Capture loop:**
  1. Launch headless page at exact `(width, height)` with `device_scale_factor=1` and `omit_background=True` for transparent alpha overlays.
  2. Step `frame = 0 .. total_frames - 1`, call `__msSeek`, await `requestAnimationFrame` + fonts ready, screenshot PNG (`RGBA`) into `/tmp/opencode/html-motion-<pid>/frame_%06d.png`.
  3. Encode PNG sequence via FFmpeg (`prores_ks -profile:v 4444 -pix_fmt yuva444p10le` for `.mov` alpha overlays, or `libx264` for opaque cards).
- **Built-in HTML/SVG templates (`templates/motion-html/`):**
  - `saas-feature-card.html`: animated UI card + floating cursor click + highlight ring.
  - `device-mockup.html`: phone/laptop bezel framing a staged product screenshot/screen-recording crop with gentle 3D perspective tilt.
  - `metric-counter.html`: easing numerical counter + sparkline/progress bar using brand tokens.
  - `kinetic-captions.html`: word-by-word pop/highlight overlay rendered from `captions.json`.
- **CLI:** `ms motion html --template <name|path> --params <json> --out <out.mov> --width 1080 --height 1920 --fps 30 --duration 3.0 [--transparent]`

### 5.4 Promo and AMV Transitions (`src/media_studio/transitions.py`)

Pure FFmpeg filtergraph transition engine operating between adjacent clips `(clip_a, clip_b)` or as beat-hit inserts, reusable in `edl.py` and `promo.py`:

- Transitions: `whip_pan_left`, `whip_pan_right`, `zoom_punch_in`, `zoom_punch_out`, `rgb_split_hit`, `flash_white`, `light_leak_warm`, `slide_up`.
- Each transition specifies `duration_ms` (default `250` ms, frame-quantized), optional paired SFX tag (`whoosh`, `impact`, `riser`), and deterministic pixel/codec normalization (`yuv420p`, matched resolution/fps).

### 5.5 Subject-Tracked Auto-Reframe (`src/media_studio/reframe.py`)

Converts `16:9` source timelines or clips into `9:16` (`1080x1920`) vertical deliverables cleanly:

1. **Subject Tracking Pass:** Samples frames (via FFmpeg / OpenCV or optional SAM 2 bbox if mask prompt provided) to compute per-window focal center `(cx, cy)` using motion energy + face/edge saliency, smoothed with an exponential/Kalman window so the virtual camera pans smoothly without jitter.
2. **Crop-Pan Execution:** Generates time-varying FFmpeg `crop=w=iw*(9/16)...:x='...'` keyframes (or fallback letterbox+blurred-background mode `--mode blur-pad` for wide UI screencasts that cannot be cropped without losing text).
3. **Safe-Zone Layout Switching:** Automatically shifts titles, logos, and captions inside `9:16` safe margins (`top >= 220px`, `bottom >= 380px`, `right >= 140px` at 1080x1920) so platform UI buttons never cover hooks or CTAs.
4. **CLI:** `ms video reframe <source> --aspect 9:16 --mode auto|track|blur-pad --out <out.mp4>`

---

## 6. Speech, Audio Cleanup, SFX Pack, and Word-Highlight Captions

### 6.1 TTS and Vocal Cleanup (`src/media_studio/tts.py`, `src/media_studio/audio.py`)

Supports both synthetic narration and user-recorded voiceovers:

1. **`ms audio tts` Engine Ladder:**
   - **Primary (`edge-tts`):** Free Microsoft neural voices (`id-ID-ArdiNeural`, `id-ID-GadisNeural`, `en-US-AndrewMultilingualNeural`, etc.) producing MP3/WAV + exact word-boundary timing metadata (`WordBoundary` events -> `words.json` directly, avoiding transcription for TTS).
   - **Offline Fallback (`piper` / `kokoro`):** Local ONNX neural TTS when network is unavailable or offline mode is selected (`--engine kokoro|piper`).
   - **Last-Resort Fallback (`espeak-ng`):** Always available on system `PATH` for CI/offline smoke tests when neural engines are absent.
   - Outputs WAV normalized to `-16 LUFS` (dialogue standard before final mix ducking) plus a `.license` sidecar (`Provider`, `Voice`, `Prompt` script text) and optional `words.json`.
2. **`ms audio clean` (User Voiceover Cleanup):**
   - Uses `DeepFilterNet` when installed, or falls back deterministically to a tuned FFmpeg broadcast voice chain (`highpass=f=85`, `afftdn=nf=-25`, `acompressor`, `equalizer` presence boost, two-pass `loudnorm=-16 LUFS`).
   - Updates `tools.py` `ms doctor` checks so missing external DeepFilterNet/UVR venvs report as `optional` (not failing core checks) while showing FFmpeg fallback availability.

### 6.2 Bundled CC0 SFX & Transition Audio Mix (`src/media_studio/sfx.py`)

Addresses `docs/notes/amv-next-steps.md` item #4 for both promotional videos and AMVs:

- Ships a small deterministic CC0 starter SFX pack under `src/media_studio/data/sfx/` (`whoosh-soft.wav`, `whoosh-fast.wav`, `impact-sub.wav`, `impact-hit.wav`, `riser-build.wav`, `ui-pop.wav`) plus catalog integration via `ms library search --type audio`.
- `mix_transition_sfx(timeline_events, base_audio, out_wav)` places SFX cues at transition timestamps and drop downbeats, avoiding clipping via `amix` + limiter (`alimiter=limit=-1.5dB`) and preserving voiceover clarity.
- CLI: `ms audio sfx list`, `ms audio sfx mix <audio> --events <edl_or_cues.json> --out <mixed.wav>`.

### 6.3 Word-Highlight Captions (`src/media_studio/captions.py`)

Reuses `media-mcp`'s existing Whisper pipeline (`~/.clone/Github/SuperBypassUdinnn/media-mcp/transcribe/.venv`) rather than installing a second Whisper copy in `media-studio`:

1. **Word Timestamp Acquisition:**
   - Accepts: (a) `words.json` generated directly by `ms audio tts`, (b) `media-mcp` Whisper JSON output via `extract_word_timestamps(media_path)` invoking `media-mcp`'s transcribe script/cache, or (c) user-supplied SRT/VTT/JSON.
2. **Caption Styles (`promo-pop`, `hormozi-bold`, `clean-saas`, `amv-lyric`):**
   - Groups words into readable 2-4 word chunks (max ~24 chars/line) respecting punctuation pauses.
   - Renders either fast `.ass` subtitle burn-in with active-word color highlight (`accent` brand color) positioned inside `9:16` or `16:9` safe zones, OR `html_motion` `kinetic-captions.html` transparent `.mov` overlay for drop-shadow/pill/scale-pop typography.
3. **CLI:** `ms video captions <source> --words <words.json|auto> --style promo-pop --aspect 9:16 --out <captioned.mp4>`

---

## 7. Pluggable Free-Quota Generative Layer (`src/media_studio/gen/`)

### 7.1 Provider Architecture

`src/media_studio/gen/base.py` defines the request/result contract:

```python
@dataclass(frozen=True)
class GenRequest:
    kind: Literal["image", "video", "storyboard"]
    prompt: str
    negative_prompt: str = ""
    width: int = 1080
    height: int = 1920
    duration: float = 4.0
    fps: int = 24
    aspect_ratio: str = "9:16"
    references: tuple[Path, ...] = ()   # Brand Kit / product / spokesperson refs
    seed: int | None = None
    provider: str | None = None         # explicit override or auto-cascade

@dataclass(frozen=True)
class GenResult:
    ok: bool
    provider: str
    model: str
    asset_path: Path | None
    sidecar_path: Path | None
    fallback_used: bool
    fallback_reason: str | None
    quota_remaining: int | None
    metadata: dict[str, Any]
```

### 7.2 Batch 1 Free-Quota Providers and Plugin Slots

| Provider ID | Kind | Auth / Free Quota Mechanism | Notes |
| --- | --- | --- | --- |
| `aistudio` | `image`, `storyboard` (+ `video` when enabled on key) | Existing `GEMINI_API_KEY` / Google AI Studio key pool | Uses Gemini / Imagen image generation; attaches `references` for style/subject conditioning. |
| `huggingface` | `image`, `video` (img2vid / txt2vid) | `HF_TOKEN` (optional free tier) + ZeroGPU Spaces | Routes to free HuggingFace Inference / Gradio endpoints with queue/timeout guards. |
| `keyless` | `image` | Zero-auth public free endpoints (e.g. Pollinations image endpoint) | Emergency/zero-key fallback for draft B-roll stills animated via 2.5D parallax. |
| `fal`, `replicate`, `kling`, `seedance` | `video`, `image` | Plugin adapter slots + `scripts/keys-wizard.sh` | Activated automatically when free-trial API keys exist in `~/.config/opencode/secrets.env`; reported as `skipped (no key)` otherwise. |

### 7.3 Local Quota Ledger and Graceful Cascade (`src/media_studio/gen/quota.py`, `pipeline.py`)

- **Quota Store:** `~/.local/state/media-studio/gen-quota.json` tracks per-provider daily/monthly counts, per-run call caps (`--max-gen-calls`, default `6`), and 429 cooldown windows.
- **Graceful Cascade:**
  1. Try configured providers matching `request.kind` in priority order (`explicit` -> `aistudio` -> `huggingface` -> keyed trial slots -> `keyless`).
  2. If image-only succeeds while `kind == "video"`, animate the generated still via 2.5D parallax / Ken Burns (`parallax.py` or FFmpeg `zoompan`) and record `fallback_used=True` (`motion-from-still`).
  3. If all generative providers fail or hit quota limits, fall back to deterministic B-roll (`acquire.py` stock search + parallax/motion card) and record a non-fatal warning in the QC report.
- **Provenance & Ledger Integration:** Every generated asset lands under `<project>/assets/{design,video}/gen-<id>.{png,mp4}` with a full REUSE `.license` sidecar (`SPDX-License-Identifier`, `Provider`, `Model`, `Prompt`, `Seed`, `References`, `Generated-At`, `SHA256`) and triggers `write_index_and_credits()`, tagged `[AI Generated: <provider>/<model>]`.

---

## 8. Promotional Video Orchestrator (`src/media_studio/promo.py`) and `media-create`

### 8.1 Hybrid Promo Pipeline (`ms promo plan|render|qc`)

1. **`ms promo plan <project> --script <script.txt|prompt> --aspect 9:16,16:9`:**
   - Reads `brand`, `brand.references`, and staged `assets` from `project.yaml`.
   - Breaks the script/prompt into typed beats:
     - `hook` (0-3 s: bold headline slam + product/hero shot + impact SFX)
     - `problem` / `context` (user footage or AI B-roll + kinetic callout)
     - `solution` / `demo` (screen recording / `device-mockup.html` / `saas-feature-card.html` + cutout product)
     - `proof` (`metric-counter.html` + testimonial/captions)
     - `cta` (`promo-cta-endcard` + logo reveal + URL)
   - Outputs `promo-plan.json` (shot types, missing B-roll flags, estimated `ms gen` calls, TTS segment timings, SFX cues) and an optional **storyboard preview sheet** (`out/promo/storyboard.png`) before spending AI video quota.
2. **Quota & Storyboard Gate:**
   - Default interactive mode prints the planned `ms gen` calls and waits for confirmation before invoking generative video/image APIs.
   - `--auto --max-gen-calls N` executes continuously within the quota cap.
3. **`ms promo render <project> --plan promo-plan.json --deliverables reel-9x16,master-16x9 [--force]`:**
   - Generates TTS/cleans user VO -> computes word timestamps -> places cuts + transitions + SFX + music ducking -> applies subject-tracked auto-reframe + safe-zone captions/overlays -> renders both `1080x1920` (`9:16`) and `1920x1080` (`16:9`) deliverables (`-14.0 LUFS`, `>= 30 fps`).
4. **Integrated QC (`ms promo qc` + `media-mcp` Escalation):**
   - Runs `ms video qc` + `ms see` contact sheets on both deliverables.
   - Optionally runs `media-mcp` verification (`media_avsync` for transition/beat sync, `media_ocr` for caption/CTA text legibility inside safe zones, `media_motion` for visual pacing) when `media-mcp` is available.

### 8.2 Updates to `~/.agents/skills/media-create/SKILL.md`

The `media-create` skill gains a dedicated **Promo Workflow Track** alongside the existing AMV track:
- Stage 1 recognizes promotional briefs and scaffolds `brand.references` + dual deliverables (`1080x1920` and `1920x1080`).
- Stage 3 enforces the **Storyboard & Quota Gate** before `ms gen` calls unless `--auto` is requested.
- Stage 4 routes speech through `ms audio tts` / `ms audio clean`, missing B-roll through `ms gen`, and transitions through `ms audio sfx`.
- Stage 7 pairs `ms see` with `media-mcp` OCR/AV-sync spot checks on captions and CTAs.

---

## 9. Complete New CLI Surface and Manifest Additions

### 9.1 New Commands

```text
# Audio, Speech & SFX
ms audio tts     <project> --text <str|file> [--voice ID] [--engine auto|edge|kokoro|piper|espeak]
                           [--out <wav>] [--words-out <json>]
ms audio clean   <input> --out <clean.wav> [--engine auto|deepfilter|ffmpeg] [--lufs -16.0]
ms audio sfx     list [--tag TAG]
ms audio sfx     mix <audio> --events <json> --out <mixed.wav>

# Video Reframe, Captions & Transitions
ms video reframe     <source> --aspect 9:16|16:9|1:1 [--mode auto|track|blur-pad] --out <out.mp4>
ms video captions    <source> --words <words.json|auto> [--style promo-pop|hormozi-bold|clean-saas]
                              [--aspect 9:16|16:9] --out <out.mp4>
ms video transition  <clip_a> <clip_b> --kind <name> [--duration-ms 250] --out <out.mp4>

# HTML/SVG Motion
ms motion html   --template <name|path> [--params <json>] [--project <project>]
                 --out <out.mov> [--width 1080] [--height 1920] [--fps 30] [--duration 3.0] [--transparent]

# Free-Quota Generative Layer
ms gen image      --prompt <str> [--project <p>] [--refs <paths...>] [--aspect 9:16] [--provider auto|...] --out <png>
ms gen video      --prompt <str> [--project <p>] [--image <still>] [--duration 4.0] [--provider auto|...] --out <mp4>
ms gen storyboard --script <file> [--project <p>] --out <dir>
ms gen quota      [--reset PROVIDER]
ms gen providers

# Promotional Video Orchestrator
ms promo plan    <project> --script <file|text> [--aspect 9:16,16:9] [--out promo-plan.json]
ms promo render  <project> --plan <promo-plan.json> [--auto] [--max-gen-calls 6] [--force]
ms promo qc      <project> [--deliverables reel-9x16,master-16x9]
```

### 9.2 `project.yaml` Backward-Compatible Additions

All existing `project.yaml` files remain 100% valid. New optional keys:
- `brand.references`: `{ product: [...], spokesperson: [...], style_board: [...] }`
- `brand.voice`: `{ engine, voice_id, rate }`
- `brand.safe_zones`: `{ reel_9x16: { top, bottom, left, right } }`
- `design.variants[].image`, `image_fit`, `image_position`, `headline_size`, `subhead_size`
- `video.music.duration`, `video.music.clamp_to_video`

---

## 10. Optional Extras and `ms doctor` Integration

`pyproject.toml` adds clean optional extras without bloating core installs:

```toml
tts = ["edge-tts>=7.0"]
render-html = ["playwright>=1.50"]
gen = ["httpx>=0.28", "google-genai>=1.0"]
promo = ["media-studio[tts,render-html,gen,rhythm]"]
```

- `ms doctor --deep` reports `edge-tts`, `playwright` (checking `~/.cache/ms-playwright/`), `httpx` / `google-genai`, and `media-mcp` availability as **optional** checks with exact fallback explanations when absent.
- Missing external DeepFilterNet/UVR venvs are reclassified from alarming `FAIL` warnings to `optional` (`ffmpeg vocal cleanup fallback active`).
- `scripts/keys-wizard.sh` adds optional registration prompts for `HF_TOKEN`, `FAL_KEY`, `REPLICATE_API_TOKEN`, `KLING_API_KEY`, and `SEEDANCE_API_KEY` alongside `GEMINI_API_KEY`.

---

## 11. Quality Control and Acceptance Benchmark

### 11.1 Automated Verification Layers

1. **Unit & Mocked Provider Tests:** Deterministic tests for `cli/` parity, `design` cutout layering & adaptive safe areas, `AudioCue` duration clamping, `reframe` smooth crop math, `captions` chunking/ASS generation, `tts` fallback ladder, `sfx` mixing, `gen` quota/cascade/sidecars, and `promo` planner.
2. **Real-Binary Local E2E Tests (`@pytest.mark.slow`):**
   - `html_motion`: renders `saas-feature-card.html` with Playwright + Chromium into a transparent `.mov` and verifies alpha + exact frame count via `ffprobe`.
   - `transitions` + `sfx`: joins two synthetic clips with `whip_pan_left` + `whoosh-fast.wav`, verifies no black frames and target loudness.
   - `reframe` + `captions`: crops a `1920x1080` moving subject clip to `1080x1920` and burns word-highlight captions inside the `9:16` safe zone.

### 11.2 Proving Ground Benchmark: 30-Second Dual-Aspect Promo (`scr2026-promo-baseline`)

Phase B + C is complete when a real 30-second promotional video project produces passing deliverables in both formats:
- **`reel-9x16` (`1080x1920`, `30 fps`, `30.0 s ± 0.2 s`, `-14.0 LUFS ± 1.0 LU`, `<= -1.5 dBTP`)**
- **`master-16x9` (`1920x1080`, `30 fps`, `30.0 s ± 0.2 s`, `-14.0 LUFS ± 1.0 LU`, `<= -1.5 dBTP`)**

The deliverables must combine: staged brand logo + cutout product asset, TTS/cleaned voiceover + auto-ducked music + transition SFX, at least one Playwright HTML UI/metric card, at least one EffectCraft promo title/CTA end-card, at least one `ms gen` (or Graceful Cascade fallback) B-roll shot logged in `CREDITS.md`, and word-highlighted captions positioned cleanly inside `9:16` safe zones verified via `ms see`.


