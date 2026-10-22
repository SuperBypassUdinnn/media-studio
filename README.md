# media-studio

A headless, script-driven media production toolkit for the OpenCode agent on this machine:
video and audio editing, 3D model editing and rendering, graphic design, and acquisition of
free licensed assets.

Status: locked design, implementation not started. The authoritative specification is
[`docs/design.md`](docs/design.md).

## What it will provide

| Area | Surface |
| --- | --- |
| Video and audio | MLT XML generation rendered by `melt`, FFmpeg encoding, loudness normalization, music ducking, ffprobe QC |
| 3D | Blender CLI plus a headless Blender MCP, `gltf-transform`, `assimp`, `trimesh`/`pymeshlab` conversion and repair, GPU render and QC |
| Design | Brand tokens, SVG templates, Inkscape and ImageMagick export, legibility and overflow checks |
| Assets | Self-hosted 3D aggregator plus in-house clients for video, audio, music, images, icons, and fonts, with per-asset license sidecars |
| Audio neural | DeepFilterNet denoise and demucs stem separation behind one wrapper CLI |

## Planned layout

```
docs/design.md            full specification and decision record
src/media_studio/         CLI, MLT writer, runners, ledger, asset clients
src/media_studio/mcp/     asset MCP server
templates/project/        project scaffold used by /media-new
tests/                    pytest suites and fixtures
scripts/selftest.sh       end-to-end selftest behind /studio-test
```

## Requirements on this machine

Blender 5.2.2 LTS, FFmpeg 7.0.2, melt 7.42.0 with Kdenlive 26.08.2, Inkscape 1.4.4,
ImageMagick 7.1.2, Node 26, Python 3.14, and the existing torch virtualenvs under
`~/.clone/Github/DeepFilterNet` and `~/.clone/Github/ultimatevocalremovergui`.

## Non-goals

No GUI automation beyond the Blender and Kdenlive fallbacks, no paid asset services, no
DaVinci Resolve scripting, and no cloud rendering.
