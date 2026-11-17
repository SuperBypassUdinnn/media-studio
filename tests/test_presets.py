"""Tests for the preset registry: integrity, search, parameters, and application."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from media_studio import design, media
from media_studio.cli import main
from media_studio.ledger import stage_asset
from media_studio.manifest import Project, load_project
from media_studio.mlt import Clip, Timeline, Title
from media_studio.presets import (
    KINDS,
    PresetError,
    Recipe,
    load_recipes,
    resolve_effects,
    search,
    validate_effects,
    validate_recipe,
)
from media_studio.project import scaffold_project
from media_studio.video import build_timeline

_HAVE_MELT = shutil.which("melt") is not None
_HAVE_MELT_AND_MAGICK = _HAVE_MELT and shutil.which("magick") is not None
NEEDS_MELT = pytest.mark.skipif(not _HAVE_MELT, reason="melt not installed")
NEEDS_MELT_AND_MAGICK = pytest.mark.skipif(
    not _HAVE_MELT_AND_MAGICK, reason="melt and magick required for snapshot comparison"
)


@pytest.fixture(scope="module")
def recipes() -> dict:
    return load_recipes()


def test_every_recipe_is_well_formed(recipes: dict) -> None:
    problems = [problem for recipe in recipes.values() for problem in validate_recipe(recipe)]

    assert problems == [], problems


def test_the_registry_covers_every_kind(recipes: dict) -> None:
    kinds = {recipe.kind for recipe in recipes.values()}

    assert kinds == set(KINDS)
    assert len(recipes) >= 7


def test_recipe_ids_match_their_file_names(recipes: dict) -> None:
    for recipe in recipes.values():
        assert recipe.path is not None
        assert recipe.path.stem == recipe.id
        assert recipe.license and recipe.provenance


def test_search_finds_by_intent(recipes: dict) -> None:
    assert search(recipes, "transition dissolve")[0].id == "video-cross-dissolve"
    assert search(recipes, "ducking voiceover")[0].id == "audio-music-bed"
    assert search(recipes, "nothing-matches-this") == []
    assert len(search(recipes, "")) == len(recipes)


def test_search_is_local_and_cheap(recipes: dict) -> None:
    """A search returns recipes, not prose: the caller decides what to print."""

    matches = search(recipes, "blur")

    assert matches
    assert all(hasattr(recipe, "summary") for recipe in matches)


def test_resolve_applies_defaults_and_coerces_overrides(recipes: dict) -> None:
    brighten = recipes["video-brighten"]

    assert brighten.resolve()["amount"] == 1.15
    assert brighten.resolve({"amount": 1.4})["amount"] == 1.4
    assert isinstance(recipes["title-lower-third"].resolve({"size": "72"})["size"], int)


def test_resolve_rejects_unknown_and_out_of_range_values(recipes: dict) -> None:
    brighten = recipes["video-brighten"]

    with pytest.raises(PresetError, match="has no parameter 'brigthness'"):
        brighten.resolve({"brigthness": 2})
    with pytest.raises(PresetError, match="must be at most"):
        brighten.resolve({"amount": 99})
    with pytest.raises(PresetError, match="must be at least"):
        brighten.resolve({"amount": 0.1})


def test_render_substitutes_parameters_into_properties(recipes: dict) -> None:
    brighten = recipes["video-brighten"]

    assert brighten.render(brighten.resolve({"amount": 1.5}))["level"] == "1.5"
    assert brighten.implementation["mlt_filter"] == "brightness"


def test_audio_and_title_recipes_resolve_their_settings(recipes: dict) -> None:
    bed = recipes["audio-music-bed"]
    settings = bed.audio_settings(bed.resolve({"gain_db": -9, "depth": 0.25, "fade_in": 2.0}))

    assert settings["gain_db"] == -9.0
    assert settings["duck_level"] == 0.25
    assert settings["fade_in"] == 2.0

    title = recipes["title-lower-third"]
    resolved = title.title_settings(title.resolve({"size": 64, "position": "+10+900"}))

    assert resolved["size"] == "64"
    assert resolved["position"] == "+10+900"

    caption = recipes["title-caption-top"]
    defaults = caption.title_settings(caption.resolve())

    def y_offset(position: str) -> int:
        return int(position.rsplit("+", 1)[1])

    # The top caption sits above the middle of a 1080p frame; the lower third sits below it.
    assert defaults["position"] == "+120+80"
    assert y_offset(defaults["position"]) < y_offset(resolved["position"])


def test_resolve_effects_keeps_placement_keys_out_of_parameters(recipes: dict) -> None:
    resolved = resolve_effects(
        [{"id": "title-lower-third", "text": "Hello", "start": 1.0, "duration": 2.0}], recipes
    )

    assert len(resolved) == 1
    recipe, values = resolved[0]
    assert recipe.id == "title-lower-third"
    assert "text" not in values


def test_resolve_effects_explains_an_unknown_preset(recipes: dict) -> None:
    with pytest.raises(PresetError, match="no preset 'nope'"):
        resolve_effects([{"id": "nope"}], recipes)


def test_validate_effects_reports_problems(recipes: dict) -> None:
    problems = validate_effects(
        (
            {"id": "not-a-preset"},
            {"id": "video-brighten", "at": "shot-1", "amount": 99},
            {"id": "title-lower-third", "at": "shot-1"},
        )
    )

    joined = "\n".join(problems)
    assert "no preset 'not-a-preset'" in joined
    assert "must be at most" in joined
    assert "a title effect needs text" in joined


def test_validate_effects_is_silent_for_a_good_list() -> None:
    assert validate_effects(({"id": "video-brighten", "at": "1", "amount": 1.2},)) == []
    assert validate_effects(()) == []


def test_cli_lists_searches_and_shows(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["presets", "list"]) == 0
    listing = capsys.readouterr().out
    assert "video-cross-dissolve" in listing
    # Derive the count instead of pinning it, so adding a recipe never breaks this test.
    assert f"{len(load_recipes())} preset(s)" in listing

    assert main(["presets", "search", "blur"]) == 0
    assert "video-soften" in capsys.readouterr().out

    assert main(["presets", "show", "audio-music-bed"]) == 0
    shown = capsys.readouterr().out
    assert "duck_level" in shown or "depth" in shown
    assert "effects:" in shown

    assert main(["presets", "show", "nope"]) == 1
    assert "no preset 'nope'" in capsys.readouterr().err


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("preset-clip")
    destination = work / "gray.mp4"
    completed = subprocess.run(
        [
            media.FFMPEG,
            "-hide_banner",
            "-nostats",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=160x90:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-300:]
    return destination


@pytest.fixture(scope="module")
def patterned_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An asymmetric moving pattern, so a crop, a mirror, or a tint has to change pixels."""

    work = tmp_path_factory.mktemp("preset-pattern")
    destination = work / "pattern.mp4"
    completed = subprocess.run(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-300:]
    return destination


