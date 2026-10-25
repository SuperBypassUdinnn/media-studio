# Plan: a Kdenlive-compatible handoff document

Status: proposed (2026-10-26). Supersedes the assumption in section 5.1 of `docs/design.md` that the
rendered `.mlt` doubles as the handoff.

## Problem, as measured

Kdenlive 26.08.2 will not open the `.mlt` our writer produces. Verified through the GUI on
2026-10-26: File > Open answers "Incorrect project file — Version of the project file cannot be
read"; accepting the following "Attempting to open nonetheless" shows a Clip Problems dialog naming
this project's own two issues, and the load then ends on an empty project. The control — a project
Kdenlive saved itself, opened through the same dialog — loads normally, so the document is the
problem, not the method.

Evidence: `~/.local/state/opencode-media-studio/kdenlive-evidence/` holds the rejection screenshot,
the Clip Problems screenshot, both method-control screenshots, and
`kdenlive-reference-schema.mlt`, an empty project saved by Kdenlive itself.

Two causes:

1. The document carries no `kdenlive:` properties. The reference carries
   `kdenlive:docproperties.version` (`1.1`), `kdenlive:docproperties.kdenliveversion`,
   `kdenlive:docproperties.documentid`, `kdenlive:docproperties.audioChannels`, a `main_bin`
   **playlist** that holds the bin and the document properties, per-track
   `kdenlive:trackheight`/`kdenlive:timeline_active`/`kdenlive:audio_track`, a sequence tractor with
   `kdenlive:sequenceproperties.*`, a final tractor marked `kdenlive:projectTractor`, and
   `kdenlive:uuid`/`kdenlive:producer_type` on bin producers.
2. The fade filler is referenced from `/tmp/opencode/<slug>/video/black-<w>x<h>-<fps>.mp4`, which is
   purged after encoding, so a handed-off project points at a file that no longer exists (Kdenlive
   reports it as an unreferenced clip and the title's `Sans` family as a placeholder).

## Decisions

1. **A separate artifact, not the render input.** `ms video handoff <project> [--deliverable ID]`
   writes `out/kdenlive/<deliverable>.kdenlive` and leaves `out/master.mlt` exactly as `melt` needs
   it. Rationale: the render input stays minimal and stable, a Kdenlive envelope is version-coupled
   metadata, and a bad envelope can never break a render.
2. **Extension `.kdenlive`.** Kdenlive's Open dialog filters on it; a `.mlt` is invisible in its file
   list. The saved extension is also what Kdenlive itself defaults to.
3. **The envelope is generated, never hand-edited.** The handoff document is derived from the same
   `Timeline` object the writer already builds, so there is one source of truth for the edit.
4. **The filler becomes project-owned.** Generated black is written to
   `out/frames/black-<w>x<h>-<fps>.mp4` and referenced from there, because it is derived media, it is
   not licensed (so it needs no sidecar), and nothing purges `out/`. `/tmp/opencode` stays for scratch
   intermediates only.
5. **The document does not lie about its version.** We write `kdenlive:docproperties.version` `1.1`
   (document format) and `kdenliveversion` as the value Kdenlive reports here, and we state in the
   report that the handoff targets Kdenlive 26.08.2. If a future Kdenlive refuses the format, the
   envelope needs updating rather than a silent fallback.
6. **No clip-problem suppression.** The title font family stays `Sans`, which Kdenlive maps to an
   installed family; the handoff report names any clip problem the toolkit can predict (a missing
   source path) instead of pretending there is none.

## Implementation

1. `src/media_studio/mlt.py`: add an `envelope()` (or a `to_kdenlive_xml()`) that takes the same
   `Timeline` and returns the document with the envelope around the existing producers, playlists,
   and tractors. Keep the current `to_xml()` untouched.
2. `src/media_studio/video.py`: write the filler into the project's `out/frames/` instead of
   `/tmp/opencode`, and expose the handoff as a function that returns the written path plus a report.
3. `src/media_studio/cli.py`: `ms video handoff`, printing the artifact path, the envelope fields it
   wrote, the Kdenlive version it targets, and any predicted clip problem.
4. Tests, all offline:
   - structural: the handoff document has a `main_bin` playlist carrying
     `kdenlive:docproperties.version`, a sequence tractor with `kdenlive:sequenceproperties.*`, a
     `kdenlive:projectTractor`, per-track `kdenlive:trackheight`, and the `.kdenlive` extension;
   - equivalence: `melt` renders the handoff document and the render input to the same duration and
     the same measured loudness;
   - durability: the document references only paths inside the project;
   - guard: a project whose sources are missing still produces a document, and the report says so.
5. Verification, once, by hand at the display: open `out/kdenlive/<id>.kdenlive` in Kdenlive through
   File > Open (typing the name into the Name field, since the dialog's filter hides nothing this
   time) and screenshot the loaded timeline with its clips.
6. Docs: replace the section 5.1 handoff note with the outcome, update the `/video` acceptance row,
   and update the `media-studio-video` skill with the envelope facts and the two method traps
   (a file argument imports as a clip; the Open dialog filters on `.kdenlive`).

## Risks

- The envelope schema is reverse-engineered from one Kdenlive version; a different version may want
  more or fewer fields, and the structural test cannot prove compatibility. Only the GUI check can,
  which is why step 5 is part of the plan rather than optional.
- Writing `kdenlive:docproperties.kdenliveversion` as a constant is a claim about the environment;
  the report must state the version it was measured against.
- Moving the filler into `out/frames/` changes what a re-render reads. The equivalence test in
  step 4 covers it.

## Out of scope

Rendering from Kdenlive, round-tripping a Kdenlive edit back into `project.yaml`, and supporting
Kdenlive's sequences, guides, or effect stacks.
