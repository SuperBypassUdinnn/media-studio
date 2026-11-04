# AMV Elite Pipeline — E2E Verification (Task 5)

End-to-end verification of the AMV motion & typography preset chain:

impact frame -> AMV title preset -> 2.5D parallax composite -> `ms see` contact sheet.

Verified on this CachyOS machine (2026-11-04), media-studio 0.1.0.

## Toolchain

| Tool | Path | Version |
|---|---|---|
| effectcraft-cli | `~/.local/bin/effectcraft-cli` | 0.19.0 |
| ffmpeg / ffprobe | `/usr/bin/ffmpeg`, `/usr/bin/ffprobe` | system |
| ms (media-studio CLI) | `.venv/bin/ms` | 0.1.0 |

## Presets registered (`AMV_MOTION_PRESETS`)

All three presets target 1920x1080 @ 60 fps:

| Preset | Character |
|---|---|
| `amv-title-cyber` | Animated tracking, cyan/magenta glow (`#00FFFF`), semi-transparent backdrop |
| `amv-hud-accent` | Swiss grid line elements, corner crosshairs `[ + ]`, tick-mark accents |
| `amv-drop-shutter` | Vertical slit mask animation opening on the beat drop |

## E2E chain

Scratch project: `/tmp/opencode/amv-e2e/amv-elite/` (not committed).

### 1. Impact frame at the beat drop

```
ms video impact-frame source.mp4 \
  --at f120 --frames 2 --mode invert --fps 60 --out impact.mov
```

```
impact invert: 2 frame(s) at 2.000s
  output: impact.mov (1.2 MB)
```

ffprobe:
- codec `prores`, 1920x1080, 60/1 fps

### 2. AMV title preset render (ProRes 4444 RGBA with alpha)

```
ms motion title amv-elite \
  --text "CYBER BEAT" \
  --preset amv-title-cyber \
  --out amv-title.mov \
  --duration 3.0 --width 1920 --height 1080 --fps 60
```

ffprobe:
- codec `prores`, pixel format `yuva444p12le` (alpha)
- 1920x1080, 60/1 fps, 3.00 s
- verdict: pass (render succeeded; CLI display note below)

Artifacts: `amv-title.mov`, `amv-title.ecp`, `amv-title_compiled.ecproj`, `amv-title.json`.

### 3. 2.5D parallax composite (background + subject cutout + AMV title foreground)

```
ms video parallax \
  --bg background.mov --subject subject.mov --fg amv-title.mov \
  --duration 3.0 --fps 60 --resolution 1920x1080 --motion push_in \
  --out shot.mov
```

```
composed push_in parallax shot -> shot.mov
  resolution: 1920x1080 @ 60 fps
  duration:   3.0 s
  layers:     3
  size:       361511185 bytes
```

ffprobe:
- codec `prores`, pixel format `yuva444p12le` (alpha)
- 1920x1080, 60/1 fps, 3.000000 s

### 4. `ms see` verification — clean 12-tile contact sheet

```
ms see shot.mov --out contact-sheet --columns 4 --rows 3
```

```
peeked shot.mov (video (alpha, checkerboard))
  codec     prores, 1920x1080
  fps       60
  duration  3.00 s
  audio     none
  sheet    contact-sheet/shot.see.png
```

Contact sheet: `contact-sheet/shot.see.png` (PNG, 1280x540 = 4 columns x 3 rows = 12 tiles, alpha rendered over a checkerboard so transparency is visible).

## Verdict

| Stage | Result |
|---|---|
| Impact frame generation | pass |
| AMV title preset render (ProRes 4444 RGBA) | pass |
| Parallax composite (3 layers, alpha) | pass |
| `ms see` 12-tile contact sheet | pass |

## Notes

- `ms motion title` reports `verdict: pass` only when `--out` is an absolute path; a relative `--out` crashes on `relative_to` in the verdict print (pre-existing, see `docs/notes/effectcraft-probe.md`). The render itself completes and writes the file.
- The AMV title preset is carried through the parallax chain as the foreground plane, so its alpha channel (cyber glow title) composites over the subject and background.
- Scratch artifacts live under `/tmp/opencode/amv-e2e/` and are not committed.
