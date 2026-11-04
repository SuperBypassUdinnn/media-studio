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