def render_pair(
    tmp_path: Path, clip: Path, service: str, properties: dict, prefix: str = "pair"
) -> tuple[Path, Path]:
    """Render the same clip with and without one filter, and return both videos."""

    def timeline(filters: tuple) -> Timeline:
        return Timeline(
            width=160,
            height=90,
            fps=24.0,
            clips=(Clip(resource=clip, duration=1.0, filters=filters),),
        )

    rendered: list[Path] = []
    for name, filters in (("plain", ()), ("filtered", ((service, properties),))):
        path = tmp_path / f"{prefix}-{name}.mlt"
        path.write_text(timeline(filters).to_xml(tmp_path), encoding="utf-8")
        rendered.append(media.render_timeline(path, tmp_path / f"{prefix}-{name}.mkv"))
    return rendered[0], rendered[1]


@NEEDS_MELT
@pytest.mark.parametrize(
    ("recipe_id", "overrides"),
    [
        ("video-warm-grade", {}),
        ("video-crop-vertical", {"left": 40, "right": 40}),
        ("video-sepia", {}),
        ("video-glitch-wave", {"amplitude": 12}),
    ],
)
def test_a_look_recipe_reaches_the_frame(
    tmp_path: Path, patterned_clip: Path, recipes: dict, recipe_id: str, overrides: dict
) -> None:
    recipe = recipes[recipe_id]
    service = recipe.implementation["mlt_filter"]
    properties = recipe.render(recipe.resolve(overrides))

    plain_frame, filtered_frame = render_pair(
        tmp_path, patterned_clip, service, properties, prefix=recipe_id
    )

    assert media.pixel_digest(filtered_frame) != media.pixel_digest(plain_frame)


