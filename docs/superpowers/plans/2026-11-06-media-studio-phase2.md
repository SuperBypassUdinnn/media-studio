# Media-Studio Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build five new capabilities (asset library, acquisition pipeline, beat-synced ramp, EDL cut-plan tool, color LUT grading) plus update the media-create skill, maturing the headless AMV production stack into a full semi-manual, visually-inspected, library-backed editing pipeline.

**Architecture:** A shared asset library (`library.py`) underpins an acquisition pipeline (`acquire.py`) that checks local cache before hitting external sources; three new video-processing modules (`beatramp.py`, `edl.py`, `lut.py`) compose on top of existing Phase 1 primitives (`ramp.py`, `beatfx.py`, `audio.py`); the `media-create` skill is updated last to surface the complete workflow.

**Tech Stack:** Python 3.12, ffmpeg 7.0.2, librosa (already installed), ImageMagick (for font preview), existing venv; no new required dependencies.

**Spec:** `docs/superpowers/specs/2026-11-06-media-studio-phase2-design.md`

## Global Constraints

- Resolution floor 1920x1080 on all video-output commands; message contains "below the 1920x1080 minimum floor".
- Default `--fps 60`, `--codec h264` on all new video-output commands.
- Custom exception per module (`LibraryError`, `AcquireError`, `BeatRampError`, `EdlError`, `LutError`), all subclassing `RuntimeError`; CLI handlers catch domain exception → `error: <msg>` to stderr + `EXIT_FAILURE`.
- Temp dirs always cleaned in `finally`; intermediates under `/tmp/opencode/<module>-<stem>-<pid>/`.
- Library catalog at `~/.local/share/media-studio/library/catalog.json`; tests use `tmp_path` to isolate from real catalog.
- Symlinks in `assets/` are relative (portable if library root moves).
- All new `.cube` files committed under `src/media_studio/data/luts/` (5 bundled CC0 LUTs).
- `ruff check src/ tests/` and `ruff format --check src/ tests/` clean before every commit.
- Commits: conventional, plain text, no emojis. pip via `.venv/bin/python -m pip`; ruff at `~/.local/bin/ruff`.
- Pre-existing test failures (`test_presets.py` snapshots, `test_mlt.py`) are out of scope.

## Review Focus

- **Symlink across filesystem boundaries:** `link_asset_to_project` creates a relative symlink; if library root and project are on different mount points, `os.path.relpath` produces a path that crosses the mount and the symlink may dangle. Task 1 test must assert symlink target resolves to the same inode as the library file.
- **Catalog concurrent writes:** two agent sessions writing `catalog.json` simultaneously can corrupt it (last-write-wins, partial JSON). Task 1 must implement file-lock (via `fcntl.flock` or `filelock`) around `save_catalog`; test must assert the lock is acquired.
- **beatramp collision when two trigger events are within min-gap:** the higher-energy event must win deterministically. Task 3 test must include a fixture where downbeat and snare fall 0.3 s apart (< 0.5 s min-gap) and assert only one ramp is emitted with the correct kind.
- **EDL render with zero beatfx/ramp fields:** a cut with empty `beatfx: []` and `ramp: []` must still produce a valid trimmed clip. Task 4 test must include a cut with no effects.
- **LUT applied to a clip whose color space is not yuv420p:** `lut3d` requires the input pix_fmt to be compatible; applying to a ProRes (yuv444p10le) intermediate without conversion silently produces wrong output. Task 5 must convert to `yuv420p` before `lut3d` and back if the target codec is ProRes.

---

### Task 1: Asset Library (`ms library`)

