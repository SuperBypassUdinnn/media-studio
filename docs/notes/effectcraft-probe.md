# EffectCraft Headless Probe

Headless EffectCraft execution verified on this CachyOS machine (2026-11-04).

## Binary

| Tool | Path | Version |
|---|---|---|
| effectcraft-cli | `~/.local/bin/effectcraft-cli` | 0.19.0 |
| effectcraft (GUI) | `~/.local/bin/effectcraft` | 0.21.0 |

`effectcraft-cli` is the headless engine (required and present per `ms doctor`). The GUI `effectcraft` binary opens a window and is not usable headless without a display. `ms` is the media-studio CLI (`.venv/bin/ms`, media-studio 0.1.0).

Headless mode confirmed via `effectcraft-cli info --json`: `"mode": "headless"`, CPU renderer by default (`renderer.active: cpu`); GPU opt-in via `--gpu`.

## Test project

```
ms project init probe-effectcraft --root /tmp/opencode --title "EffectCraft Probe"
```

Note: `ms motion title` takes `--text` (not `--style`), and `--out` must be an absolute path for the CLI verdict to print.

## Render

```
ms motion title /tmp/opencode/probe-effectcraft \
  --text "PROBE KINETIC" \
  --out /tmp/opencode/probe-effectcraft/probe-title.mov \
  --duration 3 --animation fade_in_up
```

Verdict: pass (exit 0). Output: `probe-title.mov`.

Probed with `ffprobe`:
- codec `prores`, pixel format `yuva444p12le` (alpha)
- 1280×720, 30 fps, 3.00 s, 90 frames, no audio
- render time ~1.2 s

Render artifacts: `probe-title.mov`, `probe-title.ecp`, `probe-title_compiled.ecproj`, `probe-title.json`.

## Verify (`ms see`)

```
ms see /tmp/opencode/probe-effectcraft/probe-title.mov \
  --out /tmp/opencode/probe-effectcraft/see-title.png
```

```
peeked probe-title.mov (video (alpha, checkerboard))
  codec     prores, 1280x720
  fps       30
  duration  3.00 s
  audio     none
  sheet    /tmp/opencode/probe-effectcraft/see-title.png
```

Contact sheet: `/tmp/opencode/probe-effectcraft/see-title.png` (PNG, 1280×540, 4×3 checkerboard tiles).

## VRAM

GPU: NVIDIA GeForce GTX 1650 Max-Q, 4096 MiB VRAM.
- Idle baseline: ~3 MiB used.
- Peak during headless render: 176 MiB / 4096 MiB (~4%).
- Headless renderer runs on CPU by default.

## Notes

- effectcraft renames `.mp4` → `.mov` for ProRes RGBA output; use `--out ...mov` so `ms motion title` reports `verdict: pass`.
- `ms motion title` reporting crashes on `relative_to` when `--out` is relative; pass an absolute path.
- Full probe report: `.superpowers/sdd/2026-11-04-media-create-workflow/task-3-report.md`.
- Scratch project: `/tmp/opencode/probe-effectcraft/` (not committed).
