"""Dual-aspect promotional video orchestrator (`9:16` + `16:9`).

Composes brand reference packs, TTS/cleaned voiceover, generative/fallback
B-roll, EffectCraft promo presets, Playwright HTML motion cards, FFmpeg
transitions, CC0 SFX mixing, subject-tracked reframe, safe-zone captions,
and FFprobe/LUFS/`ms see` quality control.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media_studio import audio, media, video
from media_studio.captions import CaptionsError, extract_word_timestamps, render_captions
from media_studio.effectcraft import EffectCraftError, render_promo_preset
from media_studio.gen.base import GenError, GenRequest
from media_studio.gen.pipeline import generate_asset
from media_studio.html_motion import HtmlMotionError, render_html_motion
from media_studio.manifest import Project
from media_studio.reframe import ASPECTS, OUTPUT_SIZE, ReframeError, reframe_video
from media_studio.see import SeeError, peek
from media_studio.sfx import SfxCue, SfxError, mix_transition_sfx
from media_studio.transitions import TransitionError, apply_transition
from media_studio.tts import TtsError, WordTiming, synthesize_speech

SCRATCH_ROOT = Path("/tmp/opencode")
BEAT_KINDS = ("hook", "problem", "demo", "proof", "cta")
DEFAULT_ASPECTS = ("9:16", "16:9")
DEFAULT_DELIVERABLES = ("reel-9x16", "master-16x9")
DELIVERABLE_ASPECT_MAP = {
    "reel-9x16": "9:16",
    "master-16x9": "16:9",
    "square-1x1": "1:1",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class PromoError(RuntimeError):
    """Raised when promo planning, rendering, or QC fails."""


@dataclass(frozen=True)
class PromoBeat:
    """One typed narrative beat in a promotional video plan."""

    kind: str
    text: str
    duration: float
    asset_id: str | None = None
    template: str | None = None
    preset: str | None = None
    transition: str = "whip_pan_left"
    sfx: str = "whoosh-fast"
    gen_prompt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "duration": round(float(self.duration), 3),
            "asset_id": self.asset_id,
            "template": self.template,
            "preset": self.preset,
            "transition": self.transition,
            "sfx": self.sfx,
            "gen_prompt": self.gen_prompt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromoBeat:
        return cls(
            kind=str(data["kind"]),
            text=str(data["text"]),
            duration=float(data["duration"]),
            asset_id=data.get("asset_id"),
            template=data.get("template"),
            preset=data.get("preset"),
            transition=str(data.get("transition") or "whip_pan_left"),
            sfx=str(data.get("sfx") or "whoosh-fast"),
            gen_prompt=data.get("gen_prompt"),
        )


@dataclass(frozen=True)
class PromoPlan:
    """Complete beat-by-beat promotional video plan."""

    project_slug: str
    aspects: tuple[str, ...]
    script: str
    beats: tuple[PromoBeat, ...]
    estimated_gen_calls: int
    references: tuple[str, ...]
    total_duration: float
    out_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "aspects": list(self.aspects),
            "script": self.script,
            "beats": [beat.to_dict() for beat in self.beats],
            "estimated_gen_calls": self.estimated_gen_calls,
            "references": list(self.references),
            "total_duration": round(float(self.total_duration), 3),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, out_path: Path | None = None) -> PromoPlan:
        beats = tuple(PromoBeat.from_dict(item) for item in data.get("beats") or ())
        return cls(
            project_slug=str(data["project_slug"]),
            aspects=tuple(str(a) for a in data.get("aspects") or DEFAULT_ASPECTS),
            script=str(data["script"]),
            beats=beats,
            estimated_gen_calls=int(data.get("estimated_gen_calls", 0)),
            references=tuple(str(r) for r in data.get("references") or ()),
            total_duration=float(data.get("total_duration", sum(b.duration for b in beats))),
            out_path=out_path,
        )


@dataclass(frozen=True)
class PromoRenderResult:
    """Outcome of :func:`render_promo`, including storyboard gate state."""

    gated: bool
    storyboard: Path | None
    deliverables: dict[str, Path]
    qc: dict[str, Any]
    notes: tuple[str, ...] = ()


def save_promo_plan(plan: PromoPlan, path: Path) -> Path:
    """Persist ``plan`` as formatted JSON at ``path``."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan.to_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def load_promo_plan(path: Path) -> PromoPlan:
    """Load a :class:`PromoPlan` from a JSON file."""

    source = Path(path)
    if not source.is_file():
        raise PromoError(f"promo plan not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PromoError(f"invalid promo plan JSON in {source}: {error}") from error
    if not isinstance(payload, dict):
        raise PromoError(f"promo plan in {source} must be a JSON object")
    return PromoPlan.from_dict(payload, out_path=source)


def _split_script_segments(script: str) -> list[str]:
    segments = [part.strip() for part in _SENTENCE_SPLIT.split(script.strip()) if part.strip()]
    if len(segments) >= len(BEAT_KINDS):
        # Collapse extra middle segments into the demo beat so we emit 5 clean beats.
        head = segments[0]
        problem = segments[1]
        cta = segments[-1]
        proof = segments[-2]
        demo = " ".join(segments[2:-2])
        return [head, problem, demo, proof, cta]

    defaults = [
        "Stop scrolling and see what changes today.",
        "Manual workflows slow every launch down.",
        "One deterministic pipeline handles every format.",
        "Built for broadcast speed and measurable quality.",
        "Start your next campaign now.",
    ]
    padded = list(segments)
    while len(padded) < len(BEAT_KINDS):
        padded.append(defaults[len(padded)])
    return padded


def _beat_durations(total_duration: float) -> list[float]:
    weights = (0.12, 0.22, 0.30, 0.20, 0.16)
    raw = [round(total_duration * w, 3) for w in weights]
    diff = round(total_duration - sum(raw), 3)
    raw[-1] = round(raw[-1] + diff, 3)
    return raw


def _collect_references(project: Project) -> tuple[str, ...]:
    refs = project.brand.references
    ordered = [*refs.product, *refs.spokesperson, *refs.style_board]
    seen: set[str] = set()
    unique: list[str] = []
    for item in ordered:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return tuple(unique)


def _visual_asset_candidates(project: Project) -> list[str]:
    video_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    ref_ids = set(_collect_references(project))
    candidates: list[str] = []
    for asset_id, rel in project.assets.items():
        if "logo" in asset_id.lower() or asset_id in ref_ids:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix in video_exts or suffix in image_exts:
            candidates.append(asset_id)
    return candidates


def plan_promo(
    project: Project,
    *,
    script: str,
    aspects: tuple[str, ...] = DEFAULT_ASPECTS,
    out_path: Path | None = None,
    target_duration: float = 30.0,
) -> PromoPlan:
    """Decompose ``script`` into typed promo beats with brand references and quota estimate."""

    cleaned_script = script.strip()
    if not cleaned_script:
        raise PromoError("script must not be empty")
    if target_duration <= 0.0:
        raise PromoError(f"target_duration must be positive, got {target_duration}")
    if not aspects:
        raise PromoError("at least one aspect ratio is required")
    for aspect in aspects:
        if aspect not in ASPECTS:
            raise PromoError(f"unknown aspect {aspect!r}; expected one of {sorted(ASPECTS)}")

    segments = _split_script_segments(cleaned_script)
    durations = _beat_durations(target_duration)
    references = _collect_references(project)
    visual_assets = _visual_asset_candidates(project)

    beat_configs: dict[str, dict[str, Any]] = {
        "hook": {
            "preset": "promo-hook-slam",
            "template": None,
            "transition": "whip_pan_left",
            "sfx": "impact-hit",
            "needs_broll": True,
        },
        "problem": {
            "preset": "promo-kinetic-callout",
            "template": None,
            "transition": "zoom_punch_in",
            "sfx": "whoosh-fast",
            "needs_broll": True,
        },
        "demo": {
            "preset": None,
            "template": "saas-feature-card",
            "transition": "rgb_split_hit",
            "sfx": "ui-pop",
            "needs_broll": False,
        },
        "proof": {
            "preset": None,
            "template": "metric-counter",
            "transition": "flash_white",
            "sfx": "riser-build",
            "needs_broll": False,
        },
        "cta": {
            "preset": "promo-cta-endcard",
            "template": None,
            "transition": "light_leak_warm",
            "sfx": "impact-sub",
            "needs_broll": False,
        },
    }

    beats: list[PromoBeat] = []
    asset_cursor = 0
    estimated_gen_calls = 0

    for kind, text, dur in zip(BEAT_KINDS, segments, durations, strict=True):
        cfg = beat_configs[kind]
        asset_id: str | None = None
        gen_prompt: str | None = None

        if cfg["needs_broll"]:
            if asset_cursor < len(visual_assets):
                asset_id = visual_assets[asset_cursor]
                asset_cursor += 1
            else:
                gen_prompt = f"{project.title} {kind} b-roll: {text}"
                estimated_gen_calls += 1

        beats.append(
            PromoBeat(
                kind=kind,
                text=text,
                duration=dur,
                asset_id=asset_id,
                template=cfg["template"],
                preset=cfg["preset"],
                transition=cfg["transition"],
                sfx=cfg["sfx"],
                gen_prompt=gen_prompt,
            )
        )

    plan = PromoPlan(
        project_slug=project.slug,
        aspects=tuple(aspects),
        script=cleaned_script,
        beats=tuple(beats),
        estimated_gen_calls=estimated_gen_calls,
        references=references,
        total_duration=round(sum(b.duration for b in beats), 3),
        out_path=Path(out_path) if out_path is not None else None,
    )
    if out_path is not None:
        save_promo_plan(plan, Path(out_path))
    return plan


def _write_storyboard(project: Project, plan: PromoPlan, destination: Path) -> Path:
    """Render a deterministic storyboard contact-sheet PNG for human quota approval."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    primary = project.brand.palette.get("primary", "#0F172A").lstrip("#")
    accent = project.brand.palette.get("accent", "#38BDF8").lstrip("#")

    tile_inputs: list[str] = []
    for idx, _beat in enumerate(plan.beats):
        col = primary if idx % 2 == 0 else accent
        tile_inputs.extend(["-f", "lavfi", "-i", f"color=c=0x{col}:s=320x180:d=0.1"])

    n_tiles = max(1, len(plan.beats))
    if not tile_inputs:
        tile_inputs = ["-f", "lavfi", "-i", f"color=c=0x{primary}:s=320x180:d=0.1"]

    cmd = [media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *tile_inputs]
    if n_tiles > 1:
        stack_in = "".join(f"[{i}:v]" for i in range(n_tiles))
        cmd.extend(
            ["-filter_complex", f"{stack_in}hstack=inputs={n_tiles}[outv]", "-map", "[outv]"]
        )
    cmd.extend(["-frames:v", "1", str(destination)])
    media.run_command(cmd, label="render promo storyboard", expect_stdout=False)
    return destination


def _aspect_for_deliverable(project: Project, deliverable_id: str) -> str:
    for d in project.deliverables:
        if d.id == deliverable_id and d.resolution:
            if d.resolution == "1080x1920":
                return "9:16"
            if d.resolution == "1920x1080":
                return "16:9"
            if d.resolution == "1080x1080":
                return "1:1"
    if deliverable_id in DELIVERABLE_ASPECT_MAP:
        return DELIVERABLE_ASPECT_MAP[deliverable_id]
    if "9x16" in deliverable_id or "reel" in deliverable_id:
        return "9:16"
    if "1x1" in deliverable_id or "square" in deliverable_id:
        return "1:1"
    return "16:9"


def _resolve_reference_paths(project: Project, references: tuple[str, ...]) -> list[str]:
    resolved: list[str] = []
    for ref in references:
        rel = project.assets.get(ref)
        if rel:
            candidate = project.root / rel
            if candidate.is_file():
                resolved.append(str(candidate))
    return resolved


def _render_solid_clip(
    output: Path, *, width: int, height: int, duration: float, color: str
) -> Path:
    col = color.lstrip("#")
    if col.lower() in {"0f172a", "000000", "000"}:
        col = "1e293b"
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            (
                f"gradients=s={width}x{height}:r=30:d={duration:.4f}"
                f":c0=0x{col}:c1=0x0c4a6e:c2=0x1e293b:c3=0x334155:speed=0.8"
            ),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration:.4f}",
            "-vf",
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(output),
        ],
        label=f"solid clip {output.name}",
        expect_stdout=False,
    )
    return output


def _normalize_clip(
    source: Path, output: Path, *, width: int, height: int, duration: float
) -> Path:
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps=30,format=yuv420p"
    )
    if source.suffix.lower() in image_exts:
        cmd = [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(source),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration:.4f}",
            "-vf",
            vf,
            "-t",
            f"{duration:.4f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(output),
        ]
    else:
        cmd = [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration:.4f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
            "-t",
            f"{duration:.4f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output),
        ]
    media.run_command(cmd, label=f"normalize clip {output.name}", expect_stdout=False)
    return output


def _composite_overlay(base: Path, overlay: Path, output: Path, *, width: int, height: int) -> Path:
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(base),
            "-i",
            str(overlay),
            "-filter_complex",
            f"[1:v]scale={width}:{height}[ov];[0:v][ov]overlay=0:0:shortest=1,format=yuv420p[outv]",
            "-map",
            "[outv]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "copy",
            str(output),
        ],
        label=f"composite overlay {output.name}",
        expect_stdout=False,
    )
    return output


def _pad_audio(source: Path, output: Path, *, total_duration: float) -> Path:
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            f"aformat=sample_rates=48000:channel_layouts=stereo,apad=whole_dur={total_duration:.6f},atrim=0:{total_duration:.6f}",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        label=f"pad audio {output.name}",
        expect_stdout=False,
    )
    return output


def _mux_video_and_audio(video_in: Path, audio_in: Path, output: Path) -> Path:
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_in),
            "-i",
            str(audio_in),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output),
        ],
        label=f"mux {output.name}",
        expect_stdout=False,
    )
    return output


def _mix_vo_and_music(
    vo_wav: Path, ducked_music: Path, output: Path, *, total_duration: float
) -> Path:
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(vo_wav),
            "-i",
            str(ducked_music),
            "-filter_complex",
            (
                f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total_duration:.6f}[vo];"
                f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{total_duration:.6f}[bgm];"
                "[vo][bgm]amix=inputs=2:duration=first:weights=1 0.45:normalize=0[outa]"
            ),
            "-map",
            "[outa]",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        label=f"mix vo and music {output.name}",
        expect_stdout=False,
    )
    return output


def _resolve_music_path(project: Project) -> Path | None:
    music_id: str | None = None
    if project.video is not None and project.video.music is not None:
        music_id = project.video.music.asset
    elif "music" in project.assets:
        music_id = "music"
    elif "bgm" in project.assets:
        music_id = "bgm"
    if not music_id:
        return None
    rel = project.assets.get(music_id)
    if not rel:
        return None
    candidate = project.root / rel
    return candidate if candidate.is_file() else None


def render_promo(
    project: Project,
    plan: PromoPlan,
    *,
    auto: bool = False,
    max_gen_calls: int = 6,
    force: bool = False,
    deliverables: tuple[str, ...] = DEFAULT_DELIVERABLES,
    tts_engine: str = "auto",
) -> PromoRenderResult:
    """Render a promotional video plan, enforcing the Storyboard/Quota Gate unless ``auto=True``."""

    if not plan.beats:
        raise PromoError("promo plan has no beats")

    storyboard_path = project.root / "out" / "promo" / "storyboard.png"
    notes: list[str] = []

    if not auto:
        _write_storyboard(project, plan, storyboard_path)
        notes.append(
            f"gated: storyboard written to {storyboard_path} "
            f"(estimated_gen_calls={plan.estimated_gen_calls}; pass auto=True to render)"
        )
        return PromoRenderResult(
            gated=True,
            storyboard=storyboard_path,
            deliverables={},
            qc={},
            notes=tuple(notes),
        )

    out_dir = project.root / "out" / "video"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_paths = {deliv_id: out_dir / f"{deliv_id}.mp4" for deliv_id in deliverables}
    if not force:
        for path in target_paths.values():
            if path.exists():
                raise PromoError(
                    f"deliverable already exists: {path} (pass force=True to overwrite)"
                )

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"promo-{os.getpid()}-", dir=str(SCRATCH_ROOT)))
    try:
        # 1. Voiceover (clean existing VO asset if declared, else synthesize via TTS).
        vo_wav = scratch / "voiceover.wav"
        words_json = scratch / "words.json"
        vo_asset_rel = project.assets.get("voiceover") or project.assets.get("vo")
        if vo_asset_rel and (project.root / vo_asset_rel).is_file():
            vo_src = project.root / vo_asset_rel
            audio.clean_voiceover(vo_src, output=vo_wav, target_lufs=-16.0)
            try:
                extracted = extract_word_timestamps(vo_src)
                words_json.write_text(
                    json.dumps(
                        [{"word": w.word, "start": w.start, "end": w.end} for w in extracted]
                    ),
                    encoding="utf-8",
                )
            except CaptionsError as error:
                notes.append(f"captions skipped for pre-recorded VO: {error}")
                words_json.write_text("[]", encoding="utf-8")
        else:
            voice_id = project.brand.voice.voice_id if project.brand.voice else "id-ID-ArdiNeural"
            voice_rate = project.brand.voice.rate if project.brand.voice else ""
            rate = voice_rate or "+0%"
            engine = (
                project.brand.voice.engine
                if tts_engine == "auto" and project.brand.voice
                else tts_engine
            )
            try:
                tts_res = synthesize_speech(
                    plan.script,
                    output=vo_wav,
                    voice=voice_id,
                    engine=engine,
                    rate=rate,
                    words_out=words_json,
                    target_lufs=-16.0,
                    scratch=scratch,
                )
                words_json = tts_res.words_path
            except TtsError as error:
                raise PromoError(f"voiceover synthesis failed: {error}") from error

        # 2. Acquire/generate B-roll + render motion overlays per beat.
        primary_col = project.brand.palette.get("primary", "#0F172A")
        accent_col = project.brand.palette.get("accent", "#38BDF8")
        ref_paths = _resolve_reference_paths(project, plan.references)
        gen_calls_used = 0

        min_beat_dur = min(b.duration for b in plan.beats)
        trans_td = min(0.25, max(0.06, min_beat_dur * 0.35))
        trans_ms = max(60, round(trans_td * 1000))
        # Quantize td to 30fps to match transitions.apply_transition frame quantization.
        quant_td = max(1, round(trans_ms * 30 / 1000.0)) / 30.0

        beat_clips: list[Path] = []
        sfx_cues: list[SfxCue] = []
        elapsed = 0.0

        for idx, beat in enumerate(plan.beats):
            clip_dur = beat.duration + (quant_td if idx > 0 else 0.0)
            base_clip = scratch / f"beat-{idx:02d}-base.mp4"

            asset_rel = project.assets.get(beat.asset_id) if beat.asset_id else None
            if asset_rel and (project.root / asset_rel).is_file():
                _normalize_clip(
                    project.root / asset_rel,
                    base_clip,
                    width=1920,
                    height=1080,
                    duration=clip_dur,
                )
            elif beat.gen_prompt and gen_calls_used < max_gen_calls:
                gen_out = project.root / "assets" / "video" / f"gen-{project.slug}-{idx:02d}.mp4"
                try:
                    gen_res = generate_asset(
                        GenRequest(
                            kind="video",
                            prompt=beat.gen_prompt,
                            duration=clip_dur,
                            fps=30,
                            resolution="1920x1080",
                            references=ref_paths,
                        ),
                        project=project,
                        output=gen_out,
                        max_gen_calls=max(1, max_gen_calls - gen_calls_used),
                    )
                    gen_calls_used += 1
                    _normalize_clip(
                        gen_res.output, base_clip, width=1920, height=1080, duration=clip_dur
                    )
                except GenError as error:
                    notes.append(f"gen fallback on beat {idx}: {error}")
                    _render_solid_clip(
                        base_clip, width=1920, height=1080, duration=clip_dur, color=primary_col
                    )
            elif beat.template:
                html_out = scratch / f"beat-{idx:02d}-html.mov"
                _render_solid_clip(
                    base_clip, width=1920, height=1080, duration=clip_dur, color=primary_col
                )
                try:
                    html_res = render_html_motion(
                        beat.template,
                        output=html_out,
                        params={"title": beat.text, "label": beat.kind.upper()},
                        project=project,
                        width=1920,
                        height=1080,
                        fps=30,
                        duration=clip_dur,
                        transparent=True,
                    )
                    if html_res.output.is_file():
                        if html_res.transparent:
                            composited = scratch / f"beat-{idx:02d}-html-comp.mp4"
                            _composite_overlay(
                                base_clip, html_res.output, composited, width=1920, height=1080
                            )
                            base_clip = composited
                        else:
                            _normalize_clip(
                                html_res.output,
                                base_clip,
                                width=1920,
                                height=1080,
                                duration=clip_dur,
                            )
                except HtmlMotionError as error:
                    notes.append(f"html_motion fallback on beat {idx}: {error}")
            else:
                _render_solid_clip(
                    base_clip, width=1920, height=1080, duration=clip_dur, color=primary_col
                )

            final_beat_clip = base_clip
            if beat.preset:
                preset_out = scratch / f"beat-{idx:02d}-preset.mov"
                try:
                    ec_res = render_promo_preset(
                        beat.preset,
                        preset_out,
                        text=beat.text,
                        subtext=project.title,
                        project=project,
                        width=1920,
                        height=1080,
                        fps=30,
                        duration=clip_dur,
                    )
                    if ec_res.ok and ec_res.output.is_file():
                        composited = scratch / f"beat-{idx:02d}-comp.mp4"
                        _composite_overlay(
                            base_clip, ec_res.output, composited, width=1920, height=1080
                        )
                        final_beat_clip = composited
                except EffectCraftError as error:
                    notes.append(f"effectcraft fallback on beat {idx}: {error}")

            beat_clips.append(final_beat_clip)
            if idx > 0:
                sfx_cues.append(SfxCue(path_or_id=beat.sfx, at=max(0.0, elapsed), gain_db=-4.0))
            elapsed += beat.duration

        # 3. Join clips sequentially with transitions.
        master_video = beat_clips[0]
        for idx in range(1, len(beat_clips)):
            joined = scratch / f"joined-{idx:02d}.mp4"
            try:
                apply_transition(
                    master_video,
                    beat_clips[idx],
                    kind=plan.beats[idx].transition,
                    output=joined,
                    duration_ms=trans_ms,
                    width=1920,
                    height=1080,
                    fps=30,
                )
                master_video = joined
            except TransitionError as error:
                raise PromoError(f"transition failed at beat {idx}: {error}") from error

        master_info = media.probe(master_video)
        master_dur = max(plan.total_duration, master_info.duration)

        # 4. Mix voiceover + optional ducked music + transition SFX at -14 LUFS.
        padded_vo = scratch / "vo-padded.wav"
        _pad_audio(vo_wav, padded_vo, total_duration=master_dur)
        base_bed = padded_vo
        music_path = _resolve_music_path(project)
        if music_path is not None:
            trimmed_music = scratch / "music-trimmed.wav"
            _pad_audio(music_path, trimmed_music, total_duration=master_dur)
            ducked_music = scratch / "music-ducked.wav"
            audio.duck_music(trimmed_music, padded_vo, ducked_music)
            vo_plus_music = scratch / "vo-plus-music.wav"
            _mix_vo_and_music(padded_vo, ducked_music, vo_plus_music, total_duration=master_dur)
            base_bed = vo_plus_music

        mixed_audio = scratch / "mixed-audio.wav"
        try:
            valid_cues = [cue for cue in sfx_cues if 0.0 <= cue.at <= master_dur]
            mix_transition_sfx(
                base_bed,
                valid_cues,
                output=mixed_audio,
                total_duration=master_dur,
                target_lufs=-14.0,
            )
        except SfxError as error:
            raise PromoError(f"SFX mix failed: {error}") from error

        # 5. Per-deliverable reframe, captions, and final loudness encode.
        raw_words = json.loads(words_json.read_text(encoding="utf-8"))
        word_timings = [
            WordTiming(
                word=str(item["word"]),
                start=float(item["start"]),
                end=float(item["end"]),
            )
            for item in raw_words
            if isinstance(item, dict) and item.get("word")
        ]
        rendered_paths: dict[str, Path] = {}
        qc_results: dict[str, Any] = {}

        for deliv_id, final_mp4 in target_paths.items():
            aspect = _aspect_for_deliverable(project, deliv_id)
            aspect_video = master_video
            if aspect != "16:9":
                reframed = scratch / f"{deliv_id}-reframed.mp4"
                try:
                    reframe_video(master_video, aspect=aspect, mode="auto", output=reframed)
                    aspect_video = reframed
                except ReframeError as error:
                    raise PromoError(f"reframe failed for {deliv_id}: {error}") from error

            muxed = scratch / f"{deliv_id}-muxed.mp4"
            _mux_video_and_audio(aspect_video, mixed_audio, muxed)

            captioned = muxed
            if word_timings:
                captioned_out = scratch / f"{deliv_id}-captioned.mp4"
                render_captions(
                    muxed,
                    words=word_timings,
                    style="promo-pop",
                    aspect=aspect,
                    accent_color=accent_col,
                    output=captioned_out,
                    engine="ass",
                )
                captioned = captioned_out

            video.encode_with_loudness(captioned, final_mp4, target_lufs=project.loudness_lufs)
            rendered_paths[deliv_id] = final_mp4
            qc_results[deliv_id] = video.qc_report(
                final_mp4,
                expected_duration=plan.total_duration,
                target_lufs=project.loudness_lufs,
                engine="promo",
            )

        return PromoRenderResult(
            gated=False,
            storyboard=storyboard_path if storyboard_path.is_file() else None,
            deliverables=rendered_paths,
            qc=qc_results,
            notes=tuple(notes),
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _run_media_mcp_checks(path: Path) -> dict[str, Any]:
    """Best-effort hook for media-mcp AV-sync verification when available."""

    default_root = str(Path.home() / ".clone" / "Github" / "SuperBypassUdinnn" / "media-mcp")
    media_mcp_root = Path(os.environ.get("MEDIA_MCP_ROOT", default_root))
    python_bin = media_mcp_root / "media" / ".venv" / "bin" / "python"
    server_py = media_mcp_root / "media" / "server.py"
    if not python_bin.is_file() or not server_py.is_file():
        return {"available": False, "status": "unavailable", "reason": "media-mcp not installed"}

    probe_script = (
        "import asyncio, json, sys\n"
        "from media import server\n"
        "async def run():\n"
        "    await server.media_beats(sys.argv[1])\n"
        "    res = await server.media_avsync(sys.argv[1])\n"
        "    print(res)\n"
        "asyncio.run(run())\n"
    )
    try:
        completed = subprocess.run(
            [str(python_bin), "-c", probe_script, str(path)],
            cwd=str(media_mcp_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": True, "status": "error", "error": str(error)}

    if completed.returncode != 0:
        return {
            "available": True,
            "status": "error",
            "error": (completed.stderr or completed.stdout or "").strip()[-400:],
        }
    return {
        "available": True,
        "status": "ok",
        "avsync": completed.stdout.strip()[-800:],
    }


def qc_promo(
    project: Project,
    *,
    deliverables: tuple[str, ...] = DEFAULT_DELIVERABLES,
    use_media_mcp: bool = True,
) -> dict[str, Any]:
    """Run FFprobe/LUFS QC and ``ms see`` contact-sheet verification on promo deliverables."""

    if not deliverables:
        raise PromoError("deliverables must not be empty")

    expected_by_id = {d.id: d.duration for d in project.deliverables}
    results: dict[str, Any] = {}
    all_ok = True

    for deliv_id in deliverables:
        path = project.root / "out" / "video" / f"{deliv_id}.mp4"
        if not path.is_file():
            raise PromoError(f"missing deliverable: {path}")

        report = video.qc_report(
            path,
            expected_duration=expected_by_id.get(deliv_id),
            target_lufs=project.loudness_lufs,
            engine="promo",
        )
        aspect = _aspect_for_deliverable(project, deliv_id)
        exp_w, exp_h = OUTPUT_SIZE[aspect]
        dim_ok = report["width"] == exp_w and report["height"] == exp_h
        fps_ok = float(report["fps"] or 0.0) >= 30.0
        report["checks"]["dimensions_match_aspect"] = dim_ok
        report["checks"]["fps_at_least_30"] = fps_ok
        report["ok"] = bool(all(report["checks"].values()))

        sheet_path = project.root / "out" / "promo" / f"{deliv_id}-sheet.png"
        try:
            peek_res = peek(path, sheet_path, columns=4, rows=3, width=320)
            report["contact_sheet"] = str(peek_res.sheet) if peek_res.sheet else None
        except SeeError as error:
            raise PromoError(f"ms see failed on {path}: {error}") from error

        if use_media_mcp:
            report["media_mcp"] = _run_media_mcp_checks(path)

        if not report["ok"]:
            all_ok = False
        results[deliv_id] = report

    return {"project": project.slug, "ok": all_ok, "deliverables": results}