@NEEDS_MELT
def test_a_video_effect_preset_changes_the_picture(
    tmp_path: Path, clip: Path, recipes: dict
) -> None:
    """The recipe must reach the frame, so this renders both ways and measures."""

    brighten = recipes["video-brighten"]
    properties = brighten.render(brighten.resolve({"amount": 1.5}))

    plain_frame, filtered_frame = render_pair(tmp_path, clip, "brightness", properties, "brighten")

    plain_luma = media.mean_luminance_at(plain_frame, 0.5)
    filtered_luma = media.mean_luminance_at(filtered_frame, 0.5)

    # `brightness` multiplies the level, so 1.5 brightens and 0.6 darkens.
    assert filtered_luma > plain_luma + 20.0, {"plain": plain_luma, "filtered": filtered_luma}


def _project_with_effects(tmp_path: Path, effects: str) -> Path:
    root = scaffold_project("preset-demo", root=tmp_path).path
    project = load_project(root)
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    for name in ("shot-1.mp4", "shot-2.mp4"):
        subprocess.run(
            [
                media.FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=24:duration=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(sources / name),
            ],
            capture_output=True,
            check=True,
        )
    staged = {}
    for name, asset_id in (("shot-1.mp4", "shot-1"), ("shot-2.mp4", "shot-2")):
        staged[asset_id] = stage_asset(
            project,
            class_name="video",
            license_text="CC0",
            local_file=sources / name,
            provider="fixture",
            retrieved_at="2026-10-25T00:00:00+00:00",
        ).relative

    manifest = f"""slug: preset-demo
title: Preset Demo
deliverables:
  - {{id: master, kind: video, resolution: 160x90, fps: 24, duration: 4.0, loudness_lufs: -14.0}}
assets:
  shot-1: {staged["shot-1"]}
  shot-2: {staged["shot-2"]}
video:
  shots:
    - {{ asset: shot-1 }}
    - {{ asset: shot-2 }}
{effects}"""
    (root / "project.yaml").write_text(manifest, encoding="utf-8")
    return root


def _project_from_recipe(root: Path, recipe: Recipe) -> Project:
    """Write a minimal project.yaml for one design recipe and load it.

    The test project needs no staged assets: design templates render without a logo or fonts
    (compose falls back to sans-serif and drops the logo image).
    """

    root.mkdir(parents=True, exist_ok=True)
    template = recipe.implementation["template"]
    size = recipe.implementation["size"]
    values = {parameter.name: parameter.default for parameter in recipe.parameters}
    headline = str(values.get("headline", ""))
    subhead = str(values.get("subhead", ""))
    manifest = (
        f"slug: {root.name}\n"
        f"title: {root.name}\n"
        f"design:\n"
        f"  variants:\n"
        f"    - {{id: post, template: {template}, size: {size}, "
        f'headline: "{headline}", subhead: "{subhead}"}}\n'
    )
    (root / "project.yaml").write_text(manifest, encoding="utf-8")
    return load_project(root)


def test_every_design_recipe_renders_through_the_design_path(tmp_path: Path) -> None:
    recipes = [r for r in load_recipes().values() if r.kind == "design"]
    assert {r.id for r in recipes} >= {
        "design-social-post",
        "design-story",
        "design-poster-a3",
        "design-video-thumbnail",
    }
    for recipe in recipes:
        assert validate_recipe(recipe) == [], validate_recipe(recipe)
        project = _project_from_recipe(tmp_path / recipe.id, recipe)
        report = design.render(project)
        assert report.ok, report.problems
        assert (project.root / "out/images/post.png").is_file()


