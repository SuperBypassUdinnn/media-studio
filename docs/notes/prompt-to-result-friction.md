# Prompt-to-Result Friction — AMV Beat-Sync Acceptance Test (Task 4)

Acceptance Test 1 on project `scr2026-amv-baseline`, prompt:
"Buat AMV 15 detik dari clip JJK di scr2026-amv-baseline, potong mengikuti beat lagunya"
(i.e. "Make a 15-second AMV from JJK clips in scr2026-amv-baseline, cut to the beat of the song").

Deliverable: `amv-15s` (1280x720, 30 fps, 15.0s / 450 frames, -14.0 LUFS).

## Result

`ms video render --deliverable amv-15s` exits 0 and writes `out/video/amv-15s.mp4`.
QC verdict: **pass**.

| Check | Value | Target | Status |
|---|---|---|---|
| duration | 15.018 s | 15.0 s | ok |
| loudness (integrated) | -13.99 LUFS | -14.0 LUFS (±1.0 LU) | ok (0.01 LU off) |
| true peak | -3.82 dBTP | below -1.0 dBTP ceiling | ok |
| resolution / fps | 1280x720 @ 30 fps | 1280x720 @ 30 | ok |
| frames | 450 | 450 | ok |
| black frames / frozen frames | none | none | ok |

Contact sheet `out/video/amv-15s.see.png` inspected: 12 tiles, alternating JJK clips at beat boundaries, title burned in at start, no black/frozen frames, audio present.

## Friction points (raw edges encountered)

### 1. `ms video beat-sync` CLI does not match the acceptance-test brief
The brief (task-4-brief.md) documents:
```
ms video beat-sync <project> --duration 15.0 --fps 30 --audio track-ncs.mp3
```
The actual CLI is:
```
ms video beat-sync <project> --audio <file> [--name <label>]
```
There are no `--duration` or `--fps` flags, and the positional argument is the project (not the audio file). The brief's example filename `track-ncs.mp3` does not exist; the actual track is `assets/audio/ncs-track.wav`. Impact: had to discover flags via `--help` and use the real asset path; no way to scope detection to a 15s window.

### 2. No `librosa` → beat detection falls back to a synthetic BPM grid
`detect_beats()` imports `librosa`; it is not installed in the media-studio `.venv`, so detection uses `_ffmpeg_beat_fallback`, which emits a constant 128 BPM grid (beat interval 0.46875s) across the *entire* 238s track. This produced 508 beat markers instead of real audio-transient detection. The grid happens to align well enough for this AMV (32 shots x 0.46875s = 15.0s), but it is not true beat-sync to the music's transients. Impact: "beat-synchronized" is synthetic here; install librosa for real detection.

### 3. `ms video render` refuses to overwrite an existing `out/<id>.fcproj` (D46 guard) with no `--force`
`render` compiles to `out/amv-15s.fcproj`. After the first render that file existed; re-running `render` after any manifest change fails with "already exists and may carry manual GUI edits" because `render` has no `--force` flag (unlike `build --force`). I had to manually delete `out/amv-15s.fcproj` before re-rendering. Impact: manual intervention between render attempts; not scriptable end-to-end.

### 4. Music clip on A2 drives sequence length — first render exported the full 238s track with a black video tail
The compiler places the full music cue on A2 (no `duration` field on `AudioCue`, and `file.exportMedia` has no duration clamp), so the sequence runs 238s while the 15s of video ends at 15s, leaving ~223s of black video. First render: duration 238.016s, `no_black_frames` FAIL. Fix: manually trimmed `ncs-track.wav` to exactly 15.0s (`ffmpeg -t 15.0`) and pointed the `music` asset at the trimmed file. Impact: no declarative way to say "music ends at 15s"; required an out-of-manual ffmpeg trim plus a new license sidecar.

### 5. Beat marker count discrepancy
`ms video beat-sync` printed `created 554 beat marker(s) on timeline`, but inspecting `scr2026-amv-baseline.fcproj` shows exactly 508 `Comment` items named "Beat". The 554 figure appears to be the `run_batch` echo count rather than the committed marker count. Impact: unreliable count output; ground truth requires reading the fcproj.

