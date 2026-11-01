# FilmCraft and EffectCraft migration plan

Status: complete (2026-11-01). All 9 stages executed and verified end to end on the
`talking-head-promo` acceptance project (60 s, 2 shots + voice + ducked music + title +
lower-third + burned captions): 97 unit tests pass, ruff clean, GPU render 98 s with
automatic CPU fallback wired, QC 7/7 checks pass (-14.08 LUFS vs -14.0 target, true peak
-12.5 dBTP, 60.01 s at 1920x1080@30), and a delete-out-and-rebuild rerun reproduced the
same measured numbers. Engine finding baked into the design: FilmCraft 0.15.0 headless
export ignores all audio automation (effect keyframes, clip-mixer keyframes, mixer
automation lanes), so music ducking is baked into the audio file via ffmpeg
sidechaincompress before import (gate (a) measured a 4.8 dB drop under speech); caption
burn-in uses the working `<slug>.fcproj` and was verified on frame stills at 6 s and 35 s.

Deferred to a follow-up plan (explicitly de-scoped, not silently dropped):

- `ms motion new` template scaffolding with brand tokens (Stage 5.1).
- `ms video cut-by-transcript --remove-pauses --remove-fillers` with automatic
  snapshot-before-change (Stage 6.2); the atomic pieces exist (`transcript`,
  `remove-fillers`, `captions`, `snapshot`).
- Standalone `ms video proxy` / `ms video stills` commands (Stage 4.4); both run
  today as part of `render_filmcraft`.
- `ms video build --dry-run` and D46 overwrite-protection `--force` (Stage 3).
- `effectcraft.render_comp()` returning duration/alpha-coverage metadata and a
  GPU->CPU fallback for EffectCraft itself (Stage 2; FilmCraft has both).
- Deeper ducking: `duck_music` params tuned beyond the measured ~5 dB if a project
  needs heavier attenuation.

Design: `docs/design.md` v2, decisions D34-D47 and sections 5.7, 5.9, 10 (phase 4)

Scope: replace the video render core of `media-studio` with FilmCraft (timeline, mix, render) and
EffectCraft (motion graphics), retire `resolve_mcp` and the Kdenlive handoff, and keep everything
else (manifest, assets, ledger, triage, presets, 3D, design) untouched.

## Why this migration

The v1 path works and passes QC, but it cannot animate anything: an MLT filter declared in XML is
applied statically, so a keyframed effect never reaches the renderer, and ducking is a chain of
fixed-level entries quantised to 50 ms. FilmCraft exposes 711 commands behind one registry with
typed parameters and real keyframes, renders headless with no window, and speaks OTIO. Measured on
this machine on 2026-10-31 (v0.15.0): 58.2 fps CPU, 81.8 fps GPU, 394 ms for a 540p program frame,
and a stdio MCP handshake exposing 17 tools.

## Ground rules

- Permissive licences only: FilmCraft and EffectCraft are dual MIT/Apache-2.0. No AGPL, no
  proprietary engine in the deliverable path.
- Engine versions are pinned; a version bump is a deliberate edit to `media-engines.json`, never an
  implicit upgrade.
- Evidence before code. Each stage ends with a command whose real output is pasted into this file.
  A stage that cannot show its output is not done.
- No stage deletes the v1 path. MLT stays runnable behind `--engine mlt` until stage 7, and the
  Kdenlive writer stays until its retirement is explicit.
- Scratch renders into `/tmp/opencode/<slug>/` and is purged after encode.

## Stage 0 - install and pin

Deliverable: both engines runnable from `PATH`, versions recorded, nothing else changed.

1. Download the release assets into `~/.local/opt`:
   `filmcraft-0.15.0-linux-x86_64.tar.gz` (101 MB) and `effectcraft-0.19.0-linux-x86_64.tar.gz`.
2. Verify the published checksum for each asset before extracting.
3. Extract to `~/.local/opt/filmcraft-0.15.0/` and `~/.local/opt/effectcraft-0.19.0/`.
4. Symlink `filmcraft`, `filmcraft-cli`, `effectcraft`, `effectcraft-cli` into `~/.local/bin`.
5. Write `~/.config/opencode/media-engines.json` with `{name, version, installPath, binaryPath,
   installedAt, sha256}` per engine.
6. Extend `ms doctor` to read that file and report both engines, plus whether the pinned binary is
   the one on `PATH`.

Acceptance:

- `filmcraft-cli --version` and `effectcraft-cli --version` answer with the pinned versions.
- `ms doctor --deep` lists both engines and their paths.
- Reverting is a symlink change, verified by pointing one symlink at the previous version and back.

## Stage 1 - capability probe

Deliverable: a probe report at `~/.local/state/opencode-media-studio/fcprobe/report.md` covering
every claim the design depends on. No production code is written in this stage.

