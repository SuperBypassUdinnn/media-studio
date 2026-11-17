# Media-Studio Design Toolchain Completion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the graphic-design toolchain — sync the stale docs, add ImageMagick finishing to `ms design render`, register design presets, add a logo derivative kit, and ship the SVG helper suite (build / trace / compare).

**Architecture:** Extend the existing modules in place (`design.py`, `manifest.py`, `presets.py`, `cli.py`, `tools.py`); add two focused new modules: `svg_kit.py` (declarative JSON-to-SVG builder, two-stage tracer, measurable comparison) and `logo.py` (logo derivative kit). Reuse the established Inkscape/ImageMagick subprocess patterns and the `design.py` helpers (`brand_tokens`, `font_families`, `font_environment`, `query_box`) instead of duplicating them.

**Tech Stack:** Python 3.12, argparse CLI (`ms = media_studio.cli:main`), pytest, Inkscape 1.4.4 CLI, ImageMagick 7.1.2 (`magick`), potrace 1.16, vtracer as the optional extra `vector`.

**Spec:** `docs/superpowers/specs/2026-11-10-design-svg-helper-design.md`

## Global Constraints

- No `git commit`, `git push`, or PR work in this repository unless the user explicitly authorizes it in the session. Every task ends with the working tree containing the change, all tests green, and nothing committed.
- Python 3.12; ruff `line-length = 100`, lint select `E,F,I,UP,B,SIM,RUF`; `ruff format` clean.
- Tests stay offline: no `network` marker, no new test dependency. Test fixtures are generated with `magick` or written as literal files.
- Every subprocess call captures output and raises with the failing tool's stderr tail (max 2000 chars), mirroring `DesignError` and `IngestError`.
- Reuse `design.py` helpers for tokens, fonts, fontconfig, and geometry queries; no duplicated logic.
- The only new dependency is the optional extra `vector = ["vtracer>=0.6"]`; the default install stays dependency-free and potrace remains the fallback.
- `ms doctor` reports vtracer availability but never fails when it is absent.
- Controller after each task: run `codegraph sync /home/superbypassudin/.clone/Github/SuperBypassUdinnn/media-studio` (local tooling; never mention it in any commit or PR text).

## Review Focus

1. A spec with an unknown element type, missing required field, or bad anchor must raise `SvgError` before any file is written — no half-written SVG.
2. Raster tracing of AI-generated PNGs with semi-transparent edges must flatten before tracing (fixed preprocessing order) so the result has no black matte or avoidable holes.
3. Text must stay text with stable ids (`el-text-N`) so the Inkscape-measured contrast/overflow checks attach; never path-ify text.
4. `build --base` must preserve the base canvas/viewBox and existing content bytes; appended primitives land after the last child.
5. ImageMagick finishing must never break the declared-canvas size check: the size check measures the raw Inkscape export, and the final size is reported separately.
6. `svg compare` on mismatched aspect ratios must not crash: the render is resampled to the source dimensions for diffing and the distortion is stated in the report.

## File Structure

| File | Action | Responsibility |
| --- | --- | --- |
| `src/media_studio/design.py` | modify | `VariantSpec` finish fields; `_finish_png`; final-size reporting |
| `src/media_studio/manifest.py` | modify | parse/validate `trim`, `pad`, `profile` variant keys |
| `src/media_studio/presets.py` | modify | `design` kind + template/size validation |
| `presets/design-*.yaml` | create | four design recipes |
| `src/media_studio/svg_kit.py` | create | geometry kernel, spec builder, tracer, compare |
| `src/media_studio/logo.py` | create | logo derivative kit |
| `src/media_studio/cli.py` | modify | wire `design svg build|trace|compare` and `design logo` |
| `src/media_studio/tools.py` | modify | doctor vtracer check |
| `pyproject.toml` | modify | optional extra `vector` |
| `tests/test_design.py` | modify | finishing tests |
| `tests/test_presets.py` | modify | design-preset render proof |
| `tests/test_svg_kit.py` | create | kernel, build, trace, compare, patch loop |
| `tests/test_logo.py` | create | logo kit tests |
| `README.md` | modify | status accuracy and new surfaces |
| `~/.config/opencode/commands/design.md` | modify | runner-first command guidance |
| `~/.config/opencode/skills/media-studio-design/SKILL.md` | modify | current status and new surfaces |

---

### Task 1: Sync the stale design documentation

**Files:**
- Modify: `~/.config/opencode/commands/design.md`
- Modify: `~/.config/opencode/skills/media-studio-design/SKILL.md`
- Modify: `README.md` (media-studio)

