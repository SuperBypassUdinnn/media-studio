# "Prompt to Result" Media Creation Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish an autonomous, multi-step creative media production workflow orchestrated by the `media-create` agent skill, complete with project brief scaffolding, headless rendering pipelines, visual inspection via `ms see`, and acceptance testing across video, design, and motion formats.

**Architecture:** An Orchestrator-Worker pipeline pattern where the agent follows the `media-create` skill to turn raw prompts into a structured `brief.md`, plans shot/asset execution, runs specialized CLI tools (`ms video`, `ms see`, `ms image`, `ms motion`, `ms assets`), validates results through visual contact sheets, and logs workflow friction for continuous improvement.

**Tech Stack:** Python 3.12, FFmpeg, FilmCraft, EffectCraft, Pillow, pytest, ruff, OpenCode Agent Skills.

**Spec:** `docs/superpowers/specs/2026-11-04-media-create-workflow-design.md`

## Global Constraints

- Python code must conform to Python 3.12 standards and pass `ruff check` and `ruff format`.
- Timeline calculations must use deterministic integer frames based on explicit FPS (24, 30, or 60 fps).
- Loudness target for audio deliverables defaults to `-14.0 LUFS ± 1.0` with a `-1.0 dBFS` true-peak ceiling.
- Working memory must remain text- and metadata-oriented; visual verification is performed strictly by inspecting contact sheet and waveform PNGs produced by `ms see`.
- No decorative emojis in codebase files, commit messages, or written technical artifacts.
- Commits must use Conventional Commits format (`type(scope): subject`).

## Review Focus

- **Missing `brief.md` in legacy projects:** Ensure `media-create` gracefully creates or imports a brief if run in a project created before `brief.md` scaffolding existed.
- **Corrupted or empty contact sheets from `ms see`:** Ensure the verification step halts if `ms see` returns no sheet or fails to extract frames.
- **Aspect ratio mismatch on output:** Verify that deliverable dimensions match the brief specification (16:9, 9:16, 1:1) without unexpected stretching.
- **Beat-sync transient misalignment:** Ensure audio cut points in AMV workflows land cleanly on detected musical beats without audio clicks or clipping.
- **Headless rendering failure in EffectCraft:** Ensure motion rendering detects missing fonts, missing binary, or syntax errors and prints actionable diagnostics rather than hanging silently.

---

### Task 1: Scaffold `brief.md` in Project Initialization

**Files:**
- Modify: `src/media_studio/project.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Consumes: `scaffold_project(slug: str, title: str | None = None, root: Path | None = None) -> Project`
- Produces: `root / slug / "brief.md"` initialized from template with project title, slug, and standard sections.

- [ ] **Step 1: Write the failing test**

In `tests/test_project.py`, add a test verifying that `scaffold_project()` creates `brief.md`:

```python
def test_scaffold_creates_brief_md(tmp_path: Path) -> None:
    project = scaffold_project("test-brief", title="Test Brief Project", root=tmp_path)
    brief_path = project.root / "brief.md"
    assert brief_path.is_file()
    content = brief_path.read_text()
    assert "# Creative Brief: Test Brief Project" in content
    assert "## 1. Intent & Purpose" in content
    assert "## 4. Technical Constraints" in content
    assert "-14.0 LUFS" in content
    assert "## 6. Execution Mode" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_project.py::test_scaffold_creates_brief_md -v`
Expected: FAIL (`assert brief_path.is_file()` returns False)

- [ ] **Step 3: Implement `brief.md` generation in `src/media_studio/project.py`**

Define `BRIEF_TEMPLATE` containing the standard Markdown schema specified in Section 5.1 of the design document. In `scaffold_project()`, write `(project_dir / "brief.md").write_text(...)` with slug and title substituted.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_project.py -v`
Expected: PASS (all tests pass)

- [ ] **Step 5: Code quality & commit**

Run: `/home/superbypassudin/.local/bin/ruff check src/media_studio/project.py tests/test_project.py`
Run: `/home/superbypassudin/.local/bin/ruff format src/media_studio/project.py tests/test_project.py`
Commit:
```bash
git add src/media_studio/project.py tests/test_project.py
git commit -m "feat(project): scaffold brief.md upon project initialization"
```

---

### Task 2: Author the `media-create` Skill

