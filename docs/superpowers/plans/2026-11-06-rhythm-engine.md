# Rhythm Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AMV output dynamic by replacing the synthetic 128 BPM beat grid with real rhythm analysis (librosa) and adding beat-mapped micro-effects (camera shake, punch-in, flash, RGB split, hue shift) plus the workflow knowledge in the media-create skill.

**Architecture:** Two new capabilities feed one workflow. `analyze_rhythm()` in `audio.py` produces a `beats.json` artifact (bpm, beats, downbeats, energy windows, sections); the new `beatfx.py` module turns that artifact plus a shot's music offset into a schedule of window-based ffmpeg effects applied in sequential high-quality passes. The media-create skill gains the Rhythm & Density rules (cut density targets, effect mapping, EDL-first Gate 1) that tell the agent when and how densely to use both.

**Tech Stack:** librosa >= 0.11 (optional extra `rhythm`), ffmpeg filter graphs (`crop`, `zoompan`, `drawbox`, `split/overlay`, `hue` with `enable` windows), argparse CLI, pytest.

**Spec:** In-chat design approved 2026-11-06 ("Rhythm Engine untuk media-create"); friction evidence in `docs/notes/prompt-to-result-friction.md` (synthetic 128 BPM grid, sparse impact moments).

## Global Constraints

- librosa is an OPTIONAL extra: `rhythm = ["librosa>=0.11"]` in `pyproject.toml`; install into the venv with `.venv/bin/python -m pip install "librosa>=0.11"` (venv `bin/pip` shebang is broken — always `python -m pip`).
- `analyze_rhythm` has NO silent fallback: missing librosa raises `AudioError` whose message contains `librosa` and the install hint `pip install -e ".[rhythm]"`. `detect_beats` keeps its existing fallback behavior untouched.
- All numeric values written to `beats.json` must be plain Python floats (numpy scalars are not JSON serializable).
- `beatfx` effect kinds are exactly `{shake, punch, flash, rgb, hue}`; unknown kinds raise `BeatFxError` listing the valid kinds.
- `beatfx` applies effects in SEQUENTIAL ffmpeg passes with intermediate `-c:v libx264 -crf 12 -preset fast`; final pass uses the requested codec (h264: `libx264 -crf 18 -pix_fmt yuv420p`; prores: `prores_ks -profile:v 4444 -pix_fmt yuv444p10le`).
- `beatfx` enforces the 1920x1080 resolution floor exactly like `parallax.py` (reject smaller with a message containing "below the 1920x1080 minimum floor"); default `--fps 60`.
- Skill text (`~/.agents/skills/media-create/SKILL.md`) is formal technical English, no emojis, and its CLI references must match real `ms` subcommands.
- Test hygiene: `pytest.raises(match=...)` patterns use raw strings; piped checks must not hide exit status; run `ruff check` and `ruff format` before every commit.

## Review Focus

- numpy scalar leakage into JSON — a `beats.json` containing `np.float32` values crashes `json.dump`; Task 1's tests must assert plain-float round-tripping.
- Effect windows at clip boundaries — a window extending past the clip end must be clamped by the filter's own evaluation, not crash ffmpeg; Task 2's e2e includes an effect starting 0.05 s before the end.
- librosa first-call JIT latency (numba) — the real-analysis test allows generous time and runs on a short (4 s) file.
- `test_detect_beats_fallback_ffmpeg` breaks once librosa is installed in the shared venv (it silently takes the librosa path) — Task 1 fixes it BEFORE installing librosa, so the suite stays green between tasks.
- Overlapping effect windows — sequential passes compose correctly by construction; Task 2's e2e includes two overlapping windows to pin this.

---

### Task 1: Rhythm analysis (`ms video beats`)

**Files:**
- Modify: `pyproject.toml` (optional-dependencies block after the `sam2` entry)
- Modify: `src/media_studio/audio.py` (new `analyze_rhythm` after `detect_beats`)
- Modify: `src/media_studio/cli.py` (new `beats` subparser after the `beat-sync` block at ~L565; handler `cmd_video_beats` after `cmd_video_beat_sync` at ~L2360)
- Modify: `tests/test_ai_motion.py` (fix `test_detect_beats_fallback_ffmpeg` at L35)
- Test: `tests/test_rhythm.py` (new)