**Interfaces:**
- Consumes: the existing command `ms design render <project> [--variant X] [--check-only]`, run from the media-studio checkout as `.venv/bin/ms`.
- Produces: nothing code-wise; later tasks (9) extend these files again.

- [ ] **Step 1: Rewrite the `/design` command instructions**

Replace the agent-instruction steps so the runner is primary: step 4 becomes "Run `.venv/bin/ms design render <project> [--variant <id>] [--check-only]` from the media-studio checkout; use it for every declared variant". Keep `--check-only` semantics, the brand-token reading step, the report-the-numbers step, and the out-of-scope step. Delete step 8's claim that the phase 1 runner is not implemented; the new closing instruction is to report which files the runner wrote and whether `problems` was empty. Keep the manual Inkscape export only as an explicit fallback sentence for when the runner is unavailable.

- [ ] **Step 2: Update the skill Status section**

In `media-studio-design/SKILL.md`, replace the Status paragraph with: phase 1 rendering path is implemented (`ms design render` composes, exports PNG/PDF through Inkscape, and publishes contrast/overflow/font-size/pixel-size checks plus a contact sheet and QC report). Replace "Until the runner exists, generate the SVG and export it with an explicit `inkscape` command" with a sentence that names the runner first and keeps the manual fallback. Keep the toolchain versions.

- [ ] **Step 3: Fix the README status claims**

In `README.md`, replace "Status: phase 0 complete, phase 1 started." with an accurate phase 0 + phase 1 statement (all phase 1 paths landed unless the document says otherwise), and replace the sentence "The 3D and design paths, footage triage, and the preset library are still open." with the accurate remainder: a downloaded preset pack staged through the ledger, and the phase 3 `scr2026` baseline. Add `ms design render` to the "Using it" command block beside the other design-adjacent commands.

- [ ] **Step 4: Verify no stale claims remain**

Run: `grep -rn "not implemented\|still open\|Until the runner exists" ~/.config/opencode/commands/design.md ~/.config/opencode/skills/media-studio-design/SKILL.md README.md`
Expected: no match for the three stale phrases.

---

### Task 2: ImageMagick finishing for `ms design render`

**Files:**
- Modify: `src/media_studio/manifest.py` (`VARIANT_KEYS`, variant parsing, unknown-field validation)
- Modify: `src/media_studio/design.py` (`VariantSpec`, `render_variant`)
- Test: `tests/test_design.py`

**Interfaces:**
- Consumes: existing `VariantSpec(id, template, width, height, headline, subhead)` and `render_variant(project, variant, environment)`.
- Produces: `VariantSpec.trim: bool = False`, `VariantSpec.pad: int = 0`, `VariantSpec.profile: str | None = None`; `render_variant` result gains `"finish": {"trim": bool, "pad": int, "profile": str | None, "final_size": [w, h]}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_design.py`, add three tests reusing the existing inline-project helper style:

```python
def test_trim_and_pad_shrink_then_reframe_the_png(tmp_path):
    project = _project_with_variant(tmp_path, extra="trim: true, pad: 12")
    report = design.render(project)
    finish = report.variants[0]["finish"]
    assert finish["trim"] is True and finish["pad"] == 12
    w, h = finish["final_size"]
    assert w <= 1080 and h <= 1080  # trimmed content plus 2x12 border never exceeds canvas

def test_finish_key_value_validation_rejects_bad_input(tmp_path):
    # pad: -4, profile: adobe-rgb, trim: "yes" each produce a manifest problem
    problems = design.validate_design(_project_with_variant(tmp_path, extra="pad: -4"))
    assert any("pad" in problem for problem in problems)

def test_finish_profile_is_recorded(tmp_path):
    report = design.render(_project_with_variant(tmp_path, extra="profile: srgb"))
    assert report.variants[0]["finish"]["profile"] == "srgb"
```

Add the `_project_with_variant(tmp_path, extra)` helper beside the existing fixtures (it writes a `project.yaml` with one `post` variant, 1080x1080, template `social-post`, and the extra YAML keys inline).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_design.py -q`
Expected: FAIL (`finish` key missing / unknown-field validation).

- [ ] **Step 3: Implement the manifest keys and the finish step**

In `manifest.py`: `VARIANT_KEYS = frozenset({"id", "template", "size", "headline", "subhead", "trim", "pad", "profile"})`; parse `trim` as strict bool, `pad` as int in `0..1000`, `profile` in `(None, "srgb")`, raising `ManifestError` with the offending value. In `design.py`: extend `VariantSpec` with the three defaults; in `render_variant`, after the existing pixel-size check and before returning, when any finish field is set run `magick <png> -trim +repage -bordercolor none -border <pad> -colorspace sRGB <png>` (only the requested stages, in the order trim, border, colorspace), record `final_size` with `_png_size`, and append a problem if magick fails.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_design.py -q`
Expected: PASS (16 old + 3 new).

