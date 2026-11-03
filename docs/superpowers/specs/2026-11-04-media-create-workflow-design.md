# Design: "Prompt to Result" Media Creation Workflow

- **Date:** 2026-11-04
- **Status:** Draft / Pending Review
- **Authors:** Antigravity (Agent) & superbypassudin (User)
- **Target Repository:** `media-studio` (`/home/superbypassudin/.clone/Github/SuperBypassUdinnn/media-studio`)
- **Associated Skill:** `media-create` (`~/.agents/skills/media-create/SKILL.md`)

---

## 1. Executive Summary & Intent

The goal of this system is to establish a robust **"Prompt to Result"** autonomous media creation workflow. Given a raw natural-language prompt (creative idea, reference links, rough brief, or script), the agent acts as a **Master Creative Director and Pipeline Orchestrator**: planning the breakdown, preparing and staging assets, assembling deterministic timelines, executing headless renders, inspecting visual contact sheets and loudness metrics via `ms see`, and delivering production-ready media deliverables.

Rather than attempting to encapsulate all production logic in a monolithic CLI binary, this architecture implements an **Orchestrator-Worker Pipeline Pattern** where an agent skill (`media-create`) drives the existing specialized CLI tools (`ms video`, `ms see`, `ms image`, `ms motion`, `ms assets`, `ms triage`) through structured lifecycle checkpoints and deterministic artifact contracts.

## 2. Research Grounding & Architectural Topology

Based on current industry standards for AI creative pipelines (e.g., LangGraph multi-step media pipelines, Remotion Claude Code agent skills, and video production toolkits), production-grade automated media generation requires four fundamental principles:

1. **Orchestrator-Worker Pipeline Topology:**
   The central orchestrator decomposes natural language into timed shot blueprints, delegating heavy generation, transcoding, and rendering to deterministic external CLI tools (`ffmpeg`, `FilmCraft`, `EffectCraft`, `Pillow`).

2. **Deterministic Timeline Mental Model:**
   All timeline math is calculated strictly in integer frames (`duration_frames = round(seconds * fps)`), explicit frame rates (e.g., 24, 30, 60 fps), and standard aspect ratios (16:9 widescreen, 9:16 vertical shorts, 1:1 square), avoiding fractional drift.

3. **Context Bloat Defense:**
   The agent's working context is kept strictly text- and metadata-oriented (YAML/JSON manifests, file paths, loudness metrics, cue timestamps). Heavy media payloads are never read directly as binary into the LLM context; visual verification is performed selectively by reading generated contact sheet PNGs (`ms see`) and QC JSON reports (`ms video qc`).

4. **Targeted Self-Correction Loop:**
   When an inspection or QC verification fails (e.g., audio drift, loudness violation, black frames, or visual glitch), the orchestrator isolates and regenerates only the faulty segment or parameter rather than resetting the entire production pipeline.

```text
 [ User Prompt / Creative Brief ]
               │
               ▼
   [ 1. Orchestrator Agent ] ──(media-create skill)
               │
               ├──► Writes/Updates brief.md & project.yaml
               │
        ┌──────┴──────────────────────────────┐
        ▼                                     ▼
 [ Asset Preparation & Staging ]       [ Composition & Sequencing ]
 (ms assets, ms video ingest,          (FilmCraft .fcproj,
  ms image remove-bg, etc.)             EffectCraft motion.yaml)
        │                                     │
        └──────────────► [ Rendering Engine ] ◄┘
                         (ms video render,
                          ms motion render)
                                │
                                ▼
                   [ Visual QA & Critique ]
                   (ms see contact sheets,
                    ms video qc loudness)
                                │
                                ▼
                  [ Checkpoint Gate / Deliver ]
```

## 3. The 9-Stage Orchestration Lifecycle (`media-create` Skill)

The orchestration skill `media-create` governs the progression through nine explicit stages:

### Stage 1: Prompt Intake & Classification
- **Input:** User prompt (raw idea, reference video/image, script, or change request).
- **Action:** Parse intent, target distribution channel (YouTube, Instagram Reels, TikTok, presentation), and media discipline (Video AMV, Talking Head / Captions, Social Graphic / Poster, Motion Graphics intro/title, 3D asset).
- **Artifact:** Locate or initialize project directory (`~/Projects/<slug>/`).

### Stage 2: Creative Briefing (`brief.md`)
- **Action:** Generate or update `brief.md` in the project root.
- **Contract:** Defines intent, target audience, style references, technical constraints (duration, resolution, fps, loudness target), and measurable success criteria.
- **Living Document:** Every revision of user intent updates `brief.md` before changing timeline code.

