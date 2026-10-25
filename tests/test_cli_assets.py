"""End-to-end tests for the assets and project validate commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_studio.cli import main
from media_studio.project import scaffold_project


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return scaffold_project("cli-assets", root=tmp_path).path


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "downloads" / "rock.hdr"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"hdr-payload")
    return path


def test_stage_then_index_reports_licence_and_credit(
    project_root: Path, source_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "assets",
            "stage",
            str(project_root),
            "--class",
            "3d",
            "--file",
            str(source_file),
            "--license",
            "CC BY 4.0",
            "--attribution",
            "Example Author",
            "--source-url",
            "https://example.org/rock",
            "--provider",
            "Poly Haven",
        ]
    )
    stage_output = capsys.readouterr().out

    assert exit_code == 0
    assert "staged assets/3d/rock.hdr" in stage_output
    assert "CC-BY-4.0" in stage_output

    exit_code = main(["assets", "index", str(project_root)])
    index_output = capsys.readouterr().out

    assert exit_code == 0
    assert "1 staged" in index_output
    index = json.loads((project_root / "assets" / "index.json").read_text(encoding="utf-8"))
    assert index["assets"][0]["provider"] == "Poly Haven"
    assert index["totals"]["attribution_required"] == 1
    credits = (project_root / "assets" / "CREDITS.md").read_text(encoding="utf-8")
    assert "Example Author" in credits


def test_stage_prints_a_paste_ready_manifest_line(
    project_root: Path, source_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "assets",
            "stage",
            str(project_root),
            "--class",
            "3d",
            "--file",
            str(source_file),
            "--license",
            "CC0",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "declare:  rock: assets/3d/rock.hdr" in output
    assert "under `assets:` in project.yaml" in output


def test_stage_duplicate_reports_the_original(
    project_root: Path, source_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = [
        "assets",
        "stage",
        str(project_root),
        "--class",
        "3d",
        "--file",
        str(source_file),
        "--license",
        "CC0",
    ]
    main(arguments)
    capsys.readouterr()
    main(arguments)
    output = capsys.readouterr().out

    assert "already staged: assets/3d/rock.hdr" in output


def test_stage_warns_when_credit_is_missing(
    project_root: Path, source_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "assets",
            "stage",
            str(project_root),
            "--class",
            "3d",
            "--file",
            str(source_file),
            "--license",
            "CC BY 4.0",
        ]
    )

    assert exit_code == 0
    assert "requires attribution and no credit line was recorded" in capsys.readouterr().err


def test_stage_quarantines_unknown_licence(
    project_root: Path, source_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "assets",
            "stage",
            str(project_root),
            "--class",
            "3d",
            "--file",
            str(source_file),
            "--license",
            "unclear",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "assets/unverified/rock.hdr" in captured.out
    assert "quarantined" in captured.err


def test_stage_refuses_noncommercial_licence(
    project_root: Path, source_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "assets",
            "stage",
            str(project_root),
            "--class",
            "3d",
            "--file",
            str(source_file),
            "--license",
            "CC BY-NC 4.0",
        ]
    )

    assert exit_code == 1
    assert "forbids commercial use" in capsys.readouterr().err
    assert not (project_root / "assets" / "3d" / "rock.hdr").exists()


def test_list_is_empty_for_a_fresh_project(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["assets", "list", str(project_root)])

    assert exit_code == 0
    assert "no staged assets" in capsys.readouterr().out


def test_list_shows_staged_assets(
    project_root: Path, source_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        [
            "assets",
            "stage",
            str(project_root),
            "--class",
            "3d",
            "--file",
            str(source_file),
            "--license",
            "CC0",
        ]
    )
    capsys.readouterr()

    exit_code = main(["assets", "list", str(project_root)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "assets/3d/rock.hdr" in output
    assert "CC0-1.0" in output


def test_validate_accepts_the_template(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["project", "validate", str(project_root)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "0 error(s), 0 warning(s)" in output


def test_validate_rejects_a_broken_deliverable(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project_root / "project.yaml").write_text(
        "slug: cli-assets\ndeliverables:\n  - {id: one, kind: holo}\n", encoding="utf-8"
    )

    exit_code = main(["project", "validate", str(project_root)])

    assert exit_code == 1
    assert "kind 'holo'" in capsys.readouterr().out


def test_missing_project_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["assets", "list", str(tmp_path / "nowhere")])

    assert exit_code == 1
    assert "no project.yaml" in capsys.readouterr().err
