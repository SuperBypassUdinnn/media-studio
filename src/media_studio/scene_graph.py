"""Composable multi-engine Scene-Graph orchestrator for Media-Studio projects."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from media_studio import video
from media_studio.manifest import Project
from media_studio.reframe import OUTPUT_SIZE
from media_studio.sfx import SfxCue, mix_transition_sfx
from media_studio.stage_3d import Stage3DSpec, render_3d_stage_clip
from media_studio.typography_3d import (
    Typography3DSpec,
    composite_z_depth_sandwich,
)


@dataclass(frozen=True)
class SceneNode:
    """One composable scene production node."""

    id: str
    duration: float
    engine: str = "composite"
    shot_scale: str = "MS"
    camera_angle: str = "low-hero"
    focal_point: tuple[float, float] = (0.5, 0.5)
    camera: dict[str, Any] = field(default_factory=dict)
    blender_3d: dict[str, Any] | None = None
    typography_3d: dict[str, Any] | None = None
    flow_gen: dict[str, Any] | None = None
    transition_out: dict[str, Any] | None = None
    sound_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SceneNode:
        fp = data.get("focal_point") or [0.5, 0.5]
        return cls(
            id=str(data.get("id", "scene")),
            duration=float(data.get("duration", 4.0)),
            engine=str(data.get("engine", "composite")),
            shot_scale=str(data.get("shot_scale", "MS")),
            camera_angle=str(data.get("camera_angle", "low-hero")),
            focal_point=(float(fp[0]), float(fp[1])),
            camera=dict(data.get("camera") or {}),
            blender_3d=dict(data["blender_3d"]) if data.get("blender_3d") else None,
            typography_3d=dict(data["typography_3d"]) if data.get("typography_3d") else None,
            flow_gen=dict(data["flow_gen"]) if data.get("flow_gen") else None,
            transition_out=dict(data["transition_out"]) if data.get("transition_out") else None,
            sound_events=tuple(dict(x) for x in data.get("sound_events") or ()),
        )


@dataclass(frozen=True)
class SceneGraph:
    """Top-level multi-scene project timeline graph."""

    project_slug: str
    aspects: tuple[str, ...] = ("9:16", "16:9")
    fps: int = 60
    subtitles: bool = False
    audio: dict[str, Any] = field(default_factory=dict)
    scenes: tuple[SceneNode, ...] = field(default_factory=tuple)

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SceneGraph:
        scenes = tuple(SceneNode.from_dict(item) for item in data.get("scenes") or ())
        return cls(
            project_slug=str(data.get("project_slug", "project")),
            aspects=tuple(str(a) for a in data.get("aspects") or ("9:16", "16:9")),
            fps=int(data.get("fps", 60)),
            subtitles=bool(data.get("subtitles", False)),
            audio=dict(data.get("audio") or {}),
            scenes=scenes,
        )


def load_scene_graph(path: Path) -> SceneGraph:
    """Parse a scene-graph.json file into a SceneGraph instance."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SceneGraph.from_dict(data)