**Files:**
- Create: `/home/superbypassudin/.agents/skills/media-create/SKILL.md`
- Create Symlink: `/home/superbypassudin/.config/opencode/skills/media-create` -> `~/.agents/skills/media-create`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-11-04-media-create-workflow-design.md`
- Produces: Executable agent skill definition loaded by OpenCode for orchestrating end-to-end prompt-to-result production.

- [ ] **Step 1: Write `~/.agents/skills/media-create/SKILL.md`**

Author the skill containing:
1. Role definition: Master Creative Director and Pipeline Orchestrator.
2. The 9-stage lifecycle: Prompt Intake -> Briefing (`brief.md`) -> Plan (Gate 1) -> Asset Preparation -> Assembly -> Render -> Visual QA (`ms see`) -> Result Review (Gate 2) -> Iteration/Delivery.
3. Checkpoint rules & override tokens: `plan+result` (default), `auto` (`"kerjakan langsung"`), `draft-first` (`"tunjukkan draft dulu"`).
4. Concrete CLI command reference for each media discipline:
   - Video: `ms video ingest`, `ms video beat-sync`, `ms video scenes`, `ms video remove-bg`, `ms video render`, `ms see`
   - Motion: `ms motion render`, `ms motion title`, `ms motion lyrics`
   - Design: `ms image remove-bg`, `ms image extract-signature`, `ms design render`
   - Audio: `ms triage`, loudness verification.
5. Strict rule: Visual verification must use `ms see` and inspect the generated sheet PNG before claiming task completion.

- [ ] **Step 2: Create symlink in OpenCode skills directory**

Run:
```bash
ln -s /home/superbypassudin/.agents/skills/media-create /home/superbypassudin/.config/opencode/skills/media-create
```

- [ ] **Step 3: Verify skill registration**

Verify the skill directory exists and the symlink resolves cleanly:
```bash
ls -la /home/superbypassudin/.config/opencode/skills/media-create/SKILL.md
```

- [ ] **Step 4: Commit in `~/.agents` repository**

Run session files check, stage, and commit:
```bash
cd /home/superbypassudin/.agents
git add skills/media-create/SKILL.md
git commit -m "feat(skills): add media-create prompt-to-result orchestration skill"
```

---

### Task 3: EffectCraft Headless Probe (Carve-In)

**Files:**
- Create: `docs/notes/effectcraft-probe.md`
- Scratch/Test Project: `/tmp/opencode/probe-effectcraft/`

**Interfaces:**
- Consumes: `effectcraft` binary via system PATH or `ms motion`
- Produces: Verified headless render output proving EffectCraft execution on this machine.

- [ ] **Step 1: Check EffectCraft binary availability & version**

Run: `which effectcraft && effectcraft --version || echo "check exit=$?"`
Verify: Binary exists and prints version or help cleanly without GUI error.

- [ ] **Step 2: Initialize minimal test project and render kinetic title**

Run:
```bash
ms project init /tmp/opencode/probe-effectcraft --title "EffectCraft Probe"
cd /tmp/opencode/probe-effectcraft
ms motion title "PROBE KINETIC" --style minimal --out probe-title.mp4
```

- [ ] **Step 3: Verify render output via `ms see`**

Run:
```bash
ms see /tmp/opencode/probe-effectcraft/probe-title.mp4 --out /tmp/opencode/probe-effectcraft/see-title.png
```
Inspect: Verify video resolution, duration (~3-5s), and contact sheet.

- [ ] **Step 4: Document findings in `docs/notes/effectcraft-probe.md`**

Record binary path, execution command, render duration, VRAM usage, and sample contact sheet path.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/notes/effectcraft-probe.md
git commit -m "docs(notes): record headless EffectCraft probe verification"
```

---

### Task 4: Acceptance Test 1 — Video AMV Beat-Sync Workflow

**Files:**
- Test Project: `~/Projects/scr2026-amv-baseline/`
- Output Log: `docs/notes/prompt-to-result-friction.md`

**Interfaces:**
- Consumes: Prompt `"Buat AMV 15 detik dari clip JJK di scr2026-amv-baseline, potong mengikuti beat lagunya"`
- Produces: 15.0s rendered AMV deliverable, contact sheet, and friction log entries.

- [ ] **Step 1: Parse prompt and write `brief.md` in project root**

Create/update `~/Projects/scr2026-amv-baseline/brief.md`:
- Intent: 15-second high-energy anime music video from Jujutsu Kaisen clips.
- Duration: 15.0 seconds (450 frames @ 30 fps).
- Loudness: -14.0 LUFS.
- Music: Existing NCS track in `assets/audio/`.
- Cuts: Beat-synchronized to audio transients.

- [ ] **Step 2: Execute beat detection on audio track**

Run:
```bash
ms video beat-sync ~/Projects/scr2026-amv-baseline/assets/audio/track-ncs.mp3 --duration 15.0 --fps 30
```
Capture: Detected beat timestamps/frame numbers.

- [ ] **Step 3: Assemble timeline in `project.yaml`**

Map selected JJK clips to timeline cuts matching the detected beat frames up to frame 450 (15.0s).

