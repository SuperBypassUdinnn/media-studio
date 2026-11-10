# Media-Studio SVG Helper Suite — Design

**Date:** 2026-11-10
**Status:** Draft — pending user review
**Parent spec:** `docs/design.md` §5.3 (Design)

## 1. Purpose

Two recurring failures motivated this subsystem:

1. One-shot raster-to-vector conversion of AI-generated logos (SCR 2026 logo) produced
   hole-riddled SVGs. Repairing them required the agent to see the difference between the
   source PNG and the traced SVG and patch the vector by hand.
2. Drawing freeform vector shapes (curves, domes, sharp notches, small details) forced the
   agent to write bespoke Python geometry functions for a single logo (Simkuliah Monitor
   session `ses_e4681c9acffeEvtaUax7OQD7IB`).

The SVG helper suite removes both frictions with one module and three subcommands: a
declarative composition builder, a two-stage raster tracer, and a measurable visual
comparison loop. The agent writes compact JSON specs and edits SVG text — never geometry
code.

## 2. Decisions

| # | Decision | Reason |
| --- | --- | --- |
| S1 | Declarative JSON spec, not a Python API surface | The goal is "no from-scratch scripts"; JSON is compact, diffable, and storable as a project artifact under `design/` |
| S2 | Tracer ladder: vtracer (optional) then potrace | potrace 1.16 + mkbitmap 1.16 are installed but bitonal-only; vtracer (PyPI wheel) is the only quality color tracer available; autotrace is not installed; Inkscape's Trace Bitmap is unreliable headless; ImageMagick cannot trace at all (preprocessing only) |
| S3 | Stage-2 repair = agent edits SVG text plus `svg compare` feedback | Agents edit text well; the helper's job is primitives plus measurable visual feedback, not a GUI |
| S4 | One geometry kernel (`taper`) covers curves, domes, notches, and fine details | A single tested offset-outline kernel replaces per-logo geometry code |
| S5 | vtracer ships as the optional extra `vector` | Keeps the default install dependency-free; `ms doctor` reports availability |
| S6 | `build --base` appends primitives into an existing SVG | Bridges trace stage 1 to stage-2 patching without rewriting a spec |
| S7 | Trace output groups paths per color layer with stable IDs | Stage-2 patches target layers; hand edits stay localized and diffable |

## 3. CLI surface

```
ms design svg build   --spec <file.json> [--project <project>] [--base <file.svg>]
                      [--out <file.svg>] [--export png,pdf] [--check] [--sheet]
ms design svg trace   <source.png> [--out <file.svg>] [--engine auto|vtracer|potrace]
                      [--mode color|bw] [--colors N] [--threshold PCT]
                      [--flatten-bg #RRGGBB] [--fill-holes R] [--despeckle R]
ms design svg compare <source.png> <svg> [--out <sheet.png>] [--json <report.json>]
                      [--min-area PX2] [--fail-above PCT]
```

All three are project-optional. With `--project` they read brand tokens and fonts from
`project.yaml` and write under the project layout (`out/images/`, `out/reports/`);
without it they use explicit values and write where `--out` points (default: beside the
source). Every command reports the paths of everything it wrote and the numbers it
measured.

## 4. Build spec format

```json
{
  "canvas": {"width": 1080, "height": 1350},
  "safe_area": 64,
  "background": {"type": "solid", "color": "{primary}"},
  "variables": {"tagline": "Belajar Tanpa Batas"},
  "elements": [
    {"type": "text", "role": "heading", "text": "Judul", "x": "center", "y": 240,
     "size": 96, "color": "{ink}"},
    {"type": "stack", "direction": "vertical", "gap": 24, "x": 64, "y": 420,
     "children": [
       {"type": "rect", "width": 200, "height": 8, "fill": "{accent}", "radius": 4},
       {"type": "text", "role": "body", "text": "{tagline}\nparagraf", "size": 32,
        "color": "{ink}"}]},
    {"type": "taper", "from": [880, 1150], "angle": 60, "length": 220, "bend": -60,
     "widths": [48, 8], "fill": "{accent}"},
    {"type": "image", "href": "assets/design/logo.svg", "x": "right", "y": "bottom",
     "width": 180}
  ]
}
```

- `canvas` (required unless `--base`): width and height in px.
- `safe_area`: px inset (number, or `{top, right, bottom, left}`) used by the overflow
  check and by the anchors below; default 0.
- `background`: `solid` only in v1, painted as the first rect.
- `variables`: string substitutions applied to every string field before composition.
  Brand tokens `{primary}`, `{accent}`, `{ink}` are always available (project palette with
  `media_studio.design` defaults); with `--project` the project's font families are used
  for `role: heading|body`, otherwise `sans-serif`.

