"""Compiling ``project.yaml`` manifests into FilmCraft ``.fcproj`` timelines.

This module maps project declarations (deliverables, shots, voice, music, titles, motion
graphics) into FilmCraft engine commands, executes them via ``FilmCraftRunner``, and writes
a native ``.fcproj`` file that can be rendered headlessly or opened in the FilmCraft GUI.

Track Layout Architecture (Locked in Design v2):
    V1: A-Roll footage clips (main picture)
    V2: B-Roll footage / EffectCraft alpha ProRes 4444 overlay
    V3: Titles / graphics layers
    A1: Voice dialogue track
    A2: Background music bed
    A3: Sound effects (SFX)
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import audio, media
from .filmcraft import FilmCraftError, FilmCraftRunner
from .manifest import AudioCue, Deliverable, Project, audio_span, parse_resolution

TICKS_PER_SECOND = 254_016_000_000
DEFAULT_SEQUENCE_ID = 7  # FilmCraft assigns sequence id 7 to the first newSequence
DEFAULT_SHOT_SECONDS = 3.0
DEFAULT_FPS = 30.0
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080


def db_from_gain(gain: float) -> float:
    """Convert linear gain to decibels (0.0 to 1.0 -> -inf to 0.0 dB)."""
    if gain <= 0.0001:
        return -60.0
    return round(20.0 * math.log10(gain), 2)


def ticks_from_seconds(seconds: float) -> int:
    """Convert seconds to integer FilmCraft ticks."""
    return round(seconds * TICKS_PER_SECOND)


def _cue_span(project: Project, cue: AudioCue, video_seconds: float) -> float | None:
    """Seconds to place for a cue, clamped to the edit and to what the source has left."""

    relative = project.assets.get(cue.asset)
    source_seconds: float | None = None
    if relative:
        try:
            source_seconds = media.probe(project.root / relative).duration
        except media.MediaError:
            source_seconds = None
    return audio_span(cue, video_seconds, source_seconds)


def seconds_from_ticks(ticks: int | float) -> float:
    """Convert integer FilmCraft ticks (or float seconds) to seconds."""
    if isinstance(ticks, float):
        return round(ticks, 4)
    return round(ticks / TICKS_PER_SECOND, 4)


@dataclass(frozen=True)
class TrackConfig:
    """Configuration for one timeline track."""

    track_id: str
    name: str
    label: str
    kind: str  # 'video' or 'audio'


DEFAULT_TRACKS: tuple[TrackConfig, ...] = (
    TrackConfig("V1", "A-Roll", "Iris", "video"),
    TrackConfig("V2", "B-Roll / Motion", "Violet", "video"),
    TrackConfig("V3", "Titles", "Orange", "video"),
    TrackConfig("A1", "Voice", "Cerulean", "audio"),
    TrackConfig("A2", "Music", "Forest", "audio"),
    TrackConfig("A3", "SFX", "Rose", "audio"),
)


@dataclass(frozen=True)
class CompileResult:
    """The outcome of compiling a manifest into a .fcproj project."""

    fcproj_path: Path
    sequence_id: int
    imported_items: dict[str, int] = field(default_factory=dict)
    placed_clips: list[dict[str, Any]] = field(default_factory=list)
    commands_executed: int = 0
    duration_seconds: float = 0.0

    @property
    def output_path(self) -> Path:
        """Alias for fcproj_path."""
        return self.fcproj_path

    @property
    def shots_placed(self) -> int:
        """Count of video clips placed on V1."""
        return sum(1 for c in self.placed_clips if c.get("track") == "V1")

    @property
    def audio_cues_placed(self) -> int:
        """Count of audio cues placed on A1/A2/A3."""
        return sum(1 for c in self.placed_clips if str(c.get("track", "")).startswith("A"))

    @property
    def titles_placed(self) -> int:
        """Count of title text elements placed."""
        return sum(
            1 for c in self.placed_clips if c.get("track") == "V3" or c.get("type") == "title"
        )


@dataclass
class CompilePlan:
    """Dry-run output: what ``build`` would do, without touching the engine."""

    fcproj_path: Path
    """Resolved destination of the compiled ``.fcproj``."""

    width: int
    height: int
    fps: float

    imports: dict[str, Path] = field(default_factory=dict)
    """asset id -> resolved media path that ``file.import`` would receive."""

    bake_duck: bool = False
    """Whether the music duck would be baked into the music file before import (D48)."""

    shots: int = 0
    motion_overlays: int = 0
    voice_cues: int = 0
    music_cues: int = 0
    titles: int = 0
    duration_seconds: float = 0.0
    """Timeline duration the V1 shot chain would occupy."""


class CompilerError(FilmCraftError):
    """Raised when manifest compilation fails."""


class TimelineCompiler:
    """Compiles a ``Project`` manifest into a native FilmCraft ``.fcproj`` timeline."""

    def __init__(self, runner: FilmCraftRunner | None = None) -> None:
        self.runner = runner or FilmCraftRunner()
        # Stage 2 project guard: engine commands may only touch files in the project.
        self.runner.allowed_root = None  # set per compile() to the project root

    def _resolve_asset_path(self, project: Project, asset_id: str) -> Path:
        """Resolve a declared asset id to an existing file path."""
        relative = project.assets.get(asset_id)
        if not relative:
            raise CompilerError(
                f"Asset id {asset_id!r} is not declared under assets: in project.yaml"
            )
        path = (project.root / relative).resolve()
        if not path.is_file():
            raise CompilerError(f"Asset file for {asset_id!r} does not exist at {path}")
        return path

    def _get_deliverable(self, project: Project, deliverable_id: str | None = None) -> Deliverable:
        """Find the target video deliverable."""
        if deliverable_id:
            for d in project.deliverables:
                if d.id == deliverable_id:
                    return d
            raise CompilerError(f"Deliverable {deliverable_id!r} not found in project.yaml")

        for d in project.deliverables:
            if d.kind == "video":
                return d

        return Deliverable(
            id="master",
            kind="video",
            container="mp4",
            resolution="1920x1080",
            fps=DEFAULT_FPS,
            duration=None,
            loudness_lufs=-14.0,
        )

    def plan(
        self,
        project: Project,
        output_fcproj: Path | None = None,
        deliverable_id: str | None = None,
    ) -> CompilePlan:
        """Compute what ``compile`` would do, without executing a single command.

        Used by ``ms video build --dry-run``: validates the manifest, resolves the
        deliverable geometry and every import path, and counts the placements. The
        music duck is reported as a flag, never baked (that writes files).
        """
        deliverable = self._get_deliverable(project, deliverable_id)
        target_path = output_fcproj or (project.root / f"{project.slug}.fcproj")
        target_path = target_path.resolve()

        width, height = (
            parse_resolution(deliverable.resolution)
            if deliverable.resolution
            else (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        )
        fps = float(deliverable.fps or DEFAULT_FPS)

        imports: dict[str, Path] = {}
        bake_duck = False
        voice_cues = 0
        music_cues = 0
        if project.video:
            for shot in project.video.shots:
                imports.setdefault(shot.asset, self._resolve_asset_path(project, shot.asset))
            if project.video.voice:
                voice_cues = 1
                imports.setdefault(
                    project.video.voice.asset,
                    self._resolve_asset_path(project, project.video.voice.asset),
                )
            if project.video.music:
                music_cues = 1
                imports.setdefault(
                    project.video.music.asset,
                    self._resolve_asset_path(project, project.video.music.asset),
                )
                bake_duck = bool(project.video.music.duck and project.video.voice)

        motion_raw = project.raw.get("motion") if isinstance(project.raw, dict) else None
        motion_seqs = motion_raw.get("sequences", []) if isinstance(motion_raw, dict) else []
        motion_overlays = 0
        for mseq in motion_seqs:
            if not isinstance(mseq, dict) or not mseq.get("id"):
                continue
            candidates = [
                project.root / "out" / "motion" / f"{mseq['id']}.mov",
                project.root / "out" / f"{mseq['id']}.mov",
            ]
            if any(p.exists() for p in candidates):
                motion_overlays += 1

        duration = sum(
            (shot.duration or DEFAULT_SHOT_SECONDS)
            for shot in (project.video.shots if project.video else [])
        )

        return CompilePlan(
            fcproj_path=target_path,
            width=width,
            height=height,
            fps=fps,
            imports=imports,
            bake_duck=bake_duck,
            shots=len(project.video.shots) if project.video else 0,
            motion_overlays=motion_overlays,
            voice_cues=voice_cues,
            music_cues=music_cues,
            titles=len(project.video.titles) if project.video else 0,
            duration_seconds=duration,
        )

    def compile(
        self,
        project: Project,
        output_fcproj: Path | None = None,
        deliverable_id: str | None = None,
        force: bool = False,
    ) -> CompileResult:
        """Compile the project manifest into a .fcproj timeline."""
        deliverable = self._get_deliverable(project, deliverable_id)
        target_path = output_fcproj or (project.root / f"{project.slug}.fcproj")
        target_path = target_path.resolve()
        if target_path.exists() and not force:
            # D46: manual GUI edits must never be silently overwritten.
            raise CompilerError(
                f"{target_path} already exists and may carry manual GUI edits; "
                "inspect it (ms video inspect) and re-run with --force to overwrite "
                "(a snapshot is archived first)."
            )
        if target_path.exists() and force:
            # D46 snapshot rule: archive the current state before clobbering it.
            snapshot_timeline(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        self.runner.allowed_root = target_path.parent

        # Parse resolution & fps
        width, height = (
            parse_resolution(deliverable.resolution)
            if deliverable.resolution
            else (
                DEFAULT_WIDTH,
                DEFAULT_HEIGHT,
            )
        )
        fps = float(deliverable.fps or DEFAULT_FPS)

        commands: list[dict[str, Any]] = []

        # 1. Project & Sequence creation
        commands.append({"id": "file.newProject", "params": {"name": project.slug}})
        commands.append(
            {
                "id": "file.newSequence",
                "params": {
                    "name": "main",
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "video": 3,
                    "audio": 3,
                },
            }
        )

        # 2. Track labelling and configuration
        for track in DEFAULT_TRACKS:
            commands.append(
                {
                    "id": "timeline.setTrack",
                    "params": {
                        "track": track.track_id,
                        "name": track.name,
                    },
                }
            )

        # Execute project + sequence + track setup
        init_results = self.runner.run_batch(commands, save_as=target_path)
        seq_id = DEFAULT_SEQUENCE_ID  # engine default when newSequence reports nothing
        for res in init_results:
            if res.command == "file.newSequence" and isinstance(res.result, dict):
                seq_id = int(res.result.get("sequence", seq_id))

        # 3. Collect assets to import
        assets_to_import: dict[str, Path] = {}
        if project.video:
            for shot in project.video.shots:
                if shot.asset not in assets_to_import:
                    assets_to_import[shot.asset] = self._resolve_asset_path(project, shot.asset)
            if project.video.voice and project.video.voice.asset not in assets_to_import:
                assets_to_import[project.video.voice.asset] = self._resolve_asset_path(
                    project, project.video.voice.asset
                )
            if project.video.music and project.video.music.asset not in assets_to_import:
                assets_to_import[project.video.music.asset] = self._resolve_asset_path(
                    project, project.video.music.asset
                )

        # Baked ducking: FilmCraft 0.15.0 headless export ignores audio automation
        # (effect keyframes, clip-mixer keyframes, and track-mixer lanes); only
        # static mixer strip gain renders. When duck is requested, apply the duck
        # to the music file itself via ffmpeg sidechaincompress before import, so
        # the export carries a real duck. Strip gain is then static on A2.
        if (
            project.video
            and project.video.music
            and project.video.music.duck
            and project.video.voice
        ):
            try:
                music_path = assets_to_import[project.video.music.asset]
                voice_path = assets_to_import[project.video.voice.asset]
                ducked_path = project.root / "out" / "audio" / f"{music_path.stem}-ducked.wav"
                audio.duck_music(music_path, voice_path, ducked_path)
                assets_to_import[project.video.music.asset] = ducked_path
            except audio.AudioError as error:
                raise CompilerError(f"failed to bake music ducking: {error}") from error

        # Import assets one by one to reliably map asset_id -> item_id
        imported_items: dict[str, int] = {}
        for asset_id, path in assets_to_import.items():
            import_res = self.runner.exec(
                "file.import",
                params={"paths": [str(path)]},
                project_path=target_path,
                save=True,
            )
            raw_items = (
                import_res.result.get("items", []) if isinstance(import_res.result, dict) else []
            )
            if raw_items:
                imported_items[asset_id] = raw_items[0]
            else:
                err_msg = f"Failed to import asset {asset_id} from {path}: {import_res.result}"
                raise CompilerError(err_msg)

        # 4. Assembly commands (shots, voice, music, titles)
        assembly_commands: list[dict[str, Any]] = []
        placed_clips: list[dict[str, Any]] = []
        cur_timeline_seconds = 0.0

        if project.video:
            # Total picture length: the declared deliverable duration, or the shot chain.
            video_seconds: float | None = deliverable.duration
            if video_seconds is None:
                video_seconds = sum(
                    (shot.duration or DEFAULT_SHOT_SECONDS) for shot in project.video.shots
                )
            # A-Roll Video Shots on V1
            for shot in project.video.shots:
                item_id = imported_items.get(shot.asset)
                if item_id is None:
                    continue
                shot_dur = shot.duration or DEFAULT_SHOT_SECONDS
                place_params: dict[str, Any] = {
                    "item": item_id,
                    "track": "V1",
                    "seconds": cur_timeline_seconds,
                    "sourceIn": {"seconds": float(shot.in_point)},
                    "duration": {"seconds": float(shot_dur)},
                    "video": True,
                    "audio": False,
                }
                assembly_commands.append({"id": "timeline.place", "params": place_params})
                placed_clips.append(
                    {
                        "asset": shot.asset,
                        "track": "V1",
                        "start": cur_timeline_seconds,
                        "duration": shot_dur,
                        "sourceIn": shot.in_point,
                    }
                )
                cur_timeline_seconds += shot_dur

            # Motion Graphics / Alpha ProRes Overlays on V2
            motion_raw = project.raw.get("motion") if isinstance(project.raw, dict) else None
            motion_seqs = motion_raw.get("sequences", []) if isinstance(motion_raw, dict) else []
            for mseq in motion_seqs:
                if not isinstance(mseq, dict):
                    continue
                motion_id = mseq.get("id")
                if not motion_id:
                    continue
                candidates = [
                    project.root / "out" / "motion" / f"{motion_id}.mov",
                    project.root / "out" / f"{motion_id}.mov",
                ]
                motion_file = next((p for p in candidates if p.exists()), None)
                if motion_file:
                    import_res = self.runner.exec(
                        "file.import",
                        {"paths": [str(motion_file)]},
                        project_path=target_path,
                    )
                    items = (
                        import_res.result.get("items", [])
                        if isinstance(import_res.result, dict)
                        else []
                    )
                    if items:
                        m_item = items[0]
                        m_insert = float(mseq.get("insert_at", mseq.get("start", 0.0)))
                        m_dur = float(mseq.get("duration", 4.0))
                        assembly_commands.append(
                            {
                                "id": "timeline.place",
                                "params": {
                                    "item": m_item,
                                    "track": "V2",
                                    "seconds": m_insert,
                                    "duration": {"seconds": m_dur},
                                    "video": True,
                                    "audio": False,
                                },
                            }
                        )
                        placed_clips.append(
                            {
                                "asset": str(motion_file),
                                "track": "V2",
                                "start": m_insert,
                                "duration": m_dur,
                                "type": "motion_overlay",
                            }
                        )

            # Voiceover on A1
            if project.video.voice:
                voice_item = imported_items.get(project.video.voice.asset)
                if voice_item is not None:
                    assembly_commands.append(
                        {
                            "id": "timeline.place",
                            "params": {
                                "item": voice_item,
                                "track": "A1",
                                "seconds": 0.0,
                                "sourceIn": {"seconds": float(project.video.voice.in_point)},
                                "video": False,
                                "audio": True,
                            },
                        }
                    )
                    placed_clips.append(
                        {
                            "asset": project.video.voice.asset,
                            "track": "A1",
                            "start": 0.0,
                            "sourceIn": project.video.voice.in_point,
                        }
                    )

            # Music Bed on A2 with Gain
            if project.video.music:
                music_item = imported_items.get(project.video.music.asset)
                if music_item is not None:
                    music_span = _cue_span(project, project.video.music, float(video_seconds))
                    place_params: dict[str, Any] = {
                        "item": music_item,
                        "track": "A2",
                        "seconds": 0.0,
                        "sourceIn": {"seconds": float(project.video.music.in_point)},
                        "video": False,
                        "audio": True,
                    }
                    if music_span is not None:
                        place_params["duration"] = {"seconds": music_span}
                    assembly_commands.append({"id": "timeline.place", "params": place_params})
                    # Apply music gain via mixer strip
                    gain_db = db_from_gain(project.video.music.gain)
                    assembly_commands.append(
                        {
                            "id": "mixer.setStrip",
                            "params": {"strip": "A2", "volumeDb": gain_db},
                        }
                    )
                    placed_clips.append(
                        {
                            "asset": project.video.music.asset,
                            "track": "A2",
                            "start": 0.0,
                            "sourceIn": project.video.music.in_point,
                            "gainDb": gain_db,
                        }
                    )

            # Titles on V3
            for title in project.video.titles:
                title_params: dict[str, Any] = {
                    "text": title.text,
                    "seconds": float(title.duration),
                    "size": title.size,
                    "track": 3,
                    "time": ticks_from_seconds(float(title.start)),
                }
                assembly_commands.append({"id": "graphics.newText", "params": title_params})
                placed_clips.append(
                    {
                        "type": "title",
                        "track": "V3",
                        "text": title.text,
                        "start": title.start,
                        "duration": title.duration,
                    }
                )

        # Execute assembly batch
        if assembly_commands:
            self.runner.run_batch(assembly_commands, project_path=target_path, save=True)

        # Audio ducking is baked into the music file before import (see the
        # collect-assets phase); FilmCraft 0.15.0 headless export ignores
        # keyframe automation, so no per-clip keyframes are written here.

        return CompileResult(
            fcproj_path=target_path,
            sequence_id=seq_id,
            imported_items=imported_items,
            placed_clips=placed_clips,
            commands_executed=len(commands) + len(assets_to_import) + len(assembly_commands),
            duration_seconds=cur_timeline_seconds,
        )


def snapshot_timeline(project_path: Path) -> Path:
    """Create a timestamped backup snapshot of a .fcproj project file."""
    if not project_path.exists():
        raise CompilerError(f"Cannot snapshot non-existent project: {project_path}")
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    snapshot_path = project_path.with_name(f"{project_path.name}.snapshot-{timestamp}")
    shutil.copy2(project_path, snapshot_path)
    return snapshot_path


def diff_timelines(
    current_path: Path,
    snapshot_path: Path,
    runner: FilmCraftRunner | None = None,
) -> dict[str, Any]:
    """Compare two .fcproj files and report clip differences, duration shifts, and changes."""
    r = runner or FilmCraftRunner()
    current_data = r.inspect(current_path, target="sequence")
    snapshot_data = r.inspect(snapshot_path, target="sequence")

    curr_seq = current_data.get("sequence", {})
    snap_seq = snapshot_data.get("sequence", {})

    curr_dur = seconds_from_ticks(curr_seq.get("duration", 0))
    snap_dur = seconds_from_ticks(snap_seq.get("duration", 0))

    def _extract_clips(seq_dict: dict[str, Any]) -> list[dict[str, Any]]:
        clips: list[dict[str, Any]] = []
        # Check direct 'clips' list or nested video/audio tracks
        direct_clips = seq_dict.get("clips", [])
        if direct_clips:
            for item in direct_clips:
                clip_id = item.get("clip") or item.get("id")
                clips.append(
                    {
                        "clip": clip_id,
                        "name": item.get("name"),
                        "track": item.get("track", "Unknown"),
                        "start": seconds_from_ticks(item.get("start", 0)),
                        "duration": seconds_from_ticks(item.get("duration", 0)),
                    }
                )
            return clips

        for track in seq_dict.get("video", []) + seq_dict.get("audio", []):
            track_name = track.get("name", "Unknown")
            for item in track.get("items", []) or track.get("clips", []):
                clip_id = item.get("clip") or item.get("id")
                clips.append(
                    {
                        "clip": clip_id,
                        "name": item.get("name"),
                        "track": track_name,
                        "start": seconds_from_ticks(item.get("start", 0)),
                        "duration": seconds_from_ticks(item.get("duration", 0)),
                    }
                )
        return clips

    curr_clips = _extract_clips(curr_seq)
    snap_clips = _extract_clips(snap_seq)

    curr_clip_map = {c.get("clip"): c for c in curr_clips if c.get("clip")}
    snap_clip_map = {c.get("clip"): c for c in snap_clips if c.get("clip")}

    added = [c for cid, c in curr_clip_map.items() if cid not in snap_clip_map]
    removed = [c for cid, c in snap_clip_map.items() if cid not in curr_clip_map]
    modified = []

    for cid, c in curr_clip_map.items():
        if cid in snap_clip_map:
            prev = snap_clip_map[cid]
            if (
                prev.get("start") != c.get("start")
                or prev.get("duration") != c.get("duration")
                or prev.get("name") != c.get("name")
                or prev.get("track") != c.get("track")
            ):
                modified.append({"clip": cid, "before": prev, "after": c})

    return {
        "current_duration": curr_dur,
        "snapshot_duration": snap_dur,
        "duration_delta": round(curr_dur - snap_dur, 4),
        "clips_added": added,
        "clips_removed": removed,
        "clips_modified": modified,
        "added": added,
        "removed": removed,
        "modified": modified,
        "has_changes": bool(added or removed or modified or abs(curr_dur - snap_dur) > 0.001),
    }


def compile_manifest_to_fcproj(
    project: Project,
    output_fcproj: Path | None = None,
    deliverable_id: str | None = None,
    runner: FilmCraftRunner | None = None,
    force: bool = False,
) -> CompileResult:
    """Compile a project manifest into a native .fcproj timeline (convenience wrapper)."""
    return TimelineCompiler(runner=runner).compile(
        project,
        output_fcproj=output_fcproj,
        deliverable_id=deliverable_id,
        force=force,
    )


def plan_manifest_compile(
    project: Project,
    output_fcproj: Path | None = None,
    deliverable_id: str | None = None,
    runner: FilmCraftRunner | None = None,
) -> CompilePlan:
    """Preview a manifest compilation without touching the engine or disk (convenience wrapper)."""
    return TimelineCompiler(runner=runner).plan(
        project,
        output_fcproj=output_fcproj,
        deliverable_id=deliverable_id,
    )
