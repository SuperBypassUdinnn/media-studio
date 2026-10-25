"""Tests for the asset search fan-out and the keyless source clients.

Everything here runs offline: each client receives an injected fetcher that serves recorded
fixture responses, so parsing is pinned without touching a provider.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from media_studio.ledger import classify_license
from media_studio.search import (
    SearchError,
    dedupe_candidates,
    default_save_path,
    load_outcome,
    rank_candidates,
    run_search,
    save_outcome,
)
from media_studio.sources import (
    client_names,
    clients_for,
    freesound,
    internet_archive,
    jamendo,
    met,
    module_named,
    openverse,
    pixabay,
    wikimedia,
)
from media_studio.sources.base import (
    Candidate,
    SourceError,
    as_float,
    as_int,
    build_url,
    license_from_family,
    license_from_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sources"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def router(*fail_hosts: str) -> Callable[[str], bytes]:
    """A fetcher that serves fixtures and can simulate a dead provider."""

    def fetch(url: str) -> bytes:
        for host in fail_hosts:
            if host in url:
                raise SourceError(f"{url}: simulated outage")
        if "api.openverse.org" in url:
            return fixture("openverse_images.json")
        if "commons.wikimedia.org" in url:
            return fixture("wikimedia_imageinfo.json")
        if "advancedsearch.php" in url:
            return fixture("ia_search.json")
        if "archive.org/metadata" in url:
            return fixture("ia_metadata.json")
        if "collectionapi.metmuseum.org" in url and "/objects/" in url:
            object_id = url.rsplit("/", 1)[-1]
            return fixture(f"met_object_{object_id}.json")
        if "collectionapi.metmuseum.org" in url:
            return fixture("met_search.json")
        if "pixabay.com/api/videos" in url:
            return fixture("pixabay_videos.json")
        if "pixabay.com/api/" in url:
            return fixture("pixabay_images.json")
        if "freesound.org/apiv2" in url:
            return fixture("freesound.json")
        if "api.jamendo.com" in url:
            return fixture("jamendo.json")
        raise SourceError(f"unexpected url: {url}")

    return fetch


def candidate(**overrides: object) -> Candidate:
    base = {
        "source": "test",
        "identifier": "one",
        "title": "Forest canopy",
        "kind": "image",
        "license_text": "CC0",
        "download_url": "https://cdn.example.org/one.jpg",
    }
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


def test_build_url_repeats_list_parameters_and_drops_empty_ones() -> None:
    url = build_url(
        "https://example.org/s",
        {"q": "forest", "fl[]": ["identifier", "title"], "rows": 2, "empty": "", "none": None},
    )

    assert url.startswith("https://example.org/s?")
    assert "fl%5B%5D=identifier" in url
    assert "fl%5B%5D=title" in url
    assert "rows=2" in url
    assert "empty" not in url
    assert "none" not in url


def test_numeric_helpers_ignore_booleans_and_junk() -> None:
    assert as_int("12") == 12
    assert as_int(12.9) == 12
    assert as_int(True) is None
    assert as_int(None) is None
    assert as_int("tall") is None
    assert as_float("1.5") == 1.5
    assert as_float(True) is None
    assert as_float("") is None


def test_licence_helpers_map_provider_wording() -> None:
    assert license_from_family("cc0") == "CC0"
    assert license_from_family("by", "4.0") == "CC BY 4.0"
    assert license_from_family("by-sa", "3.0") == "CC BY-SA 3.0"
    assert license_from_family("by-nc", "4.0") == "CC BY-NC 4.0"
    assert license_from_family("all-rights-reserved") == ""
    assert license_from_url("https://creativecommons.org/publicdomain/zero/1.0/") == "CC0"
    assert license_from_url("https://example.org/nothing") == ""


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never read the real secrets file, and give keyed clients a value to work with."""

    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "config"))
    for name in (
        "PIXABAY_API_KEY",
        "FREESOUND_API_KEY",
        "JAMENDO_CLIENT_ID",
        "SKETCHFAB_API_TOKEN",
    ):
        monkeypatch.setenv(name, "test-key-0123456789")


def test_registry_names_and_kind_support() -> None:
    assert client_names() == (
        "met",
        "wikimedia",
        "internet_archive",
        "pixabay",
        "freesound",
        "jamendo",
        "openverse",
    )
    assert [module.NAME for module in clients_for("image")] == [
        "met",
        "wikimedia",
        "internet_archive",
        "pixabay",
        "openverse",
    ]
    assert [module.NAME for module in clients_for("video")] == [
        "wikimedia",
        "internet_archive",
        "pixabay",
    ]
    assert [module.NAME for module in clients_for("music")] == [
        "internet_archive",
        "jamendo",
        "openverse",
    ]
    assert module_named("OpenVerse") is openverse
    assert module_named("nowhere") is None


