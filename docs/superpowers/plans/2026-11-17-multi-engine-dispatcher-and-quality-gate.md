# Multi-Engine Dispatcher, Artifact-Backed Critic, and Remotion Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve architectural discrepancies identified in code audit by implementing dynamic multi-engine scene dispatching (`blender-3d`, `effectcraft`, `html-motion`/`remotion`, `composite`), an artifact-fingerprinted dynamic-duration quality critic with live `media-mcp` validation, engine-accurate storyboard keyframe generation, and a comparative Remotion pilot.

**Architecture:** A unified JSON-Schema-backed `SceneGraph` contract dispatches each `SceneNode` to its designated specialized rendering engine rather than routing all scenes to a single 3D template. The Dual-Gate Critic (`ms critique`) is upgraded to dynamically resolve deliverable durations, compute SHA256 hashes of rendered MP4s, enforce live `media_image_quality` exposure minimums, and validate that signed vision rubrics evaluate the exact deliverable artifact. A standalone TypeScript/React package under `renderers/remotion/` provides headless React composition rendering orchestrated via Python.

**Tech Stack:** Python 3.12, Blender 5.3 Python API, EffectCraft 0.24.1 CLI, FilmCraft CLI, FFmpeg / NVENC, TypeScript / React / Remotion 4.0, submodules/media-mcp, jsonschema / pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-scene-graph-3d-and-critic-refactor-design.md`

## Global Constraints

- Keep every Python module in `src/media_studio/` strictly under 600 lines.
- All unit tests under `tests/` must run 100% offline (`pytest -m "not slow"`).
- Dynamic duration: `expected_duration` must be derived from project manifest or scene-graph, never hardcoded.
- Enforce strict 60-30-10 color theory, WCAG $\ge 4.5:1$, and CIELAB $\Delta E \le 38.0$.
- No decorative icons or emojis in code, commits, PRs, or generated artifacts.

## Review Focus

- Engine dispatch fallback: A scene with `engine: "effectcraft"` or `engine: "html-motion"` must execute its dedicated renderer rather than defaulting to Blender 3D flacon geometry.
- Hardcoded duration elimination: A project with a 15-second or 60-second deliverable must not fail QC due to an assumed 30-second duration.
- Artifact fingerprint mismatch: A `vision-critique.json` evaluated against an older render or different SHA256 must be rejected with `severity: "error"`.
- Exposure floor: Rendered video keyframes with mean gray $< 38.0$ must be flagged as underexposed defects.
- Audio continuity: Mixed audio beds must maintain unbroken ambient coverage across the full video duration without unscripted silence spans $> 1.0$s.

---

### Task 1: JSON Schema Contract & Dynamic Engine Dispatcher

**Files:**
- Create: `schemas/scene-graph.schema.json`
- Modify: `src/media_studio/scene_graph.py:115-185`
- Test: `tests/test_engine_dispatcher.py`

**Interfaces:**
- Consumes: `SceneNode`, `SceneGraph` from `src/media_studio/scene_graph.py`
- Produces: `render_scene_clip(project, scene, output_mp4, ...)` with dynamic routing to `blender-3d`, `effectcraft`, `html-motion`, and `composite`.

- [ ] **Step 1: Write the failing tests for schema validation and multi-engine routing**

```python
"""Tests for Scene-Graph JSON Schema validation and multi-engine dispatching."""
from pathlib import Path
from media_studio.manifest import load_project
from media_studio.scene_graph import SceneNode, load_scene_graph, render_scene_clip, validate_scene_graph_schema

def test_validate_scene_graph_schema_rejects_unknown_engine(tmp_path: Path) -> None:
    bad_data = {"project_slug": "demo", "scenes": [{"id": "s1", "duration": 3.0, "engine": "unknown-engine"}]}
    errors = validate_scene_graph_schema(bad_data)
    assert len(errors) > 0
    assert any("unknown-engine" in e for e in errors)