def test_a_video_effect_reaches_the_planned_clip(tmp_path: Path) -> None:
    root = _project_with_effects(
        tmp_path,
        "effects:\n  - { id: video-brighten, at: shot-2, amount: 1.4 }\n",
    )
    project = load_project(root)

    timeline, _evidence = build_timeline(project, project.deliverables[0])

    assert timeline.clips[0].filters == ()
    services = [service for service, _properties in timeline.clips[1].filters]
    assert services == ["brightness"]
    assert timeline.clips[1].filters[0][1]["level"] == "1.4"


def test_a_transition_effect_reaches_the_following_clip(tmp_path: Path) -> None:
    root = _project_with_effects(
        tmp_path,
        "effects:\n  - { id: video-cross-dissolve, at: shot-1, duration: 0.7 }\n",
    )
    project = load_project(root)

    timeline, _evidence = build_timeline(project, project.deliverables[0])

    assert timeline.clips[1].transition == "crossfade"
    assert timeline.clips[1].crossfade == 0.7
    assert timeline.duration < 4.0  # the overlap shortens the edit


def test_an_unknown_effect_is_refused_by_validate(tmp_path: Path) -> None:
    root = _project_with_effects(tmp_path, "effects:\n  - { id: video-nope }\n")
    project = load_project(root)

    assert validate_effects(project.effects)
    with pytest.raises(Exception, match="no preset 'video-nope'"):
        build_timeline(project, project.deliverables[0])


# Snapshot coverage --------------------------------------------------------------------------

# Every recipe of a rendered kind is snapshotted, so adding one adds its coverage automatically.
SNAPSHOT_DIRECTORY = Path(__file__).parent / "snapshots" / "presets"
SNAPSHOT_VIDEO_EFFECTS = sorted(
    recipe.id for recipe in load_recipes().values() if recipe.kind == "video-effect"
)
SNAPSHOT_TITLES = sorted(recipe.id for recipe in load_recipes().values() if recipe.kind == "title")
# Mean absolute difference per channel on the 0-255 scale, measured on this machine. A video effect
# renders identically between runs (0.00), so 1.5 is pure headroom. A title goes through
# kdenlivetitle and qtblend, whose rasterisation drifts slightly across runs/contexts, so its
# tolerance is looser while the guard below still fails a title that vanished.
SNAPSHOT_TOLERANCE = 1.5
SNAPSHOT_TITLE_TOLERANCE = 4.0
# A snapshot has to differ from the unfiltered frame, or the recipe proves nothing. Measured against
# the fixture below: at least 1.0 for every video effect once the defaults fit the frame, and well
# above 5.0 for a title whose geometry fits inside the fixture canvas.
SNAPSHOT_MINIMUM_CHANGE = 1.0
SNAPSHOT_TITLE_MINIMUM_CHANGE = 5.0
# Some defaults describe a 1920x1080 frame, which the small 160x90 fixture cannot honour, so those
# recipes are snapshotted with overrides that actually land visibly on the fixture frame.
SNAPSHOT_OVERRIDES: dict[str, dict] = {
    "video-crop-vertical": {"left": 40, "right": 40},
    "title-caption-top": {"position": "+8+8", "size": 24},
    "title-lower-third": {"position": "+8+52", "size": 24},
}


def frame_png(video: Path, destination: Path, at: float = 0.5) -> Path:
    """One decoded frame as PNG, so two renders can be compared pixel by pixel."""

    completed = subprocess.run(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(at),
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-300:]
    return destination