def test_pixabay_maps_images_and_videos() -> None:
    images = pixabay.search("forest", "image", 5, router())

    assert [item.license_text for item in images] == [
        "Pixabay Content Licence",
        "Pixabay Content Licence",
    ]
    assert images[0].size_label == "1920x1280"
    assert images[0].creator == "Alice"
    assert images[0].licensed is True
    info = classify_license(images[0].license_text)
    assert info is not None and info.spdx == "LicenseRef-Pixabay"
    assert info.attribution_required is False

    videos = pixabay.search("rain", "video", 5, router())

    assert videos[0].kind == "video"
    assert videos[0].download_url is not None
    assert videos[0].download_url.endswith("rain_window_1920x1080.mp4")
    assert videos[0].duration == 12.0
    assert videos[0].size_label == "12s"


def test_freesound_maps_licence_and_offers_the_preview() -> None:
    found = freesound.search("rain", "audio", 5, router())

    assert found[0].license_text == "CC0"
    assert found[0].download_url is not None
    assert found[0].download_url.endswith("555001_high.mp3")
    assert found[0].duration == 30.5
    assert found[0].creator == "cleo"
    assert "preview" in found[0].extra["quality"]
    # The second sound has only a low-quality preview; it is still better than nothing.
    assert found[1].license_text == "CC BY 4.0"
    assert found[1].download_url is not None
    assert found[1].download_url.endswith("555002_low.mp3")


def test_jamendo_maps_cc_licences() -> None:
    found = jamendo.search("rain", "music", 5, router())

    assert [item.license_text for item in found] == ["CC BY 4.0", "CC BY-NC-ND 4.0"]
    assert found[0].creator == "The Band"
    assert found[0].download_url is not None
    assert found[0].download_url.startswith("https://prod-1.storage.jamendo.com/")
    assert found[0].extra["album"] == "Weather"


def test_jamendo_surfaces_the_providers_own_refusal_reason() -> None:
    body = (
        b'{"headers": {"status": "failed", "code": 5, "error_message": '
        b'"Jamendo Api Invalid Client Id Error: Your credential is not authorized."}, '
        b'"results": []}'
    )

    with pytest.raises(SourceError) as caught:
        jamendo.search("rain", "music", 5, lambda _url: body)

    message = str(caught.value)
    assert "refused the request" in message
    assert "code 5" in message
    assert "credential is not authorized" in message


def test_jamendo_refusal_names_where_the_credential_came_from() -> None:
    """A refusal is only diagnosable if it says which credential the provider actually saw."""

    body = json.dumps(
        {"headers": {"status": "failed", "code": 5, "error_message": "not authorized"}}
    ).encode()

    with pytest.raises(SourceError, match="credential from environment"):
        jamendo.search("rain", "music", 5, lambda _url: body)


def test_jamendo_reports_a_status_without_a_reason() -> None:
    body = b'{"headers": {"status": "failed"}, "results": []}'

    with pytest.raises(SourceError, match="no reason given"):
        jamendo.search("rain", "music", 5, lambda _url: body)


def test_a_keyed_client_is_skipped_rather_than_failed_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)

    outcome = run_search("forest", "image", limit=3, fetch=router())
    reports = {report.source: report for report in outcome.reports}

    assert reports["pixabay"].skipped is True
    assert reports["pixabay"].ok is False
    assert "PIXABAY_API_KEY" in reports["pixabay"].note
    assert reports["wikimedia"].ok is True
    assert "skipped" in outcome.render()


def test_a_key_never_appears_in_an_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIXABAY_API_KEY", "super-secret-key-value-9f4c")

    def failing(url: str) -> bytes:
        raise SourceError(f"{url}: HTTP 503 Service Unavailable")

    with pytest.raises(SourceError) as caught:
        pixabay.search("forest", "image", 3, failing)

    message = str(caught.value)
    assert "super-secret-key-value-9f4c" not in message
    assert "***" in message


def test_the_skipped_state_survives_a_saved_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("JAMENDO_CLIENT_ID", raising=False)
    outcome = run_search("rain", "music", limit=2, fetch=router())

    restored = load_outcome(save_outcome(outcome, tmp_path / "saved.json"))

    skipped = [report.source for report in restored.reports if report.skipped]
    assert skipped == ["jamendo"]