def compute_scene_cache_key(project: Project, scene: SceneNode, *, aspect: str, fps: int) -> str:
    """Compute content-addressed SHA256 cache key for one scene node."""
    meta = {
        "project": project.slug,
        "scene_id": scene.id,
        "duration": scene.duration,
        "engine": scene.engine,
        "camera": scene.camera,
        "b3d": scene.blender_3d,
        "typo": scene.typography_3d,
        "aspect": aspect,
        "fps": fps,
        "brand": getattr(project.brand, "palette", {}),
    }
    raw = json.dumps(meta, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def render_scene_clip(
    project: Project,
    scene: SceneNode,
    output_mp4: Path,
    *,
    aspect: str = "16:9",
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
) -> Path:
    """Render one scene node to an encoded MP4 clip using its configured engine."""
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    palette = getattr(project.brand, "palette", {}) or {}
    primary = palette.get("primary", "#080B11")
    accent = palette.get("accent", "#F59E0B")

    with tempfile.TemporaryDirectory(prefix=f"sc-{scene.id}-", dir="/tmp/opencode") as tmp_dir:
        scratch = Path(tmp_dir)
        bg_clip = scratch / "bg.mp4"

        # 1. Base 3D Render
        stage_spec = Stage3DSpec(
            model_path=None,
            preset_geometry="luxury-perfume-flacon",
            shot_scale=scene.shot_scale,
            camera_angle=scene.camera_angle,
            focal_length_mm=float(scene.camera.get("focal_length_mm", 85.0)),
            dof_fstop=float(scene.camera.get("dof_fstop", 1.8)),
            primary_color=primary,
            accent_color=accent,
        )
        matte_mov = scratch / "matte.mov" if scene.typography_3d else None
        render_3d_stage_clip(
            stage_spec,
            bg_clip,
            matte_mov=matte_mov,
            duration=scene.duration,
            fps=fps,
            width=width,
            height=height,
        )

        # 2. Typography 3D & Z-Depth Composite
        if scene.typography_3d and scene.typography_3d.get("text"):
            typo_spec = Typography3DSpec(
                text=str(scene.typography_3d.get("text")),
                subtext=str(scene.typography_3d.get("subtext", "")),
                color=palette.get("ink", "#F8FAFC"),
                accent=accent,
                z_layer=str(scene.typography_3d.get("z_layer", "behind-subject")),
                focal_point=scene.focal_point,
            )
            # Create transparent typo overlay then composite
            typo_mov = scratch / "typo.mp4"
            shutil.copyfile(bg_clip, typo_mov)
            composite_z_depth_sandwich(
                bg_clip,
                typo_mov,
                matte_mov,
                output_mp4,
                z_layer=typo_spec.z_layer,
                apply_optical_grade=True,
            )
        else:
            shutil.copyfile(bg_clip, output_mp4)

    return output_mp4


def render_scene_graph(
    project: Project,
    graph: SceneGraph,
    *,
    scenes: Sequence[str] | None = None,
    deliverables: tuple[str, ...] = ("reel-9x16", "master-16x9"),
    force: bool = False,
) -> dict[str, Path]:
    """Render all scene nodes, assemble multi-track audio, and export video deliverables."""
    results: dict[str, Path] = {}
    fps = graph.fps
    cache_dir = project.root / "out" / "cache" / "scenes"
    cache_dir.mkdir(parents=True, exist_ok=True)

    for deliv in deliverables:
        aspect = "9:16" if "9x16" in deliv else "16:9"
        w, h = OUTPUT_SIZE.get(aspect, (1920, 1080))
        scene_clips: list[Path] = []
        elapsed = 0.0
        sfx_cues: list[SfxCue] = []

        for sc in graph.scenes:
            ckey = compute_scene_cache_key(project, sc, aspect=aspect, fps=fps)
            cached_mp4 = cache_dir / f"{ckey}.mp4"
            should_render = force or not cached_mp4.is_file() or (scenes and sc.id in set(scenes))

            if should_render:
                render_scene_clip(
                    project, sc, cached_mp4, aspect=aspect, width=w, height=h, fps=fps
                )
            scene_clips.append(cached_mp4)

            # Process scene-adaptive sound events
            for se in sc.sound_events:
                aid = se.get("asset_id") or "impact-sub"
                sfx_cues.append(
                    SfxCue(
                        path_or_id=aid,
                        at=max(0.0, elapsed + float(se.get("start_offset_s", 0.0))),
                        gain_db=float(se.get("gain_db", -4.0)),
                        lead_in_sec=float(se.get("lead_in_sec", 0.0)),
                        trail_out_sec=float(se.get("trail_out_s", 0.0)),
                    )
                )
            elapsed += sc.duration

        # Concat video scenes
        final_mp4 = project.root / "out" / "video" / f"{deliv}.mp4"
        final_mp4.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="concat-", dir="/tmp/opencode") as c_dir:
            c_scratch = Path(c_dir)
            list_txt = c_scratch / "scenes.txt"
            list_txt.write_text(
                "\n".join(f"file '{p.resolve()}'" for p in scene_clips), encoding="utf-8"
            )
            v_concat = c_scratch / "v_concat.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_txt),
                    "-c",
                    "copy",
                    str(v_concat),
                ],
                check=True,
            )

            # Mix audio bed
            a_mixed = c_scratch / "a_mixed.wav"
            mix_transition_sfx(
                None, sfx_cues, output=a_mixed, total_duration=elapsed, target_lufs=-14.0
            )

            # Final mux
            video.encode_with_loudness(
                v_concat,
                final_mp4,
                audio_track=a_mixed,
                target_lufs=-14.0,
                fps=fps,
            )

        results[deliv] = final_mp4

    return results
