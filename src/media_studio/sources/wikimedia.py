"""Wikimedia Commons: images, audio, and video with explicit licence metadata. Keyless.

API: https://commons.wikimedia.org/w/api.php with generator=search over namespace 6 (File:)
and prop=imageinfo, which returns the direct upload URL plus extmetadata.
"""

from __future__ import annotations

import html
import re

from .base import (
    Candidate,
    Fetcher,
    SourceError,
    as_float,
    as_int,
    build_url,
    fetch_json,
    license_from_url,
    text,
)

NAME = "wikimedia"
KINDS = ("image", "video", "audio")
API = "https://commons.wikimedia.org/w/api.php"
TAGS = re.compile(r"<[^>]+>")
SEARCH_FILTER = {"image": "filetype:bitmap", "video": "filetype:video", "audio": "filetype:audio"}


def plain(value: object) -> str:
    """Strip the HTML that Commons embeds in artist and credit fields."""

    return text(html.unescape(TAGS.sub("", str(value or ""))))


def search(query: str, kind: str, limit: int, fetch: Fetcher) -> list[Candidate]:
    """Search File: pages and map them onto candidates."""

    search_term = f"{SEARCH_FILTER.get(kind, 'filetype:bitmap')} {query}".strip()
    url = build_url(
        API,
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": search_term,
            "gsrnamespace": "6",
            "gsrlimit": min(max(limit, 1), 50),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
        },
    )
    payload = fetch_json(url, fetch)
    pages = payload.get("query", {}).get("pages", {}) if isinstance(payload, dict) else {}
    if not isinstance(pages, dict):
        raise SourceError(f"{url}: unexpected response shape")

    candidates: list[Candidate] = []
    for page in pages.values():
        # Commons marks a missing File: page with `"missing": ""`, which is falsy: test for the
        # key, not for truth.
        if not isinstance(page, dict) or "missing" in page:
            continue
        info = (page.get("imageinfo") or [{}])[0]
        if not isinstance(info, dict):
            continue
        metadata = info.get("extmetadata") or {}
        license_text = plain(metadata.get("LicenseShortName", {}).get("value"))
        license_url = text(metadata.get("LicenseUrl", {}).get("value"))
        if not license_text:
            license_text = license_from_url(license_url)
        title = text(page.get("title"))
        candidates.append(
            Candidate(
                source=NAME,
                identifier=title,
                title=title.removeprefix("File:"),
                kind=kind,
                license_text=license_text,
                license_url=license_url,
                creator=plain(metadata.get("Artist", {}).get("value")),
                source_page=text(info.get("descriptionurl")),
                download_url=text(info.get("url")) or None,
                width=as_int(info.get("width")),
                height=as_int(info.get("height")),
                duration=as_float(metadata.get("Duration", {}).get("value")),
                extra={"mime": text(info.get("mime"))},
            )
        )
    return candidates