def mean_absolute_difference(left: Path, right: Path) -> float:
    """How far apart two images are, averaged over every channel, on the 0-255 scale."""

    completed = subprocess.run(
        [
            "magick",
            str(left),
            str(right),
            "-compose",
            "difference",
            "-composite",
            "-format",
            "%[fx:mean*255]",
            "info:",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(completed.stdout.strip())


def compare_with_snapshot(
    rendered: Path, recipe_id: str, tolerance: float = SNAPSHOT_TOLERANCE
) -> None:
    """Compare against the recorded frame, or record it when MS_PRESET_SNAPSHOTS=regen.

    The baselines encode this machine's `melt`, encoder, and font versions. Regenerating them
    therefore rewrites committed files with whatever is installed here, which is meant to be a
    deliberate, reviewable change rather than a step to make a failing test pass.
    """

    baseline = SNAPSHOT_DIRECTORY / f"{recipe_id}.png"
    if os.environ.get("MS_PRESET_SNAPSHOTS") == "regen":
        baseline.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rendered, baseline)
        return
    assert baseline.is_file(), f"no snapshot at {baseline}; run with MS_PRESET_SNAPSHOTS=regen"
    difference = mean_absolute_difference(rendered, baseline)
    assert difference <= tolerance, (
        f"{recipe_id} differs from its snapshot by {difference:.2f} of {tolerance}"
    )


@NEEDS_MELT_AND_MAGICK
@pytest.mark.parametrize("recipe_id", SNAPSHOT_VIDEO_EFFECTS)
def test_every_video_effect_matches_its_snapshot(
    tmp_path: Path, patterned_clip: Path, recipes: dict, recipe_id: str
) -> None:
    recipe = recipes[recipe_id]
    properties = recipe.render(recipe.resolve(SNAPSHOT_OVERRIDES.get(recipe_id)))

    plain, filtered = render_pair(
        tmp_path, patterned_clip, recipe.implementation["mlt_filter"], properties, recipe_id
    )

    plain_frame = frame_png(plain, tmp_path / f"{recipe_id}-plain.png")
    filtered_frame = frame_png(filtered, tmp_path / f"{recipe_id}.png")

    # A snapshot that matches the unfiltered frame would pass while the recipe did nothing.
    assert mean_absolute_difference(filtered_frame, plain_frame) >= SNAPSHOT_MINIMUM_CHANGE, (
        f"{recipe_id} left the fixture frame unchanged"
    )
    compare_with_snapshot(filtered_frame, recipe_id)


@NEEDS_MELT_AND_MAGICK
@pytest.mark.parametrize("recipe_id", SNAPSHOT_TITLES)
def test_every_title_recipe_matches_its_snapshot(
    tmp_path: Path, patterned_clip: Path, recipes: dict, recipe_id: str
) -> None:
    recipe = recipes[recipe_id]
    settings = recipe.title_settings(recipe.resolve(SNAPSHOT_OVERRIDES.get(recipe_id)))
    title = Title(
        text="Snapshot",
        start=0.2,
        duration=0.8,
        size=int(settings["size"]),
        colour=settings["colour"],
        geometry=settings["position"],
    )

    def timeline(titles: tuple) -> Timeline:
        return Timeline(
            width=160,
            height=90,
            fps=24.0,
            clips=(Clip(resource=patterned_clip, duration=1.0),),
            titles=titles,
        )

    rendered: list[Path] = []
    for name, titles in (("plain", ()), ("titled", (title,))):
        path = tmp_path / f"{recipe_id}-{name}.mlt"
        path.write_text(timeline(titles).to_xml(tmp_path), encoding="utf-8")
        video = media.render_timeline(path, tmp_path / f"{recipe_id}-{name}.mkv")
        rendered.append(frame_png(video, tmp_path / f"{recipe_id}-{name}.png"))

    plain_frame, titled_frame = rendered
    # The recipe's default geometry describes a 1080p frame; on this fixture the text still has to
    # land somewhere, and this is the assertion that proves it did.
    assert mean_absolute_difference(titled_frame, plain_frame) >= SNAPSHOT_TITLE_MINIMUM_CHANGE, (
        f"{recipe_id} drew no visible title"
    )
    if not os.environ.get("CI"):
        compare_with_snapshot(titled_frame, recipe_id, SNAPSHOT_TITLE_TOLERANCE)