**Interfaces:**
- Consumes: nothing new (subprocess, pathlib only at module level; librosa imported lazily).
- Produces: `audio.analyze_rhythm(audio_path: Path, *, window: float = 0.5) -> dict` returning exactly `{"bpm": float, "beats": list[float], "downbeats": list[float], "energy": list[float], "window": float, "duration": float, "sections": list[dict]}` where each section dict is `{"start": float, "end": float, "level": "high" | "low"}`. CLI writes this dict as JSON.

- [ ] **Step 1: Fix the fallback test FIRST (before librosa exists)**

In `tests/test_ai_motion.py`, add `import sys` to the imports and change `test_detect_beats_fallback_ffmpeg` to force the ImportError path so it survives librosa being installed later:

```python
def test_detect_beats_fallback_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "librosa", None)
    audio_file = tmp_path / "beat.wav"
    # ... existing body unchanged ...
```

- [ ] **Step 2: Run the fallback test to verify it still passes**

Run: `cd /home/superbypassudin/.clone/Github/SuperBypassUdinnn/media-studio && .venv/bin/python -m pytest tests/test_ai_motion.py -q`
Expected: all pass (`None` in `sys.modules` makes `import librosa` raise ImportError).

- [ ] **Step 3: Add the `rhythm` extra to pyproject.toml**

After the `sam2` line in `[project.optional-dependencies]`:

```toml
# Real rhythm analysis for ms video beats (beat/downbeat/energy detection).
rhythm = ["librosa>=0.11"]
```

Then install: `.venv/bin/python -m pip install "librosa>=0.11"`.

- [ ] **Step 4: Write failing tests in tests/test_rhythm.py**

Test cases (exact names and assertions):
- `test_analyze_rhythm_requires_librosa(tmp_path, monkeypatch)`: `monkeypatch.setitem(sys.modules, "librosa", None)`; a dummy file at `tmp_path / "x.wav"`; `pytest.raises(AudioError, match=r"librosa")`.
- `test_analyze_rhythm_click_track` — guarded by `NEEDS_LIBROSA = pytest.mark.skipif(_importlib.util.find_spec("librosa") is None, reason="librosa not installed")` (use `importlib.util` at module top). Build a 4 s 120 BPM click track with `numpy` + the stdlib `wave` module (click = 30 ms 1000 Hz burst every 0.5 s over a quiet 440 Hz sine bed, 44100 Hz mono 16-bit). Assert: `0.9 <= result["bpm"] / 120 <= 1.1` OR beats count within `3.5 <= len(beats) <= 9` (beat_track on clicks is reliable; accept either signal), `len(result["downbeats"]) <= len(result["beats"])`, `set(result["downbeats"]) <= set(result["beats"])`, `abs(result["duration"] - 4.0) < 0.2`, `len(result["sections"]) >= 1`, and every value in `beats + energy + downbeats` is a `float` (not `np.floating`) — plain-Python check via `type(v) is float`.
- `test_beats_json_roundtrip` — `json.dumps(json.loads(json.dumps(result)))` succeeds (pins the plain-float constraint).
- `test_cli_beats_writes_json(tmp_path)`: real 4 s click wav; run `main(["video", "beats", str(wav), "--out", str(out)])`; exit code 0; file exists; parsed JSON has keys `bpm`, `beats`, `downbeats`, `energy`, `sections`.

