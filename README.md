# media-studio

A headless, script-driven media production toolkit for the OpenCode agent on this machine:
video and audio editing, 3D model editing and rendering, graphic design, and acquisition of
free licensed assets.

Status: phase 0 implemented. The authoritative specification is
[`docs/design.md`](docs/design.md).

Phase 0 ships the project convention and the environment gate: `ms doctor`, `ms project init`,
the project template, the interactive key wizard, and a selftest that includes a live stdio MCP
handshake against `3d-asset-server`. Timeline authoring, rendering, the asset ledger, footage
triage, and the preset library are specified and belong to phase 1.

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
bash scripts/selftest.sh
```

## Requirements on this machine

Blender 5.2.2 LTS, FFmpeg 7.0.2, melt 7.42.0 with Kdenlive 26.08.2, Inkscape 1.4.4,
ImageMagick 7.1.2, Node 26, Python 3.14, and the existing torch virtualenvs under
`~/.clone/Github/DeepFilterNet` and `~/.clone/Github/ultimatevocalremovergui`.

## Non-goals

No GUI automation beyond the Blender and Kdenlive fallbacks, no paid asset services, no
DaVinci Resolve scripting, and no cloud rendering.