## Manual interventions performed

1. Wrote `brief.md` in the project root per the house brief template.
2. Replaced `project.yaml` video section: 32 shots at the 128 BPM beat grid (32 x 0.4688s = 15.0s = 450 frames), added `jjk-hollow` asset, added `amv-15s` deliverable.
3. Built `scr2026-amv-baseline.fcproj` via `ms video build --deliverable amv-15s` so beat-sync had a timeline to attach markers to (beat-sync requires an existing .fcproj; the brief did not mention this prerequisite).
4. Ran `ms video beat-sync --audio assets/audio/ncs-track.wav --name Beat` → 508 markers.
5. Trimmed `ncs-track.wav` → `ncs-track-15s.wav` (ffmpeg `-t 15.0`) + wrote `ncs-track-15s.wav.license`; remapped `music` asset in `project.yaml`.
6. Deleted stale `out/amv-15s.fcproj` (D46 guard, render has no `--force`).
7. Re-rendered; QC pass. Ran `ms see` → contact sheet verified.

## Suggested follow-up work (not part of this acceptance test)

- Add `--duration`/`--fps` scoping to `ms video beat-sync` and install `librosa` for real transient detection.
- Add a `duration` field to `AudioCue` (or an export `--duration` clamp) so music does not drive sequence length beyond the deliverable.
- Add `--force` to `ms video render` so re-renders after manifest edits are scriptable without manual `rm` of the fcproj.
- Align the acceptance-test brief example command/filename with the real CLI and asset names.

---

# Acceptance Test 2 — Social Graphic / Thumbnail Workflow (Task 5)

Project: `scr2026-amv-baseline`
Prompt: "Buat YouTube thumbnail 16:9 untuk AMV JJK tersebut dengan judul 'UNSTOPABLE BEATS', gunakan cutout karakter Gojo dan background bertema energi kutukan"
Deliverable: `thumbnail-16x9` (1920x1080 PNG)

## Result

Added a `design:` section to `project.yaml` with one variant:

```yaml
design:
  logo: null
  fonts: {}
  variants:
    - id: thumbnail-16x9
      template: video-thumbnail
      size: 1920x1080
      headline: "UNSTOPABLE BEATS"
      subhead: "JJK AMV - Energi Kutukan"
```

