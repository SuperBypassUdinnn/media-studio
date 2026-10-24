"""The Metropolitan Museum of Art Open Access: keyless, CC0 images.

Search returns object ids only, so each candidate costs one more request for its details.
Only public-domain objects are offered, which is the Met's CC0 set.

The Met retired `/public/collection/v1/search` on 2026-10-01 and answers it with HTTP 410 naming
`/public/collection/v1.1/search` as the replacement: Elastic-backed, paginated with offset and
limit, and the same `{"total", "objectIDs"}` shape. Verified live on 2026-10-24.
"""

from __future__ import annotations

from .base import (
    Candidate,
    Fetcher,
    SourceError,
    build_url,
    fetch_json,
    text,
)

NAME = "met"
KINDS = ("image",)
SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects"
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"


def search(query: str, kind: str, limit: int, fetch: Fetcher) -> list[Candidate]:
    """Search public-domain objects that have an image."""

    url = build_url(SEARCH, {"q": query, "hasImages": "true", "limit": max(limit, 1)})
    payload = fetch_json(url, fetch)
    ids = payload.get("objectIDs") if isinstance(payload, dict) else None
    if not isinstance(ids, list):
        raise SourceError(f"{url}: unexpected response shape")

    candidates: list[Candidate] = []
    for object_id in [value for value in ids if isinstance(value, int)][: max(limit, 1)]:
        detail = fetch_json(f"{OBJECT}/{object_id}", fetch)
        if not isinstance(detail, dict) or not detail.get("isPublicDomain"):
            continue
        image = text(detail.get("primaryImage"))
        if not image:
            continue
        candidates.append(
            Candidate(
                source=NAME,
                identifier=str(object_id),
                title=text(detail.get("title")) or f"Met object {object_id}",
                kind="image",
                license_text="CC0",
                license_url=CC0_URL,
                creator=text(detail.get("artistDisplayName")),
                source_page=text(detail.get("objectURL")),
                download_url=image,
                extra={
                    "department": text(detail.get("department")),
                    "date": text(detail.get("objectDate")),
                    "medium": text(detail.get("medium")),
                },
            )
        )
    return candidates
