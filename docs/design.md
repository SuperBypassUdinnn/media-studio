# media-studio - Design Document

Status: locked design, implementation deferred (2026-10-23)

Owner: SuperBypassUdinnn

Related: `~/.config/opencode` (global OpenCode setup), `~/Projects/scr2026-motion-logo-v3`
(proving ground for the local engine conventions in section 8).

## 1. Purpose

Give the OpenCode agent first-class, reusable, headless capabilities for four kinds of
production work, on this machine, with free assets and recorded attribution:

1. Video and audio editing and finishing.
2. 3D model conversion, repair, animation, and rendering.
3. Graphic design (vector-first, brand-driven).
4. Acquisition of free assets: 3D models, PBR materials, HDRIs, video footage, sound
   effects, background music, images, icons, and fonts.

The capability must be global (available to every session and project), deterministic
(script-driven, reproducible), and honest about quality (measured, not assumed).

## 2. Locked decisions

Each row is a decision the user made during the design interview, with the reason it was
chosen. Section 11 records the interview itself.

| # | Decision | Choice | Reason |
| --- | --- | --- | --- |
| D1 | Delivery target | Global reusable capability under `~/.config/opencode` | Every future session and project inherits it; existing project becomes one consumer |
| D2 | Interface form | Adopt community MCP servers, patch when needed, wrap with skills and workflows | Reuse mature tooling, keep workflow knowledge in skills |
| D3 | First batch scope | All three domains as thin slices, plus asset search | Validates the architecture end to end instead of perfecting one domain |
| D4 | GUI automation | Headless/CLI default; GUI fallback limited to Blender and Kdenlive | Reproducibility, with an escape hatch for operations that have no headless path |
| D5 | Licensing and budget | Free/CC0 first, attribution recorded automatically, no paid asset APIs | Publication safety and zero recurring cost |
| D6 | Asset search depth | Search, download, license record, and stage into the project | Makes results immediately usable and legally traceable |
| D7 | Video/audio engine | Kdenlive/MLT XML plus FFmpeg; DaVinci Resolve excluded | Resolve 21.1.1 free cannot be scripted externally (Studio-gated) |
| D8 | 3D engine | Blender CLI first, community Blender MCP for incremental inspection, GUI MCP fallback | Headless default with a visual escape hatch |
| D9 | Design path | Local first (SVG, Inkscape, ImageMagick); Canva as phase 2 | No OAuth blocking, full precision, Canva added when it earns its place |
| D10 | QC loop | Numeric metrics plus contact sheets; vision evaluation optional | The primary model is text-only; the existing pipeline already proves numeric QC |
| D11 | Video slice scope | Assemble and finish a short promo: clips, music bed, voice-over ducking, loudness, titles, transitions, master plus social cutdowns | Real deliverable, not a toy filtergraph |
| D12 | 3D slice scope | Import, edit, animate, and export finished models | Matches the stated need to edit and manipulate models |
| D13 | Design slice scope | Brand kit plus social and poster sets | Highest reuse value for recurring content |
| D14 | Blender MCP | `sandraschi/blender-mcp` headless default; `ahujasid/blender-mcp` GUI fallback | Headless-first server exists; the popular one needs a live Blender window |
| D15 | Timeline format | Plain MLT XML from our generator; `melt` renders it; Kdenlive opens and saves a copy as `.kdenlive` | Smaller schema surface, stable across versions, still hand-editable |
| D16 | Neural audio | DeepFilterNet for denoise and demucs (from the UVR venv) for stems, in batch 1 | Both venvs already carry torch 2.12.0+cu130 with CUDA enabled |
| D17 | Extra 3D tooling | Blender plus `gltf-transform`, `assimp`, and a `trimesh`/`pymeshlab` venv | Batch conversion and optimization are far faster outside Blender |
| D18 | Testing | pytest per module plus an end-to-end selftest, exposed as `/studio-test` | The user can verify the whole system on demand |
| D19 | Definition of done | Three demo paths green plus a partial reproduction of the scr2026 motion logo | Proves the system against a real, previously solved deliverable |
| D20 | Code location | One monorepo `media-studio` at `~/.clone/Github/media-studio`; upstream projects stay pristine and separate | Isolated review, easy upstream updates |
| D21 | Asset aggregator | Self-hosted `3d-asset-server` for 3D; our own thin aggregator for video, audio, music, and design | 25-site 3D coverage is expensive to rebuild; other classes need our license and staging semantics |
| D22 | License records | Per-asset `.license` sidecar in REUSE style, generated `CREDITS.md`, derived `assets/index.json` | Compliance plus queryability and dedupe |
| D23 | Project convention | Single `project.yaml` manifest with a fixed directory layout, instantiated from a template in config | One source of truth per project |
| D24 | Disk policy | Scratch under `/tmp/opencode/<project>/` purged after encode; assets and deliverables only in the project; per-asset and per-project quotas | Root filesystem sits at 87% used with 52 GB free |
| D25 | Skill surface | Router skill plus four domain skills, and commands `/video`, `/3d`, `/design`, `/assets`, `/media-new`, `/studio-test` | Keeps always-on context small |
| D26 | Music sources | Keyless aggregators first, plus Jamendo/Pixabay once keys exist | Broad coverage now, better quality later |
| D27 | Music workflow | Search, download, stage, and place on the timeline with ducking and fades | Background music is a per-project recurring need |
| D28 | API keys | A guided wizard writes keys into `secrets.env`; values never enter chat or the repository | Credential hygiene |
| D29 | Git handling | Private GitHub repository, pushed per stage, pull requests only on request | Traceable progress without unsolicited PRs |
| D30 | Loudness target | Recorded audio normalized to -14 LUFS by default, overridable per project | YouTube and social first, broadcast on demand |
| D31 | Implementation ordering | Design doc first, implementation in later sessions | Keeps the locked design reviewable before code exists |
| D32 | Footage triage | Local index and ranking run first (metadata, technical rejects, transcript, on-screen text, embeddings); the model reviews only batched contact sheets and a ranked shortlist, and decisions are cached by content hash | Selecting shots from a large library stays fast and token-bounded |
| D33 | Preset and template library | Engine-native recipes (MLT, FFmpeg, Blender, LV2, Inkscape SVG) in a searchable registry, plus licensed free packs through the asset ledger; After Effects and Premiere templates are out of scope | Free transitions, video effects, and audio effects that actually execute headless |

## 3. Architecture

Three layers, each with one responsibility.

