"""Tests for the asset ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from media_studio.ledger import (
    LedgerError,
    LicensePolicyError,
    QuotaError,
    build_index,
    classify_license,
    read_sidecar,
    render_credits,
    sha256_of,
    sidecar_path,
    stage_asset,
    write_index_and_credits,
)
from media_studio.manifest import Project, load_project
from media_studio.project import scaffold_project

FIXED_TIME = "2026-10-24T00:00:00+00:00"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = scaffold_project("ledger-demo", root=tmp_path).path
    return load_project(root)


@pytest.fixture
def asset_file(tmp_path: Path) -> Path:
    path = tmp_path / "incoming" / "tree.glb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"glTF-binary-payload")
    return path


def test_classify_license_maps_provider_wording() -> None:
    cc0 = classify_license("CC0")
    cc_by = classify_license("CC BY 4.0")

    assert cc0 is not None and cc0.spdx == "CC0-1.0"
    assert cc0.attribution_required is False
    assert cc_by is not None and cc_by.attribution_required is True
    assert cc_by.commercial_ok is True
    assert classify_license("All rights reserved (c) me") is None
    assert classify_license("") is None


def test_classify_license_flags_noncommercial() -> None:
    info = classify_license("CC BY-NC 4.0")

    assert info is not None
    assert info.commercial_ok is False


def test_stage_asset_writes_file_and_complete_sidecar(project: Project, asset_file: Path) -> None:
    staged = stage_asset(
        project,
        class_name="3d",
        license_text="CC0",
        source_url="https://example.org/tree",
        attribution="Example Author",
        provider="Poly Haven",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )

    assert staged.relative == "assets/3d/tree.glb"
    assert staged.path.is_file()
    assert staged.sha256 == sha256_of(asset_file)
    assert staged.quarantined is False

    fields = read_sidecar(staged.path)
    assert fields["SPDX-License-Identifier"] == "CC0-1.0"
    assert fields["Source-URL"] == "https://example.org/tree"
    assert fields["Attribution"] == "Example Author"
    assert fields["Retrieved-At"] == FIXED_TIME
    assert fields["SHA256"] == staged.sha256
    assert fields["Provider"] == "Poly Haven"
    assert fields["Class"] == "3d"
    assert fields["File"] == "assets/3d/tree.glb"


def test_stage_asset_deduplicates_by_hash(project: Project, asset_file: Path) -> None:
    first = stage_asset(
        project, class_name="3d", license_text="CC0", local_file=asset_file, retrieved_at=FIXED_TIME
    )
    second = stage_asset(
        project, class_name="3d", license_text="CC0", local_file=asset_file, retrieved_at=FIXED_TIME
    )

    assert second.duplicate_of == first.relative
    assert second.relative == first.relative
    assert len(list((project.root / "assets" / "3d").iterdir())) == 2  # file plus sidecar


def test_stage_asset_quarantines_unknown_licence(project: Project, asset_file: Path) -> None:
    staged = stage_asset(
        project,
        class_name="3d",
        license_text="no idea",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )

    assert staged.quarantined is True
    assert staged.relative == "assets/unverified/tree.glb"
    assert read_sidecar(staged.path)["SPDX-License-Identifier"] == "NONE"


def test_stage_asset_refuses_noncommercial_licence(project: Project, asset_file: Path) -> None:
    with pytest.raises(LicensePolicyError, match="forbids commercial use"):
        stage_asset(
            project,
            class_name="3d",
            license_text="CC BY-NC 4.0",
            local_file=asset_file,
            retrieved_at=FIXED_TIME,
        )

    assert not (project.root / "assets" / "3d" / "tree.glb").exists()


def test_stage_asset_allows_noncommercial_on_request(project: Project, asset_file: Path) -> None:
    staged = stage_asset(
        project,
        class_name="3d",
        license_text="CC BY-NC 4.0",
        local_file=asset_file,
        allow_noncommercial=True,
        retrieved_at=FIXED_TIME,
    )

    assert staged.spdx == "CC-BY-NC-4.0"


def test_stage_asset_rejects_oversized_file(project: Project, asset_file: Path) -> None:
    project = tighter_budget(project, max_asset_bytes=4, max_project_bytes=1000)

    with pytest.raises(QuotaError, match=r"exceeds budget\.max_asset_bytes"):
        stage_asset(
            project,
            class_name="3d",
            license_text="CC0",
            local_file=asset_file,
            retrieved_at=FIXED_TIME,
        )

    assert not (project.root / "assets" / "3d" / "tree.glb").exists()


def test_stage_asset_rejects_project_overflow(project: Project, asset_file: Path) -> None:
    project = tighter_budget(project, max_asset_bytes=1000, max_project_bytes=8)

    with pytest.raises(QuotaError, match=r"over budget\.max_project_bytes"):
        stage_asset(
            project,
            class_name="3d",
            license_text="CC0",
            local_file=asset_file,
            retrieved_at=FIXED_TIME,
        )


def test_stage_asset_needs_exactly_one_source(project: Project, asset_file: Path) -> None:
    with pytest.raises(LedgerError, match="exactly one of local_file or url"):
        stage_asset(project, class_name="3d", license_text="CC0")
    with pytest.raises(LedgerError, match="exactly one of local_file or url"):
        stage_asset(
            project, class_name="3d", license_text="CC0", local_file=asset_file, url="https://x/y"
        )


def test_stage_asset_rejects_unknown_class(project: Project, asset_file: Path) -> None:
    with pytest.raises(LedgerError, match="unknown asset class"):
        stage_asset(project, class_name="holograms", license_text="CC0", local_file=asset_file)


def test_stage_asset_missing_file_is_an_error(project: Project, tmp_path: Path) -> None:
    with pytest.raises(LedgerError, match="no such file"):
        stage_asset(
            project,
            class_name="3d",
            license_text="CC0",
            local_file=tmp_path / "absent.glb",
        )


def test_stage_asset_downloads_through_the_opener(project: Project) -> None:
    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def read(self) -> bytes:
            return self.payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    requests: list[str] = []

    def opener(request: object, timeout: int | None = None) -> Response:
        requests.append(request.full_url)  # type: ignore[attr-defined]
        assert timeout is not None
        return Response(b"downloaded-payload")

    staged = stage_asset(
        project,
        class_name="music",
        license_text="CC BY 4.0",
        source_url="https://example.org/track",
        attribution="Composer",
        url="https://cdn.example.org/track.mp3?token=1",
        opener=opener,
        retrieved_at=FIXED_TIME,
    )

    assert requests == ["https://cdn.example.org/track.mp3?token=1"]
    assert staged.relative == "assets/music/track.mp3"
    assert staged.path.read_bytes() == b"downloaded-payload"
    assert read_sidecar(staged.path)["Attribution"] == "Composer"


def test_no_derivatives_licence_is_refused(project: Project, asset_file: Path) -> None:
    info = classify_license("CC BY-ND 4.0")

    assert info is not None
    assert info.commercial_ok is True
    assert info.derivatives_ok is False
    with pytest.raises(LicensePolicyError, match="forbids derivatives"):
        stage_asset(
            project,
            class_name="music",
            license_text="CC BY-ND 4.0",
            local_file=asset_file,
            retrieved_at=FIXED_TIME,
        )


def test_known_hash_skips_the_transfer(project: Project, asset_file: Path) -> None:
    first = stage_asset(
        project, class_name="3d", license_text="CC0", local_file=asset_file, retrieved_at=FIXED_TIME
    )

    def opener(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the ledger must not fetch an asset it already holds")

    second = stage_asset(
        project,
        class_name="3d",
        license_text="CC0",
        url="https://example.org/tree.glb",
        expected_sha256=first.sha256.upper(),
        opener=opener,
        retrieved_at=FIXED_TIME,
    )

    assert second.duplicate_of == "assets/3d/tree.glb"
    assert second.sha256 == first.sha256


def test_declared_content_length_refuses_before_transfer(project: Project) -> None:
    class Response:
        headers: ClassVar[dict[str, str]] = {"Content-Length": "900000000"}

        def read(self) -> bytes:
            raise AssertionError("the body must not be read once the size is known to be over")

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def opener(_request: object, timeout: int | None = None) -> Response:
        assert timeout is not None
        return Response()

    with pytest.raises(QuotaError, match="advertises 900000000 bytes"):
        stage_asset(
            project,
            class_name="3d",
            license_text="CC0",
            url="https://example.org/huge.glb",
            opener=opener,
            retrieved_at=FIXED_TIME,
        )


def test_project_budget_counts_the_whole_project(project: Project, asset_file: Path) -> None:
    project = tighter_budget(project, max_asset_bytes=1000, max_project_bytes=4000)
    (project.root / "docs" / "big.bin").write_bytes(b"x" * 5000)

    with pytest.raises(QuotaError, match=r"over budget\.max_project_bytes"):
        stage_asset(
            project,
            class_name="3d",
            license_text="CC0",
            local_file=asset_file,
            retrieved_at=FIXED_TIME,
        )


def test_missing_credit_line_is_recorded_and_flagged(project: Project, asset_file: Path) -> None:
    staged = stage_asset(
        project,
        class_name="video",
        license_text="CC BY 4.0",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )

    assert read_sidecar(staged.path)["Attribution"] == "not recorded"
    record = next(row for row in build_index(project).assets if row.path == staged.relative)
    assert record.attribution_required is True
    assert record.attribution_missing is True

    other = asset_file.with_name("second.bin")
    other.write_bytes(b"a-different-payload")
    free = stage_asset(
        project,
        class_name="audio",
        license_text="CC0",
        local_file=other,
        retrieved_at=FIXED_TIME,
    )
    assert read_sidecar(free.path)["Attribution"] == "not required"
    assert (
        next(
            row for row in build_index(project).assets if row.path == free.relative
        ).attribution_missing
        is False
    )


def test_restaging_with_a_licence_promotes_out_of_quarantine(
    project: Project, asset_file: Path
) -> None:
    quarantined = stage_asset(
        project,
        class_name="3d",
        license_text="unknown",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )
    promoted = stage_asset(
        project,
        class_name="3d",
        license_text="CC0",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )

    assert quarantined.quarantined is True
    assert promoted.relative == "assets/3d/tree.glb"
    assert promoted.duplicate_of is None
    assert not (project.root / "assets" / "unverified" / "tree.glb").exists()
    assert not (project.root / "assets" / "unverified" / "tree.glb.license").exists()
    index = build_index(project)
    assert index.totals.count == 1
    assert index.totals.quarantined == 0


def test_music_can_be_restricted_to_cc0(project: Project, asset_file: Path) -> None:
    project = require_cc0_music(project)

    with pytest.raises(LicensePolicyError, match=r"only CC0-1\.0 music may be staged"):
        stage_asset(
            project,
            class_name="music",
            license_text="CC BY 4.0",
            local_file=asset_file,
            retrieved_at=FIXED_TIME,
        )

    staged = stage_asset(
        project,
        class_name="music",
        license_text="CC0",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )
    assert staged.relative == "assets/music/tree.glb"


def test_a_refused_download_leaves_assets_untouched(project: Project) -> None:
    project = tighter_budget(project, max_asset_bytes=4, max_project_bytes=1000)

    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def read(self) -> bytes:
            return self.payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def opener(_request: object, timeout: int | None = None) -> Response:
        assert timeout is not None
        return Response(b"far-too-large-payload")

    with pytest.raises(QuotaError):
        stage_asset(
            project,
            class_name="3d",
            license_text="CC0",
            url="https://example.org/huge.glb",
            opener=opener,
            retrieved_at=FIXED_TIME,
        )

    leftovers = [path for path in (project.root / "assets").rglob("*") if path.is_file()]
    assert leftovers == []


def test_build_index_groups_duplicates_and_flags_orphans(
    project: Project, asset_file: Path
) -> None:
    stage_asset(
        project, class_name="3d", license_text="CC0", local_file=asset_file, retrieved_at=FIXED_TIME
    )
    orphan = project.root / "assets" / "design" / "logo.svg"
    orphan.write_text("<svg/>", encoding="utf-8")
    duplicate = project.root / "assets" / "3d" / "copy.glb"
    duplicate.write_bytes(asset_file.read_bytes())
    sidecar_path(duplicate).write_text(
        sidecar_path(project.root / "assets" / "3d" / "tree.glb").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    index = build_index(project)

    assert index.unlicensed == ("assets/design/logo.svg",)
    assert index.totals.count == 2
    assert len(index.duplicates) == 1
    assert index.quota.ok is True


def test_write_index_and_credits_lists_attribution(project: Project, asset_file: Path) -> None:
    stage_asset(
        project,
        class_name="3d",
        license_text="CC BY 4.0",
        attribution="Ada Lovelace",
        source_url="https://example.org/ada",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )
    stage_asset(
        project,
        class_name="video",
        license_text="CC0",
        local_file=asset_file,
        retrieved_at=FIXED_TIME,
    )

    index_path, credits_path, index = write_index_and_credits(project)

    assert json.loads(index_path.read_text(encoding="utf-8"))["totals"]["count"] == 1
    credits = credits_path.read_text(encoding="utf-8")
    assert "Ada Lovelace" in credits
    assert "CC-BY-4.0" in credits
    assert "https://example.org/ada" in credits
    assert "assets/video/tree.glb" not in credits
    assert index.totals.attribution_required == 1


def test_render_credits_names_quarantine_and_missing_sidecars(project: Project) -> None:
    quarantined = project.root / "assets" / "unverified" / "mystery.bin"
    quarantined.parent.mkdir(parents=True, exist_ok=True)
    quarantined.write_bytes(b"??")
    sidecar_path(quarantined).write_text(
        "SPDX-License-Identifier: NONE\nFile: assets/unverified/mystery.bin\n",
        encoding="utf-8",
    )
    orphan = project.root / "assets" / "design" / "logo.svg"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("<svg/>", encoding="utf-8")

    credits = render_credits(build_index(project))

    assert "Quarantined: licence unknown" in credits
    assert "assets/unverified/mystery.bin" in credits
    assert "Files without a sidecar" in credits


def require_cc0_music(project: Project) -> Project:
    """Turn the CC0-first music preference into a hard rule and return the reloaded project."""

    manifest = project.manifest_path
    body = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        body.replace("require_cc0_music: false", "require_cc0_music: true"), encoding="utf-8"
    )
    return load_project(project.root)


def tighter_budget(project: Project, *, max_asset_bytes: int, max_project_bytes: int) -> Project:
    """Rewrite the manifest with a smaller budget and return the reloaded project."""

    manifest = project.manifest_path
    body = manifest.read_text(encoding="utf-8")
    body = body.replace("max_asset_bytes: 209715200", f"max_asset_bytes: {max_asset_bytes}")
    body = body.replace("max_project_bytes: 21474836480", f"max_project_bytes: {max_project_bytes}")
    manifest.write_text(body, encoding="utf-8")
    return load_project(project.root)