**Files:**
- Create: `src/media_studio/library.py`
- Modify: `src/media_studio/cli.py` (new `ms library` subcommand group after `ms video`)
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: `pathlib.Path`, stdlib `json`, `uuid`, `hashlib`, `fcntl` (or `filelock`).
- Produces:
  - `LIBRARY_ROOT: Path` = `Path.home() / ".local/share/media-studio/library"`
  - `CATALOG_PATH: Path` = `LIBRARY_ROOT / "catalog.json"`
  - `class LibraryError(RuntimeError)`
  - `@dataclass class LibraryEntry`: `id: str`, `type: str`, `name: str`, `source_url: str`, `license: str`, `attribution: str`, `tags: list[str]`, `file_path: Path`, `sha256: str`, `thumbnail_path: Path | None`, `added_at: str`, `used_in: list[str]`
  - `load_catalog(root: Path = LIBRARY_ROOT) -> list[LibraryEntry]`
  - `save_catalog(entries: list[LibraryEntry], root: Path = LIBRARY_ROOT) -> None` — acquires file lock before writing
  - `register_asset(file: Path, *, name: str, type: str, tags: list[str], source_url: str = "", license: str = "CC0", attribution: str = "", root: Path = LIBRARY_ROOT) -> LibraryEntry`
  - `search_assets(query: str = "", *, type: str | None = None, tags: list[str] = [], root: Path = LIBRARY_ROOT) -> list[LibraryEntry]`
  - `link_asset_to_project(entry: LibraryEntry, project_assets_dir: Path) -> Path` — creates relative symlink; updates `used_in` in catalog; returns symlink path
  - `forget_asset(id: str, *, purge: bool = False, root: Path = LIBRARY_ROOT) -> None`

- [ ] **Step 1: Write failing tests in `tests/test_library.py`**

  Cover: `register_asset` creates the file in `LIBRARY_ROOT/type/`, catalog entry round-trips via `load_catalog`/`save_catalog`; `sha256` matches actual file; `search_assets` matches by tag and by type; unmatched query returns empty list; `link_asset_to_project` creates a symlink whose resolved path equals `entry.file_path` (pins the inode check); `used_in` is updated in catalog after link; `save_catalog` uses a file lock (patch `fcntl.flock` and assert it was called); `forget_asset` removes the catalog entry and optionally deletes the file.

- [ ] **Step 2: Run tests to confirm they fail**

  Run: `cd /home/superbypassudin/.clone/Github/SuperBypassUdinnn/media-studio && .venv/bin/python -m pytest tests/test_library.py -q`
  Expected: `ModuleNotFoundError: No module named 'media_studio.library'`

- [ ] **Step 3: Implement `src/media_studio/library.py`**

  `register_asset`: copy file to `root/type/<sha256[:8]>-<original-stem><suffix>`, compute SHA-256 of original, create `LibraryEntry`, append to catalog via `save_catalog`.
  `save_catalog`: open `CATALOG_PATH` with `fcntl.flock(fd, fcntl.LOCK_EX)`, write JSON, unlock in `finally`.
  `link_asset_to_project`: compute `os.path.relpath(entry.file_path, project_assets_dir)`, call `symlink_to`, assert `(project_assets_dir / symlink).resolve() == entry.file_path.resolve()` before returning.

- [ ] **Step 4: Wire `ms library` CLI subcommand group**

  Subcommands: `list [--type TYPE] [--tags TAG...]`, `search <query> [--type TYPE]`, `inspect <id>` (calls `ms see` on the asset file), `register <file> --name NAME --type TYPE --tags TAG... [--source-url URL] [--license CC0]`, `forget <id> [--purge]`.
  Handler pattern: catch `LibraryError` → stderr + `EXIT_FAILURE`.

- [ ] **Step 5: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_library.py -q && ~/.local/bin/ruff check src/ tests/ && ~/.local/bin/ruff format --check src/media_studio/library.py tests/test_library.py`
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/media_studio/library.py src/media_studio/cli.py tests/test_library.py
  git commit -m "feat(library): asset library catalog with ms library commands"
  ```

---

### Task 2: Acquisition Pipeline + Consistency Check (`acquire.py`)

**Files:**
- Create: `src/media_studio/acquire.py`
- Modify: `src/media_studio/cli.py` (no new top-level subcommand; `acquire` is used programmatically from `media-create` skill; expose `ms acquire search` for debugging)
- Test: `tests/test_acquire.py`

