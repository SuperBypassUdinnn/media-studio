# Media-Studio Phase 2 Design

**Date:** 2026-11-06
**Status:** Draft — pending user review

## Scope

This document specifies four capabilities to be built in sequence:

1. Workflow changes: semi-manual source acquisition + visual inspection + asset library
2. `ms video beatramp` — beat-synced velocity ramping
3. `ms video edl` — EDL/cut-plan tool with user approval gate
4. Color grading — CC0 LUT acquisition + hybrid apply (global base + per-shot override)

These build on the Rhythm Engine (Phase 1: `ms video beats`, `ms video beatfx`) and the AMV Elite Pipeline (`impact.py`, `parallax.py`, `ramp.py`, `effectcraft.py`).

---

## 1. Workflow Changes: Asset Acquisition + Visual Inspection + Library

### 1.1 Responsibility Split

The pipeline distinguishes three classes of asset:

| Class | Who provides | How |
|---|---|---|
| Primary footage + BGM | User (manual) | Downloaded/recorded outside the agent |
| Supporting assets | Agent (automated, with approval) | Library → external search → download |
| Fallback manual request | Agent requests, user downloads | Chat checklist with URL + destination path |

Supporting assets include: templates, presets, fonts, SFX, overlay graphics, logo files, supplementary footage/sound. The agent never downloads primary footage or BGM automatically.

### 1.2 Asset Library

**Storage root:** `~/.local/share/media-studio/library/`

Layout:

```
library/
  catalog.json          # master index
  video/                # video clips, overlays
  audio/                # SFX, music stems
  image/                # static overlays, logos, textures
  font/                 # font files
  preset/               # LUT .cube, effect presets, .drx
  thumbnail/            # cached thumbnails keyed by asset id
```

**Catalog entry schema:**

```json
{
  "id": "uuid4",
  "type": "video|audio|image|font|preset",
  "name": "human-readable name",
  "source_url": "original download URL",
  "license": "CC0|CC-BY|custom",
  "attribution": "optional attribution text",
  "tags": ["action", "sfx", "whoosh", "anime"],
  "file_path": "~/.local/share/media-studio/library/audio/whoosh-01.wav",
  "sha256": "hex digest",
  "thumbnail_path": "~/.local/share/media-studio/library/thumbnail/<id>.jpg",
  "added_at": "2026-11-06T00:00:00Z",
  "used_in": ["amv-demonslayer-3d", "tutorial-python-01"]
}
```

**New CLI commands:**

```
ms library list [--type TYPE] [--tags TAG...]
ms library search <query> [--type TYPE]
ms library inspect <id>          # runs ms see on the asset
ms library register <file> --name NAME --type TYPE --tags TAG... [--source-url URL] [--license CC0]
ms library forget <id>           # removes catalog entry; leaves file (--purge to delete)
```

**Project integration:** when an asset from the library is used in a project, the agent creates a symlink in the project's `assets/` folder pointing to the library file. The `used_in` list in the catalog entry is updated. Symlinks are relative so the library root can be moved.

**Module:** `src/media_studio/library.py` — `LibraryError`, `LibraryEntry` dataclass, `load_catalog`, `save_catalog`, `register_asset`, `search_assets`, `link_asset_to_project`.

### 1.3 Acquisition Pipeline

When the agent needs a supporting asset it follows this ordered pipeline:

**Step 1 — Library check**
Search `catalog.json` by type + tags derived from the brief. If candidates found: run `ms library inspect <id>` (invokes `ms see` on the file), show result to user, list which projects used it previously. User approves to reuse (creates symlink) or skips to Step 2.

**Step 2 — External search + visual inspection**

Dual-path by asset type:

- **Video / image:** Fetch thumbnail URL from source API (Freesound, Pixabay, Pexels, Poly Haven). Download thumbnail only. Run `ms see` on thumbnail. Present to user with source URL and license.
- **Audio / font / preset:** Download full asset to a staging dir (`/tmp/opencode/asset-stage-<pid>/`). For audio: run `ms video beats` or `ffprobe` waveform metadata + 10 s clip preview. For font: render sample text ("AMV Title 1234") via ImageMagick. For preset: apply to a 3 s test clip and run `ms see`. Present result to user.

User decision: **approve** (proceed to Step 3) or **reject** (agent tries next candidate or escalates to Step 4).

**Step 3 — Download + register**
Download full asset to library storage. Compute SHA-256. Register in catalog. Create symlink in project. Write license sidecar (`<asset>.LICENSE`). Append to project `CREDITS.md`.

**Step 4 — Manual request fallback**
If automated search fails (rate-limit, Cloudflare, no suitable result after N candidates): agent posts a chat checklist:

