"""Pixabay: photos and videos. Keyed, and free for commercial use.

API: https://pixabay.com/api/ (images) and /api/videos/ (videos), both with `key`, `q`, and
`per_page`. The Pixabay Content Licence allows commercial use and does not require attribution,
which the ledger records as ``LicenseRef-Pixabay``.

The key travels in the query string, so every error message is scrubbed before it leaves here.
"""

from __future__ import annotations

from ..keys import key_for
from .base import (
    Candidate,
    Fetcher,
    SourceError,
    SourceUnavailable,
    as_float,
    as_int,
    build_url,
    fetch_json,
    scrub,
    text,
)

NAME = "pixabay"
KINDS = ("image", "video")
KEY_NAME = "PIXABAY_API_KEY"
LICENCE = "Pixabay Content Licence"
IMAGES_API = "https://pixabay.com/api/"
VIDEOS_API = "https://pixabay.com/api/videos/"
MAX_PER_PAGE = 50


def _require_key() -> str:
    key = key_for(KEY_NAME)
    if not key:
        raise SourceUnavailable(
            f"pixabay needs {KEY_NAME}, which is not set; run "
            "`bash ~/.clone/Github/media-studio/scripts/keys-wizard.sh`"
        )
    return key


def search(query: str, kind: str, limit: int, fetch: Fetcher) -> list[Candidate]:
    """Search photos or videos."""

    key = _require_key()
    # Pixabay rejects per_page below 3, so a small --limit still asks for three.
    per_page = min(max(limit, 3), MAX_PER_PAGE)
    if kind == "image":
        url = build_url(
            IMAGES_API,
            {
                "key": key,
                "q": query,
                "image_type": "photo",
                "per_page": per_page,
                "safesearch": "true",
            },
        )
    else:
        url = build_url(
            VIDEOS_API, {"key": key, "q": query, "per_page": per_page, "safesearch": "true"}
        )
    try:
        payload = fetch_json(url, fetch)
    except SourceError as error:
        raise SourceError(scrub(str(error), key)) from error

    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise SourceError(f"pixabay returned no hits list for {kind}")

    candidates: list[Candidate] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        identifier = text(hit.get("id"))
        if not identifier:
            continue
        if kind == "image":
            candidates.append(
                Candidate(
                    source=NAME,
                    identifier=identifier,
                    title=text(hit.get("tags")) or f"pixabay photo {identifier}",
                    kind="image",
                    license_text=LICENCE,
                    creator=text(hit.get("user")),
                    source_page=text(hit.get("pageURL")),
                    download_url=text(hit.get("largeImageURL"))
                    or text(hit.get("previewURL"))
                    or None,
                    width=as_int(hit.get("imageWidth")),
                    height=as_int(hit.get("imageHeight")),
                    extra={"tags": text(hit.get("tags"))},
                )
            )
        else:
            videos = hit.get("videos") if isinstance(hit.get("videos"), dict) else {}
            best = videos.get("large") or videos.get("medium") or videos.get("small") or {}
            candidates.append(
                Candidate(
                    source=NAME,
                    identifier=identifier,
                    title=text(hit.get("tags")) or f"pixabay video {identifier}",
                    kind="video",
                    license_text=LICENCE,
                    creator=text(hit.get("user")),
                    source_page=text(hit.get("pageURL")),
                    download_url=text(best.get("url")) or None,
                    width=as_int(best.get("width")),
                    height=as_int(best.get("height")),
                    duration=as_float(hit.get("duration")),
                    extra={"tags": text(hit.get("tags"))},
                )
            )
    return candidates