| Layer | Contents | Responsibility |
| --- | --- | --- |
| Knowledge | `~/.config/opencode/skills/*`, `~/.config/opencode/commands/*` | Teach the workflow, enforce gates, route to the right tool |
| Service | `media-studio` monorepo (CLI, asset MCP server, generators, runners), upstream MCP servers | Deterministic operations with structured results |
| Application | Blender, melt/Kdenlive, FFmpeg, Inkscape, ImageMagick, torch-based audio tools | The actual rendering and processing |

### 3.1 Repositories

| Path | Content | Ownership |
| --- | --- | --- |
| `~/.clone/Github/media-studio` | Our monorepo: CLI `ms`, asset MCP server, MLT generator, Blender runner, project template, tests, this document | Ours, private GitHub repo |
| `~/.clone/Github/3d-asset-server` | Upstream 3D asset aggregator (Apache-2.0, TypeScript, 25 sites) | Upstream, pristine clone |
| `~/.clone/Github/media-mcp` | Existing local video/audio context server (unchanged) | Existing |
| Blender MCP servers | `sandraschi/blender-mcp` (headless), `ahujasid/blender-mcp` (GUI fallback) | Upstream, installed via `uvx` or clone |
| `stocky` | Existing Unsplash photo MCP server | Existing |

### 3.2 Monorepo layout

```
media-studio/
  docs/design.md            this document
  src/media_studio/         Python package: CLI, MLT writer, runners, ledger, asset clients, presets
  src/media_studio/mcp/     our asset MCP server entry point (phase 1)
  templates/project/        project scaffold copied by /media-new
  tests/                    pytest suites plus fixture assets
  scripts/selftest.sh       end-to-end selftest behind /studio-test
  scripts/keys-wizard.sh    interactive key registration into secrets.env
  scripts/mcp_probe.py      stdio MCP handshake used by the selftest
```

Implemented in phase 0: `docs/design.md`, `src/media_studio/{cli,tools,project}.py`,
`templates/project/`, `tests/`, and the three scripts. The MCP server package is phase 1.

### 3.3 Config surface (additions to `~/.config/opencode`)

| Type | Name | Purpose |
| --- | --- | --- |
| Skill | `media-studio` | Router: which domain skill and which tool for the request |
| Skill | `media-studio-video` | Video and audio workflow, MLT authoring, loudness, ducking, QC |
| Skill | `media-studio-3d` | Blender CLI and MCP workflow, conversion, repair, materials, render QC |
| Skill | `media-studio-design` | Brand tokens, SVG templates, size variants, export and legibility checks |
| Skill | `asset-search` | Query planning, source selection, license verification, staging rules |
| Command | `/video`, `/3d`, `/design`, `/assets` | Entry points per domain |
| Command | `/media-new` | Scaffold a project from the template and verify tooling |
| Command | `/studio-test` | pytest plus end-to-end selftest with a metric summary per path |
| MCP | `assets` | Our asset MCP server (video, audio, music, design sources plus the ledger); registered in phase 1 |
| MCP | `assets-3d` | Self-hosted `3d-asset-server` over stdio; registered and verified in phase 0 |
| MCP | `blender-headless` | `sandraschi/blender-mcp`; registered in phase 1 |
| MCP | `blender-gui` | `ahujasid/blender-mcp`, kept disabled unless the fallback is needed |

MCP entries that have no working server yet stay commented out in `opencode.jsonc` so startup
is never delayed by a command that cannot answer.

Existing skills stay authoritative for their areas and must not be duplicated:
`media-context` (reading video and audio), `transcribe-diarize`, `youtube-subs`,
`finding-stock-photos` (Stocky), `creating-consistent-documents`,
`creating-consistent-presentations`, `creating-consistent-spreadsheets`,
`storage-doctor`, `math-visualization`.

## 4. Project convention

A media project is a directory created from the template, addressed by a slug.

```
~/Projects/<slug>/
  project.yaml            single source of truth
  README.md               what the project is and how to reproduce it
  .gitignore
  sources/                raw footage and recordings, never overwritten
  assets/
    3d/                   models, PBR texture maps, and HDRIs, flat, one sidecar each
    video/                stock clips
    audio/                voice-over, sound effects
    music/                background music
    design/               brand kit, logos, source SVGs
    index.json            derived index of every asset, its license, and its hash
    CREDITS.md            generated from sidecars
  scripts/                project-specific generators
  out/
    frames/               scratch frame sequences
    video/                video deliverables
    images/               design deliverables
    reports/              QC reports, contact sheets, metrics
  docs/                   project notes and revision history
```

### 4.1 `project.yaml`

```yaml
slug: example-promo
title: Example Promo
status: draft

deliverables:
  - id: master-16x9
    kind: video
    resolution: 1920x1080
    fps: 60
    duration: 20.0
    loudness_lufs: -14.0
    container: mp4
  - id: social-9x16
    kind: video
    resolution: 1080x1920
    fps: 60
    duration: 20.0

timeline:
  - { start: 0.0,  end: 2.7,  shot: front-reveal,  cue: ambient-drone }
  - { start: 2.7,  end: 5.4,  shot: orbit,         cue: shimmer }

brand:
  palette: { primary: "#0B3D2E", accent: "#C8A24A", ink: "#101010" }
  fonts: { heading: "Inter", body: "Inter" }

assets:
  music: assets/music/example-track.mp3
  voice: assets/audio/voice-over.wav

budget:
  max_asset_bytes: 209715200
  max_project_bytes: 21474836480
```

Rules:

- `project.yaml` is the only place a human edits intent; generated files never override it.
- Every declared deliverable must have a measured QC result before the project is done.
- Unknown fields are rejected, so typos fail loudly instead of being ignored.

### 4.2 Asset ledger

- Every asset file gets a sidecar `<file>.license` in REUSE style:
  `SPDX-FileCopyrightText`, `SPDX-License-Identifier`, `Source-URL`, `Attribution`,
  `Retrieved-At`, `SHA256`.
- `assets/index.json` is derived from sidecars and holds the same fields plus class, byte size,
  provider, duplicate groups, and the budget state. Media dimensions and duration join the index
  once the probe lands in phase 1.
- `CREDITS.md` is generated from the index, listing attribution-required assets with the
  exact credit line.
- Deduplication is by SHA256; a repeated file is linked, not downloaded twice.
- An asset with unknown or missing license is quarantined under `assets/unverified/` and
  is never referenced by a deliverable. Staging the same bytes again with a valid licence promotes
  it out of quarantine, replacing the quarantined copy.
- Git tracks `assets/index.json`, `CREDITS.md`, and the small brand-kit assets under
  `assets/design/` including their sidecars. Bulk media under `assets/{3d,video,audio,music}/`
  stays untracked, so its sidecars stay with it rather than in the repository; the index carries
  the same fields for everything that is tracked.

### 4.3 Disk policy

