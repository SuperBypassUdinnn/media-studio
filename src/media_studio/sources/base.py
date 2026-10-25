"""Shared types and helpers for asset source clients.

A source client is a module with ``NAME``, ``KINDS``, ``search(...)``, and optionally
``resolve(...)``. Clients never write to disk: they return candidates, and the ledger stages
them. Every network read goes through an injected ``fetch`` callable so tests can run offline.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from ..keys import key_status, shadowed_by_environment

USER_AGENT = "media-studio/0.1 (local asset search)"
TIMEOUT_SECONDS = 45

Fetcher = Callable[[str], bytes]

KINDS = ("image", "video", "audio", "music", "3d")

# Licence family names as providers spell them, mapped to wording classify_license accepts.
LICENCE_WORDING = {
    "cc0": "CC0",
    "pdm": "public domain",
    "pd": "public domain",
    "publicdomain": "public domain",
    "by": "CC BY",
    "by-sa": "CC BY-SA",
    "by-nc": "CC BY-NC",
    "by-nd": "CC BY-ND",
    "by-nc-sa": "CC BY-NC-SA",
    "by-nc-nd": "CC BY-NC-ND",
    "zero": "CC0",
}

LICENCE_URL_HINTS = (
    ("creativecommons.org/publicdomain/zero", "CC0"),
    ("creativecommons.org/publicdomain/mark", "public domain"),
    ("creativecommons.org/licenses/by-nc-sa", "CC BY-NC-SA"),
    ("creativecommons.org/licenses/by-nc-nd", "CC BY-NC-ND"),
    ("creativecommons.org/licenses/by-nc", "CC BY-NC"),
    ("creativecommons.org/licenses/by-sa", "CC BY-SA"),
    ("creativecommons.org/licenses/by-nd", "CC BY-ND"),
    ("creativecommons.org/licenses/by", "CC BY"),
)
LICENCE_URL_PATH = re.compile(r"/licenses/([a-z-]+)/([0-9.]+)")


@dataclass(frozen=True)
class Candidate:
    """One search hit, with everything the ledger needs to stage it."""

    source: str
    identifier: str
    title: str
    kind: str
    license_text: str = ""
    license_url: str = ""
    creator: str = ""
    source_page: str = ""
    download_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity across searches, used for dedupe and for `ms assets pull`."""

        return f"{self.source}:{self.identifier}"

    @property
    def licensed(self) -> bool:
        """True when the wording is one the ledger can classify."""

        from ..ledger import classify_license

        return bool(self.license_text) and classify_license(self.license_text) is not None

    @property
    def size_label(self) -> str:
        if self.duration:
            return f"{self.duration:.0f}s"
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return ""

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "source": self.source,
            "identifier": self.identifier,
            "title": self.title,
            "kind": self.kind,
            "license": self.license_text,
            "license_url": self.license_url,
            "creator": self.creator,
            "source_page": self.source_page,
            "download_url": self.download_url,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "size_label": self.size_label,
            "extra": self.extra,
        }


@dataclass
class SourceReport:
    """What one source did during a search, so a partial failure stays visible.

    `skipped` marks a source that is configured out rather than broken: a keyed provider with no
    key reduces coverage, and saying so is different from reporting a failure.
    """

    source: str
    ok: bool
    count: int = 0
    note: str = ""
    skipped: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "ok": self.ok,
            "count": self.count,
            "note": self.note,
            "skipped": self.skipped,
        }

    def line(self) -> str:
        if self.skipped:
            return f"  {self.source:<16} {'skipped':<7} {self.note}"
        status = "ok" if self.ok else "failed"
        detail = f"{self.count} result(s)" if self.ok else self.note
        return f"  {self.source:<16} {status:<7} {detail}"