### 4.1 Placement

Every positional value accepts a number (px) or an anchor keyword resolved against the
canvas and safe area: `left|center|right`, `top|middle|bottom`. `x: "right"` means the
element's right edge sits at `canvas.width - safe_area.right`.

### 4.2 Element vocabulary (v1)

| Type | Fields | Notes |
| --- | --- | --- |
| `text` | `text`, `x`, `y` (baseline), `size`, `role`, `weight`, `anchor start\|middle\|end`, `letter_spacing`, `color` | `\n` becomes `tspan` lines; every text element gets a stable id (`el-text-N`) so the Inkscape-measured checks apply |
| `rect` | `x`, `y`, `width`, `height`, `radius`, `fill`, `stroke`, `stroke_width` | |
| `circle` / `ellipse` | `cx`, `cy`, `r` / `rx`, `ry` | plus fill/stroke |
| `line` | `x1`, `y1`, `x2`, `y2`, `stroke`, `stroke_width`, `cap` | |
| `polygon` / `polyline` | `points: [[x, y], ...]` | closed / open; sharp vertices are first-class |
| `path` | `d` | raw SVG path passthrough — the escape hatch for anything v1 does not model |
| `image` | `href`, `x`, `y`, `width` (or `height`), `opacity` | missing file is an error, never a placeholder |
| `group` | `children`, `transform` (raw SVG transform string), `opacity` | composition only, no coordinate remapping |
| `stack` | `direction v\|h`, `x`, `y`, `gap`, `align start\|center\|end`, `children` | sequential layout; child heights estimated as `size * 1.2 * lines` for text (documented approximation) |
| `spacer` | `width` or `height` | inside stacks |
| `taper` | see 4.3 | the freeform brush |
| `arc` | see 4.4 | domes and bows |

Unknown type, missing required field, or a bad value is an `SvgError` before anything is
written. Every element accepts an optional `id`; auto ids are assigned when absent.

### 4.3 `taper` semantics

A closed filled outline (never a stroked path) along a spine with a width profile —
the "brush" the Simkuliah session hand-rolled every time.

- Spine, exactly one form:
  - `points: [[x, y], ...]` (2 or more): polyline spine; direction changes between
    vertices are sharp by design (lekukan tajam).
  - `from: [x, y]`, `angle: deg`, `length: px`, `bend: px`: a quadratic spine from `from`
    toward `angle` for `length`, with the midpoint bowed perpendicular by signed `bend`
    (lengkungan; a symmetric spine with mirrored `widths` gives a dome/kubah).
  - Angles: 0 = right (+x), positive = clockwise on screen (SVG's y-down convention),
    stated in the report so no agent has to guess.
- Width profile, exactly one form: `width` (constant), `widths: [base, tip]` (linear along
  arc length), or `widths: [w0, w1, ...]` with one value per spine vertex (piecewise).
  Arity must match the spine form or it is an `SvgError`.
- `caps: round|flat` (default `round`).
- Construction: sample the spine, compute unit normals, offset ±width/2, then emit one
  smooth closed cubic Bézier outline (Catmull-Rom through the offset samples). Filled with
  `fill`; no stroke.

### 4.4 `arc` semantics

- Explicit form: `cx`, `cy`, `rx`, `ry`, `start_deg`, `end_deg`, `close: pie|chord|open`.
- Convenience form: `from: [x, y]`, `to: [x, y]`, `sag: px` (signed sagitta — the chord's
  perpendicular bow).
- Renders as an SVG arc path; `stroke` (with `stroke_width`, `cap`) or `fill` for pie/chord.

## 5. Trace — stage 1 (automatic)

`ms design svg trace` converts a raster (PNG primarily; anything ImageMagick reads) into a
layered SVG.

### 5.1 Engine ladder

- `--engine auto` (default): vtracer when importable in the environment, else potrace.
  `--engine vtracer` with vtracer missing fails with the install command
  (`uv pip install -e '.[vector]'`).
- vtracer path: `vtracer.convert_image_to_svg_py(...)` with `colormode: color|binary`,
  speckle filtering, and color precision mapped from `--colors`. This is the quality path
  for AI-generated PNGs.
- potrace path:
  - `--mode bw` (default for potrace): `magick` threshold (`--threshold`, percent, default
    50) to a PBM, then `potrace -s` for a single-path SVG.
  - `--mode color`: quantize to `--colors` (default 6) with magick (`+dither`), split one
    binary mask per quantized color, trace each mask with potrace, and stack the paths as
    `<g id="trace-layer-N" fill="#...">` from largest area to smallest. Medium quality,
    zero new dependencies — acceptable for flat-color logos.

### 5.2 Preprocessing (the "bolong-bolong" mitigations)

Applied by ImageMagick to a scratch copy before tracing, in this order:
`--flatten-bg` (composite alpha over a solid colour — semi-transparent AI edges are the
main hole source), `--fill-holes R` (`-morphology Close disk:R`), `--despeckle R`
(`-morphology Open disk:R`). The preprocessed intermediate is kept as
`<out stem>.trace-input.png` so stage-2 comparisons use the same pixels the tracer saw.

### 5.3 Output structure

- Root `svg` sized to the source pixel size.
- One `<g id="trace-layer-N">` per color layer (potrace color mode and vtracer alike),
  each carrying its fill; potrace layers are ordered largest area first, vtracer keeps its
  native painter order (background first).
- A header comment records engine, parameters, and the source file hash for
  reproducibility.

## 6. Compare — stage 2 (measured feedback)

`ms design svg compare` closes the repair loop with something the agent can read:

1. Render the SVG at the source PNG's exact pixel size with Inkscape (project fontconfig
   applied when `--project` is given).
