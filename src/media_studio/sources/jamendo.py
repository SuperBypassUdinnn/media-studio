"""Jamendo: Creative Commons music. Keyed by client id.

API: https://api.jamendo.com/v3.0/tracks/ with `client_id`, `format=json`, `search`, and
`include=licenses` so every track carries its licence URL. `audioformat=mp32` returns a
streamable MP3 that the ledger can stage.

The client id travels in the query string, so every error message is scrubbed before it leaves.
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

NAME = "jamendo"
KINDS = ("music",)
KEY_NAME = "JAMENDO_CLIENT_ID"
API = "https://api.jamendo.com/v3.0/tracks/"
MAX_LIMIT = 100


def search(query: str, kind: str, limit: int, fetch: Fetcher) -> list[Candidate]:
    """Search tracks and map them onto candidates."""

    key = key_for(KEY_NAME)
    if not key:
        raise SourceUnavailable(
            f"jamendo needs {KEY_NAME}, which is not set; run "
            "`bash ~/.clone/Github/media-studio/scripts/keys-wizard.sh`"
        )
    url = build_url(
        API,
        {
            "client_id": key,
            "format": "json",
            "limit": min(max(limit, 1), MAX_LIMIT),
            "search": query,
            "include": "licenses musicinfo",
            "audioformat": "mp32",
            "order": "relevance",
        },
    )
    try:
        payload = fetch_json(url, fetch)
    except SourceError as error:
        raise SourceError(scrub(str(error), key)) from error

    if not isinstance(payload, dict):
        raise SourceError("jamendo returned an unexpected body")
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    if text(headers.get("status")) != "success":
        # Jamendo explains itself in `error_message`, and the wording matters: an unauthorized
        # client id is a credential problem, not an empty search.
        reason = text(headers.get("error_message")) or text(headers.get("warnings"))
        code = text(headers.get("code"))
        detail = f" (code {code})" if code else ""
        raise SourceError(f"jamendo refused the request{detail}: {reason or 'no reason given'}")

    results = payload.get("results")
    if not isinstance(results, list):
        raise SourceError("jamendo returned no results list")

    candidates: list[Candidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        identifier = text(item.get("id"))
        if not identifier:
            continue
        candidates.append(
            Candidate(
                source=NAME,
                identifier=identifier,
                title=text(item.get("name")) or f"jamendo track {identifier}",
                kind="music",
                license_text=license_from_url(text(item.get("license_ccurl"))),
                license_url=text(item.get("license_ccurl")),
                creator=text(item.get("artist_name")),
                source_page=text(item.get("shareurl")),
                download_url=text(item.get("audio")) or None,
                duration=as_float(item.get("duration")),
                extra={"album": text(item.get("album_name"))},
            )
        )
    return candidates
