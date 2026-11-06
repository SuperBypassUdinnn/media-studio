# AMV Rebuild — Next Steps

Recorded 2026-11-07 after the first Phase 2 rebuild (Demon Slayer / Hensonn - Sahara, 27 s 1080p60).

## What the rebuild proved

The Phase 2 pipeline ran end to end on a real project with the new semi-manual workflow:

1. User supplied primary footage and BGM; the agent supplied supporting assets.
2. Stage 0 consistency check passed (footage and music both high-energy).
3. Segment-level loudness normalization to -14 LUFS on the extracted window.
4. Real rhythm analysis (librosa) at 114.8 BPM with a clean downbeat grid.
5. Hand-crafted EDL (34 cuts: build 1.3-1.6 s, silence gap 2.08 s, drop 0.4-0.65 s) snapped to downbeats.
6. Scene detection plus MCI/AOBMC motion interpolation to 60 fps for 14 shots cut from two full clips.
7. `ms video edl render` produced 33 beatfx plus 2 velocity ramps.
8. Color grading with the bundled `anime-vibrant.cube` LUT.
9. 3D titles via the EffectCraft `amv-title-cyber` preset.
10. QC through `ms see`; final deliverable at 27 s, 1920x1080, 60 fps, -14.01 LUFS.

Artifacts: `~/Projects/amv-demonslayer-3d/out/video/amv-sahara-v2-graded.mp4`, `edl-v2.json`, `assets/video/shots-v2/60fps/`.

## Development options (not yet implemented)

### 1. Extend the shot pool and duration

The current cut uses 14 of the shots cut from two full clips. Additional sub-cuts from the same sources, or a longer music segment (the track is 171 s; only 23-50 s is used), would support a 40-60 s edit. Cost: more EDL authoring per cut; the tooling already supports it.

### 2. Try the other bundled LUTs

Five bundled LUTs ship (`anime-vibrant`, `cinematic-teal-orange`, `horror-bleach`, `warm-sunset`, `cool-blue`). Only `anime-vibrant` has been previewed on real footage. A per-style preview pass would confirm which fits battle footage best, and could motivate per-shot LUT assignment in the EDL.

### 3. Densify velocity ramps

The rebuild placed only two ramps (drop hit and finale). Ramps on every drop downbeat would sharpen the "kick" feel. The trade-off is render time: each ramp pass retimes a clip, so more ramps cost more encode.

### 4. Add supporting sound effects

The Rhythm & Density rules call for a micro-effect on every hit in a climax; the audio side (whoosh, impact, riser SFX on transitions) is not yet covered. This needs the asset library to be populated with CC0 SFX and mixed per cut.

### 5. Build #2 on a different track

A second track with a different tempo profile (for example a slow build into a hard drop) would stress-test the cut-density rules and the beatramp trigger mapping against music the current mapping was not tuned on.

### 6. Shot selection automation

The rebuild chose cut points by reading scene detection output and the `ms see` contact sheets by hand. A helper that proposes cut candidates from scene detection plus motion analysis would remove that manual step.

### 7. Beat-aligned and scene-aware review sheets

`ms see` samples frames uniformly, which missed the densest action in the source clips; scene detection ran separately by hand. Improving the review sheet itself is tracked in `docs/notes/ms-see-vs-media-mcp.md`.