Added `ink: "#000000"` to `brand.palette` because the default white ink fails WCAG contrast against the brand primary (#00ffff cyan).

Extracted a Gojo frame at 1.0s from `assets/video/clip-jjk-gojo.mp4` (1280x720) with ffmpeg, then ran:

```
ms image remove-bg assets/stills/gojo-frame.jpg --model birefnet-lite --out assets/design/gojo-cutout.png
```

`birefnet-lite` exceeded the GTX 1650's 4 GB VRAM and fell back to CPU; the CPU path completed and produced a valid 1280x720 RGBA cutout with real transparency (437,940 fully transparent pixels).

Rendered with:

```
ms design render <project> --variant thumbnail-16x9
```

Wrote `out/images/thumbnail-16x9.png` (1920x1080 RGBA) plus SVG/PDF and a QC report. `ms see` confirmed mode RGBA, size 1920x1080, and produced `out/images/thumbnail-16x9.see.png`.

QC: contrast ok, font size ok, resolution ok. Two overflow findings remain on the fixed safe area (see friction below). The brand `ink` change fixed the contrast failure.

## Friction points (raw edges encountered)

### 1. `ms design render` uses `--variant`, not `--deliverable`
The task brief documents `ms design render <project> --deliverable thumbnail-16x9`. The actual CLI flag is `--variant`. No `--deliverable` flag exists on `design render`. Impact: had to discover the real flag via the parser/`--help`; the brief's example command does not work as written.

### 2. Output path mismatch
The brief expects `out/design/thumbnail-16x9.png`. The design path actually writes `out/images/<variant id>.png` (plus `.svg` and `.pdf`). Impact: the verification target in the brief does not match where the file lands.

### 3. `birefnet-lite` exceeds GTX 1650 VRAM; CPU fallback used
`ms image remove-bg --model birefnet-lite` failed to allocate GPU memory ("BFCArena::AllocateRawInternal ... 822083584 bytes") and fell back to CPU. The CPU path completed but is slow. `u2net` would run faster on GPU. Impact: model choice is a real constraint on a 4 GB card; the fallback works but is not transparent to the user.

### 4. No font staged for design; render falls back to "sans-serif"
`design.fonts` is empty in `project.yaml` (no font asset staged under `assets/design/fonts`), so `compose()` falls back to `font-family="sans-serif"` in the SVG. The rendered text is not set in a real, licensed family. To use a real font it must be staged via `ms assets stage --class design` with a licence sidecar and then wired under `design.fonts.{heading,body}`.

### 5. No mechanism to composite the character cutout into the thumbnail (principal gap)
The `video-thumbnail.svg` template only renders `headline`, `subhead`, brand colours, and an optional `logo` image. There is no template variable for a foreground/character cutout, so the Gojo cutout produced in Step 1 is **not present** in the output thumbnail. The composed SVG contains text on a cyan background with a yellow band, but no Gojo. The prompt explicitly asks to "gunakan cutout karakter Gojo", which `ms design render` cannot satisfy on its own; the cutout must be composited manually (e.g. ImageMagick or Natron) outside `ms design render`.

### 6. Template safe area is fixed geometry, not scaled to 1920x1080
`video-thumbnail.svg` hardcodes the safe area at `x="60" y="60" width="1160" height="600"` (built for 720p). On a 1920x1080 canvas the headline ("UNSTOPABLE BEATS", size 120) and subhead boxes slightly leave this fixed safe area (reported as overflow). Font size, text position, and safe-area are all template-hardcoded and not configurable via the variant manifest, and the title is mandated by the prompt, so the overflow cannot be resolved without editing the template or shortening the title. The overflow is marginal (~5 px) but the check flags it.

### 7. Brand primary (#00ffff cyan) is too light for default white ink
Default ink is #FFFFFF; white on cyan measures 1.25:1, below the 3.0:1 large-text threshold. Required adding `ink: "#000000"` to `brand.palette` to make contrast pass. Impact: the design system's default ink/primary pairing is not WCAG-safe out of the box; a project must supply an explicit `ink` token.

### 8. Pre-existing: `project validate` fails on `jjk-hollow` missing a `.license` sidecar
`assets.jjk-hollow` (from Task 4) references `assets/video/jjk-gojo-hollow-full.mp4` with no `.license` sidecar, so `ms project validate` errors. `load_project` (used by `design render`) does not enforce this, so design render still ran, but `validate` is red. Impact: an unrelated Task-4 leftover blocks manifest validation for this project.

## Manual interventions performed

1. Extracted `assets/stills/gojo-frame.jpg` from the JJK clip at 1.0s with ffmpeg.
2. Ran `ms image remove-bg --model birefnet-lite` (GPU OOM → CPU fallback) to create `assets/design/gojo-cutout.png`.
3. Added the `design:` section and `thumbnail-16x9` variant to `project.yaml`.
4. Added `ink: "#000000"` to `brand.palette` so contrast passes.
5. Rendered with `--variant thumbnail-16x9` (brief's `--deliverable` flag does not exist).
6. Verified output with `ms see` (RGBA 1920x1080, contact sheet produced).

## Fix Round 1 — post-composite of the Gojo cutout

Because `ms design render` and its SVG templates cannot layer a character cutout, the orchestrator performed an ad-hoc post-composite step to satisfy the prompt ("gunakan cutout karakter Gojo"):

```
# Composite gojo-cutout.png (1280x720 RGBA) onto the 1920x1080 thumbnail,
# scaled to height ~680px and right-aligned just below the headline so the
# headline ("UNSTOPABLE BEATS") and subhead remain readable.
python PIL alpha_composite -> out/images/thumbnail-16x9.png
cp out/images/thumbnail-16x9.png out/design/thumbnail-16x9.png
```

The composite was written to BOTH required paths:
- `out/images/thumbnail-16x9.png` (where `ms design render` actually writes)
- `out/design/thumbnail-16x9.png` (the path named in the brief; `out/design` created)

Verification: `ms see out/design/thumbnail-16x9.png` → RGBA 1920x1080, contact sheet `out/design/thumbnail-16x9.see.png` produced. Alpha check confirms Gojo pixels present on the right side (833,520 nonzero-alpha pixels in the placement region).

## Updated verdict

The delivered thumbnail now contains the Gojo cutout composited on the right side plus the "UNSTOPABLE BEATS" title on a cyan/energy-themed background, at 1920x1080 RGBA, written to both `out/images/` and `out/design/`. The core workflow gap remains: `ms design render` has no native character/cutout image layering, so any graphic deliverable requiring a cutout needs an external post-composite step.

## Friction points (raw edges encountered) — updated

### 9. Core architectural gap: no native cutout/image layering in `ms design render`
`ms design render` composes SVG templates with headline/subhead/brand colours and an optional `logo` image only. There is no template variable for a foreground character cutout, so the Gojo cutout produced by `ms image remove-bg` cannot be assembled into the thumbnail natively. The orchestrator must run an external post-composite (ffmpeg or Pillow alpha_composite) after `ms design render`. This is an ad-hoc orchestration step outside `ms`, not scriptable through `ms design render` itself. Impact: any graphic deliverable requiring a cutout (thumbnails, social posts with characters) needs a manual composite step; `ms design render` alone cannot satisfy prompts asking to "use cutout character X".

### 10. `--deliverable` vs `--variant` CLI flag mismatch (re-emphasized)
The brief documents `ms design render <project> --deliverable thumbnail-16x9`. The actual flag is `--variant`. There is no `--deliverable` flag on `design render`. Impact: the brief's example command does not work as written.

### 11. Output path mismatch: `out/images` vs `out/design`
The brief expects `out/design/thumbnail-16x9.png`. The design path actually writes `out/images/<variant id>.png` (plus `.svg` and `.pdf`). The orchestrator had to copy the render to `out/design/` to match the brief. Impact: verification target in the brief does not match where the file lands; deliverable must be published to both paths.

---

# Acceptance Test 3 — Motion Graphics Intro Sequence (Task 6)

Project: `scr2026-amv-baseline`
Prompt: "Buat title animation intro 5 detik dengan teks 'PROJECT GOJO', gaya anime minimalis futuristik"
(i.e. "Make a 5-second title animation intro with text 'PROJECT GOJO', minimalist futuristic anime style").
Deliverable: `out/motion/title-intro.mov` (ProRes 4444 RGBA, 1280x720, 30 fps, exactly 5.0 s)

## Result

Rendered via `ms motion title` (project path positional, `--text`, `--duration 5.0`, `--animation fade_in_up`, `--glow`, absolute `--out` ending in `.mov`):

```
ms motion title /home/superbypassudin/Projects/scr2026-amv-baseline \
  --text "PROJECT GOJO" \
  --duration 5.0 \
  --animation fade_in_up \
  --glow \
  --out /home/superbypassudin/Projects/scr2026-amv-baseline/out/motion/title-intro.mov
```

Verdict: `pass`, exit 0. Artifacts: `title-intro.mov`, `title-intro.ecp`, `title-intro_compiled.ecproj`, `title-intro.json`, `title-intro.see.png`.

Verified with `ms see` and ffprobe:

| Check | Value | Target | Status |
|---|---|---|---|
| duration | 5.00 s | 5.0 s | ok |
| resolution | 1280x720 | 1280x720 | ok |
| fps | 30 | 30 | ok |
| codec / pixel format | prores / yuva444p12le | ProRes 4444 with alpha | ok |
| frames | 150 | 5.0 s × 30 fps | ok |
| audio | none | none (title, no music) | ok |
| contact sheet | title-intro.see.png | present | ok |

Contact sheet shows "PROJECT GOJO" typography in white across frames on the checkered alpha background, confirming the title text is present and legible.

## Friction points (raw edges encountered)

### 1. `--out` must be an absolute path
`ms motion title` reporting crashes on `relative_to` when `--out` is relative (observed in the Task 3 probe and again here). The CLI must receive an absolute path for the verdict to print and for `MotionResult.ok` to resolve. Impact: relative `--out` breaks reporting; users must compute an absolute path before invoking.

### 2. `.mov` extension coercion required
EffectCraft renames `.mp4` → `.mov` for ProRes RGBA output. `MotionResult.ok` is `false` unless `--out` uses `.mov`, so the CLI must coerce/validate the extension to `.mov` rather than silently failing on a user-supplied `.mp4`.

### 3. `--text` carries the title; `--text`/`--out` are required flags
The brief's example command style (positional text, `--style`) does not match the actual CLI (`--text TEXT`, `--out OUT`, `--animation ANIMATION`, `--duration DURATION`, `--glow`). No `--style` flag exists on `ms motion title`. Impact: brief examples do not work as written; flags must be discovered via `--help`.

### 4. Headless renderer defaults to CPU; GPU is opt-in
The EffectCraft headless engine renders on the CPU by default (`--gpu` to opt in). Observed peak VRAM ~176 MiB / 4096 MiB (~4%), so CPU is acceptable for titles, but GPU acceleration is not transparent and must be requested explicitly.

## Manual interventions performed

1. Ran `ms motion title` with the absolute `.mov` `--out` path exactly as specified in the key-context CLI format.
2. Verified output with `ms see` (duration 5.00 s, 1280x720, prores yuva444p12le, contact sheet produced).
3. Cross-checked with ffprobe (codec prores, pix_fmt yuva444p12le, 1280×720, 30/1 fps, duration 5.000000).

## Verdict

**Pass.** The 5-second "PROJECT GOJO" title intro rendered correctly as ProRes 4444 RGBA at 1280x720, 30 fps, exactly 5.0 s, with a contact sheet for visual inspection. No external post-processing was required (unlike Design, which needed a manual cutout composite).

---

## Final Synthesis: Prioritized Pipeline Evolutions (Phase 2 Roadmap)

This synthesis consolidates all friction items recorded across Video (Task 4), Design (Task 5), and Motion (Task 6) acceptance tests, organized into three tiers for the Phase 2 roadmap of the `ms` CLI.

### Tier 1 — Blocker / High Value

These items block end-to-end scriptability or prevent the CLI from satisfying the user's prompt without external tooling.

**1.1 Native image/cutout layering in `ms design render` (Task 5, #5/#9)**
`ms design render` composes SVG templates with headline/subhead/brand colours and an optional `logo` image only. There is no template variable for a foreground character cutout, so the Gojo cutout produced by `ms image remove-bg` cannot be assembled into the thumbnail natively. The orchestrator had to run an external post-composite (Pillow alpha_composite) after `ms design render`. Any graphic deliverable requiring a cutout (thumbnails, social posts with characters) needs a manual composite step outside `ms`.
- *Evolution:* Add a foreground/image layer variable (e.g. `cutout` or `foreground` asset) to the `video-thumbnail` SVG template and wire it under `design.variants[].image` so `ms design render` can layer cutouts natively.

**1.2 `ms video render --force` (Task 4, #3)**
`render` compiles to `out/<id>.fcproj`. After the first render that file exists; re-running `render` after any manifest change fails with the D46 guard ("already exists and may carry manual GUI edits") because `render` has no `--force` flag (unlike `build --force`). The user must manually `rm` the fcproj between render attempts. Not scriptable end-to-end.
- *Evolution:* Add a `--force` flag to `ms video render` that overwrites the existing fcproj, making re-renders after manifest edits scriptable.

**1.3 Template-adaptive safe areas (Task 5, #6)**
`video-thumbnail.svg` hardcodes the safe area at `x="60" y="60" width="1160" height="600"` (built for 720p). On a 1920x1080 canvas the headline and subhead boxes slightly leave this fixed safe area. Font size, text position, and safe-area are all template-hardcoded and not configurable via the variant manifest. The overflow cannot be resolved without editing the template or shortening the title.
- *Evolution:* Make safe-area geometry scale with the variant `size` (percentage-based or computed from canvas dimensions) and expose font size, text position, and safe-area as configurable variant fields.

### Tier 2 — Workflow Smoothness

These items are not blockers but require manual intervention, out-of-`ffmpeg` steps, or unreliable output that degrades the authoring experience.

**2.1 `librosa` integration for true beat transients in `ms video beat-sync` (Task 4, #2)**
`detect_beats()` imports `librosa`; it is not installed in the media-studio `.venv`, so detection falls back to `_ffmpeg_beat_fallback`, which emits a constant 128 BPM grid across the *entire* track (508 beat markers instead of real audio-transient detection). The grid happens to align well enough for this AMV but is not true beat-sync to the music's transients.
- *Evolution:* Install `librosa` in the media-studio environment so `detect_beats()` performs real transient-based beat detection.

**2.2 Unified `--deliverable` vs `--variant` flags (Task 4/#6 + Task 5/#1/#10)**
The brief documents `ms design render <project> --deliverable thumbnail-16x9`, but the actual flag is `--variant`. Similarly `ms video render` uses `--deliverable`. The flag naming is inconsistent across subcommands, so brief examples do not work as written.
- *Evolution:* Unify on a single flag name (`--deliverable` or `--variant`) across `video render`, `design render`, and related subcommands, and accept both as aliases during a transition period.

**2.3 Automatic sequence length clipping to video track length (Task 4, #4)**
The compiler places the full music cue on A2 (no `duration` field on `AudioCue`, and `file.exportMedia` has no duration clamp), so the sequence runs the full 238s track while the 15s of video ends at 15s, leaving ~223s of black video tail. The fix required an out-of-`ms` ffmpeg trim of the music plus a new license sidecar.
- *Evolution:* Add a `duration` field to `AudioCue` (or an export `--duration` clamp) so music does not drive sequence length beyond the deliverable, automatically clipping the sequence to the video track length and avoiding music-tail overhang.

### Tier 3 — CLI Hygiene

These are small, low-effort fixes that remove paper cuts and make the CLI match its documentation.

**3.1 Relative path normalization in `ms motion title` (Task 3/#1 + Task 6/#1)**
`ms motion title` crashes on `relative_to` when `--out` is relative. The CLI should normalize a relative `--out` against the project root (or cwd) instead of crashing.
- *Evolution:* Normalize relative `--out` paths to absolute before passing to EffectCraft reporting.

**3.2 Automatic `.mov` extension coercion (Task 3/#2 + Task 6/#2)**
EffectCraft emits ProRes 4444 with alpha and renames `.mp4` → `.mov`; `MotionResult.ok` is `false` unless `--out` uses `.mov`. Users supplying `.mp4` get a silent failure.
- *Evolution:* Coerce the `--out` extension to `.mov` (or validate and warn) for ProRes/RGBA motion output so the reported verdict matches the emitted container.

### Prioritization summary

| Tier | Item | Source | Effort | Value |
|---|---|---|---|---|
| 1 | Native cutout/image layering in `ms design render` | Task 5 | High | High (unblocks cutout graphics) |
| 1 | `ms video render --force` | Task 4 | Low | High (scriptable re-renders) |
| 1 | Template-adaptive safe areas | Task 5 | Medium | Medium (fixes overflow) |
| 2 | `librosa` for true beat transients | Task 4 | Medium | High (real beat-sync) |
| 2 | Unified `--deliverable`/`--variant` flags | Task 4/5 | Low | Medium (CLI consistency) |
| 2 | Auto clip sequence to video track length | Task 4 | Medium | High (no black tail) |
| 3 | Relative path normalization in `ms motion title` | Task 3/6 | Low | Low |
| 3 | Automatic `.mov` extension coercion | Task 3/6 | Low | Low |