**Interfaces:**
- Consumes: `library.search_assets`, `library.register_asset`, `library.link_asset_to_project`; `ingest.probe_media`; `audio.analyze_rhythm`; subprocess `ms see` and `ffmpeg`.
- Produces:
  - `class AcquireError(RuntimeError)`
  - `@dataclass class AcquireResult`: `entry: LibraryEntry`, `symlink: Path`, `source: str` (one of `"library"`, `"external"`, `"manual"`)
  - `acquire_asset(*, type: str, tags: list[str], project_assets_dir: Path, project_slug: str, library_root: Path = LIBRARY_ROOT) -> AcquireResult | None` — returns `None` when manual fallback is needed
  - `manual_request_message(*, name: str, type: str, source_url: str, license: str, destination: Path) -> str` — returns the formatted checklist string for chat
  - `check_brief_consistency(shots: list[Path], bgm: Path, brief_text: str) -> list[str]` — returns list of inconsistency descriptions (empty = consistent); calls `ms see` on first+last frames, `ms video beats` on BGM
  - `inspect_thumbnail(url: str, out_dir: Path) -> Path` — downloads thumbnail URL to `out_dir`, returns local path
  - `inspect_audio_preview(file: Path, out_dir: Path, duration: float = 10.0) -> dict` — trims 10 s clip, runs `ffprobe` for metadata, returns `{duration, sample_rate, channels, bpm_estimate}`

- [ ] **Step 1: Write failing tests in `tests/test_acquire.py`**

  Cover:
  - `acquire_asset` returns `source="library"` when `search_assets` returns a match (patch library); returns `None` and prints manual checklist when no library match and external fetch is patched to fail.
  - `manual_request_message` output contains the destination path, license, and source URL; checklist format includes `- [ ]`.
  - `check_brief_consistency` returns empty list when brief mentions "action" and mock `ms see` output describes bright colors; returns a non-empty list when brief says "dark horror" and mock palette is saturated warm.
  - `inspect_thumbnail` downloads a URL to `tmp_path`; file exists after call (patch `requests.get` or `urllib.request`).
  - `inspect_audio_preview` trims a real 1 s sine-wave wav to 10 s max and returns valid metadata dict.

- [ ] **Step 2: Run tests to confirm they fail**

  Run: `.venv/bin/python -m pytest tests/test_acquire.py -q`
  Expected: `ModuleNotFoundError: No module named 'media_studio.acquire'`

- [ ] **Step 3: Implement `src/media_studio/acquire.py`**

  `acquire_asset` pipeline order: (1) `library.search_assets(type=type, tags=tags)` → if results, return first (ranked by `used_in` count desc). (2) External: for video/image types call `inspect_thumbnail`; for audio/font/preset download to staging dir then inspect. (3) If external succeeds and inspection passes, call `library.register_asset` + `library.link_asset_to_project` → return `source="external"`. (4) If external fails → return `None` (caller emits `manual_request_message`).

  `check_brief_consistency`: extract first frame of each shot (`ffmpeg -vframes 1 -ss 0`), run `ms see` on the frames, run `ms video beats` on BGM for tempo. Compare dominant color palette description from `ms see` output with keywords in `brief_text` using simple keyword matching (e.g., if brief contains "dark" or "horror" but `ms see` output contains "bright" or "saturated" → flag inconsistency).

- [ ] **Step 4: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_acquire.py -q && ~/.local/bin/ruff check src/ tests/ && ~/.local/bin/ruff format --check src/media_studio/acquire.py tests/test_acquire.py`
  Expected: all pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/media_studio/acquire.py src/media_studio/cli.py tests/test_acquire.py
  git commit -m "feat(acquire): asset acquisition pipeline with library check and consistency check"
  ```

---

### Task 3: Beat-Synced Velocity Ramp (`ms video beatramp`)

**Files:**
- Create: `src/media_studio/beatramp.py`
- Modify: `src/media_studio/cli.py` (new `beatramp` subparser after `beatfx` block; handler `cmd_video_beatramp` after `cmd_video_beatfx`)
- Test: `tests/test_beatramp.py`

**Interfaces:**
- Consumes: `ramp.apply_fast_slow_fast`, `ramp.apply_slow_snap`, `ramp.apply_flash_freeze`; `beatfx.auto_schedule` pattern (re-implement with ramp-aware min-gap); `ingest.probe_media`.
- Produces:
  - `BEATRAMP_KINDS: frozenset[str]` = `{"fast_slow_fast", "slow_snap", "flash_freeze"}`
  - `TRIGGER_MAP: dict[str, str]` = `{"downbeat": "fast_slow_fast", "snare": "slow_snap", "build_peak": "flash_freeze"}`
  - `MIN_GAP_S: float` = `0.5`
  - `class BeatRampError(RuntimeError)`
  - `@dataclass(frozen=True) class RampEvent`: `time: float`, `kind: str`, `trigger: str`
  - `@dataclass(frozen=True) class BeatRampResult`: `source: Path`, `destination: Path`, `events: tuple[RampEvent, ...]`, `fps: float`, `resolution: str`, `size_bytes: int`
  - `schedule_ramp_events(beats: dict, *, offset: float, duration: float, map: dict[str, str] = TRIGGER_MAP, max_per_second: float = 1.0) -> list[RampEvent]`
  - `apply_ramp_events(source: Path, events: list[RampEvent], destination: Path | None = None, *, fps: float = 60.0, resolution: str = "1920x1080", codec: str = "h264") -> BeatRampResult`