- [ ] **Step 5: Lint**

Run: `ruff check src/media_studio/design.py src/media_studio/manifest.py tests/test_design.py && ruff format --check src/media_studio/design.py src/media_studio/manifest.py tests/test_design.py`
Expected: clean.

### Task 3: Register design presets in the engine-native registry

**Files:**
- Modify: `src/media_studio/presets.py` (`KINDS`, `validate_recipe`)
- Create: `presets/design-social-post.yaml`, `presets/design-story.yaml`, `presets/design-poster-a3.yaml`, `presets/design-video-thumbnail.yaml`
- Test: `tests/test_presets.py`

**Interfaces:**
- Consumes: `Recipe` schema (`id`, `title`, `kind`, `engine`, `intent`, `applies_to`, `summary`, `implementation`, `parameters`, `license`, `provenance`), `validate_recipe(recipe) -> list[str]`, `load_recipes()`, `design.template_directory()`, `design.render(project, variant_id=None)`.
- Produces: `KINDS = ("transition", "video-effect", "audio-effect", "title", "design")`; four recipes whose `implementation` carries `template` (an existing `templates/design/*.svg` stem) and `size` (`WxH`).

- [ ] **Step 1: Write the failing test**

In `tests/test_presets.py`:

```python
def test_every_design_recipe_renders_through_the_design_path(tmp_path):
    recipes = [r for r in load_recipes().values() if r.kind == "design"]
    assert {r.id for r in recipes} >= {"design-social-post", "design-story",
                                       "design-poster-a3", "design-video-thumbnail"}
    for recipe in recipes:
        assert validate_recipe(recipe) == []
        project = _project_from_recipe(tmp_path / recipe.id, recipe)
        report = design.render(project)
        assert report.ok, report.problems
        assert (project.root / "out/images/post.png").is_file()
```

Add `_project_from_recipe(root, recipe)` beside the existing fixture helpers: it writes a `project.yaml` with one variant `post` using `implementation["template"]`, `implementation["size"]` split into width/height, and the `headline`/`subhead` parameter defaults.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_presets.py -q`
Expected: FAIL (no design recipes; kind rejected).

- [ ] **Step 3: Implement the kind and the four recipes**

In `presets.py`: add `"design"` to `KINDS`; in `validate_recipe` add — design kind requires `implementation.get("template")` (a stem that exists in `design.template_directory()`), and `implementation.get("size")` when present must match `^\d+x\d+$`. Create the four YAML files: `kind: design`, `engine: inkscape`, `applies_to: [design]`, intent tags (`social`, `instagram`, `post` / `story`, `vertical` / `poster`, `print`, `a3` / `thumbnail`, `youtube`), `implementation: {template: social-post|poster-a3|video-thumbnail, size: 1080x1080|1080x1920|3508x4961|1280x720}`, parameters `headline` (string, default "Headline") and `subhead` (string, default "Subhead"), `license: CC0-1.0`, provenance naming the template and the verified canvas.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_presets.py -q`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `ruff check src/media_studio/presets.py tests/test_presets.py && ruff format --check src/media_studio/presets.py tests/test_presets.py`
Expected: clean.

---

### Task 4: `svg_kit` geometry kernel — `taper` and `arc`

**Files:**
- Create: `src/media_studio/svg_kit.py`
- Test: `tests/test_svg_kit.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SvgError(RuntimeError)`; `taper_path(element: dict[str, object]) -> str` (the `d` value of one closed outline); `arc_path(element: dict[str, object]) -> str` (the `d` value of one arc path).

- [ ] **Step 1: Write the failing tests**

In `tests/test_svg_kit.py` (helper `_numbers(d)` extracts floats from a path string):

