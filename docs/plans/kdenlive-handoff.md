# Plan: a Kdenlive-compatible handoff document

Status: implemented 2026-10-26; verification open. Supersedes the assumption in section 5.1 of
`docs/design.md` that the rendered `.mlt` doubles as the handoff.

## Outcome after the first implementation

`ms video handoff` is in, with the envelope and the project-owned filler, and the two causes this
plan named are fixed. What the display showed:

- Kdenlive 26.08.2 now **accepts** the document. The "Incorrect project file — Version of the
  project file cannot be read" dialog is gone, the load proceeds straight to the Clip Problems
  dialog, and `out/kdenlive/master.kdenlive` appears in Kdenlive's Open Recent list.
- It then **crashes** while loading. Kdenlive reported "Kdenlive crashed on last start" on the next
  launch, so the acceptance criterion "the .mlt opens in Kdenlive" is still not met.
- Clip Problems lists one `Unreferenced … Fixed` row pointing into the project and the title font
  substitution `Sans` to `Noto Sans`. Both are named by the toolkit now: `ms video handoff` prints the
  substitution as a note (computed with `fc-match`, so it is predicted rather than discovered), and
  the unreferenced row is the nested-tractor layout described below. Neither is claimed to be
  harmless — the crash follows them and nothing here isolates the cause.

Screenshots and the document itself are kept in
`~/.local/state/opencode-media-studio/kdenlive-evidence/`: `handoff-document.kdenlive`,
`handoff-accepted-then-crashed.png` (the crash notice on the next launch) and
`clip-problems-same-rows-in-both-rounds.png`, which is a single capture because the dialog showed the
same two rows before and after the envelope was added — a fresh capture would have been the same
bytes, so the earlier one stands for both rounds.

### Next step, from that outcome

The remaining difference is structural, not metadata. A project Kdenlive writes lays the timeline out
as: a dedicated black track producer carrying `kdenlive:playlistid`, one tractor per track holding
**two** playlists with `hide="video"` or `hide="audio"` attributes and per-track `kdenlive:` fields,
and a sequence tractor that references those per-track tractors, plus
`kdenlive:sequenceproperties.tracks*` describing the track model as JSON. Our writer instead puts
playlists directly on `main` and nests its own A/B and title tractors inside it, which is correct MLT
and what `melt` needs, but is not the shape Kdenlive's timeline model can build from.

So the next implementation mirrors that layout in the handoff document only (the render input stays
as it is), then repeats step 5 at the display. A crash leaves no message to work from, so the
iteration should be driven by a diff against the reference document rather than by guessing.

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
   `Timeline` object the writer already builds, so there is one source of truth for the edit. The
   implementation writes a few fields the plan did not list — `decimalPoint`, the proxy flags,
   `sequenceproperties.zoom`, `hasVideo`/`hasAudio` and `documentuuid` — because the reference
   document carries them; they are format data, not policy.
4. **The filler becomes project-owned.** Generated black is written to
   `out/frames/black-<w>x<h>-<fps>.mp4` and referenced from there, because it is derived media, it is
   not licensed (so it needs no sidecar), and nothing purges `out/`. `/tmp/opencode` stays for scratch
   intermediates only.
5. **The document does not lie about its version, and does not overclaim it either.**
   `kdenlive:docproperties.version` `1.1` declares the *document format* Kdenlive requires before it
   will read anything, and `kdenliveversion` is probed from the installed binary at handoff time, so
   on a machine without Kdenlive that property is simply omitted. Note what the stamp does not say:
   Kdenlive's acceptance proves the format is readable, not that the timeline layout matches what
   Kdenlive builds, which is what the crash below is about.
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
  which is why step 5 is part of the plan rather than optional. **That risk has now materialised: the
  check ran and failed**, in the shape described under "Outcome" — the document is accepted and then
  the application crashes. A crash also produces no diagnostic, so the next iteration has to be a
  structural diff against the reference document, not a guess.
- Writing `kdenlive:docproperties.kdenliveversion` as a constant is a claim about the environment;
  the report must state the version it was measured against.
- Moving the filler into `out/frames/` changes what a re-render reads. The equivalence test in
  step 4 covers it.

## Out of scope

Rendering from Kdenlive, round-tripping a Kdenlive edit back into `project.yaml`, and supporting
Kdenlive's sequences, guides, or effect stacks.