- [ ] **Step 1: Write failing tests in `tests/test_beatramp.py`**

  Cover:
  - `schedule_ramp_events` with synthetic beats.json (downbeat at 0.0, snare at 0.3, build_peak at 2.0, offset=0, duration=4): only downbeat and build_peak emitted (snare at 0.3 < 0.5 from downbeat at 0.0 → collision; downbeat has higher energy → wins); kinds match TRIGGER_MAP.
  - Collision tie-break: when energies equal, downbeat wins over snare (deterministic).
  - `map` override: `{"downbeat": "slow_snap"}` → emitted event has kind `"slow_snap"`.
  - `apply_ramp_events` real-ffmpeg e2e: 2 s 1920x1080 testsrc2 clip + one `fast_slow_fast` event at t=0.5; destination exists, h264, duration within 0.3 of 2.0 s.
  - Resolution floor: `resolution="1280x720"` → `BeatRampError` matching `r"below the 1920x1080 minimum floor"`.
  - Zero events → single copy-encode pass, no crash.

- [ ] **Step 2: Run tests to confirm they fail**

  Run: `.venv/bin/python -m pytest tests/test_beatramp.py -q`
  Expected: `ModuleNotFoundError: No module named 'media_studio.beatramp'`

- [ ] **Step 3: Implement `src/media_studio/beatramp.py`**

  `schedule_ramp_events`: identify trigger types from beats.json — downbeats from `beats["downbeats"]`; snares = beats in `beats["beats"]` not in downbeats that fall inside a high-energy section; build_peaks = last beat before each low→high section transition. Merge all into candidates `[(time, kind, trigger, energy)]`. Apply energy-ranked greedy selection with `MIN_GAP_S = 0.5` (identical logic to `beatfx.auto_schedule` but gap is 0.5 s not 0.2 s). Re-sort ascending.

  `apply_ramp_events`: probe source, validate resolution floor, for each event call the matching `ramp.*` function (which writes a new file); chain outputs sequentially; final pass re-encodes to destination codec. Clean staging dir in `finally`.

- [ ] **Step 4: Wire CLI subcommand**

  ```
  ms video beatramp <source> --beats PATH --offset SECONDS
                    [--map JSON] [--max-per-second 1] [--out PATH]
                    [--fps 60] [--resolution 1920x1080] [--codec {h264,prores}]
  ```

  `cmd_video_beatramp`: load beats.json, call `schedule_ramp_events`, call `apply_ramp_events`, print event list and destination. Catch `(BeatRampError, IngestError, json.JSONDecodeError)` → stderr + `EXIT_FAILURE`.

- [ ] **Step 5: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_beatramp.py -q && ~/.local/bin/ruff check src/ tests/ && ~/.local/bin/ruff format --check src/media_studio/beatramp.py tests/test_beatramp.py src/media_studio/cli.py`
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/media_studio/beatramp.py src/media_studio/cli.py tests/test_beatramp.py
  git commit -m "feat(video): beat-synced velocity ramp via ms video beatramp"
  ```

---

### Task 4: EDL Cut-Plan Tool (`ms video edl`)

**Files:**
- Create: `src/media_studio/edl.py`
- Modify: `src/media_studio/cli.py` (new `ms video edl` subcommand group: `generate`, `render`, `preview`)
- Test: `tests/test_edl.py`