```python
def test_taper_straight_spine_starts_and_ends_at_half_width_offsets():
    d = taper_path({"from": [100, 100], "angle": 0, "length": 100,
                    "widths": [20, 10], "caps": "flat"})
    xs, ys = _numbers(d)[0::2], _numbers(d)[1::2]
    assert min(xs) == pytest.approx(100, abs=0.5) and max(xs) == pytest.approx(200, abs=0.5)
    assert min(ys) == pytest.approx(90, abs=0.5)   # half of the 20 px base width above the spine
    assert max(ys) == pytest.approx(105, abs=0.5)  # half of the 10 px tip width below the spine
    assert d.rstrip().endswith("Z")

def test_taper_width_arity_mismatch_raises():
    with pytest.raises(SvgError, match="widths"):
        taper_path({"points": [[0, 0], [50, 0], [100, 0]], "widths": [10, 4]})

def test_taper_bend_bulges_toward_the_bend_sign():
    d = taper_path({"from": [0, 0], "angle": 0, "length": 100, "bend": 30, "width": 10})
    xs, ys = _numbers(d)[0::2], _numbers(d)[1::2]
    middle = [y for x, y in zip(xs, ys) if 40 <= x <= 60]
    assert min(middle) < -10  # bulge is half the bend (15 px) above the spine, plus half width

def test_arc_sagitta_bulges_and_pie_closes():
    d = arc_path({"from": [0, 0], "to": [100, 0], "sag": 20, "close": "pie"})
    assert "A" in d and d.rstrip().endswith("Z")

def test_arc_explicit_angles_are_clockwise_on_screen():
    d = arc_path({"cx": 50, "cy": 50, "rx": 40, "ry": 40, "start_deg": 0,
                  "end_deg": 90, "close": "open"})
    xs, ys = _numbers(d)[0::2], _numbers(d)[1::2]
    assert xs[0] == pytest.approx(90, abs=0.5) and ys[0] == pytest.approx(50, abs=0.5)
    assert xs[-1] == pytest.approx(50, abs=0.5) and ys[-1] == pytest.approx(90, abs=0.5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the kernel**

Create `svg_kit.py` with `SvgError`, `taper_path`, `arc_path` and private helpers: spine sampling (polyline resampled by arc length; quadratic Bézier for `from/angle/length/bend`, 32 samples, `bend` = signed perpendicular midpoint offset in px), per-sample half width (constant / linear base→tip / piecewise per vertex), normals from central differences, left/right offset polylines, round or flat caps, and Catmull-Rom→cubic-Bézier emission of one closed path. Angles: 0 = +x, positive clockwise on screen (y-down). `arc_path`: explicit center form and `from/to/sag` chord form (sagitta = bow at chord midpoint), with `close: pie|chord|open`; emit `A` segments with `large-arc`/`sweep` flags consistent with the clockwise convention.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py -q`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `ruff check src/media_studio/svg_kit.py tests/test_svg_kit.py && ruff format --check src/media_studio/svg_kit.py tests/test_svg_kit.py`
Expected: clean.

### Task 5: `svg_kit` declarative builder and `ms design svg build`

**Files:**
- Modify: `src/media_studio/svg_kit.py`
- Modify: `src/media_studio/cli.py` (nested `design svg` parsers and `cmd_design_svg_build`)
- Test: `tests/test_svg_kit.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `design.brand_tokens(project)`, `design.font_families(project, environment)`, `design.font_environment(project)`, `design.check_contrast(svg_text)`, `design.check_min_font_size(svg_text)`, `design.query_box(svg_path, element_id)`; `taper_path`, `arc_path`.
- Produces: `build_spec(spec: dict[str, object], *, project: Project | None = None, base: str | None = None) -> str`; `write_svg(text: str, out: Path) -> Path`; `render_png(svg_path: Path, png_path: Path, *, width: int, height: int | None = None, environment: dict[str, str] | None = None) -> Path`; `check_safe_area(svg_path: Path, safe_area: dict[str, float] | float) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_svg_kit.py`:

```python
def test_build_renders_every_v1_primitive_and_substitutes_tokens(tmp_path):
    spec = _full_spec()  # every element type from the spec's §4.2 table
    svg = build_spec(spec)
    for tag in ("<rect", "<circle", "<ellipse", "<line", "<polygon", "<polyline",
                "<path", "<image", "<g", "<text"):
        assert tag in svg
    assert 'fill="#C8A24A"' in svg          # default accent token

def test_anchor_right_places_the_element_edge_at_the_safe_margin():
    svg = build_spec({"canvas": {"width": 1000, "height": 1000}, "safe_area": 50,
                      "elements": [{"type": "rect", "x": "right", "y": 0,
                                    "width": 100, "height": 10}]})
    assert 'x="850"' in svg

def test_stack_vertical_gap_offsets_children():
    svg = build_spec({"canvas": {"width": 100, "height": 100},
                      "elements": [{"type": "stack", "direction": "vertical", "gap": 5,
                                    "x": 0, "y": 100, "children": [
                                        {"type": "rect", "width": 10, "height": 10},
                                        {"type": "rect", "width": 10, "height": 10}]}]})
    assert 'y="100"' in svg and 'y="115"' in svg

def test_text_lines_become_tspans_with_a_stable_id():
    svg = build_spec({"canvas": {"width": 100, "height": 100},
                      "elements": [{"type": "text", "text": "a\nb", "x": 10, "y": 20,
                                    "size": 12, "role": "body"}]})
    assert svg.count("<tspan") == 2 and 'id="el-text-1"' in svg