```
I could not automatically acquire the following asset. Please download it manually:

- [ ] Name: Epic Whoosh SFX
      Type: audio / SFX
      Source: https://freesound.org/people/example/sounds/12345/
      License: CC0
      Save to: ~/Projects/amv-demonslayer-3d/assets/audio/whoosh-01.wav
      After saving, reply "done" to continue.
```

After the user replies "done", the agent validates the file exists, runs inspect, registers in library, creates symlink.

### 1.4 Brief vs. Footage Consistency Check

After user provides primary footage + BGM, before searching for supporting assets:

1. Agent runs `ms see` on the first and last shots of each footage clip.
2. Agent runs `ms video beats` on the BGM.
3. Agent compares detected mood/palette/pacing against `brief.md` description.
4. If inconsistency detected: agent posts specific evidence (frame screenshot path + detected palette, beat BPM vs expected energy level) and asks: "revise brief or swap footage/BGM?"
5. If the conflict is structural (e.g., brief says "high-energy battle" but footage is calm slice-of-life throughout), agent offers to trigger a grill-me session to re-align.

### 1.5 Changes to media-create Skill

The 9-stage lifecycle gains a new **Stage 0 — Source Handoff & Consistency Check** inserted before Stage 1:

- User provides primary footage + BGM paths in `project.yaml` under `assets.primary`.
- Agent runs the brief-vs-footage consistency check (Section 1.4).
- If consistent: proceed to Stage 1.
- If inconsistent: flag + ask. If user confirms proceed: note the discrepancy in `brief.md` and continue.

Stage 4 (Asset Acquisition) is replaced with the full acquisition pipeline (Section 1.3): library check → external search + visual inspect → download + register → manual fallback.

Gate 1 now requires the agent to also report which library assets will be reused (with project history) alongside the cut list and effect schedule.

### 1.6 New Modules and Files

| File | Purpose |
|---|---|
| `src/media_studio/library.py` | Catalog CRUD, `LibraryEntry`, `search_assets`, `link_asset_to_project` |
| `src/media_studio/acquire.py` | Acquisition pipeline orchestration: library → inspect → external → manual fallback |
| `src/media_studio/cli.py` | New `ms library` subcommand group |
| `tests/test_library.py` | Unit tests: catalog round-trip, search, link, dedup by hash |
| `tests/test_acquire.py` | Unit tests: pipeline ordering, manual fallback message format, brief-vs-footage flag |
| `~/.agents/skills/media-create/SKILL.md` | Stage 0 addition, Stage 4 rewrite, Gate 1 upgrade |

---

## 2. `ms video beatramp` — Beat-Synced Velocity Ramping

### 2.1 Purpose

`ramp.py` already implements velocity ramping effects (`fast_slow_fast`, `slow_snap`, `flash_freeze`). `beatramp` reads a `beats.json` artifact (from `ms video beats`) and automatically schedules ramp events at the appropriate beat positions within a shot's music window, mirroring the auto-schedule pattern of `beatfx`.

### 2.2 Trigger Mapping

| Beat event | Detected as | Default ramp kind |
|---|---|---|
| Downbeat | `beats.json` downbeats list | `fast_slow_fast` |
| Snare / strong onset | Non-downbeat beat in a high-energy section | `slow_snap` |
| Build peak | Last beat before a section transition from low→high energy | `flash_freeze` |

The mapping is applied deterministically given the same `beats.json` and `--offset`. The user can override per-event kind via a JSON flag `--map '{"downbeat":"slow_snap"}'`.

### 2.3 CLI Interface

```
ms video beatramp <source> --beats PATH --offset SECONDS
                  [--map JSON]
                  [--max-per-second 1]
                  [--out PATH]
                  [--fps 60]
                  [--resolution 1920x1080]
                  [--codec {h264,prores}]
```

Exactly one ramp per trigger event (ramps are longer operations than beatfx micro-effects; `--max-per-second` defaults to 1 to avoid collisions). If two trigger events fall within the minimum ramp duration of each other, the higher-energy one wins.

Output: same `BeatRampResult` dataclass pattern as `BeatFxResult` — source, destination, effects list, passes, fps, resolution, size\_bytes.

### 2.4 Implementation Notes

- New module: `src/media_studio/beatramp.py`
- Reuses `ramp.py` functions directly (`apply_fast_slow_fast`, `apply_slow_snap`, `apply_flash_freeze`). `beatramp.py` is an orchestration layer, not a reimplementation.
- Reuses `beatfx.py`'s `auto_schedule` pattern for event selection (energy-ranked greedy, min gap = minimum ramp duration for the slowest ramp kind, default 0.5 s).
- Resolution floor, destination resolution, codec handling: identical to `beatfx.py`.
- CLI wiring: `beatramp` subparser inserted after `beatfx` block in `cli.py`.

### 2.5 Testing