**Interfaces:**
- Consumes: `beatfx.auto_schedule`, `beatramp.schedule_ramp_events`, `lut.apply_lut` (from Task 5; edl.py imports lazily — if `lut` not available, `color_lut` field is ignored with a warning), `ingest.probe_media`, `audio.analyze_rhythm`.
- Produces:
  - `EDL_VERSION: int` = `1`
  - `class EdlError(RuntimeError)`
  - `@dataclass class EdlCut`: `id: str`, `shot: str`, `in_ms: int`, `out_ms: int`, `music_offset_ms: int`, `beatfx: list[str]`, `ramp: list[str]`, `color_lut: str | None`, `notes: str`
  - `@dataclass class EdlDocument`: `version: int`, `music: str`, `beats_json: str`, `total_duration_s: float`, `cuts: list[EdlCut]`
  - `load_edl(path: Path) -> EdlDocument`
  - `save_edl(doc: EdlDocument, path: Path) -> None`
  - `generate_edl(shots: list[Path], beats_json: Path, music: Path, *, total_duration_s: float, out: Path) -> EdlDocument`
  - `render_edl(doc: EdlDocument, *, out: Path, fps: float = 60.0, codec: str = "h264") -> Path`
  - `preview_edl_cut(doc: EdlDocument, cut_id: str, *, out: Path, fps: float = 60.0) -> Path`

- [ ] **Step 1: Write failing tests in `tests/test_edl.py`**

  Cover:
  - `load_edl` / `save_edl` round-trip: schema preserves all fields including empty `beatfx`/`ramp` lists and `color_lut: null`.
  - `generate_edl` with 3 shots (1 s each) and `total_duration_s=3.0`: emits 3 cuts with consecutive `music_offset_ms`; each cut duration within beat-density range (0.2–2.0 s); all shots covered.
  - `render_edl` real-ffmpeg e2e: 3 cuts from testsrc2 clips (1 s each), no beatfx/ramp/color_lut; output h264, duration within 0.5 of 3.0 s, file exists.
  - `render_edl` with one cut having `beatfx: ["flash@0.2"]`: output still valid (pins non-empty beatfx path).
  - `preview_edl_cut` renders only the specified cut_id; output duration matches that cut's `(out_ms - in_ms) / 1000` within 0.3 s.
  - `load_edl` on malformed JSON → `EdlError` matching `r"invalid EDL"`.
  - `render_edl` on a cut with `in_ms > out_ms` → `EdlError` matching `r"in_ms must be less than out_ms"`.

- [ ] **Step 2: Run tests to confirm they fail**

  Run: `.venv/bin/python -m pytest tests/test_edl.py -q`
  Expected: `ModuleNotFoundError: No module named 'media_studio.edl'`

- [ ] **Step 3: Implement `src/media_studio/edl.py`**

  `generate_edl`: probe each shot for duration; greedily assign shot windows to fill `total_duration_s` using `beats.json` section energy to target cut density (high-energy section → 0.2–0.8 s, build → 0.8–1.5 s, verse/low → 1–2 s); populate `beatfx` from `beatfx.auto_schedule` and `ramp` from `beatramp.schedule_ramp_events` using `music_offset_ms / 1000` as `offset`.

  `render_edl`: for each cut — trim clip to `[in_ms, out_ms]` via ffmpeg `-ss`/`-to`; apply beatfx effects (parse shorthand `"kind@time_s"` → `ScheduledEffect`); apply ramp events (parse shorthand → `RampEvent`); apply LUT if `color_lut` is set (lazy import `lut.apply_lut`); write cut intermediate. After all cuts: concat using ffmpeg concat demuxer; mix music track with `amix`/`adelay` placing each cut at its `music_offset_ms`. Clean staging in `finally`.

- [ ] **Step 4: Wire CLI subcommand group**

  ```
  ms video edl generate --beats PATH --shots SHOT [SHOT...] [--music PATH]
                         [--duration SECONDS] [--out edl.json]
  ms video edl render <edl.json> [--out PATH] [--fps 60] [--codec {h264,prores}]
  ms video edl preview <edl.json> --cut ID [--out PATH] [--fps 60]
  ```

  Each handler catches `(EdlError, IngestError, json.JSONDecodeError)` → stderr + `EXIT_FAILURE`. `generate` prints a summary table: `| ID | Shot | In | Out | Dur | Effects |`.

- [ ] **Step 5: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_edl.py -q && ~/.local/bin/ruff check src/ tests/ && ~/.local/bin/ruff format --check src/media_studio/edl.py tests/test_edl.py src/media_studio/cli.py`
  Expected: all pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/media_studio/edl.py src/media_studio/cli.py tests/test_edl.py
  git commit -m "feat(video): EDL cut-plan tool with generate, render, preview"
  ```