def test_base_append_keeps_the_canvas_and_existing_content():
    base = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect id="keep" width="10" height="10" fill="#000"/></svg>')
    svg = build_spec({"elements": [{"type": "circle", "cx": 5, "cy": 5, "r": 2}]}, base=base)
    assert 'viewBox="0 0 10 10"' in svg and 'id="keep"' in svg and "<circle" in svg

def test_unknown_element_type_raises():
    with pytest.raises(SvgError, match="unknown element"):
        build_spec({"canvas": {"width": 10, "height": 10},
                    "elements": [{"type": "hexagon"}]})

def test_text_outside_the_safe_area_is_reported(tmp_path):
    svg_path = write_svg(build_spec({"canvas": {"width": 200, "height": 100}, "safe_area": 10,
                                     "elements": [{"type": "text", "text": "x", "x": 100,
                                                   "y": 99, "size": 40, "role": "body"}]}),
                         tmp_path / "x.svg")
    problems = check_safe_area(svg_path, 10)
    assert any("safe area" in problem for problem in problems)
```

In `tests/test_cli.py`:

```python
def test_design_svg_build_writes_the_file(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"canvas": {"width": 100, "height": 100},
                                "elements": [{"type": "rect", "width": 10, "height": 10}]}))
    code = main(["design", "svg", "build", "--spec", str(spec),
                 "--out", str(tmp_path / "out.svg")])
    assert code == 0 and (tmp_path / "out.svg").is_file()

def test_design_svg_build_rejects_a_bad_spec(tmp_path, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"elements": [{"type": "hexagon"}]}))
    assert main(["design", "svg", "build", "--spec", str(spec)]) == 1
    assert "error:" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py tests/test_cli.py -q`
Expected: FAIL (missing functions/commands).

- [ ] **Step 3: Implement the builder and the CLI**

In `svg_kit.py`: spec parsing with the exact vocabulary and validation from spec §4 (canvas required unless `base`; base `viewBox` supplies the canvas; `safe_area` number or per-edge dict; background solid; `variables`; token substitution `{primary}`, `{accent}`, `{ink}` via `design.brand_tokens` with the module defaults, fonts via `design.font_families`/`sans-serif`); anchors per §4.1; stack layout with the documented text-height estimate; every element type per §4.2; stable auto ids (`el-text-N`, `el-<type>-N`); append before `</svg>` when `base` is given. `check_safe_area` measures each id'd element with `design.query_box` and reports boxes crossing the inset. In `cli.py`: nested `svg` subparser with `build --spec/--project/--base/--out/--export/--check/--sheet`, project resolution through the existing `load_project`, `--check` running the design trio plus `check_safe_area` and writing `out/reports/<stem>-svg-qc.json`, `--export` calling `render_png` for png/pdf, `--sheet` writing one montage row beside the output; errors return `EXIT_FAILURE` with `error: <message>`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `ruff check src/media_studio/svg_kit.py src/media_studio/cli.py tests/test_svg_kit.py tests/test_cli.py && ruff format --check src/media_studio/svg_kit.py src/media_studio/cli.py tests/test_svg_kit.py tests/test_cli.py`
Expected: clean.

---

### Task 6: `svg_kit` two-stage tracer and `ms design svg trace`

**Files:**
- Modify: `src/media_studio/svg_kit.py`
- Modify: `src/media_studio/cli.py`
- Modify: `pyproject.toml` (optional extra `vector`)
- Modify: `src/media_studio/tools.py` (doctor check)
- Test: `tests/test_svg_kit.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `SvgError`; magick, potrace, inkscape; optional `vtracer`.
- Produces: `TraceResult(svg_path: Path, input_path: Path, engine: str, layers: int)`; `trace(source: Path, out: Path, *, engine: str = "auto", mode: str = "color", colors: int = 6, threshold: int = 50, flatten_bg: str | None = None, fill_holes: int = 0, despeckle: int = 0) -> TraceResult`.

- [ ] **Step 1: Write the failing tests**