def test_openverse_reports_a_bot_wall_instead_of_parsing_html() -> None:
    html = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"

    with pytest.raises(SourceError, match="bot challenge"):
        openverse.search("forest", "image", 5, lambda _url: html)

    assert openverse.is_bot_wall(html) is True
    assert openverse.is_bot_wall(b'{"results": []}') is False


def test_met_offers_only_public_domain_objects_with_an_image() -> None:
    found = met.search("vangogh", "image", 3, router())

    assert len(found) == 1
    assert found[0].identifier == "436535"
    assert found[0].title == "Wheat Field with Cypresses"
    assert found[0].creator == "Vincent van Gogh"
    assert found[0].license_text == "CC0"
    assert found[0].licensed is True
    assert found[0].download_url == (
        "https://images.metmuseum.org/CRDImages/ep/original/DT1567.jpg"
    )
    assert found[0].extra["department"] == "European Paintings"


def test_met_uses_the_paginated_search_endpoint() -> None:
    """The Met retired /public/collection/v1/search on 2026-10-01; never call it again."""

    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        if "/search" in url:
            return fixture("met_search.json")
        return fixture(f"met_object_{url.rsplit('/', 1)[-1]}.json")

    met.search("van gogh", "image", 2, fetch)

    assert "/public/collection/v1.1/search" in seen[0]
    assert "limit=2" in seen[0]
    assert "/v1/search" not in seen[0]


def test_http_errors_carry_the_provider_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error

    import media_studio.sources.base as base_module

    body = (
        b'{"message":"/public/collection/v1/search was retired on 2026-10-01",'
        b'"replacement":"/public/collection/v1.1/search"}'
    )

    def fake_urlopen(_request: object, timeout: int | None = None) -> object:
        assert timeout is not None
        raise urllib.error.HTTPError("https://example.org/x", 410, "Gone", {}, io.BytesIO(body))

    monkeypatch.setattr(base_module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SourceError) as caught:
        base_module.http_fetch("https://example.org/x")

    message = str(caught.value)
    assert "HTTP 410 Gone" in message
    assert "was retired on 2026-10-01" in message
    assert "/public/collection/v1.1/search" in message


def test_transport_errors_are_reported_without_a_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.error

    import media_studio.sources.base as base_module

    def fake_urlopen(_request: object, timeout: int | None = None) -> object:
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(base_module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SourceError, match="name resolution failed"):
        base_module.http_fetch("https://example.org/x")


def test_met_rejects_an_unexpected_shape() -> None:
    with pytest.raises(SourceError, match="unexpected response shape"):
        met.search("vangogh", "image", 3, lambda _url: b'{"total": 0}')


def test_openverse_maps_licences_and_music_category() -> None:
    images = openverse.search("forest", "image", 5, router())

    assert [item.license_text for item in images] == ["CC0", "CC BY 4.0"]
    assert images[0].download_url == "https://cdn.example.org/abc-123.jpg"
    assert images[0].width == 4000
    assert images[0].licensed is True

    audio = openverse.search("forest", "audio", 5, router())

    assert audio[1].kind == "music"
    assert audio[1].duration == 142.5


def test_openverse_rejects_an_unexpected_shape() -> None:
    with pytest.raises(SourceError, match="unexpected response shape"):
        openverse.search("forest", "image", 5, lambda _url: b'{"nope": true}')


def test_wikimedia_strips_html_and_skips_missing_pages() -> None:
    found = wikimedia.search("autumn forest", "image", 5, router())

    assert len(found) == 1
    assert found[0].identifier == "File:Autumn forest path.jpg"
    assert found[0].title == "Autumn forest path.jpg"
    assert found[0].creator == "Someone Else"
    assert found[0].license_text == "CC BY-SA 4.0"
    assert found[0].size_label == "1920x1280"
    assert found[0].download_url == (
        "https://upload.wikimedia.org/wikipedia/commons/x/xx/Autumn_forest_path.jpg"
    )


def test_internet_archive_search_is_page_only() -> None:
    found = internet_archive.search("forest", "audio", 5, router())

    assert [item.license_text for item in found] == ["CC0", ""]
    assert all(item.download_url is None for item in found)
    assert found[0].source_page == "https://archive.org/details/forest-sounds-2020"
    assert found[1].licensed is False


def test_internet_archive_resolves_the_best_file() -> None:
    found = internet_archive.search("forest", "audio", 5, router())
    url, name = internet_archive.resolve(found[0], router())

    assert name == "forest-sounds-2020.mp3"
    assert url == "https://archive.org/download/forest-sounds-2020/forest-sounds-2020.mp3"


