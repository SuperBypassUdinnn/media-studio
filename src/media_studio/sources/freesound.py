"""Freesound: sound effects and field recordings. Keyed token access.

API: https://freesound.org/apiv2/search/text/ with a `token`. The download offered here is the
high-quality MP3 **preview**, which token access can fetch; original file downloads need OAuth2
and are deliberately out of scope, so the candidate says so in its extra metadata.

The token travels in the query string, so every error message is scrubbed before it leaves here.
"""

from __future__ import annotations

from ..keys import key_for
from .base import (
    Candidate,
    Fetcher,
    SourceError,
    SourceUnavailable,
    as_float,
    build_url,
    fetch_json,
    license_from_url,
    scrub,
    text,
)

NAME = "freesound"
KINDS = ("audio",)
KEY_NAME = "FREESOUND_API_KEY"
API = "https://freesound.org/apiv2/search/text/"
FIELDS = "id,name,username,license,duration,previews,tags"
MAX_PAGE_SIZE = 50


def search(query: str, kind: str, limit: int, fetch: Fetcher) -> list[Candidate]:
    """Search sounds and map them onto candidates."""

    key = key_for(KEY_NAME)
    if not key:
        raise SourceUnavailable(
            f"freesound needs {KEY_NAME}, which is not set; run "
            "`bash scripts/keys-wizard.sh` in the media-studio repository"
        )
    url = build_url(
        API,
        {
            "query": query,
            "token": key,
            "fields": FIELDS,
            "page_size": min(max(limit, 1), MAX_PAGE_SIZE),
            "sort": "score",
        },
    )
    try:
        payload = fetch_json(url, fetch)
    except SourceError as error:
        raise SourceError(scrub(str(error), key)) from error

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SourceError("freesound returned no results list")

    candidates: list[Candidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        identifier = text(item.get("id"))
        if not identifier:
            continue
        previews = item.get("previews") if isinstance(item.get("previews"), dict) else {}
        candidates.append(
            Candidate(
                source=NAME,
                identifier=identifier,
                title=text(item.get("name")) or f"freesound {identifier}",
                kind="audio",
                license_text=license_from_url(text(item.get("license"))),
                license_url=text(item.get("license")),
                creator=text(item.get("username")),
                source_page=f"https://freesound.org/s/{identifier}/",
                download_url=text(previews.get("preview-hq-mp3"))
                or text(previews.get("preview-lq-mp3"))
                or None,
                duration=as_float(item.get("duration")),
                extra={"quality": "high-quality mp3 preview, not the original file"},
            )
        )
    return candidates