- [ ] **Step 5: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_rhythm.py -q`
Expected: FAIL/ERROR (`AttributeError`/`ImportError` — `analyze_rhythm` not defined; CLI test fails on unknown subcommand).

- [ ] **Step 6: Implement `analyze_rhythm` in src/media_studio/audio.py**

Behavior contract (implementation details are the implementer's):
- Lazy `import librosa` inside the function; `ImportError`/`ModuleNotFoundError` → `AudioError(f"librosa is not installed; install with: pip install -e \".[rhythm]\" ({error})")`.
- `librosa.load(path, sr=None, mono=True)`; `librosa.beat.beat_track(y=y, sr=sr)` for tempo+beats; `librosa.feature.rms` with `hop_length=int(window*sr)`, `frame_length=2*hop_length` for the energy windows; section threshold = 60th percentile of the energy list; contiguous runs above → `level: "high"`, runs at/below → `level: "low"`; section boundaries in seconds via window index times.
- Downbeat heuristic (pinned): assume 4/4 — find the beat index nearest the highest-energy window start; `downbeats = beats[phase::4]` for that index `phase`.
- Cast every numeric to Python `float`/`int` before returning (`float(np_value)`); round times to 4 decimals.

- [ ] **Step 7: Wire the CLI subcommand**

Subparser (after `beat_p.set_defaults(func=cmd_video_beat_sync)`):

```
ms video beats <audio> [--out PATH] [--window 0.5]
```

`cmd_video_beats`: validate the audio file exists; call `analyze_rhythm`; write JSON to `--out` (default: `<audio-stem>.beats.json` beside the audio); print one line `bpm <bpm>, <n> beats (<m> downbeats), <k> high-energy section(s)` and the output path; catch `AudioError` → `error: ...` to stderr + `EXIT_FAILURE`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rhythm.py tests/test_ai_motion.py -q`
Expected: all pass (librosa path exercised for real on the click track; fallback test still green).

- [ ] **Step 9: Lint, format, and e2e on real music**