def test_internet_archive_reports_an_item_without_a_matching_file() -> None:
    item = candidate(
        source="internet_archive",
        identifier="pdf-only",
        kind="video",
    )

    with pytest.raises(SourceError, match="no file matching"):
        internet_archive.resolve(item, lambda _url: b'{"files": [{"name": "book.pdf"}]}')


def test_run_search_reports_every_source_and_ranks_licences() -> None:
    outcome = run_search("forest", "image", limit=5, fetch=router())

    expected = [module.NAME for module in clients_for("image")]
    assert [report.source for report in outcome.reports] == expected
    assert all(report.ok for report in outcome.reports)
    assert not any(report.skipped for report in outcome.reports)
    # The saved Internet Archive response carries one item with no licence URL, and the
    # fixture router serves it for every class; it is hidden rather than staged.
    assert outcome.hidden_unlicensed == 1
    licences = [classify_license(item.license_text).spdx for item in outcome.candidates]
    assert licences[0] == "CC0-1.0"
    assert "CC-BY-4.0" in licences


def test_run_search_hides_unlicensed_candidates_by_default() -> None:
    hidden = run_search("forest", "audio", limit=5, fetch=router())
    shown = run_search("forest", "audio", limit=5, fetch=router(), include_unlicensed=True)

    assert hidden.hidden_unlicensed == 1
    assert shown.hidden_unlicensed == 0
    assert len(shown.candidates) == len(hidden.candidates) + 1
    assert any(not item.licensed for item in shown.candidates)


def test_run_search_keeps_going_when_one_source_fails() -> None:
    outcome = run_search("forest", "image", limit=5, fetch=router("api.openverse.org"))

    reports = {report.source: report for report in outcome.reports}
    assert reports["openverse"].ok is False
    assert "simulated outage" in reports["openverse"].note
    assert reports["wikimedia"].ok is True
    assert outcome.candidates


def test_run_search_rejects_an_unknown_class() -> None:
    with pytest.raises(SearchError, match="unknown class"):
        run_search("forest", "design", fetch=router())


def test_run_search_explains_an_empty_source_selection() -> None:
    with pytest.raises(SearchError, match="registered clients"):
        run_search("forest", "video", sources=("openverse",), fetch=router())


def test_ranking_prefers_licence_then_query_overlap() -> None:
    items = [
        candidate(identifier="by", title="Forest canopy", license_text="CC BY 4.0"),
        candidate(identifier="cc0-far", title="Unrelated subject", license_text="CC0"),
        candidate(identifier="cc0-near", title="Forest canopy close", license_text="CC0"),
    ]

    ordered = rank_candidates(items, "forest canopy")

    assert [item.identifier for item in ordered] == ["cc0-near", "cc0-far", "by"]


def test_ranking_puts_noncommercial_last() -> None:
    items = [
        candidate(identifier="nc", license_text="CC BY-NC 4.0"),
        candidate(identifier="by", license_text="CC BY 4.0"),
    ]

    assert [item.identifier for item in rank_candidates(items, "forest")] == ["by", "nc"]


def test_dedupe_collapses_repeated_identity_and_url() -> None:
    items = [
        candidate(identifier="a"),  # kept; its download URL is the canonical one
        candidate(identifier="a"),  # same key, dropped
        candidate(identifier="b"),  # different key, same download URL, dropped
        candidate(identifier="c", download_url="https://cdn.example.org/three.jpg"),
    ]

    assert [item.identifier for item in dedupe_candidates(items)] == ["a", "c"]


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    outcome = run_search("forest", "image", limit=5, fetch=router())

    path = save_outcome(outcome, tmp_path / "search.json")
    restored = load_outcome(path)

    assert restored.query == "forest"
    assert restored.kind == "image"
    assert [item.key for item in restored.candidates] == [item.key for item in outcome.candidates]
    assert [report.source for report in restored.reports] == [
        report.source for report in outcome.reports
    ]
    assert restored.candidates[0].download_url == outcome.candidates[0].download_url


def test_default_save_path_is_slugged() -> None:
    path = default_save_path("Forest  Canopy / 4K!", "image")

    assert path.name == "image-forest-canopy-4k.json"
    assert path.parent.name == "searches"


def test_load_rejects_a_foreign_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"candidates": "not a list"}', encoding="utf-8")

    with pytest.raises(SearchError, match="not a media-studio search result"):
        load_outcome(broken)

    with pytest.raises(SearchError, match="no such search result"):
        load_outcome(tmp_path / "absent.json")