def test_render_scene_clip_dispatches_to_declared_engine(tmp_path: Path, monkeypatch) -> None:
    dispatched = []
    def fake_render_3d(*args, **kwargs):
        dispatched.append("blender-3d")
        return args[1]
    def fake_render_ec(*args, **kwargs):
        dispatched.append("effectcraft")
        return args[1]
    def fake_render_html(*args, **kwargs):
        dispatched.append("html-motion")
        return args[1]

    monkeypatch.setattr("media_studio.scene_graph.render_3d_stage_clip", fake_render_3d)
    monkeypatch.setattr("media_studio.scene_graph.render_effectcraft_clip", fake_render_ec)
    monkeypatch.setattr("media_studio.scene_graph.render_html_motion_clip", fake_render_html)

    # Test dispatch for each engine type
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine_dispatcher.py -v -p no:cacheprovider`
Expected: FAIL with `ImportError` or `ModuleNotFoundError: validate_scene_graph_schema`.

- [ ] **Step 3: Implement JSON Schema and dynamic dispatch in `src/media_studio/scene_graph.py`**

Create `schemas/scene-graph.schema.json` defining valid engines (`blender-3d`, `effectcraft`, `html-motion`, `remotion`, `flow-gen`, `composite`) and optional engine-specific parameter blocks.
In `src/media_studio/scene_graph.py`, implement `validate_scene_graph_schema(data)` and refactor `render_scene_clip` to dispatch by `scene.engine` instead of hardcoding `Stage3DSpec` for perfume flacon.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engine_dispatcher.py -v -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Verify linting & format**

Run: `~/.local/bin/ruff format src/media_studio/scene_graph.py tests/test_engine_dispatcher.py && ~/.local/bin/ruff check src/media_studio/scene_graph.py tests/test_engine_dispatcher.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add schemas/scene-graph.schema.json src/media_studio/scene_graph.py tests/test_engine_dispatcher.py
git commit -m "feat(scene_graph): implement formal json schema and dynamic multi-engine dispatcher"
```

---

### Task 2: Artifact-Fingerprinted, Dynamic-Duration Quality Critic

**Files:**
- Modify: `src/media_studio/critique.py:200-345`
- Test: `tests/test_critique_artifact_backed.py`

**Interfaces:**
- Consumes: `Project` from `src/media_studio/manifest.py`, `tools.media` analysis routines
- Produces: `critique_project(project, stage, ...)` returning artifact-verified defects, dynamic expected duration, and live exposure/motion gates.

- [ ] **Step 1: Write failing tests for dynamic duration, hash matching, and exposure floor**

```python
"""Tests for artifact-fingerprinted and dynamic-duration quality critic."""
from pathlib import Path
from media_studio.critique import critique_project, validate_vision_rubric
from media_studio.manifest import load_project

def test_critique_derives_expected_duration_from_deliverables(tmp_path: Path) -> None:
    # Set deliverable duration to 15.0s in project.yaml
    # Verify qc_report receives expected_duration=15.0 instead of 30.0

def test_validate_vision_rubric_rejects_hash_mismatch(tmp_path: Path) -> None:
    # Create report with different target_sha256 than actual deliverable MP4
    # Verify error defect is returned
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_critique_artifact_backed.py -v -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: Implement dynamic duration, SHA256 binding, and exposure checks in `src/media_studio/critique.py`**