### Stage 3: Planning & Step Blueprint (Gate 1)
- **Action:** Formulate concrete, numbered execution plan:
  1. Asset inventory: which local clips/tracks are ready, which need transcoding (`ms video ingest`), background removal (`ms image remove-bg`), or search (`ms assets search`).
  2. Timeline cuts & cue points: frame ranges, audio bed, beat synchronization points.
  3. Compositing & motion graphics: lower-thirds, title sequences, color grade.
- **Checkpoint Gate 1:** Present plan to user for approval (skipped in `auto` mode).

### Stage 4: Asset Acquisition & Preparation
- **Action:** Execute atomic CLI commands to ready all media:
  - Transcode raw consumer camera/video files to Resolve/FilmCraft-safe formats: `ms video ingest`.
  - Isolate subjects or remove backgrounds: `ms image remove-bg` or `ms video remove-bg`.
  - Cut audio or extract cues: `ms video beat-sync`, `ms video transcript`.
  - Stage licensed / CC0 music or sound effects: `ms assets stage`.

### Stage 5: Timeline Assembly & Composition
- **Action:** Write/update `project.yaml` deliverables, assets, timeline tracks, and transitions, or generate `.fcproj` (FilmCraft) / motion sequence definitions.
- **Rule:** Maintain deterministic frame math.

### Stage 6: Headless Rendering
- **Action:** Invoke rendering engines:
  - Video composition: `ms video render <project> [--deliverable <id>]`
  - Motion graphics: `ms motion render <project>`
  - Static design: `ms design render <project>`

### Stage 7: Automated Visual QA & Critique (`ms see`)
- **Action:** Run `ms see` on rendered output and read the resulting PNG sheet:
  - Contact sheet analysis: check for black frames, missing assets, frame rate stutter.
  - Alpha verification: check checkerboard composite for edge halos or dropped limbs.
  - Loudness check: verify BS.1770 integrated loudness reaches target (default `-14.0 LUFS ± 1.0`).
- **Rule:** The agent MUST read the contact sheet image using the `read` tool before claiming success.

### Stage 8: Result Review (Gate 2)
- **Checkpoint Gate 2:** Present final metrics, deliverable file path, and contact sheet overview to user for acceptance or feedback.

### Stage 9: Targeted Self-Correction or Delivery
- **If Feedback / Defect:** Identify the precise stage of failure (asset, cue point, effect), apply surgical edit, and re-render only the affected deliverable.
- **If Approved:** Mark project status as completed, summarize deliverables, and deliver final paths.

## 4. Checkpoint Protocol & Override Tokens

To balance human control with autonomous speed, the skill implements a token-based checkpoint protocol:

| Prompt Indicator Token | Mode | Protocol Behavior |
|---|---|---|
| *(default)* | `plan+result` | Halts at **Gate 1** (Plan Approval) and **Gate 2** (Final Result Review). User explicitly validates the design and output. |
| `"kerjakan langsung"` / `"langsung saja"` / `"full auto"` | `auto` | Bypasses Gate 1 and Gate 2. Agent autonomously plans, executes, verifies via `ms see` + QC, and only reports on completion or fatal error. |
| `"tunjukkan draft dulu"` / `"draft dulu"` / `"preview"` | `draft-first` | Renders a fast preview (540p proxy or 5-second trimmed sample) -> pauses for review -> upon approval, renders full deliverable. |

### Universal Safety Intercept
Regardless of mode (including `auto`), if automated QC fails (e.g., audio clipping, missing asset placeholder, exit code non-zero, or corrupted visual sheet), execution **immediately stops** and presents the defect diagnosis to the user.

---

## 5. File Schemas & Project Scaffold Integration

### 5.1 `brief.md` Schema

Every project initialized via `ms project init` will now contain a standardized `brief.md` alongside `project.yaml`.

