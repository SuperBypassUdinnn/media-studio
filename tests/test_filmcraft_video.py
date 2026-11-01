"""Unit and integration tests for FilmCraft video pipeline, QC, review, and ducking."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from media_studio import media, video
from media_studio.fcproj import TimelineCompiler
from media_studio.filmcraft import CommandResult, ExportResult
from media_studio.manifest import (
    AudioCue,
    Brand,
    Budget,
    Deliverable,
    Project,
    Shot,
    VideoPlan,
)
from media_studio.video import FilmCraftRenderResult, render_filmcraft


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    """Generate a tiny 2-second 320x180 test video with audio."""
    out = tmp_path / "sample.mp4"
    cmd = [
        media.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x180:rate=24:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:duration=2",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-y",
        str(out),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return out


def test_generate_proxy(sample_video: Path, tmp_path: Path) -> None:
    proxy = tmp_path / "proxy.mp4"
    res = media.generate_proxy(sample_video, proxy, height=180)
    assert res.exists()
    info = media.probe(res)
    assert info.height == 180


def test_extract_stills(sample_video: Path, tmp_path: Path) -> None:
    stills_dir = tmp_path / "stills"
    stills = media.extract_stills(sample_video, stills_dir, timestamps=[0.5, 1.5])
    assert len(stills) == 2
    for s in stills:
        assert s.exists()
        assert s.suffix == ".png"


def test_qc_report_records_engine_and_gpu(sample_video: Path) -> None:
    report = video.qc_report(
        sample_video,
        expected_duration=2.0,
        target_lufs=-14.0,
        engine="filmcraft 0.15.0",
        render_path="gpu",
        gpu_fallback_to_cpu=False,
    )
    assert report["engine"] == "filmcraft 0.15.0"
    assert report["render_path"] == "gpu"
    assert report["gpu_fallback_to_cpu"] is False
    assert "checks" in report


def test_render_filmcraft_mock(tmp_path: Path, sample_video: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    fcproj_file = project_root / "out" / "main.fcproj"
    fcproj_file.parent.mkdir(parents=True)
    fcproj_file.write_text("{}", encoding="utf-8")

    deliverable = Deliverable(
        id="main",
        kind="video",
        container="mp4",
        resolution="320x180",
        fps=24.0,
        duration=2.0,
        loudness_lufs=-14.0,
    )
    project = Project(
        root=project_root,
        slug="test",
        title="Test Project",
        status="draft",
        deliverables=(deliverable,),
        brand=Brand(),
        budget=Budget(),
        assets={"shot1": "assets/shot1.mp4"},
        timeline=(),
        video=VideoPlan(
            shots=(Shot(asset="shot1", in_point=0.0, duration=2.0),),
            voice=None,
            music=None,
            titles=(),
        ),
        design=None,
        effects=(),
        raw={},
    )

    import shutil

    mock_runner = MagicMock()

    def fake_export(*args, **kwargs):
        out_target = kwargs.get("output_file") or (args[1] if len(args) > 1 else None)
        out_path = Path(out_target)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(sample_video, out_path)
        return ExportResult(
            output=out_path,
            returncode=0,
            stdout="",
            stderr="",
            gpu_attempted=True,
            gpu_fallback_to_cpu=False,
            render_path="gpu",
            ok=True,
        )

    mock_runner.export.side_effect = fake_export

    with (
        patch("media_studio.video.FilmCraftRunner", return_value=mock_runner),
        patch("media_studio.video.qc_report") as mock_qc,
    ):
        mock_qc.return_value = {
            "ok": True,
            "engine": "filmcraft 0.15.0",
            "render_path": "gpu",
            "gpu_fallback_to_cpu": False,
            "checks": {"has_video": True, "has_audio": True},
        }
        result = render_filmcraft(
            project,
            deliverable,
            fcproj_path=fcproj_file,
            runner=mock_runner,
            generate_review=True,
        )

    assert isinstance(result, FilmCraftRenderResult)
    assert result.ok
    assert result.report["engine"] == "filmcraft 0.15.0"
    assert result.report["render_path"] == "gpu"
    assert result.proxy_path is not None and result.proxy_path.exists()
    assert len(result.stills) > 0
    assert result.contact_sheet is not None and result.contact_sheet.exists()


def test_audio_ducking_baked_file(tmp_path: Path) -> None:
    project_root = tmp_path / "proj_duck"
    project_root.mkdir()
    (project_root / "assets").mkdir()
    voice_file = project_root / "assets" / "voice.wav"
    music_file = project_root / "assets" / "music.wav"
    voice_file.write_text("voice", encoding="utf-8")
    music_file.write_text("music", encoding="utf-8")

    deliverable = Deliverable(
        id="main",
        kind="video",
        container="mp4",
        resolution="1920x1080",
        fps=30.0,
    )
    project = Project(
        root=project_root,
        slug="duck_test",
        title="Duck Test",
        status="draft",
        deliverables=(deliverable,),
        brand=Brand(),
        budget=Budget(),
        assets={"voice": "assets/voice.wav", "music": "assets/music.wav"},
        timeline=(),
        video=VideoPlan(
            shots=(),
            voice=AudioCue(asset="voice", in_point=0.0, gain=1.0),
            music=AudioCue(asset="music", in_point=0.0, gain=0.8, duck=True),
            titles=(),
        ),
        design=None,
        effects=(),
        raw={},
    )

    mock_runner = MagicMock()
    mock_runner.exec.side_effect = lambda cid, **kw: CommandResult(
        command=cid,
        returncode=0,
        stdout="",
        stderr="",
        result={"id": 1, "items": [10], "clips": [20]},
        ok=True,
    )

    expected_ducked = project_root / "out" / "audio" / "music-ducked.wav"
    with patch(
        "media_studio.audio.duck_music",
        return_value=expected_ducked,
    ) as duck_mock:
        compiler = TimelineCompiler(runner=mock_runner)
        compiler.compile(project, output_fcproj=project_root / "out.fcproj")

    # The music is ducked on disk before import, and the baked file is what
    # gets imported onto A2 (FilmCraft 0.15.0 export ignores keyframe audio).
    duck_mock.assert_called_once()
    assert duck_mock.call_args[0][0] == music_file
    assert duck_mock.call_args[0][1] == voice_file
    assert duck_mock.call_args[0][2] == expected_ducked

    import_paths = [
        call.kwargs["params"]["paths"][0]
        for call in mock_runner.exec.call_args_list
        if call.args and call.args[0] == "file.import"
    ]
    assert str(expected_ducked) in import_paths
    assert str(music_file) not in import_paths

    # No per-clip volume keyframes are written anymore.
    batch_ids = [cmd["id"] for call in mock_runner.run_batch.call_args_list for cmd in call[0][0]]
    assert "effects.setParam" not in batch_ids