- Frame sequences and media caches render into `/tmp/opencode/<slug>/` and are purged
  after encode; only deliverables, reports, and contact sheets stay in the project.
- Downloads are checked against `budget.max_asset_bytes` before transfer, and the project
  total against `budget.max_project_bytes` after staging. Exceeding a quota aborts with a
  message instead of filling the disk.
- Cold projects are archived through the existing `storage-doctor` workflow, never
  deleted ad hoc.

## 5. Capability paths

### 5.1 Video and audio

1. Read `project.yaml`, resolve assets, and refuse to run on a missing or unlicensed asset.
2. Generate plain MLT XML (`out/<deliverable>.mlt`) describing producers, playlists,
   transitions, titles, and audio filters.
3. Render headless with `melt`, writing to the scratch directory.
4. Encode the deliverable with FFmpeg (H.264/AAC for delivery, FFV1 or PNG sequence as the
   intermediate), normalize audio to the project loudness target with `loudnorm`.
5. Verify with `ffprobe`: duration, resolution, fps, stream layout, integrated loudness,
   true peak, and black or frozen frame detection. Write a report to `out/reports/`.
6. Produce a contact sheet plus representative frames for review.
7. Hand off: the user may open the generated `.mlt` in Kdenlive and save it as
   `.kdenlive` for manual refinement. That copy is a working file, never the source of
   truth, and is excluded from the repository.

As built (2026-10-24): the timeline is written by `media_studio/mlt.py`, rendered by `melt` to an
FFV1 intermediate under `/tmp/opencode/<slug>/`, encoded by FFmpeg with a **two-pass** `loudnorm`
(the measurement pass feeds `measured_I/LRA/TP/thresh` into the encode, with a `-1.5 dBTP`
ceiling), and verified by `media_studio/media.py`: seven checks covering streams, duration,
loudness distance, true peak, black frames, and frozen frames. Two MLT facts were established by reading its XML module and by rendering
probes: an XML-declared filter is applied **statically** (the deserialiser stores nested properties
as a string, so an animated `level` never reaches the filter), and the `volume` filter's `level` is
in **dB**, not a linear factor. Ducking is therefore a chain of entries, each carrying a static
level, with ramps quantised to 50 ms steps.

Audio inside the path: voice-over cleanup (high-pass, denoise, compression), music ducking
driven by voice-over segments, fades, and final loudness. Stem separation supplies
instrumental-only or vocal-only variants when a project needs them.