def http_fetch(url: str) -> bytes:
    """Default fetcher: one GET with a descriptive user agent.

    An HTTP error carries the provider's own message when it sends a JSON body, because that is
    where a retired endpoint names its replacement.
    """

    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return bytes(response.read())
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            body = error.read(2000).decode("utf-8", errors="replace").strip()
        except OSError:  # pragma: no cover - the body is already gone
            body = ""
        if body.startswith("<"):
            # HTML instead of an API answer usually means a bot challenge or a login wall.
            detail = "HTML body: bot challenge or login wall"
        elif body:
            detail = " ".join(body.split())[:200]
        suffix = f": {detail}" if detail else ""
        raise SourceError(f"{url}: HTTP {error.code} {error.reason}{suffix}") from error
    except urllib.error.URLError as error:
        raise SourceError(f"{url}: {error}") from error


class SourceError(RuntimeError):
    """Raised by a client when a source cannot answer."""


class SourceUnavailable(SourceError):
    """Raised when a source is configured out, such as a keyed provider with no key.

    Separate from a failure on purpose: a missing key is not an error, it is reduced coverage.
    """


def fetch_json(url: str, fetch: Fetcher) -> object:
    """Fetch and decode JSON, turning transport and decode failures into SourceError."""

    payload = fetch(url)
    try:
        return json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        raise SourceError(f"{url}: not JSON ({error.msg})") from error


def credential_note(key_name: str) -> str:
    """Where a credential came from, for a provider's refusal message. Never a value.

    Jamendo answers HTTP 200 with a failure body, so its refusal is the one place a provider tells
    us a credential exists but was not accepted; naming its origin there is what turns an
    unexplained "not authorized" into a diagnosable one.
    """

    status = key_status((key_name,))[0]
    if not status.present:
        return ""
    note = f"credential from {status.source}"
    if shadowed_by_environment(key_name):
        note += ", which differs from the value in secrets.env"
    return note


def scrub(text: str, *secrets: str) -> str:
    """Remove credential values from any message a user might read.

    Keyed providers put the key in the query string, and transport errors quote the URL, so every
    client that carries a credential passes it through here before raising.
    """

    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def build_url(base: str, params: dict[str, object]) -> str:
    """Build a URL with query parameters, dropping empty values."""

    clean = {key: value for key, value in params.items() if value not in (None, "", [])}
    pairs: list[tuple[str, str]] = []
    for key, value in clean.items():
        if isinstance(value, list | tuple):
            pairs.extend((key, str(item)) for item in value)
        else:
            pairs.append((key, str(value)))
    return f"{base}?{urllib.parse.urlencode(pairs)}"


def license_from_family(family: str, version: str = "") -> str:
    """Turn a provider's licence family and version into wording the ledger understands."""

    normalized = (family or "").strip().lower()
    wording = LICENCE_WORDING.get(normalized, "")
    if not wording:
        return ""
    if wording.startswith("CC ") and version:
        return f"{wording} {version}"
    return wording


def license_from_url(url: str) -> str:
    """Map a licence URL onto wording the ledger understands, version included when present."""

    lowered = (url or "").lower()
    match = LICENCE_URL_PATH.search(lowered)
    if match:
        wording = LICENCE_WORDING.get(match.group(1), "")
        if wording:
            return f"{wording} {match.group(2)}"
    for fragment, wording in LICENCE_URL_HINTS:
        if fragment in lowered:
            return wording
    return ""


def _numeric(value: object) -> int | float | str | None:
    """Accept only values that mean a number; booleans are not dimensions."""

    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    return value


def as_int(value: object) -> int | None:
    """Best-effort integer conversion for provider metadata."""

    candidate = _numeric(value)
    try:
        return int(candidate)  # anything non-numeric raises and is reported as absent
    except (TypeError, ValueError):
        return None


def as_float(value: object) -> float | None:
    """Best-effort float conversion for provider metadata."""

    candidate = _numeric(value)
    try:
        return float(candidate)  # anything non-numeric raises and is reported as absent
    except (TypeError, ValueError):
        return None


def text(value: object) -> str:
    """Normalize provider text: single line, no leading or trailing space."""

    return " ".join(str(value or "").split())