---

### Task 5: Color LUT Grading (`ms video lut`)

**Files:**
- Create: `src/media_studio/lut.py`
- Create: `src/media_studio/data/luts/cinematic-teal-orange.cube`
- Create: `src/media_studio/data/luts/anime-vibrant.cube`
- Create: `src/media_studio/data/luts/horror-bleach.cube`
- Create: `src/media_studio/data/luts/warm-sunset.cube`
- Create: `src/media_studio/data/luts/cool-blue.cube`
- Modify: `src/media_studio/cli.py` (new `ms video lut` subcommand group: `search`, `preview`, `apply`)
- Test: `tests/test_lut.py`

**Interfaces:**
- Consumes: `library.search_assets`, `library.register_asset`, `ingest.probe_media`; ffmpeg `lut3d` filter; ImageMagick `convert` for `hstack` (or ffmpeg `hstack`).
- Produces:
  - `BUNDLED_LUTS_DIR: Path` = `Path(__file__).parent / "data" / "luts"`
  - `class LutError(RuntimeError)`
  - `def apply_lut(source: Path, lut: Path, destination: Path, *, codec: str = "h264") -> Path` — converts to yuv420p before lut3d, back to target pix_fmt for prores
  - `def preview_lut(lut: Path, reference: Path, out: Path) -> Path` — extracts one frame, applies LUT, hstacks original + graded, writes PNG
  - `def search_luts(query: str = "", *, library_root: Path = LIBRARY_ROOT) -> list[Path]` — returns bundled LUTs + library preset entries tagged `lut`

- [ ] **Step 1: Create bundled LUT files**

  Generate five minimal but valid `.cube` files (LUT_3D_SIZE 17, identity transform as the base, with simple color-channel offsets for each style). Write them to `src/media_studio/data/luts/`. They need not be production-quality; they must be syntactically valid `.cube` files that ffmpeg `lut3d` accepts.

  Verify one with: `ffmpeg -f lavfi -i testsrc2=size=1920x1080:duration=1 -vf lut3d=<path> -frames:v 1 /tmp/opencode/lut-test.png` — exit 0.

- [ ] **Step 2: Write failing tests in `tests/test_lut.py`**

  Cover:
  - `apply_lut` with bundled `cinematic-teal-orange.cube` on a 1 s testsrc2 clip: output exists, h264, duration within 0.2 s.
  - `apply_lut` with a ProRes source (yuv444p10le): output is valid prores (codec `prores`) without color corruption (ffprobe pix_fmt is `yuv444p10le`).
  - `preview_lut` produces a PNG at `out` path; PNG width is double the reference frame width (hstack).
  - `search_luts` with empty library returns exactly 5 paths (bundled LUTs).
  - `apply_lut` with a `.txt` file → `LutError` matching `r"\.cube"`.
  - `apply_lut` with a missing source → `LutError` matching `r"no such file"`.

- [ ] **Step 3: Run tests to confirm they fail**

  Run: `.venv/bin/python -m pytest tests/test_lut.py -q`
  Expected: `ModuleNotFoundError: No module named 'media_studio.lut'`

- [ ] **Step 4: Implement `src/media_studio/lut.py`**

  `apply_lut`: validate `.cube` extension; probe source for pix_fmt and codec; build ffmpeg pipeline — input → `format=yuv420p` → `lut3d=<lut>` → output encoder (for h264: `libx264 -crf 18 -pix_fmt yuv420p`; for prores: `format=yuv444p10le` before `prores_ks -profile:v 4444 -pix_fmt yuv444p10le`).

  `preview_lut`: extract frame 0 from reference with `ffmpeg -vframes 1`; apply LUT to a copy; use ffmpeg `hstack` filter to combine original and graded side by side; write PNG.

- [ ] **Step 5: Wire CLI subcommand group**

  ```
  ms video lut search [--tags TAG...] [--style STYLE]
  ms video lut preview <lut_file_or_library_id> <reference_shot> [--frame 0] [--out PATH]
  ms video lut apply <source> --lut <path_or_library_id> [--out PATH] [--codec {h264,prores}]
  ```

  `inspect` resolves library IDs: if the argument matches a UUID in the catalog, use `entry.file_path`; otherwise treat as a file path.

