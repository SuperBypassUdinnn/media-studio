"""End-to-end tests for `ms assets search` and `ms assets pull`, offline."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import media_studio.cli as cli_module
import media_studio.ledger as ledger_module
from media_studio.cli import main
from media_studio.ledger import read_sidecar
from media_studio.project import scaffold_project
from media_studio.sources.base import Candidate

PAYLOAD = b"downloaded-media-payload"


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return scaffold_project("search-demo", root=tmp_path).path


def outcome_candidate(**overrides: object) -> Candidate:
    base = {
        "source": "openverse",
        "identifier": "abc-123",
        "title": "Forest canopy at dawn",
        "kind": "image",
        "license_text": "CC0",
        "creator": "Jane Doe",
        "source_page": "https://example.org/photo/abc-123",
        "download_url": "https://cdn.example.org/abc-123.jpg",
        "width": 4000,
        "height": 3000,
    }
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def offline_stage(monkeypatch: pytest.MonkeyPatch, payload: bytes = PAYLOAD) -> list[str]:
    """Route `ms assets pull` through the real ledger, but never the network."""

    fetched: list[str] = []

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def opener(request: object, timeout: int | None = None) -> Response:
        fetched.append(request.full_url)  # type: ignore[attr-defined]
        assert timeout is not None
        return Response(payload)

    real_stage = ledger_module.stage_asset

    def stage(project: object, **kwargs: object) -> object:
        kwargs["opener"] = opener
        return real_stage(project, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli_module, "stage_asset", stage)
    return fetched


def write_search(tmp_path: Path, *candidates: Candidate, kind: str = "image") -> Path:
    path = tmp_path / "search.json"
    path.write_text(
        json.dumps(
            {
                "query": "forest",
                "kind": kind,
                "searched_at": "2026-10-24T00:00:00+00:00",
                "sources": [
                    {"source": "openverse", "ok": True, "count": len(candidates), "note": ""}
                ],
                "hidden_unlicensed": 0,
                "candidates": [item.to_dict() for item in candidates],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_search_prints_candidates_and_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    meant: list[str] = []

    def fake_search(
        query: str,
        kind: str,
        *,
        limit: int = 8,
        sources: tuple[str, ...] = (),
        fetch: Callable[[str], bytes] | None = None,
        include_unlicensed: bool = False,
    ) -> object:
        from media_studio.search import SearchOutcome, SourceReport

        meant.append(f"{query}|{kind}|{limit}|{','.join(sources)}|{include_unlicensed}")
        return SearchOutcome(
            query=query,
            kind=kind,
            searched_at="2026-10-24T00:00:00+00:00",
            candidates=(outcome_candidate(),),
            reports=(SourceReport("openverse", ok=True, count=1),),
        )

    monkeypatch.setattr(cli_module, "run_search", fake_search)
    saved = tmp_path / "saved.json"

    exit_code = main(
        ["assets", "search", "forest", "--class", "image", "--limit", "3", "--save", str(saved)]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert meant == ["forest|image|3||False"]
    assert "CC0-1.0" in output
    assert "Forest canopy at dawn" in output
    assert saved.is_file()
    assert "next: ms assets pull" in output


def test_search_json_mode_keeps_stdout_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from media_studio.search import SearchOutcome, SourceReport

    monkeypatch.setattr(
        cli_module,
        "run_search",
        lambda *args, **kwargs: SearchOutcome(
            query="forest",
            kind="image",
            searched_at="2026-10-24T00:00:00+00:00",
            candidates=(outcome_candidate(),),
            reports=(SourceReport("openverse", ok=True, count=1),),
        ),
    )

    exit_code = main(["assets", "search", "forest", "--class", "image", "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["candidates"][0]["license"] == "CC0"
    assert "saved:" in captured.err


def test_search_fails_when_a_source_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "assets",
            "search",
            "forest",
            "--class",
            "video",
            "--source",
            "openverse",
            "--save",
            str(tmp_path / "x.json"),
        ]
    )

    assert exit_code == 1
    assert "no source answers for 'video'" in capsys.readouterr().err


def test_pull_stages_the_chosen_candidate(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    search = write_search(
        tmp_path,
        outcome_candidate(),
        outcome_candidate(
            identifier="def-456", title="Second option", source_page="https://example.org/2"
        ),
    )
    fetched = offline_stage(monkeypatch)

    exit_code = main(["assets", "pull", str(project_root), "--search", str(search), "--index", "1"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert fetched == ["https://cdn.example.org/abc-123.jpg"]
    assert "staged assets/design/abc-123.jpg" in output
    sidecar = read_sidecar(project_root / "assets" / "design" / "abc-123.jpg")
    assert sidecar["SPDX-License-Identifier"] == "CC0-1.0"
    assert sidecar["Attribution"] == "Jane Doe"
    assert sidecar["Source-URL"] == "https://example.org/photo/abc-123"
    assert sidecar["Provider"] == "openverse"
    assert (project_root / "assets" / "design" / "abc-123.jpg").read_bytes() == PAYLOAD


def test_pull_derives_the_staging_class_from_the_search_class(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = write_search(tmp_path, outcome_candidate(kind="music"), kind="music")
    offline_stage(monkeypatch)

    exit_code = main(["assets", "pull", str(project_root), "--search", str(search), "--index", "1"])

    assert exit_code == 0
    assert (project_root / "assets" / "music" / "abc-123.jpg").is_file()


def test_pull_index_is_one_based_and_bounded(
    project_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    search = write_search(tmp_path, outcome_candidate())

    for index in ("0", "2"):
        exit_code = main(
            ["assets", "pull", str(project_root), "--search", str(search), "--index", index]
        )
        assert exit_code == 1
        assert "between 1 and 1" in capsys.readouterr().err


def test_pull_dry_run_does_not_download(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    search = write_search(tmp_path, outcome_candidate())
    fetched = offline_stage(monkeypatch)

    exit_code = main(
        ["assets", "pull", str(project_root), "--search", str(search), "--index", "1", "--dry-run"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert fetched == []
    assert "would stage: Forest canopy at dawn" in output
    staged = [path for path in (project_root / "assets").rglob("*") if path.is_file()]
    assert staged == []


def test_pull_resolves_a_page_only_candidate(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from media_studio.sources import internet_archive

    monkeypatch.setattr(
        internet_archive,
        "resolve",
        lambda candidate, fetch: (
            "https://archive.org/download/forest-sounds/forest-sounds.mp3",
            "forest-sounds.mp3",
        ),
    )
    search = write_search(
        tmp_path,
        outcome_candidate(
            source="internet_archive",
            identifier="forest-sounds",
            kind="audio",
            license_text="public domain",
            download_url=None,
        ),
        kind="audio",
    )
    offline_stage(monkeypatch)

    exit_code = main(["assets", "pull", str(project_root), "--search", str(search), "--index", "1"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "resolved from internet_archive" in output
    assert (project_root / "assets" / "audio" / "forest-sounds.mp3").is_file()


def test_pull_explains_a_candidate_without_a_link(
    project_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    search = write_search(
        tmp_path,
        outcome_candidate(source="openverse", download_url=None),
    )

    exit_code = main(["assets", "pull", str(project_root), "--search", str(search), "--index", "1"])

    assert exit_code == 1
    assert "have no direct link" in capsys.readouterr().err


def test_pull_refuses_a_noncommercial_licence(
    project_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    search = write_search(tmp_path, outcome_candidate(license_text="CC BY-NC 4.0"))
    fetched = offline_stage(monkeypatch)

    exit_code = main(["assets", "pull", str(project_root), "--search", str(search), "--index", "1"])

    assert exit_code == 1
    assert "forbids commercial use" in capsys.readouterr().err
    assert fetched == []