```python
def test_trace_color_png_yields_one_group_per_color(tmp_path):
    source = _three_colour_png(tmp_path / "logo.png")   # magick: red, blue, green blocks
    result = trace(source, tmp_path / "logo.svg", engine="potrace", mode="color", colors=3)
    svg = result.svg_path.read_text()
    assert result.engine == "potrace" and result.layers == 3
    assert svg.count('<g id="trace-layer-') == 3
    fills = set(re.findall(r'fill="(#[0-9a-fA-F]{6})"', svg))
    assert fills >= {"#ff0000", "#0000ff", "#00ff00"}

def test_trace_preprocessing_flattens_alpha_and_removes_pinholes(tmp_path):
    source = _pinhole_png(tmp_path / "hole.png")        # semi-transparent edge + 2 px pinhole
    result = trace(source, tmp_path / "hole.svg", flatten_bg="#ffffff", fill_holes=3)
    assert result.input_path.is_file()
    alpha = _magick_fx(result.input_path, "%[fx:minima.a]")
    assert alpha == pytest.approx(1.0, abs=0.01)

@pytest.mark.skipif(not _has_vtracer(), reason="vtracer extra not installed")
def test_vtracer_engine_is_used_when_available(tmp_path):
    source = _three_colour_png(tmp_path / "logo.png")
    assert trace(source, tmp_path / "logo.svg", engine="auto").engine == "vtracer"

def test_explicit_vtracer_engine_without_the_extra_fails_with_the_install_hint(tmp_path):
    if _has_vtracer():
        pytest.skip("vtracer installed; the missing-extra path is unreachable here")
    with pytest.raises(SvgError, match=r"\[vector\]"):
        trace(_three_colour_png(tmp_path / "logo.png"), tmp_path / "logo.svg", engine="vtracer")
```

Plus a CLI test: `main(["design", "svg", "trace", str(png), "--out", str(svg)])` returns 0 and prints the layer count.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py tests/test_cli.py -q -k "trace or vtracer"`
Expected: FAIL.

- [ ] **Step 3: Implement tracing, the extra, and the doctor check**

`trace`: preprocess with magick into `<out stem>.trace-input.png` (flatten alpha over `flatten_bg`, `-morphology Close disk:R`, `-morphology Open disk:R`, in that order); vtracer via `import vtracer` → `vtracer.convert_image_to_svg_py(...)` with `colormode=color|binary`, `color_precision` derived from `colors`; potrace bw via `magick ... -threshold <PCT>%` to PBM + `potrace -s`; potrace color via `+dither -colors N`, one binary mask per quantized colour, `potrace -s` per mask, stacked as `<g id="trace-layer-N" fill="#...">` ordered by mask pixel count (vtracer keeps its native painter order). Normalise both engines' output so every colour lands in one `trace-layer-N` group; root `svg` sized to the source pixels; header comment with engine, parameters, and source SHA256. Dataclasses `TraceResult` as above. `pyproject.toml`: `vector = ["vtracer>=0.6"]`. `tools.py`: add `ToolSpec("vtracer", (sys.executable, "-c", "import vtracer"), False, "colour tracing (optional extra 'vector')")` so the doctor reports without failing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py tests/test_cli.py -q -k "trace or vtracer"`
Expected: PASS (vtracer test skips when the extra is absent).

- [ ] **Step 5: Lint**

Run: `ruff check src/media_studio/svg_kit.py src/media_studio/cli.py src/media_studio/tools.py tests/test_svg_kit.py tests/test_cli.py && ruff format --check src/media_studio/svg_kit.py src/media_studio/cli.py src/media_studio/tools.py tests/test_svg_kit.py tests/test_cli.py`
Expected: clean.

### Task 7: `svg_kit` comparison loop and `ms design svg compare`

**Files:**
- Modify: `src/media_studio/svg_kit.py`
- Modify: `src/media_studio/cli.py`
- Test: `tests/test_svg_kit.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `render_png`, `build_spec`, `taper_path`; magick `compare`, `-compose Multiply`, connected-components.
- Produces: `CompareReport(pixel_diff_pct: float, rmse: float, components_missing: int, components_extra: int, sheet: Path | None, distorted: bool)`; `compare(source: Path, svg: Path, *, out: Path | None = None, min_area: int = 16, project: Project | None = None) -> CompareReport`.

- [ ] **Step 1: Write the failing tests**

```python
def test_compare_detects_a_missing_shape_and_writes_the_sheet(tmp_path):
    source = _two_blocks_png(tmp_path / "source.png")   # 200x200: centre block + corner block
    svg = write_svg(build_spec({"canvas": {"width": 200, "height": 200}, "elements": [
        {"type": "rect", "x": 80, "y": 80, "width": 40, "height": 40, "fill": "#1e3a8a"}]}),
        tmp_path / "base.svg")
    report = compare(source, svg)
    assert report.components_missing >= 1 and report.pixel_diff_pct > 0
    assert report.sheet is not None and report.sheet.is_file()

def test_compare_on_identical_pixels_reports_near_zero(tmp_path):
    svg = write_svg(build_spec({"canvas": {"width": 120, "height": 120}, "elements": [
        {"type": "rect", "width": 120, "height": 120, "fill": "#1e3a8a"}]}), tmp_path / "x.svg")
    source = render_png(svg, tmp_path / "x.png", width=120, height=120)
    report = compare(source, svg, out=tmp_path / "same.png")
    assert report.pixel_diff_pct <= 2.0 and report.components_missing == 0

