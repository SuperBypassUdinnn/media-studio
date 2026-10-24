"""Internet Archive: movies, audio, and images. Keyless.

Search: https://archive.org/advancedsearch.php?q=...&output=json returns items with an
identifier, a title, a creator, and sometimes a licence URL. The files inside an item are
resolved on demand through https://archive.org/metadata/<identifier>, which is why a candidate
from this source carries no direct download URL until it is pulled.
"""

from __future__ import annotations

from .base import (
    Candidate,
    Fetcher,
    SourceError,
    build_url,
    fetch_json,
    license_from_url,
    text,
)

NAME = "internet_archive"
KINDS = ("image", "video", "audio", "music")
SEARCH = "https://archive.org/advancedsearch.php"
METADATA = "https://archive.org/metadata"
FIELDS = ("identifier", "title", "creator", "licenseurl", "mediatype", "year")

MEDIATYPE_FOR_KIND = {"image": "image", "video": "movies", "audio": "audio", "music": "audio"}
# Most preferred first; the first matching file in an item wins.
EXTENSIONS_FOR_KIND = {
    "image": (".jpg", ".jpeg", ".png", ".tif", ".tiff"),
    "video": (".mp4", ".m4v", ".ogv", ".webm", ".avi"),
    "audio": (".mp3", ".flac", ".ogg", ".wav", ".m4a"),
    "music": (".mp3", ".flac", ".ogg", ".wav", ".m4a"),
}


def search(query: str, kind: str, limit: int, fetch: Fetcher) -> list[Candidate]:
    """Search items of the matching mediatype."""

    mediatype = MEDIATYPE_FOR_KIND.get(kind, "movies")
    url = build_url(
        SEARCH,
        {
            "q": f"({query}) AND mediatype:({mediatype})",
            "fl[]": [f for f in FIELDS],
            "rows": min(max(limit, 1), 50),
            "page": 1,
            "output": "json",
        },
    )
    payload = fetch_json(url, fetch)
    docs = payload.get("response", {}).get("docs", []) if isinstance(payload, dict) else []
    if not isinstance(docs, list):
        raise SourceError(f"{url}: unexpected response shape")

    candidates: list[Candidate] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        identifier = text(doc.get("identifier"))
        if not identifier:
            continue
        creator = doc.get("creator")
        if isinstance(creator, list):
            creator = creator[0] if creator else ""
        candidates.append(
            Candidate(
                source=NAME,
                identifier=identifier,
                title=text(doc.get("title")) or identifier,
                kind=kind,
                license_text=license_from_url(text(doc.get("licenseurl"))),
                license_url=text(doc.get("licenseurl")),
                creator=text(creator),
                source_page=f"https://archive.org/details/{identifier}",
                download_url=None,
                extra={"mediatype": text(doc.get("mediatype")), "year": text(doc.get("year"))},
            )
        )
    return candidates


def resolve(candidate: Candidate, fetch: Fetcher) -> tuple[str, str]:
    """Find the best file inside an item; returns (download url, file name)."""

    url = f"{METADATA}/{candidate.identifier}"
    payload = fetch_json(url, fetch)
    files = payload.get("files", []) if isinstance(payload, dict) else []
    if not isinstance(files, list) or not files:
        raise SourceError(f"{url}: item has no files")

    extensions = EXTENSIONS_FOR_KIND.get(candidate.kind, EXTENSIONS_FOR_KIND["video"])
    by_extension: dict[str, str] = {}
    for entry in files:
        if not isinstance(entry, dict):
            continue
        name = text(entry.get("name"))
        lowered = name.lower()
        for extension in extensions:
            if lowered.endswith(extension) and extension not in by_extension:
                by_extension[extension] = name
    for extension in extensions:
        name = by_extension.get(extension)
        if name:
            return f"https://archive.org/download/{candidate.identifier}/{name}", name
    raise SourceError(f"{url}: no file matching {', '.join(extensions)} was found in this item")
