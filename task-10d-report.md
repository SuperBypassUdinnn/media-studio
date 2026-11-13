# Task 10D Report

## Summary

Replaced FFmpeg's `testsrc2` diagnostic pattern in the deterministic video fallback with a branded dark motion slate, applied YouTube-recommended bitrate targeting to final promo deliverable encodes, and added explicit BT.709 color metadata tags to final delivers and transitions.

## Changes

### `src/media_studio/gen/pipeline.py`
- Rewrote `_deterministic_card` to render a branded dark motion slate instead of `testsrc2`.
  - Base solid in the brand `primary` color (`#0F172A` default).
  - Subtle accent radial glow via FFmpeg `geq` using the brand `accent` color (`#38BDF8` default), limited-range `yuv420p`.
  - Optional `primary`/`accent` keyword overrides; defaults match `project.brand.palette`.
  - `model` renamed from `ffmpeg-testsrc2` to `ffmpeg-branded-slate`.
- `testsrc2` is never emitted in user-facing fallback assets.

### `src/media_studio/video.py`
- `encode_with_loudness` gained an `fps` parameter (default 30).
- YouTube-recommended upload bitrate via constrained VBV/ABR:
  - 1080p60: `-b:v 12M -maxrate 14M -bufsize 24M`
  - 1080p30: `-b:v 8M -maxrate 10M -bufsize 16M`
- Explicit BT.709 color metadata on every final deliverable encode:
  `-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709`

### `src/media_studio/promo.py`
- `render_promo` now threads `fps=fps` through to `video.encode_with_loudness` so the 60 fps deliverable targets 12 Mbps and 30 fps targets 8 Mbps.

### `src/media_studio/transitions.py`
- `apply_transition` now appends `-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709` to its encode command.
- Added `-threads 1` workaround with comment: ffmpeg 9.0.2 libx264 segfaults on the `slideup` xfade filter when `-colorspace bt709` is also set under slice threading.

### Tests (RED then GREEN)
- `tests/test_gen_pipeline.py`: `test_deterministic_card_uses_branded_slate_not_testsrc2` — asserts no `testsrc2`, primary/accent brand colors present, `yuv420p`.
- `tests/test_promo.py`: `test_encode_with_loudness_carries_youtube_bitrate_and_bt709` — asserts `12M`/`14M`/`24M` at 60 fps, `8M` at 30 fps, and all four BT.709 flags.
- `tests/test_transitions.py`: `test_apply_transition_tags_bt709_color_metadata` — asserts all four BT.709 flags in the transition encode command.

## Verification

- `pytest tests/test_gen_pipeline.py tests/test_promo.py tests/test_transitions.py tests/test_cli_package.py -q -p no:cacheprovider` — 46 passed.
- `~/.local/bin/ruff check src tests` — all checks passed.
- `~/.local/bin/ruff format --check src tests` — 147 files already formatted.
- `codegraph sync .` — done.
- `/tmp/opencode/promo-*` scratch verified cleaned (no leftovers).

## Commit

`fix(gen,promo): replace testsrc2 with branded slate, apply YouTube 12Mbps bitrate and BT.709 color tags` (commit `51273fb`, branch `feat/promo-generative-pipeline`).