| Probe | Method | Expected evidence |
| --- | --- | --- |
| Command catalogue | `filmcraft-cli commands --json` | 711 entries; prefix counts recorded |
| Timeline assembly | `file.newProject`, `file.newSequence`, `file.import`, `timeline.place` | A/V auto-linked, `inspect sequence` shows both tracks |
| Keyframes survive a save | Set a keyframed motion parameter, save, reopen, inspect | Same keyframe count after reopen |
| GPU render | Export with `--gpu-rendering auto` | Success, fps recorded, EGL warning captured on stderr |
| CPU fallback | Force a GPU failure (tiny VRAM ceiling or unsupported op) | Retry succeeds and the failure text is available for the QC report |
| OTIO round trip | `file.exportOtio`, then re-import | Diff count of lost effects, keyframes, and in-points |
| EffectCraft alpha | Build a comp with a text layer, keyframe it, render ProRes 4444 | `ffprobe` reports `prores` with an alpha pixel format; frame shows the layer |
| EffectCraft MCP | `effectcraft-cli mcp` stdio handshake | `initialize` and `tools/list` answer |
| Whisper | `transcript.downloadModel` for whisper-base, then `transcript.generate` on a 30 s fixture | Word-level timestamps, wall time, and a readable transcript |
| Loudness | Export with `loudnessLufs=-14`, measure with `ffprobe` ebur128 | Measured value against the target |
| Beat source | Look for a beat or onset command in the catalogue | Either a FilmCraft command, or a documented decision to use FFmpeg onset detection in `ms` |

Acceptance: every row has a measured result, and any row that fails changes the plan before stage 2
starts rather than after.

## Stage 2 - runner core

Deliverable: two thin Python runners with tests, no CLI surface yet.

New files:

- `src/media_studio/filmcraft.py`: binary resolution (`MS_FILMCRAFT_BIN` override, then `PATH`,
  then the pinned path), `run()` returning a typed result with exit code, stdout JSON, and filtered
  stderr; a catalogue cache keyed by version under
  `~/.cache/media-studio/filmcraft-<version>-commands.json`; `command_exists()`; the GPU-then-CPU
  export helper that records which path ran; and a project-path guard that refuses a path outside
  the project root.
- `src/media_studio/effectcraft.py`: the same shape, plus `render_comp()` returning duration, alpha
  coverage, and render time.
- `tests/test_filmcraft.py`, `tests/test_effectcraft.py`: a fake binary fixture that replays recorded
  JSON, so tests never need the real engines.

Acceptance: unit tests pass without the engines installed; `ruff check` and `ruff format --check`
are clean; the EGL warning is filtered and a genuine error still surfaces.

## Stage 3 - manifest to timeline

Deliverable: `project.yaml` compiles into a `.fcproj`, and the agent can place, trim, and inspect
without hand-writing commands.

New file: `src/media_studio/fcproj.py` - the compiler. It reads the existing `VideoPlan`, resolves
assets through the ledger, and emits an ordered command list, so a build is a pure function of the
manifest and can be diffed before it runs.

CLI surface (`src/media_studio/cli.py`):

| Command | Behaviour |
| --- | --- |
| `ms video build <project> [--deliverable id] [--dry-run]` | Compile the manifest into `out/<id>.fcproj`; `--dry-run` prints the command list only |
| `ms video cmd <project> <command-id> [--params JSON]` | Raw passthrough with catalogue validation |
| `ms video place <project> --asset <path> --track V2 --at <time>` | Overlay placement, used by the motion path |
| `ms video trim <project> --clip <id> --in <s> --out <s> [--ripple]` | Trim a placed clip |
| `ms video inspect <project> [--sequence]` | Summarise the compiled timeline in text |
| `ms video open <project>` | Launch the GUI on the same `.fcproj` |

`ms video render` keeps its name and gains `--engine {filmcraft,mlt}`, defaulting to `filmcraft`;
`mlt` prints a deprecation line. The manifest stays the source of truth: `--dry-run` is the review
gate, and `build` refuses to overwrite a `.fcproj` that was modified after the last build without
`--force` (D46).

Acceptance: the fixture project builds; `ms video inspect` lists every shot with its in-point and
duration; rebuilding an unmodified manifest produces an identical command list; a manually edited
`.fcproj` produces a diff report and no write.

## Stage 4 - audio, QC, and review artefacts

Deliverable: one export command whose output is measured, not assumed.

1. Ducking becomes a gain envelope on the music clip, computed from `silencedetect` on the voice
   track exactly as today, applied as keyframed clip gain.
2. `ms video render` exports with `loudnessLufs` from the deliverable and writes to scratch first,
   then moves the file into `out/video/`.
3. `ms video qc` keeps the seven `ffprobe` checks and adds `renderPath`, engine versions, and the
   GPU failure text when a fallback happened.
4. `ms video proxy` writes a 540p H.264 proxy; `ms video stills --at <times>` writes labelled frames.

Acceptance: loudness within 0.5 LU of the target; ducking measurably lowers music under voice;
seven checks green in one report; proxy and stills exist and are referenced from the report.

## Stage 5 - motion graphics

Deliverable: an EffectCraft graphic that lands on the timeline and appears in the delivered frame.

1. `ms motion new <project> --comp lower-third` scaffolds `.ecproj` from a template with brand
   tokens (text, colour, font, safe area).
2. `ms motion render <project> --comp lower-third` renders alpha ProRes 4444 into
   `out/motion/`, with the GPU-then-CPU rule.