Run: `~/.local/bin/ruff check src/ tests/ && ~/.local/bin/ruff format src/media_studio/audio.py src/media_studio/cli.py tests/test_rhythm.py tests/test_ai_motion.py`
E2E: `.venv/bin/ms video beats /home/superbypassudin/Projects/amv-demonslayer-3d/assets/audio/music-battle.wav --out /tmp/opencode/beats-battle.json` — exit 0; inspect the JSON: bpm plausible for the orchestral track, non-zero downbeats. Record the detected bpm in the report.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml src/media_studio/audio.py src/media_studio/cli.py tests/test_rhythm.py tests/test_ai_motion.py
git commit -m "feat(audio): real rhythm analysis with librosa and ms video beats"
```

---

### Task 2: Beat-mapped micro-effects (`ms video beatfx`)

**Files:**
- Create: `src/media_studio/beatfx.py`
- Modify: `src/media_studio/cli.py` (new `beatfx` subparser after the `beats` block; handler `cmd_video_beatfx` after `cmd_video_beats`)
- Test: `tests/test_beatfx.py` (new)

**Interfaces:**
- Consumes: `ingest.probe_media` (duration/fps), `audio.analyze_rhythm` output shape (`beats`, `energy`, `window` keys) via the beats.json file — Task 2 reads the JSON, never calls librosa.
- Produces:
  - `BEATFX_KINDS: frozenset[str]` = `{shake, punch, flash, rgb, hue}`
  - `class BeatFxError(RuntimeError)`
  - `@dataclass(frozen=True) class ScheduledEffect`: `time: float`, `kind: str`, `params: dict`
  - `@dataclass(frozen=True) class BeatFxResult`: `source: Path`, `destination: Path`, `effects: tuple[ScheduledEffect, ...]`, `passes: int`, `fps: float`, `resolution: str`, `size_bytes: int`
  - `parse_schedule(payload: list[dict]) -> list[ScheduledEffect]` — validates kind/time/params, sorts by time
  - `auto_schedule(beats: dict, *, offset: float, duration: float, max_per_second: int = 2) -> list[ScheduledEffect]`
  - `apply_effects(source: Path, destination: Path | None = None, effects: list[ScheduledEffect] = [], *, fps: float = 60.0, resolution: str = "1920x1080", codec: str = "h264") -> BeatFxResult`

- [ ] **Step 1: Write failing tests in tests/test_beatfx.py**

Classes and pinned assertions:
- `TestParseSchedule`: valid `[{time: 1.0, kind: "shake", params: {}}]` round-trips sorted by time; unknown kind raises `BeatFxError` with message containing `rgb`; negative time raises `BeatFxError` matching `r"non-negative"`; empty list is valid (zero effects).
- `TestFilters`: import the private builders and assert substrings — shake: `sin(`, `between(t,`; punch: `zoompan`, `if(between(`; flash: `drawbox`, `color=white`; rgb: `split=3`, `overlay`; hue: `hue=h=`.
- `TestAutoSchedule`: synthetic `{"beats": [0.0, 0.5, 1.0, ..., 3.5], "energy": [8 windows], "window": 0.5}` with `duration=4.0, offset=0.0, max_per_second=2` → exactly 8 effects; min gap between consecutive times >= 0.2 s; kinds cycle through `[punch, shake, flash, ...]` (assert the first is `punch`); `offset=2.0` shifts times into `[0.0, 2.0)` local range; `offset=10.0` (outside) raises `BeatFxError` matching `r"no beats fall inside"`.
- `TestApplyEffects` (real ffmpeg, marked like `TestEndToEnd` in `tests/test_video_cutout.py`): build a 1 s 1920x1080 testsrc2 clip (`libx264`); effects `[{time: 0.2, kind: "flash"}, {time: 0.5, kind: "shake"}, {time: 0.93, kind: "punch", params: {"zoom": 1.08, "duration": 0.15}}]` (last one pins the near-end boundary case); assert destination exists, `size_bytes > 0`, ffprobe codec is `h264`, duration within 0.2 of 1.0; rerun with overlapping windows `[{time: 0.3, kind: "hue"}, {time: 0.4, kind: "flash"}]` → also succeeds (pins overlap composition).
- `TestValidation`: resolution `1280x720` raises `BeatFxError` matching `r"below the 1920x1080 minimum floor"`; malformed resolution raises; unknown codec raises listing `h264`/`prores`; missing source raises with `no such file`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_beatfx.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'media_studio.beatfx'`).

- [ ] **Step 3: Implement src/media_studio/beatfx.py**

Module docstring explains the recipe lineage (beat-synced-edit cookbook, impact.py chromatic) and the sequential-pass design. Pinned behavior:
- Resolution parsing/floor: same `re` pattern and floor semantics as `parallax.py` (`MIN 1920x1080`, message contains "below the 1920x1080 minimum floor").
- Effect windows: each effect gets `[start, start + params.get("duration", default_for_kind)]`; defaults pinned — shake 0.25 s (amp_px 12, freq 24), punch 0.3 s (zoom 1.08), flash 2 frames converted to seconds via `2/fps`, rgb 0.2 s (shift_px 6), hue 0.4 s (degrees 20).
- Filter builders (private, unit-tested): shake = animated `crop` jitter with `enable='between(t,s,e)'`; punch = `zoompan` with `z='if(between(in,sf,ef),1+ZOOM*min(1,(in-sf)/frames),1)'`, `d=1`, `s=WxH`, `fps=FPS` (frame indices from time*fps); flash = full-frame `drawbox` `color=white:t=fill:enable=`; rgb = the `split=3` channel-split chain from `impact.py`'s chromatic recipe with `enable` on the overlays; hue = `hue=h=DEG:enable=`.
- `apply_effects`: parse resolution, probe source (`probe_media`, rewrap `IngestError` as `BeatFxError`), sort effects, then one ffmpeg pass per effect writing intermediates under `/tmp/opencode/beatfx-<stem>-<pid>/` with `-c:v libx264 -crf 12 -preset fast` and `-c:a copy` when the source has audio (probe says so); final pass writes the destination with the requested codec (h264: `libx264 -crf 18 -pix_fmt yuv420p`; prores: `prores_ks -profile:v 4444 -pix_fmt yuv444p10le`); clean the intermediates dir in a `finally`; zero effects → single copy-encode pass to the destination (still validates inputs).
- `auto_schedule` policy (pinned, deterministic): local beats = `[b - offset for b in beats if offset <= b < offset + duration]`; empty → `BeatFxError` matching "no beats fall inside"; map each local beat to the energy of the window containing it; sort candidates by energy descending; greedily take candidates whose distance to every already-taken time >= 0.2 s, up to `int(duration * max_per_second)`; re-sort taken times ascending; assign kinds by cycling `["punch", "shake", "flash"]` (params defaults).
- Destination: `None` → `<stem>.beatfx.mp4` beside source; dir → inside it; explicit file passthrough (mirror `_video_destination` in `video_cutout.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_beatfx.py -q`
Expected: all pass.

- [ ] **Step 5: Wire the CLI subcommand**

```
ms video beatfx <source> --beats PATH (--auto | --schedule PATH) [--offset 0.0] [--out PATH] [--fps 60] [--resolution 1920x1080] [--codec {h264,prores}] [--max-per-second 2]
```

`cmd_video_beatfx`: load beats.json (`json.loads`; malformed → error), build the schedule (`--auto` from beats+offset+probed duration, or `--schedule` via `parse_schedule`), call `apply_effects`, print `applied <n> effect(s)` then one line per effect `<mm:ss> <kind>`, then the destination and size in MB; catch `(BeatFxError, IngestError, json.JSONDecodeError)` → stderr + `EXIT_FAILURE`.

- [ ] **Step 6: Run tests and lint to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_beatfx.py tests/test_cli.py -q && ~/.local/bin/ruff check src/ tests/ && ~/.local/bin/ruff format src/media_studio/beatfx.py src/media_studio/cli.py tests/test_beatfx.py`
Expected: all pass, ruff clean.

- [ ] **Step 7: E2E on a real Demon Slayer shot**

`.venv/bin/ms video beatfx /home/superbypassudin/Projects/amv-demonslayer-3d/assets/video/shot2-tengen-1080p60.mp4 --beats /tmp/opencode/beats-battle.json --offset 3.5 --auto --out /tmp/opencode/beatfx-e2e.mp4` — exit 0; verify with `.venv/bin/ms see /tmp/opencode/beatfx-e2e.mp4` and READ the produced sheet; report the effect list and sheet path.

- [ ] **Step 8: Commit**

```bash
git add src/media_studio/beatfx.py src/media_studio/cli.py tests/test_beatfx.py
git commit -m "feat(video): beat-mapped micro-effects via ms video beatfx"
```

### Task 3: Skill upgrade and README (documentation)

**Files:**
- Modify: `~/.agents/skills/media-create/SKILL.md` (separate git repo — commit there)
- Modify: `README.md` (media-studio quickstart block)

**Interfaces:**
- Consumes: the CLI surfaces from Tasks 1–2 (`ms video beats`, `ms video beatfx` with `--beats/--auto/--offset`).
- Produces: workflow knowledge only; no code.

- [ ] **Step 1: Update media-create SKILL.md**

Additions (formal technical English, no emojis, commands must match the real CLI):
1. New stage subsection **Rhythm & Density** (insert after Stage 4 — Asset Acquisition):
   - REQUIRED before any beat-synced timeline assembly: `ms video beats <music> --out beats.json`; report the detected bpm and high-energy sections in the plan.
   - Cut-density targets: drops 0.2–0.8 s per cut, builds 0.8–1.5 s, verses 1–2 s; an AMV whose average cut exceeds 2 s reads as static.
   - Effect mapping table: strongest onsets in high-energy sections → `punch` or `flash`; downbeats → `shake`; section transitions → `speed-ramp` or `parallax` movement; every hit in a climax gets at least one micro-effect.
   - Per-shot application: `ms video beatfx <shot> --beats beats.json --offset <shot music offset> --auto` (schedule times are converted to shot-local time automatically); manual `--schedule` JSON for hand-tuned moments.
2. Gate 1 upgrade: for beat-synced video prompts the plan MUST include the cut list (per-cut timestamps) and the effect schedule (per-beat effect assignment) for user approval — EDL-first.
3. CLI reference: add `ms video beats`, `ms video beatfx` under the Video section.

- [ ] **Step 2: Update README quickstart**

After the existing `ms video scenes` / `ms video remove-bg` lines add:

```
ms video beats music.wav --out beats.json
ms video beatfx shot.mp4 --beats beats.json --offset 3.5 --auto
```

- [ ] **Step 3: Verify the skill renders correctly and commit both repos**

Run: `grep -c "video beats" ~/.agents/skills/media-create/SKILL.md` (>= 2), then:

```bash
cd ~/.agents && git add skills/media-create/SKILL.md && git commit -m "feat(skills): add Rhythm and Density rules to media-create"
cd /home/superbypassudin/.clone/Github/SuperBypassUdinnn/media-studio
git add README.md && git commit -m "docs: add rhythm and beatfx quickstart examples"
```

Note: `~/.agents` is a scoped repo — run `session_files_status` before committing and release claims if needed.

---

## Execution Notes

- Task order is load-bearing: Task 1 Step 1 fixes the fallback test BEFORE librosa is installed so the suite never goes red between tasks; Task 3 depends on both CLI surfaces existing.
- Final whole-branch review runs over the full task range on the most capable model, per subagent-driven-development.