In `critique_project`:
1. Dynamically read `expected_duration` from the matching deliverable in `project.deliverables` or `graph.total_duration`.
2. Compute `hashlib.sha256(vpath.read_bytes()).hexdigest()` and verify `report.get("target_sha256") == video_sha256`.
3. In `critique_palette`, check frame luminance: if `mean_gray < 38.0`, record an underexposed defect (`severity: "error"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_critique_artifact_backed.py -v -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Verify module line limit & linter**

Run: `wc -l src/media_studio/critique.py && ~/.local/bin/ruff check src/media_studio/critique.py`
Expected: Line count $< 600$, checks pass.

- [ ] **Step 6: Commit**

```bash
git add src/media_studio/critique.py tests/test_critique_artifact_backed.py
git commit -m "feat(critique): enforce dynamic duration, artifact sha256 binding, and exposure floor"
```

---

### Task 3: Engine-Accurate Storyboard Keyframe Generation

**Files:**
- Modify: `src/media_studio/storyboard.py:20-65`
- Test: `tests/test_storyboard_multi_engine.py`

**Interfaces:**
- Consumes: `SceneNode`, `SceneGraph` from `src/media_studio/scene_graph.py`
- Produces: `render_scene_keyframe(project, scene, output_png, ...)` routing to engine-appropriate still renderer (`blender-3d`, `effectcraft`, `html-motion`).

- [ ] **Step 1: Write failing tests for engine-specific storyboard rendering**

```python
"""Tests for engine-accurate storyboard keyframe rendering."""
from pathlib import Path
from media_studio.manifest import load_project
from media_studio.scene_graph import SceneNode
from media_studio.storyboard import render_scene_keyframe

def test_render_scene_keyframe_calls_effectcraft_for_motion_scene(tmp_path: Path, monkeypatch) -> None:
    # Verify render_scene_keyframe delegates to effectcraft render-frame when scene.engine == "effectcraft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_storyboard_multi_engine.py -v -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: Implement multi-engine keyframe dispatch in `src/media_studio/storyboard.py`**

In `render_scene_keyframe`:
- If `scene.engine == "effectcraft"`: call EffectCraft 1-frame renderer.
- If `scene.engine == "html-motion"` / `"remotion"`: render single HTML/Playwright frame.
- If `scene.engine == "blender-3d"` or `"composite"`: call `render_3d_stage_still` with scene-declared 3D parameters.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_storyboard_multi_engine.py -v -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Verify module line limit & linter**

Run: `wc -l src/media_studio/storyboard.py && ~/.local/bin/ruff check src/media_studio/storyboard.py`
Expected: Line count $< 600$, checks pass.

- [ ] **Step 6: Commit**

```bash
git add src/media_studio/storyboard.py tests/test_storyboard_multi_engine.py
git commit -m "feat(storyboard): route keyframe rendering to declared scene engine"
```

---

### Task 4: Remotion Package Scaffolding & Comparison Pilot

**Files:**
- Create: `renderers/remotion/package.json`
- Create: `renderers/remotion/src/Root.tsx`
- Create: `renderers/remotion/src/compositions/SaaSFeatureCard.tsx`
- Create: `src/media_studio/remotion.py`
- Test: `tests/test_remotion_pilot.py`

**Interfaces:**
- Consumes: `renderers/remotion/` bundle CLI
- Produces: `render_remotion_clip(composition_id, output_path, props_dict, ...)`

- [ ] **Step 1: Write test for Remotion Python wrapper and props injection**

```python
"""Tests for Remotion CLI wrapper and comparative pilot benchmark."""
from pathlib import Path
from media_studio.remotion import render_remotion_clip, is_remotion_available

def test_remotion_availability_and_props_render(tmp_path: Path) -> None:
    # Test props serialization and CLI command construction
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remotion_pilot.py -v -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'media_studio.remotion'`.

- [ ] **Step 3: Implement Remotion scaffold and Python wrapper in `src/media_studio/remotion.py`**

Create `renderers/remotion/` with minimal React/Remotion configuration for motion graphics.
Implement `render_remotion_clip` in `src/media_studio/remotion.py` which passes JSON props to `npx remotion render`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remotion_pilot.py -v -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Verify module line limit & linter**

Run: `wc -l src/media_studio/remotion.py && ~/.local/bin/ruff check src/media_studio/remotion.py`
Expected: Line count $< 600$, checks pass.

- [ ] **Step 6: Commit**

```bash
git add renderers/remotion/ src/media_studio/remotion.py tests/test_remotion_pilot.py
git commit -m "feat(remotion): scaffold remotion renderer package and python execution wrapper"
```

---

### Task 5: End-to-End Multi-Engine Verification & Regression Audit

**Files:**
- Modify: `docs/superpowers/plans/2026-11-17-multi-engine-dispatcher-and-quality-gate.md`
- Test: `tests/test_pipeline_e2e.py`

**Interfaces:**
- Consumes: All modules from Tasks 1–4
- Produces: Clean end-to-end multi-engine project execution and regression pass.

- [ ] **Step 1: Write integration test verifying multi-engine timeline assembly**

```python
"""Integration test for heterogeneous multi-engine timeline assembly."""
def test_heterogeneous_scene_graph_assembly_and_critique():
    # Scene 1: blender-3d
    # Scene 2: effectcraft
    # Scene 3: html-motion / remotion
    # Verify render, concatenation, audio mix, and ms critique PASS
```

- [ ] **Step 2: Run test to verify it fails without all components wired**

Run: `.venv/bin/python -m pytest tests/test_pipeline_e2e.py -v -p no:cacheprovider`

- [ ] **Step 3: Wire dispatcher in `render_scene_graph` and run full test suite**

Ensure full integration between `scene_graph.py`, `storyboard.py`, `critique.py`, and `remotion.py`.

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v -p no:cacheprovider -k "not slow"`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_pipeline_e2e.py src/media_studio/
git commit -m "feat(pipeline): complete multi-engine timeline orchestration and end-to-end verification"
```