- [ ] **Step 6: Run tests and lint**

  Run: `.venv/bin/python -m pytest tests/test_lut.py -q && ~/.local/bin/ruff check src/ tests/ && ~/.local/bin/ruff format --check src/media_studio/lut.py tests/test_lut.py src/media_studio/cli.py`
  Expected: all pass.

- [ ] **Step 7: Commit**

  ```bash
  git add src/media_studio/lut.py src/media_studio/data/luts/ src/media_studio/cli.py tests/test_lut.py
  git commit -m "feat(video): color LUT grading with bundled presets and ms video lut commands"
  ```

---

### Task 6: media-create Skill Update + README

**Files:**
- Modify: `~/.agents/skills/media-create/SKILL.md` (Stage 0 addition, Stage 4 rewrite, Gate 1 upgrade, CLI reference additions)
- Modify: `README.md` in `media-studio` repo (quickstart lines for new commands)

**Interfaces:**
- Consumes: all CLI surfaces from Tasks 1–5 (`ms library`, `ms video beatramp`, `ms video edl generate/render/preview`, `ms video lut search/preview/apply`).
- Produces: workflow documentation only; no code.

- [ ] **Step 1: Verify real CLI help strings match the plan**

  Run:
  ```bash
  .venv/bin/ms library --help
  .venv/bin/ms video beatramp --help
  .venv/bin/ms video edl --help
  .venv/bin/ms video lut --help
  ```
  Document any flag name differences between help output and what the plan specified. The SKILL.md must reference the actual flags, not the plan's.

- [ ] **Step 2: Update `~/.agents/skills/media-create/SKILL.md`**

  Three changes (formal technical English, no emojis):
  1. Insert **Stage 0 — Source Handoff & Consistency Check** before Stage 1: user provides primary footage + BGM in `project.yaml` under `assets.primary`; agent runs `ms see` on first+last frames of each shot and `ms video beats` on BGM; if brief vs footage inconsistency found, flag with specific evidence and ask user to revise brief or footage before proceeding.
  2. Rewrite **Stage 4 — Asset Acquisition**: pipeline is now library check → external search + visual inspect → download + register → manual fallback checklist; cite exact commands (`ms library search`, `ms library register`).
  3. Gate 1 upgrade: for beat-synced video, the approval package must include the EDL JSON (`ms video edl generate` output) as well as the effect schedule.
  4. CLI reference additions under Video: `ms video beatramp`, `ms video edl generate/render/preview`, `ms video lut search/preview/apply`; under Library: `ms library list/search/inspect/register/forget`.

- [ ] **Step 3: Update `README.md` quickstart block**

  After the existing `ms video beats` / `ms video beatfx` lines add:
  ```
  ms video beatramp shot.mp4 --beats beats.json --offset 3.5
  ms video edl generate --beats beats.json --shots shot*.mp4 --music music.wav --out edl.json
  ms video edl render edl.json --out final.mp4
  ms video lut search --style cinematic
  ms video lut apply final.mp4 --lut cinematic-teal-orange.cube
  ```

- [ ] **Step 4: Verify counts and commit both repos**

  Run:
  ```bash
  grep -c "video beatramp\|video edl\|video lut\|ms library" ~/.agents/skills/media-create/SKILL.md
  ```
  Expected: >= 8 (each command appears at least once in Rhythm & Density and once in CLI reference).

  ```bash
  cd ~/.agents
  git add skills/media-create/SKILL.md
  git commit -m "feat(skills): add Phase 2 workflow stages and CLI references to media-create"

  cd /home/superbypassudin/.clone/Github/SuperBypassUdinnn/media-studio
  git add README.md
  git commit -m "docs: add Phase 2 quickstart examples to README"
  ```

  Note: `~/.agents` is a scoped repo — run `session_files_status` before committing.

---

## Execution Notes

- Task order is load-bearing: Task 4 (`edl.py`) lazily imports Task 5 (`lut.py`); if Task 5 is not yet present, `edl render` skips LUT application with a warning (do not error). This allows Task 4 to be tested independently.
- Task 2 (`acquire.py`) does not block Tasks 3–5; it is used programmatically by the media-create skill, not by the other modules.
- The five bundled `.cube` files in Task 5 Step 1 are generated once and committed; they do not need to be beautiful LUTs, only syntactically valid.
- Final whole-branch review (per subagent-driven-development) covers all 6 tasks from base commit to HEAD on the most capable model.