- [ ] **Step 4: Render deliverable**

Run:
```bash
ms video render ~/Projects/scr2026-amv-baseline --deliverable amv-15s
```
Verify: Process completes with exit 0 and writes `out/video/amv-15s.mp4`.

- [ ] **Step 5: Visual and loudness inspection with `ms see`**

Run:
```bash
ms see ~/Projects/scr2026-amv-baseline/out/video/amv-15s.mp4
```
Read: Inspect contact sheet PNG and verify loudness is within `-14.0 ± 1.0 LUFS`.

- [ ] **Step 6: Log friction points**

Append any stumbling blocks or manual workarounds encountered into `docs/notes/prompt-to-result-friction.md`.

- [ ] **Step 7: Commit friction log update**

```bash
git add docs/notes/prompt-to-result-friction.md
git commit -m "docs(notes): record AMV beat-sync acceptance test friction"
```

---

### Task 5: Acceptance Test 2 — Social Graphic / Thumbnail Workflow

**Files:**
- Test Project: `~/Projects/scr2026-amv-baseline/`
- Output Log: `docs/notes/prompt-to-result-friction.md`

**Interfaces:**
- Consumes: Prompt `"Buat YouTube thumbnail 16:9 untuk AMV JJK tersebut dengan judul 'UNSTOPABLE BEATS', gunakan cutout karakter Gojo dan background bertema energi kutukan"`
- Produces: 1920x1080 thumbnail PNG, `ms see` verification, and friction log entries.

- [ ] **Step 1: Extract/isolate character foreground**

Extract Gojo cutout from raw footage or frame using background removal:
```bash
ms image remove-bg ~/Projects/scr2026-amv-baseline/assets/stills/gojo-frame.jpg --model birefnet-lite --out ~/Projects/scr2026-amv-baseline/assets/design/gojo-cutout.png
```
Verify: Isolated subject with transparent background.

- [ ] **Step 2: Render composite graphic via `ms design` / SVG template**

Assemble design deliverable (1920x1080) with title text `"UNSTOPABLE BEATS"`, high-contrast typography, and energy background:
```bash
ms design render ~/Projects/scr2026-amv-baseline --deliverable thumbnail-16x9
```
Verify: Generated PNG at `out/design/thumbnail-16x9.png`.

- [ ] **Step 3: Visual inspection via `ms see`**

Run:
```bash
ms see ~/Projects/scr2026-amv-baseline/out/design/thumbnail-16x9.png
```
Read: Inspect sheet, verifying composition balance, text legibility, and edge cutout cleanliness.

- [ ] **Step 4: Log friction points**

Record any CLI limitations, font sizing friction, or template constraints in `docs/notes/prompt-to-result-friction.md`.

- [ ] **Step 5: Commit friction log update**

```bash
git add docs/notes/prompt-to-result-friction.md
git commit -m "docs(notes): record thumbnail design acceptance test friction"
```

---

### Task 6: Acceptance Test 3 — Motion Graphics Intro Sequence & Synthesis

**Files:**
- Test Project: `~/Projects/scr2026-amv-baseline/`
- Output Log: `docs/notes/prompt-to-result-friction.md`

**Interfaces:**
- Consumes: Prompt `"Buat title animation intro 5 detik dengan teks 'PROJECT GOJO', gaya anime minimalis futuristik"`
- Produces: 5.0s rendered motion graphic deliverable, `ms see` inspection, and synthesized friction analysis.

- [ ] **Step 1: Define motion sequence**

Configure 5-second title animation in `project.yaml` under `motion:` or via `ms motion title`:
```bash
ms motion title "PROJECT GOJO" --duration 5.0 --style kinetic-future --out ~/Projects/scr2026-amv-baseline/out/motion/title-intro.mp4
```

- [ ] **Step 2: Render motion deliverable**

Verify: Headless execution renders `out/motion/title-intro.mp4` at exact 5.0s duration and 1920x1080 resolution.

- [ ] **Step 3: Visual inspection via `ms see`**

Run:
```bash
ms see ~/Projects/scr2026-amv-baseline/out/motion/title-intro.mp4
```
Read: Inspect contact sheet frames, verifying animation curve smoothness and typography positioning.

- [ ] **Step 4: Synthesize friction log & prioritize tool additions**

Analyze all recorded friction items across Video, Design, and Motion tests:
- Identify recurring manual steps that should become automated CLI flags or subcommands in `ms`.
- Update `docs/notes/prompt-to-result-friction.md` with final synthesis and recommendations.

- [ ] **Step 5: Final commit**

```bash
git add docs/notes/prompt-to-result-friction.md
git commit -m "docs(notes): synthesize prompt-to-result friction findings and next steps"
```

