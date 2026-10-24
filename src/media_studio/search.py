"""Fan-out asset search across the source clients, with ranking and a per-source report.

Ranking is deliberately local and cheap: licence preference first (CC0 and public domain ahead
of attribution licences), then how much of the query appears in the title, then whether the
candidate can be fetched directly. No model is involved, so a search costs no tokens.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .ledger import classify_license
from .sources import client_names, clients_for
from .sources.base import (
    Candidate,
    Fetcher,
    SourceError,
    SourceReport,
    SourceUnavailable,
    http_fetch,
)

SEARCH_KINDS = ("image", "video", "audio", "music")
SEARCH_DIRECTORY = Path("~/.cache/media-studio/searches")
SLUG = re.compile(r"[^a-z0-9]+")

LICENCE_RANK = {"CC0-1.0": 0, "Unlicense": 0, "CC-PDDC": 0, "LicenseRef-Pixabay": 0}


class SearchError(RuntimeError):
    """Raised when a search cannot be run or a saved result cannot be read."""


@dataclass(frozen=True)
class SearchOutcome:
    """Everything one fan-out search produced."""

    query: str
    kind: str
    searched_at: str
    candidates: tuple[Candidate, ...]
    reports: tuple[SourceReport, ...]
    hidden_unlicensed: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "kind": self.kind,
            "searched_at": self.searched_at,
            "sources": [report.to_dict() for report in self.reports],
            "hidden_unlicensed": self.hidden_unlicensed,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def render(self) -> str:
        """Human-readable listing, numbered the way `ms assets pull --index` counts."""

        lines = [
            f"query: {self.query}",
            f"class: {self.kind}",
            "",
            "sources",
        ]
        lines.extend(report.line() for report in self.reports)
        lines.append("")
        if not self.candidates:
            lines.append("no licensed candidates; try a broader query or --include-unlicensed")
            return "\n".join(lines)

        lines.append(f"candidates ({len(self.candidates)})")
        for index, candidate in enumerate(self.candidates, start=1):
            info = classify_license(candidate.license_text)
            licence = info.spdx if info else candidate.license_text or "unknown"
            direct = "direct" if candidate.download_url else "resolve"
            size = candidate.size_label or "-"
            lines.append(
                f"  {index:>3}  {candidate.source:<16} {licence:<14} {size:<11} "
                f"{direct:<8} {candidate.title[:60]}"
            )
        if self.hidden_unlicensed:
            lines.append(
                f"  {self.hidden_unlicensed} unlicensed candidate(s) hidden; "
                "add --include-unlicensed to see them"
            )
        return "\n".join(lines)


def rank_candidates(candidates: list[Candidate], query: str) -> list[Candidate]:
    """Order candidates by licence, then query overlap in the title, then stability."""

    words = {word for word in SLUG.split(query.lower()) if word}

    def sort_key(candidate: Candidate) -> tuple[int, int, int, str]:
        info = classify_license(candidate.license_text)
        licence_rank = LICENCE_RANK.get(info.spdx, 1) if info else 3
        if info and not info.commercial_ok:
            licence_rank = 4
        title_words = set(SLUG.split(candidate.title.lower()))
        overlap = len(words & title_words)
        direct = 0 if candidate.download_url else 1
        return (licence_rank, -overlap, direct, candidate.title.lower())

    return sorted(candidates, key=sort_key)


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Drop repeats by identity and by identical direct download URL."""

    seen_keys: set[str] = set()
    seen_urls: set[str] = set()
    unique: list[Candidate] = []
    for candidate in candidates:
        if candidate.key in seen_keys:
            continue
        if candidate.download_url and candidate.download_url in seen_urls:
            continue
        seen_keys.add(candidate.key)
        if candidate.download_url:
            seen_urls.add(candidate.download_url)
        unique.append(candidate)
    return unique