2. Write one three-panel sheet — source | render | difference blend — labelled, via
   ImageMagick (`--out`, default beside the SVG).
3. Report metrics (stdout plus optional `--json`):
   - `pixel_diff_pct` — share of differing pixels (ImageMagick AE / area).
   - `rmse` — normalized root-mean-square error.
   - `components_missing` — connected components present in the source ink mask but absent
     in the render mask (magick connected-components, `--min-area` px², default 16) — the
     measurable form of "bolong".
   - `components_extra` — the same in the other direction.
4. Exit code 0 unless `--fail-above PCT` is set and `pixel_diff_pct` exceeds it — so
   scripted repair loops can gate.

## 7. Integration with the design path

- Token and font resolution reuse `design.brand_tokens`, `design.font_families`, and
  `design.font_environment` (project fontconfig — Inkscape resolves staged fonts with no
  system install).
- Geometry measurement reuses `design.query_box` for the text-box checks.
- `--export png,pdf` reuses the Inkscape export pattern from `design.render_variant`;
  `--sheet` reuses the ImageMagick contact-sheet pattern.
- `--check` runs the same trio as `ms design render` (contrast, minimum font size,
  overflow against the spec's `safe_area`) and writes a QC report alongside the output.
- The Tahap 4 logo kit (`ms design logo`) calls `svg trace` automatically when the staged
  logo asset is raster, then derives its deliverable set from the traced SVG.

## 8. Error handling

`SvgError(RuntimeError)` mirrors `DesignError`: raised before any file is written for spec
problems (unknown element, arity mismatch, bad anchor), and with the failing tool's stderr
tail for subprocess problems (tracer missing, Inkscape export failure, magick failure).
`svg compare` never raises on a bad match — it reports.

## 9. Testing (offline; pytest, no new test dependencies)

1. `taper`/`arc` geometry: golden path-data snapshots with numeric tolerance; spine forms
   (points vs from/angle/bend), width arity errors, caps.
2. Stack layout arithmetic and anchor resolution against `safe_area`.
3. Token and variable substitution; `--base` append (primitives land after the base's last
   child; base canvas preserved).
4. Trace: a synthetic 3-colour PNG reproduces 3 layers whose fills match the source
   palette (potrace path runs live; the vtracer test skips when the extra is absent).
5. Compare: a deliberately hole-punched SVG reports `components_missing > 0` and writes the
   3-panel sheet; `--fail-above` exit codes.
6. Patch-loop proof (the SCR lesson as a test): trace a synthetic logo, punch a hole, patch
   with one `taper` via `--base`, assert the compare metric improves.
7. CLI wiring per `tests/test_cli.py` patterns (argparse, exit codes, error messages).

## 10. Acceptance criteria

1. A spec exercising every v1 primitive builds to SVG + PNG with `--check` producing the
   QC report, in and out of a project.
2. A 3-colour PNG traces to 3 correctly filled layers with potrace (and vtracer when
   installed), holes mitigated by the preprocessing flags.
3. A hole-punched SVG is detected (`components_missing > 0`) and one `taper` patch via
   `--base` measurably improves the compare metrics.
4. `ms doctor` reports vtracer availability under the design tooling.
5. Existing suites stay green; `ruff check` and `ruff format` clean.

## 11. Non-goals (v1)

- Text auto-fit / auto-shrink (v2: Inkscape measure-and-refit loop); wrapping stays
  explicit via `\n`.
- Gradients and filters (solid fills only; raw `path` + manual `defs` stay possible via
  `--base`).
- On-canvas/GUI editing; autotrace; potrace colour-quality work beyond layer stacking;
  animation.
- Tracing anything other than whole images (no region-of-interest tracing in v1).