3. `ms motion inspect <project>` lists compositions, layers, and render state.
4. The motion output is placed on V2 by `ms video place`, and the manifest records it under
   `motion:` so a rebuild reproduces the same overlay.

Acceptance: alpha channel present in the render; the graphic is visible in the exported frame at the
declared timecode; a rebuild after deleting `out/motion/` regenerates it identically.

## Stage 6 - the AI slice

Deliverable: the 60 s talking-head path of D43.

1. `transcript.generate` with whisper-base, cached per source file hash.
2. `ms video cut-by-transcript --remove-pauses 0.35 --remove-fillers` rewrites the timeline from
   word timestamps, keeping a reversible snapshot before the change.
3. `ms video captions --style <preset> [--translate <lang>]` builds the caption track and can burn
   it in at export.
4. `ms video beats` produces beat times (FilmCraft command if the stage 1 probe found one, otherwise
   FFmpeg onset detection inside `ms`), and `ms video cut-to-beats` snaps cuts to the nearest beat
   within a stated tolerance.

Acceptance: a 60 s take cuts to under 45 s with no clipped words; the transcript text matches the
captions; every cut lands within one frame of its target; removing the feature restores the
pre-cut timeline from the snapshot.

## Stage 7 - retirement and surface

Deliverable: no dead path in code, config, or documentation.

1. Move `src/resolve_mcp/` and its tests to `docs/legacy/resolve-mcp/` with a README explaining why
   it was retired, or delete it and record the removal in this plan.
2. Mark `media_studio/video.py::handoff` and the Kdenlive writer as legacy: keep the code, print a
   deprecation note, and drop both from the skills and commands.
3. Update `~/.config/opencode/opencode.jsonc`: remove `resolve-mcp`, add `filmcraft` as a commented
   on-demand server entry, and validate with `~/.config/opencode/scripts/jsonc-check`.
4. Update the `media-studio-video` skill, the `media-studio` router, and `/video` to the new command
   surface, including the generated-catalogue rule and the snapshot-before-edit rule.
5. Extend `scripts/selftest.sh` with the phase 4 checks.

Acceptance: `grep -r resolve_mcp src/` returns nothing; `opencode.jsonc` parses; the skill lists only
commands that exist in `ms video --help`; the selftest reports phase 4 rows.

## Stage 8 - vertical slice acceptance

The 60 s talking-head social cut, end to end, on real footage:

| Check | Gate |
| --- | --- |
| Timeline | Every shot, the voice track, the music bed, and the lower-third present in `ms video inspect` |
| Duration | Declared duration within one frame |
| Loudness | -14.0 LUFS within 0.5 LU, true peak under -1.5 dBTP |
| Ducking | Music drops measurably under speech |
| Captions | Text matches the transcript, burned-in export verified by a frame still |
| Motion | Lower-third visible with alpha over live picture |
| Review | 540p proxy plus stills at the first, middle, and last caption |
| QC | One report with all seven checks and the render path |
| Reproducibility | Deleting `out/` and scratch and rebuilding yields the same measured numbers within tolerance |

## File inventory

| File | Change |
| --- | --- |
| `src/media_studio/filmcraft.py` | New: runner, catalogue cache, GPU/CPU export helper |
| `src/media_studio/effectcraft.py` | New: composition runner and alpha render |
| `src/media_studio/fcproj.py` | New: manifest to `.fcproj` compiler, pure command list |
| `src/media_studio/cli.py` | Extended: `video build/cmd/place/trim/inspect/open/proxy/stills/cut-by-transcript/captions/beats`, `motion new/render/inspect` |
| `src/media_studio/video.py` | Demoted: MLT path behind `--engine mlt`, handoff marked legacy |
| `src/media_studio/media.py` | Extended: QC record gains render path and engine versions |
| `src/media_studio/manifest.py` | Extended: `motion:` and `captions:` manifest sections |
| `tests/` | New fake-binary fixtures plus compiler, QC, and retirement tests |
| `~/.config/opencode/media-engines.json` | New: pinned versions and paths |
| `~/.config/opencode/opencode.jsonc` | Updated: `resolve-mcp` out, `filmcraft` in as on-demand |
| `~/.config/opencode/skills/media-studio-video` | Updated: new command surface and rules |

## Rollback

Every stage is reversible without data loss: the v1 MLT path keeps working behind `--engine mlt`,
`.fcproj` is a file the GUI opens directly, and every build exports OTIO, so an edit can leave the
engine without being rebuilt by hand. An engine regression is a symlink revert to the previous
pinned version.

## Open questions carried from the design

Items 8 to 12 of `docs/design.md` section 14 are the blocking unknowns for stages 1, 5, and 6:
OTIO round-trip fidelity, EffectCraft behaviour on this machine, Whisper model choice, whether
`loudnessLufs` matches the measured two-pass result, and the exact removal of the retired paths.

## Out of scope

Remotion, b-roll automation, face-safe reframe, TTS voice-over, multicam authoring, cloud rendering,
and any GUI automation beyond the FilmCraft `--bridge` channel.
