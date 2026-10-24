"""Openverse: aggregated CC-licensed images and audio. Keyless, rate limited.

API: https://api.openverse.org/v1/images/ and /v1/audio/
Response: {"result_count": int, "results": [{"id", "title", "creator", "license",
"license_version", "license_url", "foreign_landing_url", "url", "width", "height",
"duration", "category"}]}
"""

from __future__ import annotations

import json

from .base import (
    Candidate,
    Fetcher,
    SourceError,
    as_float,
    as_int,
    build_url,
    license_from_family,
    license_from_url,
    text,
)

NAME = "openverse"
KINDS = ("image", "audio", "music")
API_ROOT = "https://api.openverse.org/v1"
BOT_WALL_MARKERS = ("<!doctype html", "<html", "just a moment", "cf-chl")


def is_bot_wall(payload: bytes) -> bool:
    """Openverse sits behind a Cloudflare challenge that answers HTML, not JSON.

    Reaching around a bot wall is out of scope by design, so this is reported as a fact
    with the action that would fix it rather than retried.
    """

    head = payload[:600].decode("utf-8", errors="replace").strip().lower()
    return any(marker in head for marker in BOT_WALL_MARKERS)


def search(query: str, kind: str, limit: int, fetch: Fetcher) -> list[Candidate]:
    """Search images or audio and map the hits onto candidates."""

    endpoint = "images" if kind == "image" else "audio"
    url = build_url(
        f"{API_ROOT}/{endpoint}/",
        {"q": query, "page_size": min(max(limit, 1), 50), "mature": "false"},
    )
    raw = fetch(url)
    if is_bot_wall(raw):
        raise SourceError(
            "openverse answered a bot challenge instead of JSON from this network; "
            "register client credentials at https://api.openverse.org/v1/auth_tokens/register/ "
            "to authenticate, or search another source"
        )
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        raise SourceError(f"{url}: not JSON ({error.msg})") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SourceError(f"{url}: unexpected response shape")

    candidates: list[Candidate] = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        license_text = license_from_family(
            text(item.get("license")), text(item.get("license_version"))
        ) or license_from_url(text(item.get("license_url")))
        item_kind = kind
        if kind == "audio" and text(item.get("category")).lower() == "music":
            item_kind = "music"
        candidates.append(
            Candidate(
                source=NAME,
                identifier=text(item.get("id")),
                title=text(item.get("title")) or "(untitled)",
                kind=item_kind,
                license_text=license_text,
                license_url=text(item.get("license_url")),
                creator=text(item.get("creator")),
                source_page=text(item.get("foreign_landing_url")),
                download_url=text(item.get("url")) or None,
                width=as_int(item.get("width")),
                height=as_int(item.get("height")),
                duration=as_float(item.get("duration")),
                extra={
                    "provider": text(item.get("source")),
                    "filetype": text(item.get("filetype")),
                },
            )
        )
    return candidates