def test_compare_handles_mismatched_aspect_ratios(tmp_path):
    source = _two_blocks_png(tmp_path / "s.png")
    svg = write_svg(build_spec({"canvas": {"width": 100, "height": 100}, "elements": []}),
                    tmp_path / "sq.svg")
    report = compare(source, svg)
    assert report.distorted is True and report.pixel_diff_pct >= 0.0

def test_taper_patch_via_base_improves_the_compare_metric(tmp_path):
    source = _two_blocks_png(tmp_path / "source.png")
    base_svg = write_svg(build_spec({"canvas": {"width": 200, "height": 200}, "elements": [
        {"type": "rect", "x": 80, "y": 80, "width": 40, "height": 40, "fill": "#1e3a8a"}]}),
        tmp_path / "base.svg")
    before = compare(source, base_svg, out=tmp_path / "before.png")
    patched = build_spec({"elements": [{"type": "taper", "from": [30, 20], "angle": 90,
                                        "length": 20, "width": 20, "caps": "flat",
                                        "fill": "#1e3a8a"}]}, base=base_svg.read_text())
    after = compare(source, write_svg(patched, tmp_path / "patched.svg"),
                    out=tmp_path / "after.png")
    assert after.pixel_diff_pct < before.pixel_diff_pct
```

Plus CLI tests: `compare` prints the metrics and returns 0; with `--fail-above 0` on differing images it returns 1.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py tests/test_cli.py -q -k compare`
Expected: FAIL.

- [ ] **Step 3: Implement comparison and the CLI**

`compare`: render the SVG at the source's width and height (`--export-width` and `--export-height`, so a mismatched aspect is explicit and `distorted=True` when ratios differ); `pixel_diff_pct` from `magick compare -metric AE` (integer stderr) divided by source area, `rmse` from `-metric RMSE`; ink masks with `-background white -alpha remove -alpha off -colorspace gray -threshold 50% -negate`; missing = source mask multiplied by the negated render mask, extra = the reverse; crop-free connected components with `-define connected-components:area-threshold=<min_area> -connected-components 4` and count the reported objects; three-panel labelled sheet via `magick montage -label` (source | render | difference blend, `-compose difference -composite -auto-level`). `--fail-above` exits 1 above the threshold. CLI `compare` prints the four numbers plus the sheet path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_svg_kit.py tests/test_cli.py -q -k compare`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `ruff check src/media_studio/svg_kit.py src/media_studio/cli.py tests/test_svg_kit.py tests/test_cli.py && ruff format --check src/media_studio/svg_kit.py src/media_studio/cli.py tests/test_svg_kit.py tests/test_cli.py`
Expected: clean.

---

### Task 8: Logo derivative kit and `ms design logo`

**Files:**
- Create: `src/media_studio/logo.py`
- Modify: `src/media_studio/cli.py` (`design logo` parser and `cmd_design_logo`)
- Test: `tests/test_logo.py`

**Interfaces:**
- Consumes: `project.design.logo` (asset name), `project.assets` (relative path), `design.brand_tokens(project)`, `svg_kit.trace`, `svg_kit.render_png`, Inkscape and magick.
- Produces: `LogoEntry(variant: str, size: int, png: Path, problems: tuple[str, ...])`; `LogoReport(entries: tuple[LogoEntry, ...], sheet: Path | None, report_path: Path | None, problems: tuple[str, ...])`; `logo_kit(project: Project, *, sizes: tuple[int, ...] = (1024, 512, 256, 128, 64, 32), variants: tuple[str, ...] = ("color", "mono", "reversed"), clear_space_pct: float = 8.0) -> LogoReport`.

- [ ] **Step 1: Write the failing tests**

```python
def test_logo_kit_derives_every_variant_and_size(tmp_path):
    project = _logo_project(tmp_path)   # staged SVG logo: circle + text well inside the canvas;
                                        # project.yaml declares the asset and design.logo
    report = logo_kit(project, sizes=(256, 64, 32))
    assert len(report.entries) == 9
    assert all(entry.png.is_file() for entry in report.entries)
    assert report.sheet is not None and report.sheet.is_file()
    assert not report.problems

def test_mono_and_reversed_recolour_the_solid_fills(tmp_path):
    project = _logo_project(tmp_path)
    report = logo_kit(project, sizes=(64,))
    mono = (project.root / "out/images/logo/mono.svg").read_text()
    reversed_svg = (project.root / "out/images/logo/reversed.svg").read_text()
    assert 'fill="#101010"' in mono and 'fill="#FFFFFF"' in reversed_svg

def test_a_full_bleed_logo_fails_the_clear_space_check(tmp_path):
    project = _logo_project(tmp_path, full_bleed=True)
    report = logo_kit(project, sizes=(64,))
    assert any("clear space" in problem for problem in report.problems)

def test_a_raster_logo_is_traced_before_derivation(tmp_path):
    project = _logo_project(tmp_path, raster=True)   # magick PNG with transparent background
    report = logo_kit(project, sizes=(64,))
    assert (project.root / "out/images/logo/source.svg").is_file()
    assert report.report_path.is_file()
```

