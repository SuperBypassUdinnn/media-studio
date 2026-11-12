"""Tests for the generative pipeline: cascade, motion-from-still, fallback, provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_studio.gen.base import GenRequest, ProviderQuotaError
from media_studio.gen.pipeline import (
    DETERMINISTIC_FALLBACK,
    MOTION_FROM_STILL,
    generate_asset,
    generate_storyboard,
)
from media_studio.gen.quota import QuotaStore
from media_studio.manifest import load_project
from media_studio.project import scaffold_project

FAKE_IMAGE = b"fake-image-png"
FAKE_VIDEO = b"fake-video-mp4"


class FakeProvider:
    """Configurable stand-in for a GenProvider."""

    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        kinds: tuple[str, ...] = ("image", "video"),
        image_result=FAKE_IMAGE,
        video_result=FAKE_VIDEO,
        image_error: Exception | None = None,
        video_error: Exception | None = None,
    ):
        self.name = name
        self._available = available
        self._kinds = set(kinds)
        self.image_result = image_result
        self.video_result = video_result
        self.image_error = image_error
        self.video_error = video_error
        self.image_calls = 0
        self.video_calls = 0

    def available(self) -> bool:
        return self._available

    def generates(self, kind: str) -> bool:
        return kind in self._kinds

    def generate_image(self, prompt, *, output, size, seed, references):
        self.image_calls += 1
        output.write_bytes(self.image_result)
        if self.image_error is not None:
            raise self.image_error
        from media_studio.gen.base import GenerateOutput

        return GenerateOutput(model=f"{self.name}-imagen", seed=seed, references=tuple(references))

    def generate_video(self, prompt, *, output, duration, fps, resolution, seed):
        self.video_calls += 1
        output.write_bytes(self.video_result)
        if self.video_error is not None:
            raise self.video_error
        from media_studio.gen.base import GenerateOutput

        return GenerateOutput(model=f"{self.name}-veo", seed=seed)


def _install_fakes(monkeypatch, providers) -> None:
    monkeypatch.setattr("media_studio.gen.pipeline.default_providers", lambda: list(providers))
    monkeypatch.setattr("media_studio.gen.pipeline.acquire_asset", lambda *a, **k: None)


def _write_dummy_ffmpeg(output, *, duration=0.0, fps=30, resolution="1920x1080") -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(FAKE_VIDEO)


def _write_dummy_still_to_motion(still, output, *, duration, fps, resolution, motion) -> None:
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(FAKE_VIDEO)


def test_cascade_all_providers_fail_then_deterministic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))
    quota_store = QuotaStore()

    aistudio = FakeProvider("aistudio", video_error=ProviderQuotaError("rate limited", 60))
    huggingface = FakeProvider("huggingface", kinds=("image",), image_error=RuntimeError("down"))
    keyless = FakeProvider("keyless", kinds=("image",), image_error=RuntimeError("down"))
    _install_fakes(monkeypatch, [aistudio, huggingface, keyless])
    monkeypatch.setattr("media_studio.gen.pipeline._deterministic_card", _write_dummy_ffmpeg)

    output = tmp_path / "out.mp4"
    result = generate_asset(
        GenRequest(kind="video", prompt="a sci-fi title card"),
        project=None,
        output=output,
        max_gen_calls=1,
        quota_store=quota_store,
    )

    assert result.fallback_used is True
    assert result.fallback_reason == DETERMINISTIC_FALLBACK
    assert result.provider == "deterministic"
    assert output.read_bytes() == FAKE_VIDEO
    # Failed attempts never consumed max_gen_calls or record_spend.
    status = quota_store.status()
    assert status.get("aistudio", {}).get("spent", 0) == 0
    assert status.get("huggingface", {}).get("spent", 0) == 0
    assert status.get("keyless", {}).get("spent", 0) == 0
    assert aistudio.video_calls == 1
    assert huggingface.image_calls == 1
    assert keyless.image_calls == 1


def test_video_request_from_image_provider_uses_motion_from_still(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))
    quota_store = QuotaStore()

    # aistudio video is rate limited; only an image provider succeeds.
    aistudio = FakeProvider("aistudio", video_error=ProviderQuotaError("rate limited", 120))
    huggingface = FakeProvider("huggingface", kinds=("image",))
    _install_fakes(monkeypatch, [aistudio, huggingface])
    monkeypatch.setattr("media_studio.gen.pipeline._still_to_motion", _write_dummy_still_to_motion)

    output = tmp_path / "out.mp4"
    result = generate_asset(
        GenRequest(kind="video", prompt="a slow push-in on a product"),
        project=None,
        output=output,
        max_gen_calls=1,
        quota_store=quota_store,
    )

    assert result.fallback_used is True
    assert result.fallback_reason == MOTION_FROM_STILL
    assert result.provider == "huggingface"
    assert output.read_bytes() == FAKE_VIDEO
    # One successful generative call recorded, the 429 was not counted.
    status = quota_store.status()
    assert status.get("aistudio", {}).get("spent", 0) == 0
    assert status.get("huggingface", {}).get("spent", 0) == 1


def test_image_generation_returns_first_successful_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))
    quota_store = QuotaStore()

    aistudio = FakeProvider("aistudio", image_error=RuntimeError("over quota"))
    keyless = FakeProvider("keyless", kinds=("image",))
    _install_fakes(monkeypatch, [aistudio, keyless])

    output = tmp_path / "out.png"
    result = generate_asset(
        GenRequest(kind="image", prompt="a product shot"),
        project=None,
        output=output,
        max_gen_calls=6,
        quota_store=quota_store,
    )

    assert result.fallback_used is False
    assert result.provider == "keyless"
    assert output.read_bytes() == FAKE_IMAGE
    status = quota_store.status()
    assert status.get("aistudio", {}).get("spent", 0) == 0
    assert status.get("keyless", {}).get("spent", 0) == 1


def test_image_all_providers_unavailable_returns_deterministic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))
    quota_store = QuotaStore()
    _install_fakes(
        monkeypatch,
        [
            FakeProvider("aistudio", available=False),
            FakeProvider("huggingface", available=False),
            FakeProvider("keyless", available=False),
        ],
    )

    output = tmp_path / "out.png"
    result = generate_asset(
        GenRequest(kind="image", prompt="x"),
        project=None,
        output=output,
        max_gen_calls=6,
        quota_store=quota_store,
    )

    assert result.fallback_used is True
    assert result.fallback_reason == DETERMINISTIC_FALLBACK
    assert result.provider == "deterministic"
    assert output.is_file()
    sidecar = Path(str(output) + ".license")
    assert sidecar.is_file()


def test_sidecar_and_ledger_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))
    quota_store = QuotaStore()

    keyless = FakeProvider("keyless", kinds=("image",))
    _install_fakes(monkeypatch, [keyless])
    monkeypatch.setattr("media_studio.gen.pipeline._still_to_motion", _write_dummy_still_to_motion)

    scaffold = scaffold_project("gen-demo", root=tmp_path)
    project = load_project(scaffold.path)
    output = tmp_path / "gen-demo" / "assets" / "design" / "hero.png"
    output.parent.mkdir(parents=True, exist_ok=True)

    result = generate_asset(
        GenRequest(
            kind="image",
            prompt="hero product shot",
            references=["ref-a.png"],
            seed=42,
        ),
        project=project,
        output=output,
        max_gen_calls=6,
        quota_store=quota_store,
    )

    assert result.fallback_used is False
    assert result.provider == "keyless"

    # Full REUSE sidecar next to the generated file.
    sidecar = Path(str(output) + ".license")
    fields = dict(line.split(": ", 1) for line in sidecar.read_text().splitlines() if ": " in line)
    assert fields["SPDX-License-Identifier"] == "LicenseRef-AI-Generated"
    assert fields["Attribution"] == "[AI Generated: keyless/keyless-imagen]"
    assert fields["Provider"] == "keyless"
    assert fields["Model"] == "keyless-imagen"
    assert fields["Prompt"] == "hero product shot"
    assert fields["Seed"] == "42"
    assert fields["References"] == "ref-a.png"
    assert fields["Generated-At"]
    assert fields["SHA256"]
    assert fields["Class"] == "design"
    assert fields["File"] == "hero.png"

    # Ledger index + credits updated for the project.
    index_path = project.root / "assets" / "index.json"
    credits_path = project.root / "assets" / "CREDITS.md"
    assert index_path.is_file()
    assert credits_path.is_file()
    credits = credits_path.read_text()
    assert "[AI Generated: keyless/keyless-imagen]" in credits


def test_storyboard_honours_shared_call_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))

    keyless = FakeProvider("keyless", kinds=("image",))
    _install_fakes(monkeypatch, [keyless])

    out_dir = tmp_path / "storyboard"
    results = generate_storyboard(
        ["scene one", "scene two", "scene three"],
        project=None,
        out_dir=out_dir,
        max_gen_calls=2,
    )

    assert len(results) == 2
    assert {result.provider for result in results} == {"keyless"}
    assert {p.name for p in out_dir.glob("*.png")} == {
        "000_scene-one.png",
        "001_scene-two.png",
    }


def test_video_image_fallback_leaves_no_temp_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))
    quota_store = QuotaStore()

    # aistudio video rate-limited; huggingface image succeeds -> motion-from-still.
    aistudio = FakeProvider("aistudio", video_error=ProviderQuotaError("rate limited", 120))
    huggingface = FakeProvider("huggingface", kinds=("image",))
    _install_fakes(monkeypatch, [aistudio, huggingface])
    monkeypatch.setattr("media_studio.gen.pipeline._still_to_motion", _write_dummy_still_to_motion)

    output = tmp_path / "out.mp4"
    result = generate_asset(
        GenRequest(kind="video", prompt="a slow push-in"),
        project=None,
        output=output,
        max_gen_calls=1,
        quota_store=quota_store,
    )

    assert result.fallback_used is True
    assert result.fallback_reason == MOTION_FROM_STILL
    assert output.read_bytes() == FAKE_VIDEO
    # No intermediate .png remains beside the video output.
    assert not output.with_suffix(".png").exists()


def test_image_all_providers_fail_returns_deterministic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))
    quota_store = QuotaStore()

    aistudio = FakeProvider("aistudio", image_error=ProviderQuotaError("rate limited", 60))
    huggingface = FakeProvider("huggingface", kinds=("image",), image_error=RuntimeError("down"))
    keyless = FakeProvider("keyless", kinds=("image",), image_error=RuntimeError("down"))
    _install_fakes(monkeypatch, [aistudio, huggingface, keyless])

    output = tmp_path / "out.png"
    result = generate_asset(
        GenRequest(kind="image", prompt="a product shot"),
        project=None,
        output=output,
        max_gen_calls=1,
        quota_store=quota_store,
    )

    assert result.fallback_used is True
    assert result.fallback_reason == DETERMINISTIC_FALLBACK
    assert result.provider == "deterministic"
    assert output.is_file()
    sidecar = Path(str(output) + ".license")
    assert sidecar.is_file()
    fields = dict(line.split(": ", 1) for line in sidecar.read_text().splitlines() if ": " in line)
    assert fields["SPDX-License-Identifier"] == "LicenseRef-AI-Generated"
    assert fields["Attribution"] == "[AI Generated: deterministic/pil-gradient-slate]"
    assert fields["Class"] == "design"


def test_storyboard_handles_provider_failures_across_scenes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quota_path = tmp_path / "quota.json"
    monkeypatch.setenv("MEDIA_STUDIO_QUOTA_PATH", str(quota_path))

    # First provider succeeds for scene one, then fails for the rest -> deterministic fallback.
    flaky = FakeProvider("keyless", kinds=("image",))
    call_count = {"n": 0}

    def flaky_generate_image(prompt, *, output, size, seed, references):
        call_count["n"] += 1
        output.write_bytes(FAKE_IMAGE)
        if call_count["n"] > 1:
            raise RuntimeError("provider down")
        from media_studio.gen.base import GenerateOutput

        return GenerateOutput(model="keyless-imagen", seed=seed, references=tuple(references))

    flaky.generate_image = flaky_generate_image
    _install_fakes(monkeypatch, [flaky])

    out_dir = tmp_path / "storyboard"
    results = generate_storyboard(
        ["scene one", "scene two", "scene three"],
        project=None,
        out_dir=out_dir,
        max_gen_calls=6,
    )

    assert len(results) == 3
    assert results[0].provider == "keyless" and results[0].fallback_used is False
    assert results[1].fallback_used is True
    assert results[1].fallback_reason == DETERMINISTIC_FALLBACK
    assert results[2].fallback_used is True
    assert results[2].fallback_reason == DETERMINISTIC_FALLBACK
    assert {p.name for p in out_dir.glob("*.png")} == {
        "000_scene-one.png",
        "001_scene-two.png",
        "002_scene-three.png",
    }
    for png in out_dir.glob("*.png"):
        sidecar = Path(str(png) + ".license")
        assert sidecar.is_file()