Kdenlive handoff, measured on 2026-10-26 with Kdenlive 26.08.2. The bare `.mlt` our writer produces
is a valid MLT document that `melt` renders, but Kdenlive will not open it as a project: File > Open
answers "Incorrect project file — Version of the project file cannot be read", and accepting the
following "Attempting to open nonetheless" ends on an empty project. Two causes were established.
First, that document carried no `kdenlive:` properties at all, while a project Kdenlive writes itself
carries `kdenlive:docproperties.version` (1.1), `kdenlive:docproperties.kdenliveversion`,
`kdenlive:docproperties.documentid`, a `main_bin` **playlist** holding the bin and the document
properties, per-track `kdenlive:trackheight` and `kdenlive:timeline_active`, a sequence tractor with
`kdenlive:sequenceproperties.*`, and a final tractor marked `kdenlive:projectTractor`. That reference
document is kept as evidence at
`~/.local/state/opencode-media-studio/kdenlive-evidence/kdenlive-reference-schema.mlt`, saved from an
empty project Kdenlive created through its own dialog. Second, the fades are cross-fades against a
generated black clip that the writer pointed at under `/tmp/opencode/<slug>/`, which is purged after
encoding, so a handed-off project referenced a file that no longer exists; Kdenlive reported exactly
that as a clip problem and the title's `Sans` family as a placeholder. Both are fixed — `ms video
handoff` writes the envelope and keeps generated filler in the project's `out/frames/`, and it now
predicts the font substitution instead of leaving it to be discovered. Screenshots of both dialogs sit
beside the reference. Two method notes, because they cost real time: launching `kdenlive <file>` does
not open a project on this build, it imports the file into the bin as a clip — it does the same to a
project Kdenlive saved itself, so that path proves nothing about a document's validity; and the Open
dialog filters on the `.kdenlive` extension, so a `.mlt` is invisible in its file list until the
file-type filter is changed or the name is typed into the field. A fix therefore means a
Kdenlive-compatible handoff document, and a filler clip that lives inside the project rather than in
`/tmp`. Both are now written by `ms video handoff` (see `docs/plans/kdenlive-handoff.md`), and the
measured result is progress rather than success: Kdenlive accepts the document — the version refusal
is gone, the load reaches the Clip Problems dialog, the file lands in Open Recent — and then crashes
while loading it, which Kdenlive itself confirmed with "Kdenlive crashed on last start" on its next
launch. The remaining gap is structural: Kdenlive's own timeline is a black track plus one tractor
per track holding two playlists with `hide` attributes, referenced by a sequence tractor with a JSON
track description, where ours puts playlists straight on `main` and nests its own A/B and title
tractors. Evidence, including the document and both screenshots, is in
`~/.local/state/opencode-media-studio/kdenlive-evidence/`.

### 5.2 3D

As built (2026-10-26): `media_studio/blender.py` runs Blender headless and every run carries the
environment this machine needs, in one place: `VK_ICD_FILENAMES` plus `--gpu-backend vulkan`, and
`/usr/bin` ahead of `~/.local/bin` on `PATH`. It also detects a packaging mismatch that stops
Blender from starting at all, and shims the missing library name through a private
`LD_LIBRARY_PATH` while saying so in every report. That guard was exercised for real on
2026-10-25, when Blender 17:5.2.3-3 linked `libdraco.so.10` while the installed `draco` still
shipped `libdraco.so.9`; updating `draco` to 1.5.8 resolved it the same day, the shim was removed,
and `ms doctor --deep` reported Blender 5.2.3 clean afterwards. The guard stays because the
combination can break again on any partial upgrade, but it should never be the steady state. `ms 3d inspect` measures a model (objects, vertices, triangles, non-manifold edges, loose
vertices, materials, bounds, unit scale) and names what needs repair; `ms 3d convert` applies
transforms, rebuilds outward normals, welds duplicate vertices, optionally decimates, and reports
the repaired mesh rather than the exported file, because a glTF round trip splits vertices per
attribute; `ms 3d turntable` builds a pivot, camera, key light and world, keyframes a full rotation,
renders through EEVEE on the Vulkan backend, measures per-frame luminance and time, builds a
contact sheet, and compares against a recorded baseline by luminance with a stated tolerance
(frame hashes are kept, but EEVEE's temporal jitter makes them differ between identical runs). This
build ships EEVEE only; Cycles is not available.

As built (2026-10-25): `media_studio/materials.py` closes the material half of this path. Poly Haven's
public files API needs no key and serves CC0, so `ms 3d material <model> --project P --material ID
[--hdri ID]` fetches every texture map plus an optional environment, stages each one through the
ledger with its REUSE sidecar, refreshes `assets/index.json` and `assets/CREDITS.md` (CC0 requires no
attribution, so CREDITS correctly records none while the index and sidecars keep the provenance),
binds the maps in Blender, and optionally renders a proof frame. Source role names are translated to
shader slots — `Diffuse` to base colour with an sRGB colour space, `Rough`, `nor_gl`, and `AO` as
non-colour data — and roles with no slot (`Displacement`, the packed `arm` map, `nor_dx`) are reported
as ignored instead of being silently dropped. A namespaced id from the aggregator
(`polyhaven:mossy_rock`) is accepted directly, so a search there feeds the fetch. Ambient occlusion
multiplies the base colour through a `ShaderNodeMixRGB` with named sockets, or is reported as unwired
when there is no base colour for it to modulate; the world is an environment texture on the HDRI; and
the proof camera aims at the mesh centre so the frame shows the whole object. Recording a deviation:
candidate search runs through `assets-3d`, but the file list and the download use Poly Haven's own
keyless API, because the aggregator exposes downloading only over MCP, which was not reachable from
this session; its CLI returns search results alone. Both legs are keyless and CC0. Verified live on 2026-10-25 with
`mossy_rock` plus `belfast_sunset_puresky` at 1k: 8 assets staged and indexed as CC0-1.0, four slots
bound, a 3.6 MB GLB with embedded textures, and a proof frame at 138.9 mean luma showing the
normal-mapped rock lit by the HDRI. That run also found two real defects the offline tests could not:
staging downloaded into `/tmp` and renamed into the project, which fails with `EXDEV` when the system
temp is a different filesystem, and the command passing Poly Haven role names to a shader script that
expects slot names, which bound nothing while still exiting zero. Both are fixed and covered by
regression tests.

1. Import or convert the input (glTF, GLB, OBJ, FBX, USD, or STL) through Blender;
   `gltf-transform` handles web-ready validation, Draco and meshopt compression, and
   texture dedupe.
2. Repair and normalize: recalculate normals, fix non-manifold geometry, unify units,
   normalize pivot and scale, and optionally decimate or remesh through `assimp` or the
   `trimesh` and `pymeshlab` venv.
3. Populate materials and environment: PBR sets and HDRIs fetched by the asset aggregator,
   staged flat under `assets/3d/` beside the models, one REUSE sidecar per file.
4. Author animation: keyframed turntable, camera moves, and transform animation through
   Blender Python.
5. Render headless on the discrete GPU (section 8), at preview resolution first.
6. Verify: geometry statistics, per-frame render metrics, frame hash comparison against a
   baseline, and a contact sheet.

The MCP server is for incremental inspection and exploration (scene state, object
queries, small edits). The committed artifact is the script; a scene rebuilt from scripts
must reproduce the same result.

### 5.3 Design

As built (2026-10-25): `media_studio/design.py` composes SVG templates from `templates/design/`
with brand tokens, a staged logo, and a staged font, exports PNG and PDF through Inkscape, and
publishes checks that can fail: WCAG contrast from the colours written into the file, text boxes
measured with Inkscape's own geometry query against the template's safe area, the smallest font
size present, and the exported pixel size. `ms design render` reports them and writes
`out/reports/<slug>-design-qc.json` plus a contact sheet. The font layer is
`media_studio/fonts.py`: `ms fonts search` filters installed families by name, role, and language
coverage (Indonesian included), `ms fonts specimen` batches candidates into one image set in the
project's own headline, `fonts.embedding_permission` reads the OS/2 fsType so a font that forbids
embedding is refused, and `fonts.font_environment` writes a project fontconfig that adds the staged
font directory, which makes a project's fonts resolvable by Inkscape and `melt` without installing
anything system-wide.

1. Load brand tokens from `project.yaml` (palette, fonts, logo assets).
2. Compose SVG templates per output format, with layout rules expressed in the template and
   content injected from the manifest.
3. Render with Inkscape (SVG to PNG and PDF) and post-process with ImageMagick
   (trim, resize, color profile, canvas padding).
4. Generate the size variants the project declares: Instagram post and story, A3 poster,
   YouTube thumbnail, and any custom canvas.
5. Verify: text contrast ratio, no text overflow past safe margins, minimum stroke and
   minimum font size, image resolution adequacy, and a contact sheet of every variant.
6. Documents and presentations remain with the existing `creating-consistent-*` skills;
   this path does not duplicate them.

### 5.4 Neural audio

- Denoise: DeepFilterNet CLI from `~/.clone/Github/DeepFilterNet/DeepFilterNet/.venv`.
- Stem separation: demucs from `~/.clone/Github/ultimatevocalremovergui/.venv`, producing
  vocals, drums, bass, and other.
- Both are wrapped behind a single CLI in the monorepo so the skills never call the venvs
  directly, keeping the coupling in one place.
- Fallback when a venv is broken: FFmpeg `arnndn` and `afftdn`, which always exist.

### 5.5 Footage triage (token-bounded shot selection)

As built (2026-10-25): `media_studio/triage.py` measures a library with `ms triage index`
(exposure and clipping, edge energy as a sharpness proxy, frame-to-frame motion, a perceptual hash
for duplicates, shake from `vidstabdetect`, and scene cuts), explains every reject with a reason,
and persists the cards to `footage/index.jsonl`, which doubles as the measurement cache: a second
run over unchanged clips measures nothing and reuses every card. Ranking is local and explicit
(`score_clip`), not a model. `ms triage sheets` batches the clips that have no verdict yet into
labelled contact sheets, `ms triage decide` records a verdict in `footage/decisions.jsonl` keyed by
clip and concept, and `ms triage status` reports progress. CLIP embeddings are deliberately not in
this increment: the funnel already cuts the reviewed image count drastically, and the model would
add a large download for an unproven gain.

Choosing shots from a large footage library must never stream frames into the model. The path is
a funnel, and only the last two stages cost tokens.

1. Technical index, no model: `ffprobe` metadata, scene detection, `blackdetect`, `signalstats`
   exposure and clipping, Laplacian-variance sharpness, `vidstabdetect` shake, pHash duplicate
   grouping, and SHA256 identity. Every reject is recorded with a reason and is not retried.
2. Clip card, cheap text: duration, resolution, fps, motion and sharpness scores, exposure,
   dominant palette against the brand tokens, speech presence, transcript with timecodes, and
   on-screen text through OCR.
3. Ranking without a language model: local CLIP embeddings compare the concept text with
   scene-boundary keyframes and keep the top N, default 50.
4. Batched visual review: contact sheets of at most 20 labelled cells, each cell carrying an id
   and a timecode. The model answers one JSON verdict per cell (`in`, `hold`, `out`, score,
   reason). One sheet replaces twenty separate images.
5. Second pass for `hold` only: a three to four frame strip per finalist at higher resolution,
   for motion and continuity judgement.
6. Persistence: `footage/index.jsonl`, `footage/sheets/*.jpg`, and `footage/decisions.jsonl`.
   Decisions are keyed by content hash and concept hash, so an unchanged concept re-runs at zero
   tokens and no clip is reviewed twice.
7. Parallel review: independent sheets may go to subagents that return compressed JSON, so the
   parent session never receives pixels.

The local `media` MCP server already produces frames, OCR, and transcripts with caching, and this
path consumes it instead of duplicating it. Rules: review proxies at 480p, prefer scene
boundaries over uniform sampling, and never modify the originals.

### 5.6 Preset and template library

Most free templates found online are `.mogrt`, `.prproj`, or `.aep`, which this stack cannot
execute. The library therefore covers three classes.

1. Engine-native recipes, owned here and versioned with the code: transitions, video effects,
   audio chains, 3D node groups, and title styles expressed as MLT fragments, FFmpeg
   filtergraphs, Blender node groups, LV2 chains, and parameterized Inkscape SVG. Each preset is
   a `recipe.yaml` (id, intent tags, engine, parameter schema with ranges, license, provenance)
   plus its implementation, a preview, and a snapshot test.
2. Licensed free packs, acquired through the ledger of section 6: CC0 LUT packs, overlay and
   light-leak elements, sound effect packs, OFL fonts, MIT icon sets, and public-domain
   illustrations. Attribution and provenance follow the same sidecar rules.
3. Already-installed free effect plugins: Calf, LSP, x42, Zam, Dragonfly, DeepFilterNet, and the
   FFmpeg built-ins that form the substrate for class 1.

Discovery is local and textual: a search by intent returns an id, the parameter schema, and a
preview path, so a preset costs tens of tokens instead of a documentation page. `project.yaml`
references presets by id with parameters under `effects:`, which keeps projects reproducible.

As built (2026-10-26): twelve recipes live under `presets/*.yaml` and `media_studio/presets.py`
loads, integrity-checks, searches, and parameter-validates them. `ms presets list|search|show`
exposes the registry to a person and to the agent, and `project.yaml` references a recipe by id
under `effects:` with parameter overrides; `ms project validate` rejects an unknown id, an out of
range parameter, or a title effect without text. Application is wired to the mechanisms verified
this week: a transition becomes a `luma` dissolve, a video effect becomes a static MLT filter on
the clip's producer, an audio effect sets the music bed's level, fades, and duck depth, and a title
becomes a `qtext` overlay. The two transition recipes, seven video effects, one audio effect, and two
titles are in-house and each records its provenance as a verified render. The five added on
2026-10-26 were chosen by probing which MLT filters this build actually applies: warming through
`lift_gamma_gain`, a 9:16 `crop`, a one-step `sepia`, and a `wave` displacement, plus a top caption
built on the `qtext` title mechanism the video path already renders. That probe is committed as
`scripts/probe-preset-filters.py`, which renders one clip through each candidate and prints the
frame's mean level and a digest of its pixels; saturation, hue, colorbalance, and rgba are the
candidates it measures as having no effect here, so no recipe claims them. Render coverage has two layers: each video effect is rendered on a fixed clip with and without
the filter and the decoded pixels are compared, and every recipe of a rendered kind — all seven video
effects and both titles — is compared against a recorded frame in `tests/snapshots/presets/` within a
mean absolute difference of 1.5 of 255, which is a snapshot that also has to differ from the
unfiltered frame, regenerated deliberately with `MS_PRESET_SNAPSHOTS=regen`.

Out of scope: executing After Effects or Premiere templates, redistributing third-party
templates, and paid marketplaces.

## 6. Asset acquisition

### 6.1 Source matrix (batch 1)

| Class | Sources | Credentials |
| --- | --- | --- |
| 3D models, PBR materials, HDRIs | `3d-asset-server`: Poly Haven, ambientCG, Kenney, Quaternius, Smithsonian Open Access, itch.io curated, Sketchfab, CGTrader (link-only), Fab (link-only) | Keyless; Sketchfab token only for downloads |
| Video footage | Pixabay Videos (keyed, live), Internet Archive, Pexels Videos (deferred) | Pixabay key works; Internet Archive keyless. Pexels is deferred while its account is suspended, so the wizard reports it as deferred rather than missing |
| Sound effects | Freesound (keyed, live), Kenney and Sonniss packs, Openverse audio (blocked) | Freesound token works and yields the high-quality MP3 preview, since original files need OAuth2 |
| Background music | Internet Archive, Jamendo (keyed, live 2026-10-26), ccMixter, Musopen | Jamendo answers live with its eight-character client id; its catalogue is dominated by non-commercial and no-derivatives tracks, so the licence policy admits a minority (measured: 11 of 100 instrumental results are BY or BY-SA) |
| Video footage (link-only) | Coverr, Mixkit, Videvo | No API; results carry a link and license note |
| Images (Stocky) | Unsplash through the existing `finding-stock-photos` skill | Unsplash key is optional for the toolkit; that skill owns it |
| Audio packs | Kenney (CC0), Sonniss GDC packs, ccMixter, Pixabay Audio | Keyless downloads; Pixabay key for its audio search when it opens |
| Images | Met Museum Open Access (keyless, CC0), Wikimedia Commons (keyless), Internet Archive (keyless), Stocky (Unsplash, existing), Pixabay Images, Openverse Images | Pixabay key; the rest keyless. Openverse answers a Cloudflare bot challenge from this network and needs registered credentials before it is usable here; the Met is reachable and served live on 2026-10-24 |
| Vectors and icons | Openclipart, Iconify API (200k+ icons), Simple Icons | Keyless |
| Fonts | Google Fonts (repository and API), Fontshare | Keyless |
| Preset and template packs | CC0 LUT packs, overlay and light-leak element packs, sound effect packs, OFL font files, Iconify icon sets, unDraw illustrations | Keyless; staged with license sidecars as in section 5.6 |
| Canva templates and assets | Canva remote MCP | Phase 2, OAuth app registration |

### 6.2 Rules

- CC0 and public domain are preferred; CC-BY is allowed with an automatic credit line.
  Any license that forbids commercial use is rejected for deliverables.
- For background music destined for YouTube, CC0 is the default because CC-BY tracks are
  routinely claimed by Content ID. When only CC-BY fits, the constraint is reported to the
  user before the track is used.
- Provider terms are respected: no bypassing login walls or hotlink protection, no scraping
  behind bot walls, and per-site rate limits are honored with caching.
- Every result carries license, author, and source URL. A result without license metadata is
  never downloaded silently; it is reported and skipped.
- Downloads land in the class directory with the sidecar, then the index and `CREDITS.md`
  are regenerated.

### 6.3 Key handling

- A wizard walks through registration for Pexels, Pixabay, Freesound, Sketchfab, Jamendo,
  and Unsplash one step at a time, then writes values into
  `~/.config/opencode/secrets.env` through a prompt.
- Config references keys as `{env:NAME}`; values never appear in chat, commits, or reports.
- The system works fully keyless first, so missing keys degrade coverage instead of
  breaking the path.
- The wizard carries three states per provider. `required` fails `--check` when absent,
  `optional` is reported but never counted, and `deferred` marks a provider whose key cannot be
  obtained now for a reason outside this project, so its prompt is skipped. A reported problem is
  therefore always an action the user can take.
- Registered on 2026-10-24: Pixabay, Freesound, Sketchfab (required, all answering live), Unsplash
  and BlenderKit (optional), Jamendo (required), Pexels (deferred, account suspended by the
  provider).
- Jamendo resolved on 2026-10-26, after two corrections. Its client id really is eight characters,
  and the wizard's blanket sixteen-character minimum refused it, so a valid credential could not be
  registered at all; minimums are now per credential. Once the file held the right value the API
  still answered code 5, because a stale `JAMENDO_CLIENT_ID` was exported into the session
  environment and the documented precedence (environment first, then the file) made it win:
  `env -u JAMENDO_CLIENT_ID` answered `ok` immediately, and a direct `curl` with the file's value
  succeeded while the toolkit failed. A credential-origin note now rides on a provider's refusal
  ("credential from environment, which differs from the value in secrets.env"), and
  `shadowed_by_environment()` is what detects it. This also falsifies the sentence below about
  nothing exporting the secrets file into a login shell.
- A login shell *can* export a credential (it does on this machine, as the Jamendo episode shows),
  so the toolkit reads the file directly and the environment only overrides it:
  `media_studio.keys` checks the environment first and then
  `~/.config/opencode/secrets.env`, and everything user-facing goes through a mask. Keyed clients
  scrub the credential out of any error they raise, because these providers put the key in the
  query string.

## 7. Quality control and testing

| Level | What runs | Gate |
| --- | --- | --- |
| Unit | pytest per module: manifest parsing, MLT writer, ledger, source clients with recorded fixtures | Must pass |
| Integration | Per domain: render a fixture project, compare measured metrics against golden values | Must pass within tolerance |
| End to end | `/studio-test`: scaffold a demo project, run all three paths, print a metric summary | Must pass |
| Human | Contact sheets and representative frames reviewed by the user | Subjective approval |

Measured quantities per domain:

- Video and audio: duration, resolution, fps, stream layout, integrated loudness and true
  peak, ducking effectiveness measured as loudness delta under voice-over segments, black
  and frozen frame counts, encode bitrate sanity.
- 3D: mesh and triangle counts, bounding box and unit sanity, material and texture binding,
  per-frame render time, frame hash or SSIM against a baseline, non-empty render check.
- Design: contrast ratios, text overflow detection, minimum font and stroke sizes, exported
  pixel dimensions, PDF page geometry.
- Assets: license presence, sidecar completeness, hash uniqueness, quota compliance.

Vision-based review is optional and never a gate, because the primary model is text-only.
Numeric QC plus contact sheets is the contract.

## 8. Local environment constraints

Hard-won facts from the existing pipeline that the new runners must encode.

| Constraint | Detail |
| --- | --- |
| Blender GPU selection | Blender's default GL/EGL context lands on the Intel iGPU. Use the Vulkan backend with `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json` and `--gpu-backend vulkan`. PRIME render offload does not work here. |
| Blender PATH pitfall | Blender resolves its embedded Python stdlib by finding `python3.14` on `PATH`. `~/.local/bin` first breaks startup with `ModuleNotFoundError: No module named 'math'` and disables core addons. Put `/usr/bin` ahead of `~/.local/bin`, as `bin/blender-run` does in the scr2026 project. |
| Backend mixing | Frames rendered via Vulkan differ slightly from OpenGL frames, so a whole cut must use one backend. |
| GPU and memory | GTX 1650 Max-Q with 4 GB VRAM plus Intel UHD, 31 GiB RAM. Keep previews small, avoid concurrent GPU renders, and expect Cycles to be CPU unless a device is enabled. |
| Disk | Root filesystem is 87% used with roughly 52 GB free. Scratch purge and quotas are not optional. |
| DaVinci Resolve | 21.1.1 free edition at `/opt/resolve`. External scripting is Studio-gated, so Resolve is manual handoff only. |
| Audio acceleration | `DeepFilterNet/.venv` and `ultimatevocalremovergui/.venv` both carry torch 2.12.0+cu130 with CUDA available; no new torch install is required. |
| Toolchain versions | Blender 5.2.2 LTS, FFmpeg 7.0.2, melt 7.42.0, Kdenlive 26.08.2, Inkscape 1.4.4, ImageMagick 7.1.2-34 Q16-HDRI, GIMP 3.2.6, Node 26.11.1, Python 3.14.8. MLT generation is tested against melt 7.42 and Kdenlive 26.08.2. |

## 9. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| MLT schema drift across Kdenlive versions | Generated timelines fail to open | Emit a conservative MLT subset, test against the installed pair, and keep the generator version-aware |
| Third-party asset sites change markup | Aggregator returns partial results | Per-source status reporting, caching, and link-only fallback instead of hard failure |
| License metadata missing or wrong | Legal exposure in published work | No sidecar means no use; quarantine directory; user confirmation for anything non-CC0 |
| CC-BY music claimed by Content ID | Video demonetized or blocked | CC0 default, explicit warning when CC-BY is the only option |
| Torch venvs drift or break | Neural audio stops working | Wrap behind one CLI with an FFmpeg fallback path |
| Blender MCP instability | 3D exploration stalls | Scripts are the source of truth; MCP is additive and can be dropped without losing work |
| Disk exhaustion | Failed renders and corrupt caches | Scratch purge, quotas, storage-doctor archival |
| Long renders on 4 GB VRAM | Timeouts or driver resets | Preview-first workflow, lower sample counts, sequential GPU jobs |

## 10. Phases and acceptance criteria

### Phase 0 - foundation

- Key wizard for Pexels, Pixabay, Freesound, Sketchfab, Jamendo, and Unsplash.
- Clone and build `3d-asset-server`; verify its MCP endpoint responds.
- Create the monorepo skeleton, project template, and config entries.
- This design document.

As built on 2026-10-24: `ms` exposes `doctor` (with `--deep` for the Vulkan ICD, both torch
virtualenvs, and free disk) and `project init`; the project template carries `project.yaml` plus
the fixed directory layout; 27 pytest cases pass; `ruff check`, `ruff format --check`, and
`shellcheck` are clean; `scripts/selftest.sh` passes every phase 0 check, including a live stdio
handshake against `3d-asset-server` that returns its four tools. The key wizard is written but
awaits the user's interactive run, and the upstream web UI is deliberately not built because the
MCP path does not need it.

### Phase 1 - three thin paths plus assets, triage, and presets

| Path | Acceptance criteria |
| --- | --- |
| `/assets` | A single query returns ranked, licensed candidates from at least three keyless 3D sources; download writes the file plus a complete sidecar; the index and `CREDITS.md` regenerate; duplicates collapse by hash; quota violations abort with a clear message |
| `/video` | From a fixture `project.yaml` with three clips, music, and voice-over: a generated `.mlt` renders through `melt`, the deliverable encodes with measured loudness within 0.5 LU of target, ducking measurably reduces music under voice-over, `ffprobe` QC report and contact sheet are written, and the `.mlt` opens in Kdenlive. Met for everything except the Kdenlive open, which is still **not met** after two measurement rounds on 2026-10-26: Kdenlive first refused the bare `.mlt`, and now accepts the handoff document `ms video handoff` writes — no version complaint, the load reaches the Clip Problems dialog, the file is recorded in Open Recent — but crashes while loading it. Section 5.1 records the schema, the remaining structural difference, and the evidence |
| `/3d` | A GLB, OBJ, and STL input each convert and normalize; a PBR material and HDRI from the library bind correctly; a turntable renders headless on the discrete GPU; render metrics and a contact sheet are written; the result re-exports to GLB. Met for all of it: OBJ, STL, and GLB conversion and normalisation, the headless turntable with metrics and a contact sheet, and material and HDRI binding, which was proved live on 2026-10-25 with `mossy_rock` and `belfast_sunset_puresky` at 1k (four shader slots bound, eight CC0 assets staged and indexed, a GLB with embedded textures, and a proof frame at 138.9 mean luma) |
| `/design` | Brand tokens produce Instagram post and story, A3 poster, and YouTube thumbnail; PNG, PDF, and SVG all export; contrast and overflow checks pass; a contact sheet covers every variant |
| Footage triage | A library of 200 or more clips keeps the number of reviewed images at or below 15 while still yielding one usable shot per concept beat; decisions persist, and re-running the same concept sends zero images |
| Preset library | Every registered preset applies to a fixture timeline or scene and renders without manual adjustment, with a snapshot test inside tolerance; a pack downloaded from a free source lands with a complete license sidecar and appears in `CREDITS.md`. Met for the in-house recipes: each is applied through `effects:`, every recipe of a rendered kind (seven video effects, two titles) is compared against a recorded frame within a mean absolute difference of 1.5 of 255, a snapshot that matches the unfiltered frame fails outright, and the video-effect and transition kinds are additionally verified by measuring a rendered frame; the downloaded-pack half still belongs to the asset ledger |

Implemented so far (2026-10-24): manifest loading and validation (`ms project validate`); the asset
ledger (`ms assets stage`, `ms assets index`, `ms assets list`) with REUSE sidecars, a derived index
and `CREDITS.md`, SHA256 dedupe, quarantine with promotion on restaging, non-commercial and
no-derivatives refusal, and budget aborts before anything is written; and asset search across
keyless sources (`ms assets search`) with local ranking plus `ms assets pull` to stage a chosen
candidate. The `/assets` acceptance row is met for images and audio: one query returns ranked,
licensed candidates from three keyless sources that answer live (Met Museum, Wikimedia Commons,
Internet Archive), the download writes a complete sidecar, the index and `CREDITS.md` regenerate,
duplicates collapse by hash, and quota violations abort with a message. Keyed clients are in as well: Pixabay
for photos and videos and Freesound for sound effects answer live, while a keyed provider without a
key is reported as skipped rather than failed. The video path is in and measured: manifest to MLT, `melt` render, two-pass
loudness normalisation, ducking driven by `silencedetect` on the voice track, title overlays, fades
and A/B dissolves, a QC report with seven checks, and a contact sheet. Everything else in the table
above landed on 2026-10-25: footage triage, the preset registry, the design path, the 3D path, and
material and HDRI binding, and Jamendo on 2026-10-26 once its eight-character client id could be
registered and a stale environment export stopped shadowing it. Still open in phase 1: the Kdenlive
handoff, which was then measured and needs a Kdenlive-compatible document (section 5.1 states the
causes and the reference schema); a downloaded preset pack staged through the ledger; and the phase 3
`scr2026` baseline reproduction, which the acceptance matrix still lists as pending. The selftest runs a ledger roundtrip end to end, reports every phase 0
check and every phase 1 check except that baseline row as implemented, and 260 pytest cases pass.

### Phase 2 - Canva

- Register a Canva developer app, connect the remote MCP server, and cover template use,
  Canva asset search, and export. Triggered only after phase 1 passes.

### Phase 3 - real-project proving ground

- Reproduce a segment of `~/Projects/scr2026-motion-logo-v3` through the new runner and
  compare against its recorded baselines (aligned-frame correlation 0.9558 for the extruded
  curve version with mean colour error 6.2/255, and the raster version at 0.9796 and
  3.1/255), keeping tolerance in the report.
- Add automation for recurring variants and cold-project archival.

## 11. Non-goals

- No GUI automation outside the Blender and Kdenlive fallbacks.
- No paid asset APIs or subscriptions in phase 1.
- No DaVinci Resolve automation, and no claim of parity with its color and Fairlight suites.
- No long-form multicam authoring in the first batch.
- No document, spreadsheet, or presentation generation; the existing `creating-consistent-*`
  skills own that.
- No cloud rendering, and no upload of project media to third-party services.
- No text-to-3D, image generation, or voice cloning in the first batch.

## 12. Interview record

Decisions were reached in six rounds of a structured interview on 2026-10-22 and
2026-10-23. Answers are recorded as given.

| Round | Question | Answer |
| --- | --- | --- |
| 1 | Where the capability lives | Global reusable capability in `~/.config/opencode` |
| 1 | Interface form | Adopt community MCP servers and fix them if needed; write wrapper skills with the workflow |
| 1 | First batch | All three domains as thin slices |
| 1 | GUI automation | Headless/CLI only (later amended for Blender and Kdenlive) |
| 1 | License and budget | Free and CC0 first, attribution recorded, no paid APIs |
| 1 | Asset search depth | Search, download, record license, stage into the project |
| 2 | Video and audio engine | Kdenlive/MLT XML plus FFmpeg |
| 2 | 3D control | Blender CLI first plus a community MCP |
| 2 | Design path | Local first, Canva in a later phase |
| 2 | Video slice | Assemble and finish a short promo |
| 2 | 3D slice | Import, edit, animate, and export finished models |
| 2 | Design slice | Brand kit plus social and poster sets |
| 2 | Evaluation loop | Numeric QC plus contact sheets |
| 3 | Asset search build strategy | Community 3D aggregator plus in-house clients for the other classes |
| 3 | License records | Sidecar `.license`, generated `CREDITS.md`, derived index |
| 3 | Project convention | `project.yaml` plus a fixed directory layout |
| 3 | Code location | Separate repository under `~/.clone/Github`, config points at it |
| 3 | API keys | Register every key now, through a wizard into `secrets.env` |
| 3 | Disk policy | Scratch auto-purge, assets in the project, quotas |
| 3 | Skill surface | Router plus four domain skills and four commands |
| 4 | Blender MCP | Headless default (`sandraschi/blender-mcp`) with GUI fallback (`ahujasid/blender-mcp`) |
| 4 | Timeline format | MLT XML core, opened in Kdenlive for handoff |
| 4 | Audio depth | Include denoise and stem separation now |
| 4 | 3D tooling | Add `assimp` plus `trimesh`/`pymeshlab` |
| 4 | Testing harness | pytest plus end-to-end selftest plus a command |
| 4 | Definition of done | Three paths plus partial scr2026 reproduction |
| 4 | Repository structure | One monorepo, upstream kept separate |
| 5 | Music sources | Keyless first plus registered keys |
| 5 | Music workflow | Search, download, stage, place on the timeline with ducking |
| 5 | Neural audio engine | Reuse the existing torch virtualenvs |
| 5 | GUI fallback boundary | Blender and Kdenlive only |
| 5 | Scaffolding | New command plus a template in config, projects under `~/Projects` |
| 5 | Canva | Phase 2, after phase 1 passes |
| 5 | Key registration | Guided wizard writing to `secrets.env` |
| 5 | Implementation process | Design document plus a staged plan across sessions |
| 6 | Spec confirmation | Accepted, implementation deferred |
| 6 | Git handling | Private GitHub repository now |
| 6 | Immediate scope | Design document only in this session |
| 6 | Loudness default | -14 LUFS with per-project override |
| 6 | Selftest surface | Separate `/studio-test` command |

## 13. Items resolved during phase 0

1. `3d-asset-server` run mode: native Node from a shallow clone at
   `~/.clone/Github/3d-asset-server`, built with `npm run build:server`. The web UI build is
   skipped because its `web/` workspace dependencies are not installed and the MCP path does not
   need the UI. It is registered as the stdio MCP server `assets-3d` running
   `node ~/.clone/Github/3d-asset-server/dist/cli.js mcp`. Verified 2026-10-24: `initialize` and
   `tools/list` answer with `search_assets`, `get_asset`, `download_asset`, and
   `list_providers`. Docker is not used.
2. `assimp` is not installed from the distribution. Geometry repair goes through Blender's
   importer plus the `trimesh` and `pymeshlab` Python path first; the distribution package is
   only revisited if a format proves impossible that way, and then through the `root-access`
   skill.
3. Fixture set for the selftest: a 6 s 1080p60 clip with motion and a still frame, a 12 s
   voice-over WAV at 48 kHz, a 30 s CC0 music track, one textured GLB, one STL, and a brand kit
   (palette, two OFL fonts, one SVG logo). Fixtures live in `tests/fixtures` with license
   sidecars; anything large is generated by script rather than committed.
4. Implementation language: Python only, with no new Node dependency. `gltf-transform` runs
   through `npx --yes @gltf-transform/cli` from the same Node toolchain as `3d-asset-server`,
   with the resolved version recorded by `ms doctor`.
5. CC-BY music is allowed only with a complete sidecar, `CREDITS.md` marking it
   attribution-required, and a warning line in the deliverable report. CC0 stays the default for
   YouTube-bound projects, and `budget.require_cc0_music: true` turns the preference into a hard
   rule.

## 14. Open items for phase 1

0. A/B cross-fades and fades: **done** (a `luma` dissolve, since `mix` is audio-only).
0. Titles: **done**, and the reason they looked broken is worth keeping. Declaring
   `sample_aspect_num="1" sample_aspect_den="1"` on the profile makes this MLT build drop the
   qtblend title composite entirely, while a hand-written file without those attributes renders
   text. The profile now derives the display aspect from the canvas (so a square or vertical
   deliverable is described correctly) and omits the sample aspect, and a test asserts white text
   appears in the rendered frame. `qtext` is available for text and `mix`/`luma`
   for transitions, but compositing a title over the picture needs a `qtblend`-style transition and
   a second video track, and a true cross-fade needs an A/B roll with blank padding. Both are
   deliberately deferred rather than half-built.
0. `ms assets stage` writes the file and its sidecar but does not add the asset to the manifest's
   `assets:` map, so a render refuses until the user names it. The stage output should print the
   exact YAML line to paste.
0. The Kdenlive handoff is untested: the generated `.mlt` parses and renders, but no one has opened
   it in Kdenlive on this machine yet.

1. Closed on 2026-10-26: Jamendo was two problems, not one — a wizard minimum that refused its
   eight-character client id, and a stale environment export that shadowed the corrected file. Both
   are fixed and covered by tests. A `--verify` mode on the key wizard, which makes one live call per
   provider, would still have caught the second at registration instead of at first search.
2. Openverse is unusable from this network while anonymous traffic hits its Cloudflare challenge;
   decide between registering client credentials and dropping the source.
3. Keyed coverage now exists for photos, videos, sound effects, and music; music still leans on
   Internet Archive and Wikimedia Commons as well, because Jamendo's permissive licences are a
   minority of its catalogue. Smithsonian Open Access and NASA Images are keyless candidates for a
   fourth source.
4. Funnel thresholds: the shortlist size and the sheet cell count need fixture-measured values
   rather than the current defaults of 50 clips and 20 cells.
5. Preset registry size for the first cut: a target of twelve video transitions, eight video
   effects, six audio chains, four 3D node groups, and three title styles.
6. Transport for our own asset MCP server: stdio like `assets-3d`, or its HTTP endpoint with the
   ledger behind it.
7. Whether clip cards should carry CLIP scores from a local model already present on disk, or a
   separate small embedding model installed for this purpose.