Plus a CLI test: `main(["design", "logo", str(project.root)])` returns 0 for a good logo and prints the sheet path; a full-bleed logo returns 1.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_logo.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the kit**

Resolve the source through `design.logo`: SVG is used as-is, raster goes through `svg_kit.trace` (engine auto, `flatten_bg="#ffffff"`, `fill_holes=1`) into `out/images/logo/source.svg`. Recolour variants with ElementTree: `color` unchanged, `mono` every solid `fill`/`stroke` (attributes and `style` declarations) becomes the ink token, `reversed` becomes `#FFFFFF`; gradients and embedded images are left untouched and noted in the report. Render each variant at each size (width = size, height auto) into `out/images/logo/<variant>-<size>.png`; clear-space check from the 1024 render's trimmed bounding box (smallest inset divided by canvas width must be at least `clear_space_pct`); minimum-size check from the 32 px render's mean alpha (at least 0.005, so a functionally empty icon fails); sheet via montage; report `out/reports/<slug>-logo.json`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_logo.py -q`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `ruff check src/media_studio/logo.py src/media_studio/cli.py tests/test_logo.py && ruff format --check src/media_studio/logo.py src/media_studio/cli.py tests/test_logo.py`
Expected: clean.

---

### Task 9: Document the new surfaces and verify the whole suite

**Files:**
- Modify: `README.md`
- Modify: `~/.config/opencode/commands/design.md`
- Modify: `~/.config/opencode/skills/media-studio-design/SKILL.md`

**Interfaces:**
- Consumes: every command shipped by Tasks 2–8.
- Produces: documentation only.

- [ ] **Step 1: Update README**

In `README.md`: add `ms design svg build|trace|compare` and `ms design logo` to "Using it"; extend the Design row of "What it provides" with the declarative builder, the two-stage tracer (potrace default, vtracer optional), the compare loop, and the logo kit; note `uv pip install -e '.[vector]'` for colour tracing in "Requirements". Keep the honest status lines from Task 1.

- [ ] **Step 2: Update the `/design` command**

Add an agent instruction: for ad-hoc or complex compositions use `ms design svg build --spec <file> [--export png,pdf] [--check]`; for a raster logo use `ms design logo <project>` (which traces automatically) and for PNG→SVG work run `ms design svg trace`, then iterate `ms design svg compare` plus targeted edits (including `taper` primitives via `--base`) until the reported `components_missing` and `pixel_diff_pct` stop improving. Keep the runner (`ms design render`) as the primary path for declared variants.

- [ ] **Step 3: Update the skill**

In `SKILL.md`: update Status to name `ms design render`, `ms design svg build|trace|compare`, and `ms design logo` as implemented surfaces; add a short "SVG helper suite" section (declarative spec, `taper`/`arc` primitives, trace ladder, compare metrics) and a "Logo kit" section (derivative variants, size ladder, clear-space and minimum-size checks). Keep the craft rules unchanged.

- [ ] **Step 4: Verify the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all existing tests plus the new ones; network tests stay deselected).

Run: `ruff check . && ruff format --check .`
Expected: clean.

- [ ] **Step 5: Live smoke test of the new workflow**

Run, in a scratch directory:

```bash
magick -size 240x240 xc:white -fill '#1e3a8a' -draw 'rectangle 40,40 120,120' logo.png
.venv/bin/ms design svg trace logo.png --out logo.svg
.venv/bin/ms design svg compare logo.png logo.svg --out compare.png
.venv/bin/ms design svg build --spec spec.json --base logo.svg --export png --check
```

Expected: trace reports one layer and writes `logo.svg`; compare reports the metrics and writes `compare.png`; build appends the spec's element to the traced SVG and the check verdict is reported.

---

## Execution Notes

- Order: Task 1, 2, 3, 4, 5, 6, 7, 8, 9. Tasks 4–7 are strictly sequential (same module); Task 8 consumes `svg_kit.trace`.
- After each task the controller runs `codegraph sync <repo>` and asks the task reviewer to check the task's diff against its steps.
- Commit authorization is requested from the user after the final whole-branch review; until then nothing is committed.
