# Assessment: `ms see` versus the media MCP server

Recorded 2026-11-07. Question: can the local media MCP server improve `ms see`, or is `ms see` already strong and stable enough to leave alone?

## What each side actually is

### `ms see` (this repo, `src/media_studio/see.py`, 385 lines)

A project-less visual-introspection primitive. One call answers "what does this file look like" with a facts dict plus a PNG sheet the agent can look at.

- Video: a frame contact sheet (default 4x3 grid, uniform sampling via `fps=1/interval, tile`).
- Video with alpha: frames composited over a checkerboard so transparency is visible.
- Image: a thumbnail; a checkerboard composite when the image has real alpha holes.
- Audio: a waveform plus EBU R128 loudness (LUFS) and true peak (dBTP).
- A directory with `project.yaml`: deliverables versus renders on disk, missing assets, and a contact sheet of the first rendered video.

Dependencies: `ffmpeg`/`ffprobe` plus Pillow. Tested (13 tests in `tests/test_see.py`). Invoked inside the pipeline by `acquire.py`, the `beatfx`/`edl` end-to-end checks, and the `media-create` skill.

### media MCP (separate repo, `~/.clone/Github/SuperBypassUdinnn/media-mcp`)

A cached media-analysis suite exposed as 12 MCP tools. Dependencies: a dedicated Python venv (`opencv-python-headless`, `brisque`, `numpy`, `pillow`, `mcp`), a second venv for the librosa rhythm pipeline (`rhythm/.venv`), and a transcription pipeline (`transcribe/`). Artifacts cache under `~/.cache/media-mcp` (939 entries present).

Tools: `media_probe`, `media_manifest`, `media_frames`, `media_beats`, `media_avsync`, `media_motion`, `media_ocr`, `media_search`, `media_transcript`, `media_image_quality`, `media_image_sort`, `media_cache`.

## Capability comparison

| Capability | `ms see` | media MCP | Notes |
|---|---|---|---|
| Uniform frame sheet | yes | yes | Equivalent quality. |
| Scene-ranked frame sheet | no | yes | `media_frames at='scenes'` returns ranked scene candidates. |
| Beat-aligned frames | no | yes | `media_beats` then `media_frames at='beats'` pulls one still per beat. |
| A/V cut-on-beat verdict | no | yes | `media_avsync` scores cuts against onsets with a shuffled baseline. |
| Visual motion analysis | no | yes | `media_motion` classifies burst/hold/transition/keyframe spans. |
| On-screen text (OCR) | no | yes | `media_ocr`, with crop and value-change tracking. |
| Speech transcript | no | yes | `media_transcript`, with SRT/VTT/JSON/MD output. |
| Image quality IQ | no | yes | `media_image_quality`/`_sort`: blur, brightness, contrast, BRISQUE, tier. |
| Loudness (LUFS/dBTP) | yes | no | `ms see` reports it for audio; media MCP does not. |
| Alpha checkerboard | yes | no | Critical for overlay and cutout QC; media MCP shows alpha as opaque. |
| Project awareness (`project.yaml`) | yes | no | `ms see <project>` reports deliverables, renders, missing assets. |
| URL ingest | no | yes | media MCP downloads http(s) URLs via yt-dlp. |
| Caching | no | yes | media MCP caches probe/scenes/beats/motion/quality/OCR/transcript. |
| Runtime deps | ffmpeg + Pillow | 3 venvs + MCP daemon | media MCP is far heavier. |

## Assessment

`ms see` is **strong and stable for its purpose** and should not be replaced:

- It is the only tool that is project-aware (reads `project.yaml`, diffs deliverables against renders, flags missing assets).
- It is the only tool that composites alpha correctly (checkerboard), which matters for the `remove-bg` and overlay workflows.
- It is the only tool that reports loudness on audio, which the -14 LUFS delivery gate depends on.
- It has no daemon, no cache, no venv beyond the project's own; it is a thin, deterministic ffmpeg wrapper that is safe to call from inside the pipeline.

It has **one real weakness**, observed during the AMV rebuild: **uniform frame sampling** misses the densest action in uneven footage. The rebuild worked around it by running ffmpeg scene detection by hand. A uniform sheet is the right default for a quick look, but it is not the right sampling for action-heavy source review.

media MCP covers that weakness and adds six capabilities `ms see` does not have at all (scene ranking, beat-aligned frames, avsync verdict, motion analysis, OCR, transcript, image-quality IQ). None of those are things `ms see` should grow heavy dependencies to acquire.

## Recommendation: hybrid, not replacement

1. **Keep `ms see` as-is** as the fast default and the in-pipeline primitive. Do not add the media MCP dependency to the `ms` CLI; the pipeline must stay runnable without the MCP daemon.

2. **Close `ms see`'s one real weakness with cheap, dependency-free modes.** Both are feasible with the tools already present:
   - a scene-aware sheet using ffmpeg `select='gt(scene,T)'` (no new dependency; demonstrated by hand during the rebuild);
   - a beat-aligned sheet reusing the existing `audio.analyze_rhythm` output (already implemented in Phase 1).
   These turn the two shortcomings into default behaviour without a new venv.

3. **Use media MCP as the agent-side escalation layer** for the heavy, optional analytics: OCR, transcript, motion, image-quality IQ, and the avsync verdict. The `media-create` skill should document *when* to escalate from `ms see` to a media MCP tool (for example: run `media_avsync` to prove an AMV cut is on the beat; run `media_image_quality` when triaging a batch of candidate photos in the acquisition pipeline).

4. **Do not replace `ms see` with media MCP.** Replacement would lose project awareness, alpha compositing, loudness reporting, in-pipeline integration, and the zero-daemon simplicity that makes `ms see` safe to call anywhere.

## Concrete follow-up (if pursued)

- Add `ms see --mode {uniform,scene,beats}` to `see.py` (`scene` and `beats` as above).
- Add one line to the `media-create` skill escalation table mapping each deep question to its media MCP tool.
- No change to the media MCP server is required.
