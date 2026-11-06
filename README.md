# media-studio

A headless, script-driven media production toolkit for the OpenCode agent on this machine:
video and audio editing, 3D model editing and rendering, graphic design, and acquisition of
free licensed assets.

Status: phase 0 complete, phase 1 started. The authoritative specification is
[`docs/design.md`](docs/design.md).

Phase 0 ships the project convention and the environment gate: `ms doctor`, `ms project init`,
the project template, the interactive key wizard, and a selftest that includes a live stdio MCP
handshake against `3d-asset-server`.

Phase 1 so far: manifest validation and the asset ledger (`ms project validate`,
`ms assets stage|index|list`) with REUSE sidecars, a derived `assets/index.json` and `CREDITS.md`,
SHA256 dedupe, licence policy, and budget aborts; plus asset search (`ms assets search`) with local
ranking and `ms assets pull` to stage a chosen candidate. Sources are keyless first (Met Museum,
Wikimedia Commons, Internet Archive) with keyed providers on top (Pixabay, Freesound); a keyed
provider without a key is reported as skipped, not failed. Credentials are read from the
environment and then `~/.config/opencode/secrets.env`, always masked. The video path is in as well: `ms video render` authors MLT XML, renders it with `melt`, encodes
with a two-pass `loudnorm` onto the project's loudness target, derives music ducking from the
voice track, and writes an `ffprobe`-measured QC report plus a contact sheet. The 3D and design
paths, footage triage, and the preset library are still open.

## What it provides

| Area | Surface |
| --- | --- |
| Video and audio | MLT XML generation rendered by `melt`, FFmpeg encoding, loudness normalization, music ducking, ffprobe QC |
| Footage triage | Local index and ranking first, batched contact sheets for the model, cached decisions |
| 3D | Blender CLI plus a headless Blender MCP, `gltf-transform`, `trimesh`/`pymeshlab` conversion and repair, GPU render and QC |
| Design | Brand tokens, SVG templates, Inkscape and ImageMagick export, legibility and overflow checks |
| Assets | Self-hosted 3D aggregator plus in-house clients for video, audio, music, images, icons, and fonts, with per-asset license sidecars |
| Presets | Engine-native transitions, video and audio effects, 3D node groups, and title styles in a searchable registry |
| Audio neural | DeepFilterNet denoise and demucs stem separation behind one wrapper CLI |

## Layout

```
docs/design.md            full specification and decision record
src/media_studio/         CLI today; MLT writer, runners, ledger, and asset clients in phase 1
templates/project/        project scaffold used by /media-new
tests/                    pytest suites and fixtures
scripts/selftest.sh       end-to-end selftest behind /studio-test
scripts/keys-wizard.sh    interactive key registration into secrets.env
scripts/mcp_probe.py      stdio MCP handshake used by the selftest
```

## Using it

```bash
uv venv && uv pip install -e . pytest        # once, inside the checkout
.venv/bin/python -m media_studio.cli doctor --deep
.venv/bin/python -m media_studio.cli project init my-project
.venv/bin/python -m media_studio.cli project validate my-project
.venv/bin/python -m media_studio.cli assets search "forest canopy" --class image
.venv/bin/python -m media_studio.cli assets pull my-project --search <saved path> --index 1
.venv/bin/python -m media_studio.cli assets stage my-project --class 3d \
  --file ~/Downloads/tree.glb --license "CC0" --source-url https://example.org/tree
.venv/bin/python -m media_studio.cli assets index my-project
.venv/bin/python -m media_studio.cli video render my-project          # add --preview for a look
.venv/bin/python -m media_studio.cli video qc my-project --file out/video/master-16x9.mp4
.venv/bin/python -m media_studio.cli video ingest ~/Projects/my-project/assets/video \
  --out ~/Projects/my-project/media --profile proxy    # DNxHR for free Linux Resolve
.venv/bin/python -m media_studio.cli video scenes clip.mp4             # find hard cuts
.venv/bin/python -m media_studio.cli video remove-bg clip.mp4 \
  --select 850,280 --feather 2                         # SAM 2 alpha ProRes 4444
.venv/bin/python -m media_studio.cli video beats music.wav --out beats.json
.venv/bin/python -m media_studio.cli video beatfx shot.mp4 --beats beats.json --offset 3.5 --auto
.venv/bin/python -m media_studio.cli image remove-bg photo.jpg --model u2netp
.venv/bin/python -m media_studio.cli see cutout.mov                       # facts + sheet
.venv/bin/python -m media_studio.cli see ~/Projects/my-project            # project state
bash scripts/selftest.sh
```

## Requirements on this machine

Blender 5.2.2 LTS, FFmpeg 7.0.2, melt 7.42.0 with Kdenlive 26.08.2, Inkscape 1.4.4,
ImageMagick 7.1.2, Node 26, Python 3.14, and the existing torch virtualenvs under
`~/.clone/Github/DeepFilterNet` and `~/.clone/Github/ultimatevocalremovergui`.

## Non-goals

No GUI automation beyond the Blender and Kdenlive fallbacks, no paid asset services, no
DaVinci Resolve scripting, and no cloud rendering.