- `tests/test_beatramp.py`: trigger mapping from synthetic `beats.json`, min-gap collision resolution, map override, real-ffmpeg e2e on 1920x1080 testsrc2 clip, resolution floor validation.

---

## 3. `ms video edl` — EDL/Cut-Plan Tool

### 3.1 Purpose

Produces a machine-readable, human-editable JSON cut list from the available project shots and a `beats.json`. The agent proposes cuts aligned to beat structure; the user reviews and optionally edits the JSON before the agent executes render+concat.

### 3.2 EDL JSON Schema

```json
{
  "version": 1,
  "music": "assets/audio/music-battle.wav",
  "beats_json": "assets/audio/music-battle.beats.json",
  "total_duration_s": 90.0,
  "cuts": [
    {
      "id": "cut-01",
      "shot": "assets/video/shot1-tanjiro-1080p60.mp4",
      "in_ms": 0,
      "out_ms": 2500,
      "music_offset_ms": 0,
      "beatfx": ["punch@0.3", "flash@1.1"],
      "ramp": ["fast_slow_fast@2.0"],
      "color_lut": null,
      "notes": "Opening card — slow push"
    }
  ]
}
```

Fields:
- `in_ms` / `out_ms`: trim points within the source shot
- `music_offset_ms`: where in the music timeline this cut starts (drives `--offset` for beatfx and beatramp)
- `beatfx`: shorthand list `"kind@local_time_s"` for pre-scheduled beat effects (populated by agent using `beats.json + music_offset_ms`, editable by user)
- `ramp`: shorthand list `"kind@local_time_s"` (same)
- `color_lut`: library ID of the LUT to apply, or `null` for global default

### 3.3 CLI Interface

```
ms video edl generate --beats PATH --shots SHOT [SHOT...] [--music PATH]
                       [--duration SECONDS] [--out edl.json]

ms video edl render <edl.json> [--out PATH] [--fps 60] [--codec {h264,prores}]

ms video edl preview <edl.json> [--cut ID]   # render one cut only for quick review
```

**Generate flow:**
1. Load `beats.json`.
2. For each shot (in order): probe duration, assign music window greedily to fill the target `--duration` with cuts in beat-density range (drops 0.2–0.8 s, builds 0.8–1.5 s, verses 1–2 s from the Rhythm & Density rules).
3. Populate `beatfx` and `ramp` fields from `auto_schedule` and `beatramp` logic.
4. Write `edl.json`.
5. Print a human-readable summary table to stdout (shot | in | out | duration | effects count).

**Render flow:**
1. Read and validate `edl.json`.
2. For each cut: trim shot → apply beatfx → apply beatramp → apply color LUT (if set).
3. Concat all processed cuts with the music track mixed in at the correct offsets.
4. Write final video.

### 3.4 Implementation Notes

- New module: `src/media_studio/edl.py` — `EdlCut`, `EdlDocument`, `load_edl`, `save_edl`, `generate_edl`, `render_edl`.
- `generate_edl` calls `beatfx.auto_schedule` and `beatramp` trigger mapping internally; it does not require the user to run those commands separately.
- `render_edl` calls the existing `apply_effects` (beatfx) and ramp functions per cut, then uses ffmpeg concat demuxer for the final join.
- Audio mix: ffmpeg `amix`/`adelay` to place the music at the correct offset for each cut, then `aresample` to ensure consistent sample rate.
- Module does NOT require librosa at render time — only `beats.json` on disk.

### 3.5 Testing

- `tests/test_edl.py`: schema validation, generate assigns cuts within beat-density ranges, render produces valid h264 output (real ffmpeg, testsrc2 clips), preview renders single cut, malformed JSON error.

---

## 4. Color Grading — CC0 LUT Acquisition + Hybrid Apply

### 4.1 Purpose

Apply a color grade to the final video or per shot via a LUT (`.cube` file) sourced from free CC0 repositories. Mode: hybrid — a global base LUT applies to the whole video by default; individual cuts in the EDL can override with a per-shot LUT. This makes the feature useful for AMV (per-shot variation) and non-AMV (global base sufficient).

### 4.2 LUT Sources (CC0, automated)

Agent searches in this order:

1. **Asset library** — existing LUTs already registered (type: `preset`, tags include `lut`).
2. **Poly Haven** — primarily HDRIs but some LUT-adjacent color data; skip if no LUT results.
3. **FreePresets.net / Lutify free tier** — `.cube` LUTs with CC0 or free-for-commercial license; fetched via direct download URL after scraping the free section.
4. **Bundled fallbacks** — 5 hand-verified CC0 `.cube` files shipped inside the repo under `src/media_studio/data/luts/`: `cinematic-teal-orange.cube`, `anime-vibrant.cube`, `horror-bleach.cube`, `warm-sunset.cube`, `cool-blue.cube`. Always available, no download needed.

