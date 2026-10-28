# Plan: a Kdenlive-compatible handoff document

Status: implemented 2026-10-27; verified working. Supersedes the assumption in section 5.1 of
`docs/design.md` that the rendered `.mlt` doubles as the handoff.

## Outcome

`ms video handoff` writes `out/kdenlive/<deliverable>.kdenlive` and leaves `out/<deliverable>.mlt`
as the minimal render input. Measured on 2026-10-27 with Kdenlive 26.08.2:

- `env -u MANGOHUD kdenlive out/kdenlive/master.kdenlive` opens cleanly: no SIGSEGV, no
  \"Could not recover corrupted file\", no \"Incorrect project file — Version of the project file
  cannot be read\" dialog, no Clip Problems dialog, and no crash. Project Notes is empty (the key
  oracle: every `kdenlive:control_uuid` pairing resolves to a bin clip).
- The bin carries all nine producers (clip0..clip4, title0, voice_producer, music_producer,
  sequence), the timeline shows shot-1/shot-2/shot-3 on V2 plus the title composite on V3, and the
  duration header reads 12.0s. title0 is a `kdenlivetitle` producer (producer_type Text / `6`), so
  it opens in the title editor.
- `melt` renders the handoff document back at 12.010667 s, matching the edit exactly.
- `ms video render` passes QC: -14.01 LUFS vs the -14.0 target, 1280x720 @ 30 fps, sar 1:1 / dar
  16:9, title renders at 1.5s and 3.0s over the moving picture (title_no_boxwidth proof frame
  preserved in /tmp/opencode/title-native2.png).

Acceptance criterion \"the .mlt opens in Kdenlive\" is met. See section 5.1 of `docs/design.md`
for the schema and method notes.

## What changed since the crash diagnosis

The early 2026-10-26 run crashed because the document was structurally wrong for Kdenlive's timeline
model: playlists placed straight on `main`, nested A/B and title tractors, no `kdenlive:` fields,
and the black filler referenced from `/tmp/opencode/<slug>/`. The fix has several parts, each
verified:

1. **Envelope fields:** `kdenlive:docproperties.version` (`1.1`), `kdenliveversion` probed from the
   installed binary, `documentid`, `main_bin` playlist with `xml_retain=1`, per-track
   `kdenlive:trackheight`/`kdenlive:timeline_active`/`kdenlive:audio_track`,
   `kdenlive:sequenceproperties.*` on the sequence tractor, and a final tractor marked
   `kdenlive:projectTractor`.
2. **No `kdenlive:uuid` on media producers:** Kdenlive reserves that for sequences; a media producer
   carrying one is logged as an incorrect sequence, skipped by the bin loader, and every timeline
   instance of it is dropped as \"without bin reference found and removed\". Media clips carry only
   `kdenlive:control_uuid`, which is what `constructTrackFromMelt` maps back through `binIdCorresp`.
3. **control_uuid/bin pairing:** every media producer has `kdenlive:control_uuid` and an entry in
   `main_bin`; the sequence is itself a bin clip with its own id. This makes Project Notes empty.
4. **Double-track tractors:** each sequence track is a tractor over exactly two playlists (the real
   playlist plus an empty `_b` companion reserved for same-track transition overlaps). The companion
   playlist is declared before the tractor that references it so MLT resolves the producer while
   parsing. luma transitions carry `a_track <= b_track`; when the pair had to be swapped, `reverse=1`
   keeps the same dissolve direction.
5. **Titles as `kdenlivetitle`:** `qtblend` with `compositing=1` makes the title track blend in at
   low opacity over a frozen background; `compositing=0` draws the title over moving tracks.
   `kdenlivetitle` carries an `xmldata` scene (QGraphicsTextItem with font, size, colour, geometry)
   and producer_type `6` (Text) so Kdenlive opens it in its title editor.
6. **Filler project-owned:** generated black lives in `out/frames/black-<w>x<h>-<fps>.mp4`, never
   `/tmp/opencode`, which is purged mid-session.
7. **Sample aspect 1:1 declared in both profiles** so the rendered file matches the canvas instead
   of being pillarboxed to 256:135.

## Disproved hypotheses (kept so they are not retried)

- **Double-track rewrites alone fix the crash:** `vars/d3.kdenlive` (correctly ordered, double-track)
  still DIED at ~6.8s. The earlier \"ALIVE\" `vars/d2.kdenlive` rendered only 0.04s in melt, i.e. its
  companion playlists were unresolved.
- **Skeleton-based documents are a working handoff:** `vars/s5/s6/c1/c2` survive but their rebuilt
  tracks are empty (`mt-conn ... o=-1 l=0`), so ALIVE there means clips silently dropped.
- **Producer type/qtext or qtblend/transition choice is the crash trigger:** both disproved.
- **main_bin sugar, profile fields, black producer, project tractor, mix/qtblend compositions:**
  none fix the recursion by themselves.
- **sample_aspect on the render profile drops qtblend titles:** disproved. The real cause is
  `qtblend` with `compositing=1` plus `qtext` served through `argument` (which renders nothing).
  `compositing=0` on qtblend renders the title correctly over moving tracks.

## Method traps, measured

- `kdenlive <file>` does not open a project on this build: it imports the file into the bin as a
  clip, even for a project Kdenlive saved itself. Opening must go through the GUI dialog (typing the
  name into the Name field).
- `MANGOHUD=1` exported makes Kdenlive 26.08.2 segfault at startup; launch with `env -u MANGOHUD`.
- The Open dialog filters on `.kdenlive`; write the handoff with that extension.

## Ground truth

The only oracle available in a release build is Project Notes in the right-hand dock: one line per
dropped clip with track tag and timecode. Empty Project Notes plus a melt duration parity check is
the acceptance pair. A Kdenlive-authored round trip is kept at
`~/.local/state/opencode-media-studio/kdenlive-evidence/kdenlive-authored.kdenlive` (melt renders it
at 12.053333 s vs 12.010667 s for our document).
