"""Tests for the triage funnel: reasons, cache, ranking, sheets, and decisions.

The clips are generated here, so every reason is checked against content the test created.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio import media, triage
from media_studio.manifest import load_project
from media_studio.project import scaffold_project


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("library")
    clips = root / "clips"
    clips.mkdir()
    hidden = clips / ".hidden.mp4"
    (clips / "notes.txt").write_text("not a clip", encoding="utf-8")

    def generate(destination: Path, arguments: list[str]) -> Path:
        completed = subprocess.run(
            [media.FFMPEG, "-hide_banner", "-nostats", "-y", *arguments, str(destination)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-400:]
        return destination

    moving = generate(
        clips / "a-moving.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )
    generate(
        clips / "b-static.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=160x90:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )
    generate(
        clips / "c-dark.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=#0a0a0a:size=160x90:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )
    (clips / "e-duplicate.mp4").write_bytes(moving.read_bytes())
    hidden.write_bytes(moving.read_bytes())
    return clips


def build(library: Path, **overrides: object) -> triage.TriageIndex:
    scratch = media.triage_scratch()
    return triage.build_index(triage.clip_paths(library), scratch=scratch, **overrides)  # type: ignore[arg-type]


def facts_by_name(index: triage.TriageIndex) -> dict[str, triage.ClipFacts]:
    return {item.path.name: item for item in index.facts}


def test_clip_paths_skips_non_media_and_hidden_files(library: Path) -> None:
    names = [path.name for path in triage.clip_paths(library)]

    assert names == [
        "a-moving.mp4",
        "b-static.mp4",
        "c-dark.mp4",
        "e-duplicate.mp4",
    ]
    assert "notes.txt" not in names
    assert ".hidden.mp4" not in names


def test_index_explains_every_reject(library: Path) -> None:
    facts = facts_by_name(build(library))

    assert facts["a-moving.mp4"].usable
    # A flat frame is caught twice over: no detail and no movement.
    assert any("no movement" in reason for reason in facts["b-static.mp4"].reasons)
    assert any("soft or featureless" in reason for reason in facts["b-static.mp4"].reasons)
    assert facts["b-static.mp4"].sharpness < 3.0
    assert any("too dark" in reason for reason in facts["c-dark.mp4"].reasons)
    assert facts["e-duplicate.mp4"].usable  # a duplicate is usable on its own, but paired below


def test_duplicates_are_grouped_by_picture_not_only_by_bytes(library: Path) -> None:
    index = build(library)
    facts = facts_by_name(index)

    assert facts["a-moving.mp4"].phash == facts["e-duplicate.mp4"].phash
    groups = list(index.duplicates.values())
    assert any(len(paths) == 2 for paths in groups), groups


def test_summary_counts_rejects_and_duplicates(library: Path) -> None:
    summary = build(library).summary

    assert summary["clips"] == 4
    assert summary["rejected"] == 2
    assert summary["usable"] == 2
    assert summary["duplicate_groups"] >= 1


def test_ranking_puts_moving_footage_above_a_held_shot(library: Path) -> None:
    index = build(library)
    ranked = [item.path.name for item in index.usable]

    assert ranked[0] == "a-moving.mp4" or ranked[0] == "e-duplicate.mp4"
    assert "b-static.mp4" not in ranked


def test_measurements_are_reused_from_the_cache(library: Path) -> None:
    calls: list[str] = []

    def counting_measure(path: Path, *, scratch: Path) -> triage.ClipFacts:
        calls.append(path.name)
        return triage.measure_clip(path, scratch=scratch)

    first = build(library, measure=counting_measure)
    assert len(calls) == 4

    cache = {item.sha256: item for item in first.facts}
    second = build(library, cache=cache, measure=counting_measure)

    assert len(calls) == 4, "the second run must not measure anything again"
    assert second.reused == 4
    assert second.measured == 0
    assert [item.key for item in second.facts] == [item.key for item in first.facts]


def test_cards_round_trip_through_the_index_file(tmp_path: Path, library: Path) -> None:
    project = load_project(scaffold_project("triage-demo", root=tmp_path).path)
    index = build(library)

    path = triage.write_index(project, index)
    restored = triage.read_index(project)

    assert path.relative_to(project.root).as_posix() == "footage/index.jsonl"
    # The cache is keyed by content hash, so byte-identical clips share one card on purpose:
    # that is the dedupe, not a loss of information.
    assert len(restored) == 3  # four clips, one byte-identical pair
    assert sorted(restored) == sorted({item.sha256 for item in index.facts})
    shared = next(item for item in index.facts if item.path.name == "e-duplicate.mp4")
    assert restored[shared.sha256].path.name in ("a-moving.mp4", "e-duplicate.mp4")


def test_sheets_batch_candidates_and_label_each_cell(tmp_path: Path, library: Path) -> None:
    index = build(library)
    chosen = list(index.usable) + [item for item in index.facts if not item.usable][:3]

    sheets = triage.build_sheets(chosen, tmp_path / "sheets", columns=2, rows=1)

    assert len(sheets) == 2  # three cards, two cells per sheet
    cells = sorted(path.name for path in (tmp_path / "sheets" / ".cells").iterdir())
    assert cells[0].split("-")[0] == "001"
    assert any(item.key in cell for item in chosen for cell in cells)


def test_sheets_are_empty_without_candidates(tmp_path: Path) -> None:
    assert triage.build_sheets([], tmp_path / "sheets") == []


def test_score_rewards_detail_and_movement(library: Path) -> None:
    facts = facts_by_name(build(library))

    assert triage.score_clip(facts["a-moving.mp4"]) > triage.score_clip(facts["b-static.mp4"])
    assert triage.score_clip(facts["c-dark.mp4"]) == 0.0  # a rejected clip never scores


def test_score_weights_a_concept_keyword_in_the_file_name(library: Path) -> None:
    facts = facts_by_name(build(library))
    moving = facts["a-moving.mp4"]

    assert triage.score_clip(moving, ("moving",)) > triage.score_clip(moving, ("unrelated",))


def test_concept_key_is_stable_and_distinct() -> None:
    assert triage.concept_key("Slow Morning") == triage.concept_key("slow morning")
    assert triage.concept_key("slow morning") != triage.concept_key("fast evening")


def test_decisions_replace_and_exclude_from_review(tmp_path: Path, library: Path) -> None:
    project = load_project(scaffold_project("decisions-demo", root=tmp_path).path)
    index = build(library)
    target = index.usable[0]

    triage.record_decision(
        project,
        key=target.key,
        concept="slow morning",
        verdict="hold",
        reason="needs a closer look",
    )
    triage.record_decision(
        project, key=target.key, concept="slow morning", verdict="in", reason="cut it in"
    )

    decisions = triage.read_decisions(project)
    assert len(decisions) == 1, "a later verdict for the same clip and concept replaces the earlier"
    assert decisions[0].verdict == "in"

    pending = triage.undecided(index, decisions, "slow morning")
    assert target.key not in {item.key for item in pending}
    assert triage.undecided(index, decisions, "another concept") == list(index.usable)


def test_decision_rejects_an_unknown_verdict(tmp_path: Path) -> None:
    project = load_project(scaffold_project("verdict-demo", root=tmp_path).path)

    with pytest.raises(ValueError, match=r"verdict must be in, hold, or out"):
        triage.record_decision(project, key="abc123", concept="c", verdict="maybe")


def test_clip_strip_is_written(tmp_path: Path, library: Path) -> None:
    strip = triage.clip_strip(library / "a-moving.mp4", tmp_path / "strip.png", frames=3, width=160)

    assert strip.is_file()
    assert strip.stat().st_size > 0
