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

### 3.2 Monorepo layout (planned)

```
media-studio/
  docs/design.md            this document
  src/media_studio/         Python package: CLI, MLT writer, runners, ledger, asset clients
  src/media_studio/mcp/     asset MCP server entry point
  templates/project/        project scaffold copied by /media-new
  tests/                    pytest suites plus fixture assets
  scripts/selftest.sh       end-to-end selftest behind /studio-test
```

### 3.3 Config surface (planned additions to `~/.config/opencode`)

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
| MCP | `assets` | Our asset MCP server (video, audio, music, design sources plus the ledger) |
| MCP | `assets-3d` | Self-hosted `3d-asset-server` over stdio |
| MCP | `blender-headless` | `sandraschi/blender-mcp` |
| MCP | `blender-gui` | `ahujasid/blender-mcp`, kept disabled unless the fallback is needed |

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
    3d/                   models, plus library/<source>/<slug>/ for PBR sets and HDRIs
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
- `assets/index.json` is derived from sidecars and holds the same fields plus class,
  dimensions or duration, provider, and duplicate groups.
- `CREDITS.md` is generated from the index, listing attribution-required assets with the
  exact credit line.
- Deduplication is by SHA256; a repeated file is linked, not downloaded twice.
- An asset with unknown or missing license is quarantined under `assets/_unverified/` and
  is never referenced by a deliverable.

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

Audio inside the path: voice-over cleanup (high-pass, denoise, compression), music ducking
driven by voice-over segments, fades, and final loudness. Stem separation supplies
instrumental-only or vocal-only variants when a project needs them.

### 5.2 3D

1. Import or convert the input (glTF, GLB, OBJ, FBX, USD, or STL) through Blender;
   `gltf-transform` handles web-ready validation, Draco and meshopt compression, and
   texture dedupe.
2. Repair and normalize: recalculate normals, fix non-manifold geometry, unify units,
   normalize pivot and scale, and optionally decimate or remesh through `assimp` or the
   `trimesh` and `pymeshlab` venv.
3. Populate materials and environment: PBR sets and HDRIs fetched by the asset aggregator,
   staged under `assets/3d/library/<source>/<slug>/`.
4. Author animation: keyframed turntable, camera moves, and transform animation through
   Blender Python.
5. Render headless on the discrete GPU (section 8), at preview resolution first.
6. Verify: geometry statistics, per-frame render metrics, frame hash comparison against a
   baseline, and a contact sheet.

The MCP server is for incremental inspection and exploration (scene state, object
queries, small edits). The committed artifact is the script; a scene rebuilt from scripts
must reproduce the same result.

### 5.3 Design

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

## 6. Asset acquisition

### 6.1 Source matrix (batch 1)

| Class | Sources | Credentials |
| --- | --- | --- |
| 3D models, PBR materials, HDRIs | `3d-asset-server`: Poly Haven, ambientCG, Kenney, Quaternius, Smithsonian Open Access, itch.io curated, Sketchfab, CGTrader (link-only), Fab (link-only) | Keyless; Sketchfab token only for downloads |
| Video footage | Pexels Videos, Pixabay Videos, Internet Archive | Pexels and Pixabay keys; Internet Archive keyless |
| Video footage (link-only) | Coverr, Mixkit, Videvo | No API; results carry a link and license note |
| Sound effects | Kenney (CC0), Sonniss GDC packs, Freesound, Pixabay Audio, Openverse audio, ccMixter | Freesound and Pixabay keys; rest keyless |
| Background music | Openverse aggregation (Free Music Archive, Jamendo, ccMixter, Wikimedia), Free Music Archive, Jamendo, ccMixter, Musopen, Kenney music jingles, Sonniss | FMA and Jamendo keys; Pixabay key for Pixabay audio |
| Images | Stocky (Unsplash, existing), Pexels Images, Pixabay Images, Openverse Images | Pexels and Pixabay keys; Openverse keyless with rate limits |
| Vectors and icons | Openclipart, Iconify API (200k+ icons), Simple Icons | Keyless |
| Fonts | Google Fonts (repository and API), Fontshare | Keyless |
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

### Phase 1 - three thin paths plus asset search

| Path | Acceptance criteria |
| --- | --- |
| `/assets` | A single query returns ranked, licensed candidates from at least three keyless 3D sources; download writes the file plus a complete sidecar; the index and `CREDITS.md` regenerate; duplicates collapse by hash; quota violations abort with a clear message |
| `/video` | From a fixture `project.yaml` with three clips, music, and voice-over: a generated `.mlt` renders through `melt`, the deliverable encodes with measured loudness within 0.5 LU of target, ducking measurably reduces music under voice-over, `ffprobe` QC report and contact sheet are written, and the `.mlt` opens in Kdenlive |
| `/3d` | A GLB, OBJ, and STL input each convert and normalize; a PBR material and HDRI from the library bind correctly; a turntable renders headless on the discrete GPU; render metrics and a contact sheet are written; the result re-exports to GLB |
| `/design` | Brand tokens produce Instagram post and story, A3 poster, and YouTube thumbnail; PNG, PDF, and SVG all export; contrast and overflow checks pass; a contact sheet covers every variant |

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

## 13. Open items for phase 0

1. Confirm the `3d-asset-server` run mode (pnpm build plus stdio MCP, or its HTTP MCP
   endpoint) and whether Docker is preferred over native Node.
2. Decide whether `assimp` is installed from the distribution (requires root, so through the
   `root-access` skill) or built into a virtualenv.
3. Define the exact fixture set for the selftest: a short clip, a voice-over sample, a music
   track, a GLB and an STL model, and a brand kit.
4. Decide whether the monorepo ships a Python-only implementation or adds a small Node
   dependency for `gltf-transform`.
5. Choose how CC-BY music is surfaced in the report when no CC0 alternative exists.