def run_search(
    query: str,
    kind: str,
    *,
    limit: int = 8,
    sources: tuple[str, ...] = (),
    fetch: Fetcher = http_fetch,
    include_unlicensed: bool = False,
) -> SearchOutcome:
    """Search every matching source, keeping one report per source."""

    if kind not in SEARCH_KINDS:
        raise SearchError(f"unknown class {kind!r}; use one of {', '.join(SEARCH_KINDS)}")
    clients = clients_for(kind, sources)
    if not clients:
        available = ", ".join(client_names())
        raise SearchError(
            f"no source answers for {kind!r} (narrowed by {', '.join(sources) or 'nothing'}); "
            f"registered clients: {available}"
        )

    found: list[Candidate] = []
    reports: list[SourceReport] = []
    for module in clients:
        try:
            hits = module.search(query, kind, limit, fetch)
        except SourceUnavailable as error:
            reports.append(SourceReport(module.NAME, ok=False, note=str(error), skipped=True))
            continue
        except SourceError as error:
            reports.append(SourceReport(module.NAME, ok=False, note=str(error)))
            continue
        reports.append(SourceReport(module.NAME, ok=True, count=len(hits)))
        found.extend(hits)

    licensed = [candidate for candidate in found if candidate.licensed]
    hidden = len(found) - len(licensed)
    kept = licensed if not include_unlicensed else found
    return SearchOutcome(
        query=query,
        kind=kind,
        searched_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        candidates=tuple(rank_candidates(dedupe_candidates(kept), query)),
        reports=tuple(reports),
        hidden_unlicensed=hidden if not include_unlicensed else 0,
    )


def default_save_path(query: str, kind: str) -> Path:
    """Where a search result is cached when the caller does not choose a path."""

    slug = SLUG.sub("-", query.lower()).strip("-")[:60] or "query"
    return SEARCH_DIRECTORY.expanduser() / f"{kind}-{slug}.json"


def save_outcome(outcome: SearchOutcome, path: Path | None = None) -> Path:
    """Write the outcome as JSON so `ms assets pull` can stage from it later."""

    target = Path(path).expanduser() if path else default_save_path(outcome.query, outcome.kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(outcome.to_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def load_outcome(path: Path) -> SearchOutcome:
    """Read a saved search result back."""

    source = Path(path).expanduser()
    if not source.is_file():
        raise SearchError(f"no such search result: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SearchError(f"{source} is not valid JSON: {error.msg}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise SearchError(f"{source} is not a media-studio search result")

    candidates = tuple(
        candidate_from_dict(entry) for entry in payload["candidates"] if isinstance(entry, dict)
    )
    reports = tuple(
        SourceReport(
            source=str(entry.get("source", "?")),
            ok=bool(entry.get("ok")),
            count=int(entry.get("count") or 0),
            note=str(entry.get("note") or ""),
            skipped=bool(entry.get("skipped")),
        )
        for entry in payload.get("sources", [])
        if isinstance(entry, dict)
    )
    return SearchOutcome(
        query=str(payload.get("query", "")),
        kind=str(payload.get("kind", "")),
        searched_at=str(payload.get("searched_at", "")),
        candidates=candidates,
        reports=reports,
        hidden_unlicensed=int(payload.get("hidden_unlicensed") or 0),
    )


def candidate_from_dict(entry: dict[str, object]) -> Candidate:
    """Rebuild a candidate from saved JSON."""

    extra = entry.get("extra")
    return Candidate(
        source=str(entry.get("source", "")),
        identifier=str(entry.get("identifier", "")),
        title=str(entry.get("title", "")),
        kind=str(entry.get("kind", "video")),
        license_text=str(entry.get("license") or ""),
        license_url=str(entry.get("license_url") or ""),
        creator=str(entry.get("creator") or ""),
        source_page=str(entry.get("source_page") or ""),
        download_url=str(entry.get("download_url")) if entry.get("download_url") else None,
        width=entry.get("width") if isinstance(entry.get("width"), int) else None,
        height=entry.get("height") if isinstance(entry.get("height"), int) else None,
        duration=float(entry["duration"])
        if isinstance(entry.get("duration"), int | float)
        else None,
        extra=dict(extra) if isinstance(extra, dict) else {},
    )