### 4.3 Visual Inspection for LUTs

Since thumbnails are not meaningful for LUTs, inspection works differently:

1. Take the first frame of the first shot in the project (or a user-specified reference frame).
2. Apply the candidate LUT via ffmpeg `lut3d` filter.
3. Run `ms see` to generate a contact sheet showing before/after side by side.
4. Present to user: "This is how `cinematic-teal-orange.cube` would look on your footage."
5. User approves or tries the next candidate.

### 4.4 CLI Interface

```
ms video lut search [--tags TAG...] [--style STYLE]   # search library + sources
ms video lut preview <lut_file_or_id> <reference_frame_or_shot>
ms video lut apply <source> --lut PATH [--out PATH]   # apply to a single clip
```

Color grading in the EDL pipeline (Section 3) calls `ms video lut apply` internally per cut or globally.

**Global base apply** (no EDL): `ms video lut apply final.mp4 --lut cinematic-teal-orange.cube`

**Per-shot via EDL**: each cut's `color_lut` field references a library asset ID. `edl render` resolves the symlink and calls lut apply per cut before concat.

### 4.5 Implementation Notes

- New module: `src/media_studio/lut.py` — `LutError`, `apply_lut` (ffmpeg `lut3d` filter), `preview_lut` (before/after frame composite via ffmpeg `hstack`), `search_luts`.
- Bundled LUTs path: `src/media_studio/data/luts/` (5 files, committed to repo).
- `apply_lut` validates `.cube` extension and file existence; unknown format → `LutError`.
- `preview_lut` extracts one frame (`-vframes 1 -ss <timestamp>`), applies LUT to it, stacks original + graded horizontally, writes PNG, calls `ms see` on the PNG.
- CLI wiring: `ms video lut` subcommand group with `search`, `preview`, `apply` subcommands.

### 4.6 Testing

- `tests/test_lut.py`: apply bundled LUT to testsrc2 frame produces valid output; preview produces PNG; unknown format raises `LutError`; `search_luts` returns bundled LUTs when library empty.

---

## 5. Cross-Cutting Concerns

### 5.1 media-create Skill Updates (cumulative)

After all four capabilities land, `~/.agents/skills/media-create/SKILL.md` will reflect:

- Stage 0: Source Handoff & Consistency Check (new)
- Stage 4: Asset Acquisition (rewritten — library → inspect → external → manual fallback)
- Rhythm & Density section: already in place (Phase 1)
- Gate 1: requires cut list + effect schedule + reused library assets list
- CLI reference: `ms library`, `ms video beatramp`, `ms video edl`, `ms video lut`

### 5.2 Error Handling Conventions

All new modules follow the pattern established by `beatfx.py` and `ramp.py`:
- Custom exception class per module (`LibraryError`, `AcquireError`, `BeatRampError`, `EdlError`, `LutError`), all subclassing `RuntimeError`.
- CLI handlers catch the domain exception + `subprocess.CalledProcessError` → print `error: <msg>` to stderr, return `EXIT_FAILURE`.
- Temp dirs always cleaned in `finally`.

### 5.3 Resolution and Codec Defaults

Consistent across all new video-output commands: `--resolution 1920x1080` (floor enforced), `--fps 60`, `--codec h264`. Message for undersized input contains "below the 1920x1080 minimum floor".

### 5.4 Test Strategy

- Each module has a dedicated `tests/test_<module>.py`.
- Real-ffmpeg tests use 1 s 1920x1080 `testsrc2` clips (fast) and are marked with the same `EndToEnd` pattern as `test_video_cutout.py`.
- Bundled LUT tests use the shipped `.cube` files so they need no network.
- Library catalog tests use `tmp_path` to isolate from the real `~/.local/share/media-studio/library/`.

---

## 6. File Touch Summary

| File | Change |
|---|---|
| `src/media_studio/library.py` | New |
| `src/media_studio/acquire.py` | New |
| `src/media_studio/beatramp.py` | New |
| `src/media_studio/edl.py` | New |
| `src/media_studio/lut.py` | New |
| `src/media_studio/data/luts/*.cube` | New (5 bundled CC0 LUTs) |
| `src/media_studio/cli.py` | Add `ms library`, `ms video beatramp`, `ms video edl`, `ms video lut` |
| `pyproject.toml` | No new extras needed (all stdlib + existing deps) |
| `tests/test_library.py` | New |
| `tests/test_acquire.py` | New |
| `tests/test_beatramp.py` | New |
| `tests/test_edl.py` | New |
| `tests/test_lut.py` | New |
| `~/.agents/skills/media-create/SKILL.md` | Stage 0, Stage 4 rewrite, Gate 1 upgrade, CLI reference additions |
| `README.md` | Quickstart lines for new commands |