```markdown
# Creative Brief: <Project Title>

## 1. Intent & Purpose
<Concise statement of what is being produced and why>

## 2. Target Audience & Platform
- Primary Platform: <YouTube / Instagram Reels / Presentation / TikTok>
- Aspect Ratio: <16:9 (1920x1080) | 9:16 (1080x1920) | 1:1 (1080x1080)>
- Target Audience: <e.g., anime community, corporate stakeholders, general social>

## 3. Style & Creative References
- Mood / Energy: <e.g., high-octane, melancholic, minimalist, modern tech>
- Reference Works: <links, paths, or visual references>
- Typography & Brand Colors: <fonts, hex codes, or project brand tokens>

## 4. Technical Constraints
- Target Duration: <seconds or mm:ss>
- Frame Rate: <24 fps (film/cinematic) | 30 fps (social/standard) | 60 fps (smooth AMV)>
- Loudness Target: -14.0 LUFS (EBU R128 / YouTube standard)
- True Peak Ceiling: -1.0 dBFS

## 5. Success Criteria
- [ ] Visual storytelling matches beat and pacing of audio.
- [ ] Zero black frames or unlinked asset warnings.
- [ ] Measured loudness within ±1.0 LUFS of target.
- [ ] Contact sheet inspection confirms clean transitions.

## 6. Execution Mode
- Mode: <plan+result (default) | auto | draft-first>
```

### 5.2 Scaffold Integration (`src/media_studio/project.py`)
The project initialization logic in `scaffold_project()` will be updated to write this `brief.md` template populated with project slug and title upon `ms project init <slug>`.

## 6. EffectCraft Headless Probe (Carve-In)

EffectCraft was recently installed on this machine to provide headless motion graphics capabilities (`ms motion render/title/lyrics`). As part of this design, a quick verification probe must be executed prior to running motion acceptance tests:
1. Verify the binary location (`which effectcraft` or binary resolution).
2. Execute a minimal test project with a 3-second kinetic title sequence.
3. Verify output video and render timing without GUI dependency.

---

## 7. Acceptance Test Suite & Friction Logging

To prevent premature optimization and discover real workflow pain points, three end-to-end acceptance tests spanning different media formats will be executed directly through the `media-create` orchestration loop:

### Acceptance Test 1: Video AMV (Beat-Synchronized)
- **Prompt:** `"Buat AMV 15 detik dari clip JJK di scr2026-amv-baseline, potong mengikuti beat lagunya"`
- **Pipeline Exercised:** `ms video ingest` (if needed) -> `ms video beat-sync` -> `project.yaml` timeline assembly -> `ms video render` -> `ms see` contact sheet inspection + loudness check.
- **Success Gate:** 15.0s video cut cleanly on musical transients, loudness at -14 LUFS, visual contact sheet validated.

### Acceptance Test 2: Social Graphic / Thumbnail
- **Prompt:** `"Buat YouTube thumbnail 16:9 untuk AMV JJK tersebut dengan judul 'UNSTOPABLE BEATS', gunakan cutout karakter Gojo dan background bertema energi kutukan"`
- **Pipeline Exercised:** `ms image remove-bg` -> `ms design render` (Inkscape SVG template) -> `ms see` alpha/thumbnail inspection.
- **Success Gate:** 1920x1080 PNG, typography legible, subject cleanly isolated with transparent/gradient composite.

### Acceptance Test 3: Motion Graphics Intro
- **Prompt:** `"Buat title animation intro 5 detik dengan teks 'PROJECT GOJO', gaya anime minimalis futuristik"`
- **Pipeline Exercised:** `ms motion title` / EffectCraft sequence rendering -> `ms see` frame inspection.
- **Success Gate:** 5.0s animation rendered headless, smooth easing, no dropped frames.

### Friction Log: `docs/notes/prompt-to-result-friction.md`
Every friction point encountered during these three runs (e.g., missing parameter, confusing CLI error, awkward manual file copy, slow transcoding) will be logged with:
- `Prompt`: the command issued
- `Friction`: exact friction or failure observed
- `Resolution / Gap Identified`: whether it was solved by skill guidance or requires a dedicated tool enhancement in `ms`.

---

## 8. Implementation Steps & Verification Plan

1. **Step 1: Scaffold `brief.md` in `project.py`**
   - Update `scaffold_project()` to generate `brief.md`.
   - Add unit tests in `tests/test_project.py` verifying file existence and contents.
   - Run `ruff check` and `pytest`.

2. **Step 2: Author the `media-create` Skill**
   - Create `~/.agents/skills/media-create/SKILL.md` defining the 9-stage workflow, brief template, checkpoint rules, and command mapping.
   - Symlink into `~/.config/opencode/skills/media-create/SKILL.md`.

3. **Step 3: EffectCraft Headless Probe**
   - Validate EffectCraft CLI execution on a minimal synthetic config.

4. **Step 4: Execute Acceptance Tests & Populate Friction Log**
   - Run Test 1 (AMV), Test 2 (Design), Test 3 (Motion).
   - Record findings in `docs/notes/prompt-to-result-friction.md`.

5. **Step 5: Review & Commit**
   - Commit all repo artifacts with conventional commits.

