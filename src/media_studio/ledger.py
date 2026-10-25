"""Asset ledger: staged files, license sidecars, and the derived index and credits.

Every staged file gets a sidecar named ``<file>.license`` in REUSE style. ``assets/index.json``
and ``assets/CREDITS.md`` are generated from those sidecars and are never hand-edited.

Nothing is written inside ``assets/`` until every check has passed: downloads land in a
temporary directory first, so a quota or licence refusal leaves the project untouched.
"""

from __future__ import annotations

import errno
import hashlib
import json
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .manifest import Budget, Project
from .sidecar import (
    ATTRIBUTION_NOT_RECORDED,
    ATTRIBUTION_NOT_REQUIRED,
    PLACEHOLDER_ATTRIBUTIONS,
    UNKNOWN_SPDX,
    parse_sidecar_text,
    read_sidecar,
    sidecar_path,
)

ASSET_CLASSES = ("3d", "video", "audio", "music", "design")
QUARANTINE_CLASS = "unverified"
MUSIC_CLASS = "music"
CC0_SPDX = "CC0-1.0"
INDEX_NAME = "index.json"
CREDITS_NAME = "CREDITS.md"
DOWNLOAD_TIMEOUT_SECONDS = 120
USER_AGENT = "media-studio/0.1 (local asset ledger)"
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

LICENCE_FOOTER = "Generated from the asset ledger by media-studio. Do not edit by hand; re-run"

SIDECAR_ORDER = (
    "SPDX-FileCopyrightText",
    "SPDX-License-Identifier",
    "Source-URL",
    "Attribution",
    "Retrieved-At",
    "SHA256",
    "Provider",
    "Class",
    "File",
)


class LedgerError(RuntimeError):
    """Raised when an asset cannot be staged or the ledger cannot be read."""


class QuotaError(LedgerError):
    """Raised when staging would exceed a project budget."""


class LicensePolicyError(LedgerError):
    """Raised when a licence cannot be used in a deliverable."""


@dataclass(frozen=True)
class LicenseInfo:
    """A licence decision: what it is, and what it demands."""

    spdx: str
    label: str
    attribution_required: bool
    commercial_ok: bool
    derivatives_ok: bool


# (spdx, label, attribution_required, commercial_ok, derivatives_ok, provider spellings)
LICENSE_TABLE: tuple[tuple[str, str, bool, bool, bool, tuple[str, ...]], ...] = (
    ("CC0-1.0", "CC0 1.0", False, True, True, ("cc0", "cc-0", "cc0-1.0", "cc0 1.0")),
    (
        "CC-PDDC",
        "Public domain",
        False,
        True,
        True,
        ("public domain", "public-domain", "publicdomain", "pdm", "cc-pddc"),
    ),
    ("Unlicense", "Unlicense", False, True, True, ("unlicense",)),
    (
        "CC-BY-4.0",
        "CC BY 4.0",
        True,
        True,
        True,
        ("cc by 4.0", "cc-by-4.0", "cc by", "cc-by", "ccby4", "ccby"),
    ),
    ("CC-BY-3.0", "CC BY 3.0", True, True, True, ("cc by 3.0", "cc-by-3.0", "ccby3")),
    (
        "CC-BY-SA-4.0",
        "CC BY-SA 4.0",
        True,
        True,
        True,
        ("cc by-sa 4.0", "cc-by-sa-4.0", "cc by sa", "ccbysa"),
    ),
    ("MIT", "MIT", True, True, True, ("mit", "mit license")),
    ("Apache-2.0", "Apache 2.0", True, True, True, ("apache-2.0", "apache 2.0", "apache")),
    ("OFL-1.1", "SIL Open Font License 1.1", True, True, True, ("ofl-1.1", "ofl", "sil ofl")),
    ("BSD-3-Clause", "BSD 3-Clause", True, True, True, ("bsd-3-clause", "bsd 3")),
    (
        "LicenseRef-Pixabay",
        "Pixabay Content Licence",
        False,
        True,
        True,
        (
            "pixabay content licence",
            "pixabay content license",
            "pixabay licence",
            "pixabay license",
            "pixabay",
        ),
    ),
    (
        "CC-BY-NC-4.0",
        "CC BY-NC 4.0 (non-commercial)",
        True,
        False,
        True,
        ("cc by-nc 4.0", "cc-by-nc-4.0", "cc by-nc", "ccbync"),
    ),
    (
        "CC-BY-ND-4.0",
        "CC BY-ND 4.0 (no derivatives)",
        True,
        True,
        False,
        ("cc-by-nd-4.0", "cc by-nd"),
    ),
)


def is_quarantined(spdx: str, class_name: str) -> bool:
    """True when a record has no usable licence, so no deliverable may reference it."""

    return spdx == UNKNOWN_SPDX or class_name == QUARANTINE_CLASS


@dataclass(frozen=True)
class StagedAsset:
    """The result of one staging call."""

    path: Path
    relative: str
    class_name: str
    sha256: str
    size_bytes: int
    spdx: str
    license_label: str
    attribution_required: bool
    attribution: str
    source_url: str
    provider: str
    retrieved_at: str
    duplicate_of: str | None = None

    @property
    def quarantined(self) -> bool:
        return is_quarantined(self.spdx, self.class_name)


@dataclass(frozen=True)
class AssetRecord:
    """One row of the derived index."""

    path: str
    class_name: str
    bytes: int
    sha256: str
    spdx: str
    attribution: str
    attribution_required: bool
    source_url: str
    provider: str
    retrieved_at: str

    @property
    def quarantined(self) -> bool:
        return is_quarantined(self.spdx, self.class_name)

    @property
    def attribution_missing(self) -> bool:
        """A credit line is required but no real name was recorded."""

        return self.attribution_required and (
            not self.attribution or self.attribution in PLACEHOLDER_ATTRIBUTIONS
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "class": self.class_name,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "spdx": self.spdx,
            "attribution": self.attribution,
            "attribution_required": self.attribution_required,
            "attribution_missing": self.attribution_missing,
            "quarantined": self.quarantined,
            "source_url": self.source_url,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at,
        }


@dataclass(frozen=True)
class Totals:
    """Counts across the index."""

    count: int
    bytes: int
    attribution_required: int
    quarantined: int

    def to_dict(self) -> dict[str, int]:
        return {
            "count": self.count,
            "bytes": self.bytes,
            "attribution_required": self.attribution_required,
            "quarantined": self.quarantined,
        }


@dataclass(frozen=True)
class Quota:
    """Budget state of the project."""

    max_asset_bytes: int
    max_project_bytes: int
    staged_bytes: int
    project_bytes: int
    ok: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "max_asset_bytes": self.max_asset_bytes,
            "max_project_bytes": self.max_project_bytes,
            "staged_bytes": self.staged_bytes,
            "project_bytes": self.project_bytes,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class AssetIndex:
    """The derived view of everything staged in a project."""

    generated_at: str
    slug: str
    title: str
    assets: tuple[AssetRecord, ...]
    duplicates: dict[str, list[str]]
    unlicensed: tuple[str, ...]
    totals: Totals
    quota: Quota

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "project": {"slug": self.slug, "title": self.title},
            "assets": [record.to_dict() for record in self.assets],
            "duplicates": self.duplicates,
            "unlicensed": list(self.unlicensed),
            "totals": self.totals.to_dict(),
            "quota": self.quota.to_dict(),
        }


def sha256_of(path: Path) -> str:
    """Hash a file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_license(text: str) -> LicenseInfo | None:
    """Map a provider's licence wording onto a known licence, or None when unknown."""

    normalized = " ".join(text.lower().split()) if text else ""
    if not normalized:
        return None
    for spdx, label, attribution, commercial, derivatives, spellings in LICENSE_TABLE:
        for spelling in (spdx.lower(), label.lower(), *spellings):
            if normalized == spelling or normalized.startswith(f"{spelling} "):
                return LicenseInfo(
                    spdx=spdx,
                    label=label,
                    attribution_required=attribution,
                    commercial_ok=commercial,
                    derivatives_ok=derivatives,
                )
    return None


def sidecar_text(asset: StagedAsset) -> str:
    """REUSE-style sidecar body for one staged asset."""

    values = {
        "SPDX-FileCopyrightText": asset.attribution or "unknown",
        "SPDX-License-Identifier": asset.spdx,
        "Source-URL": asset.source_url or "unknown",
        "Attribution": asset.attribution
        or (ATTRIBUTION_NOT_RECORDED if asset.attribution_required else ATTRIBUTION_NOT_REQUIRED),
        "Retrieved-At": asset.retrieved_at,
        "SHA256": asset.sha256,
        "Provider": asset.provider or "unknown",
        "Class": asset.class_name,
        "File": asset.relative,
    }
    return "".join(f"{key}: {values[key]}\n" for key in SIDECAR_ORDER)


def _safe_name(name: str) -> str:
    cleaned = UNSAFE_NAME.sub("-", Path(name.split("?")[0]).name).strip("-.")
    return cleaned or "asset.bin"


def _declared_size(response: object) -> int | None:
    """The Content-Length a server advertised, when it advertised one."""

    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if headers is not None else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _download(url: str, target_dir: Path, opener: Callable[..., object], max_bytes: int) -> Path:
    """Fetch into the staging directory, refusing the transfer when the size is already known."""

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            declared = _declared_size(response)
            if declared is not None and declared > max_bytes:
                raise QuotaError(
                    f"{url} advertises {declared} bytes, over budget.max_asset_bytes "
                    f"({max_bytes}); the transfer was refused"
                )
            data = response.read()
    except urllib.error.URLError as error:
        raise LedgerError(f"download failed for {url}: {error}") from error
    if len(data) > max_bytes:
        raise QuotaError(
            f"{url} delivered {len(data)} bytes, over budget.max_asset_bytes ({max_bytes}); "
            "the download was discarded"
        )
    destination = target_dir / _safe_name(url)
    destination.write_bytes(data)
    return destination


def _project_bytes(root: Path) -> int:
    """Total bytes the project holds, ignoring the git object store."""

    total = 0
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        total += path.stat().st_size
    return total


def _iter_sidecars(root: Path) -> list[tuple[Path, dict[str, str]]]:
    """Every sidecar under ``assets/`` with the media file it describes."""

    assets_dir = root / "assets"
    if not assets_dir.is_dir():
        return []
    found: list[tuple[Path, dict[str, str]]] = []
    for path in sorted(assets_dir.rglob("*.license")):
        fields = parse_sidecar_text(path.read_text(encoding="utf-8"))
        found.append((path.with_name(path.name[: -len(".license")]), fields))
    return found


def _staged_by_hash(root: Path) -> dict[str, str]:
    """Map SHA256 to the staged path for every licensed asset already in the project."""

    found: dict[str, str] = {}
    for media, fields in _iter_sidecars(root):
        digest = fields.get("SHA256")
        if not digest or digest in found or not media.is_file():
            continue
        if is_quarantined(fields.get("SPDX-License-Identifier", UNKNOWN_SPDX), fields["Class"]):
            continue
        found[digest] = str(media.relative_to(root))
    return found


def _existing_staged(
    project: Project, relative: str, digest: str, retrieved_at: str
) -> StagedAsset:
    """Rebuild a staging result from the sidecar of an asset that is already in the ledger."""

    path = project.root / relative
    fields = read_sidecar(path)
    spdx = fields.get("SPDX-License-Identifier", UNKNOWN_SPDX)
    info = classify_license(spdx)
    return StagedAsset(
        path=path,
        relative=relative,
        class_name=fields.get("Class", "unknown"),
        sha256=digest,
        size_bytes=path.stat().st_size if path.exists() else 0,
        spdx=spdx,
        license_label=info.label if info else spdx,
        attribution_required=bool(info and info.attribution_required),
        attribution=fields.get("Attribution", ""),
        source_url=fields.get("Source-URL", ""),
        provider=fields.get("Provider", ""),
        retrieved_at=fields.get("Retrieved-At", retrieved_at),
        duplicate_of=relative,
    )


def _quarantined_twin(root: Path, digest: str) -> Path | None:
    """A quarantined copy of the same bytes, if one exists."""

    for media, fields in _iter_sidecars(root):
        if fields.get("SHA256") != digest or not media.is_file():
            continue
        if is_quarantined(fields.get("SPDX-License-Identifier", UNKNOWN_SPDX), fields["Class"]):
            return media
    return None


def stage_asset(
    project: Project,
    *,
    class_name: str,
    license_text: str,
    source_url: str = "",
    attribution: str = "",
    provider: str = "",
    local_file: Path | None = None,
    url: str | None = None,
    expected_sha256: str | None = None,
    retrieved_at: str | None = None,
    allow_noncommercial: bool = False,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> StagedAsset:
    """Copy or download one asset into the project, with its sidecar.

    Deduplicates by SHA256, promotes a previously quarantined copy of the same bytes out of
    quarantine, refuses licences that forbid commercial use or derivatives, and aborts before
    writing inside ``assets/`` when the project budget would be exceeded. Passing
    ``expected_sha256`` skips the transfer entirely when those bytes are already staged.
    """

    if class_name not in (*ASSET_CLASSES, QUARANTINE_CLASS):
        raise LedgerError(
            f"unknown asset class {class_name!r}; use one of {', '.join(ASSET_CLASSES)}"
        )
    if (local_file is None) == (url is None):
        raise LedgerError("provide exactly one of local_file or url")

    info = classify_license(license_text)
    if info is None:
        class_name = QUARANTINE_CLASS
        spdx = UNKNOWN_SPDX
        label = f"unknown licence ({license_text or 'none given'})"
        attribution_required = False
    else:
        if not info.commercial_ok and not allow_noncommercial:
            raise LicensePolicyError(
                f"{info.label} forbids commercial use and cannot go into a deliverable; "
                "pass allow_noncommercial=True only for personal, non-commercial work"
            )
        if not info.derivatives_ok:
            raise LicensePolicyError(
                f"{info.label} forbids derivatives, and any rendered deliverable is a derivative; "
                "pick a licence that allows adaptation"
            )
        if class_name == MUSIC_CLASS and project.budget.require_cc0_music and info.spdx != CC0_SPDX:
            raise LicensePolicyError(
                f"budget.require_cc0_music is set, so only {CC0_SPDX} music may be staged; "
                f"{info.label} was refused"
            )
        spdx, label, attribution_required = info.spdx, info.label, info.attribution_required

    budget = project.budget
    now = retrieved_at or _now()
    staged_paths = _staged_by_hash(project.root)
    if expected_sha256:
        known = staged_paths.get(expected_sha256.lower())
        if known:
            return _existing_staged(project, known, expected_sha256.lower(), now)

    staging_dir = Path(tempfile.mkdtemp(prefix="media-studio-stage-"))
    try:
        if url is not None:
            incoming = _download(url, staging_dir, opener, budget.max_asset_bytes)
        else:
            assert local_file is not None
            incoming = Path(local_file).expanduser()
            if not incoming.is_file():
                raise LedgerError(f"no such file: {incoming}")

        size = incoming.stat().st_size
        if size > budget.max_asset_bytes:
            raise QuotaError(
                f"{size} bytes exceeds budget.max_asset_bytes ({budget.max_asset_bytes}); "
                "raise the budget or pick a smaller asset"
            )

        digest = sha256_of(incoming)
        duplicate_relative = staged_paths.get(digest)
        if duplicate_relative:
            return _existing_staged(project, duplicate_relative, digest, now)

        projected = _project_bytes(project.root) + size
        if projected > budget.max_project_bytes:
            raise QuotaError(
                f"staging would put the project at {projected} bytes, over "
                f"budget.max_project_bytes ({budget.max_project_bytes})"
            )

        twin = _quarantined_twin(project.root, digest)
        if twin is not None:
            twin.unlink(missing_ok=True)
            sidecar_path(twin).unlink(missing_ok=True)

        target_dir = project.root / "assets" / class_name
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_name(incoming.name if local_file is not None else str(url))
        destination = target_dir / filename
        if destination.exists() and sha256_of(destination) != digest:
            destination = target_dir / f"{digest[:8]}-{filename}"
        if url is not None:
            try:
                incoming.replace(destination)
            except OSError as error:
                # The staging directory lives in the system temp, which is often a different
                # filesystem from the project (tmpfs against a home volume), and renaming across
                # devices fails with EXDEV. A move copies and then unlinks instead.
                if error.errno != errno.EXDEV:
                    raise
                shutil.move(str(incoming), str(destination))
        else:
            shutil.copy2(incoming, destination)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    staged = StagedAsset(
        path=destination,
        relative=str(destination.relative_to(project.root)),
        class_name=class_name,
        sha256=digest,
        size_bytes=destination.stat().st_size,
        spdx=spdx,
        license_label=label,
        attribution_required=attribution_required,
        attribution=attribution,
        source_url=source_url,
        provider=provider,
        retrieved_at=now,
        duplicate_of=None,
    )
    sidecar_path(destination).write_text(sidecar_text(staged), encoding="utf-8")
    return staged


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def build_index(project: Project) -> AssetIndex:
    """Derive the asset index from the sidecars on disk."""

    records: list[AssetRecord] = []
    duplicates: dict[str, list[str]] = {}
    unlicensed: list[str] = []

    assets_dir = project.root / "assets"
    if assets_dir.is_dir():
        for path in sorted(assets_dir.rglob("*")):
            if not path.is_file() or path.name.endswith(".license"):
                continue
            if path.name in (INDEX_NAME, CREDITS_NAME):
                continue
            relative = str(path.relative_to(project.root))
            fields = read_sidecar(path)
            if not fields:
                unlicensed.append(relative)
                continue
            spdx = fields.get("SPDX-License-Identifier", UNKNOWN_SPDX)
            info = classify_license(spdx)
            digest = fields.get("SHA256", "")
            duplicates.setdefault(digest, []).append(relative)
            records.append(
                AssetRecord(
                    path=relative,
                    class_name=fields.get("Class", "unknown"),
                    bytes=path.stat().st_size,
                    sha256=digest,
                    spdx=spdx,
                    attribution=fields.get("Attribution", ""),
                    attribution_required=bool(info and info.attribution_required),
                    source_url=fields.get("Source-URL", ""),
                    provider=fields.get("Provider", ""),
                    retrieved_at=fields.get("Retrieved-At", ""),
                )
            )

    return AssetIndex(
        generated_at=_now(),
        slug=project.slug,
        title=project.title,
        assets=tuple(records),
        duplicates={digest: paths for digest, paths in duplicates.items() if len(paths) > 1},
        unlicensed=tuple(sorted(unlicensed)),
        totals=Totals(
            count=len(records),
            bytes=sum(record.bytes for record in records),
            attribution_required=sum(1 for record in records if record.attribution_required),
            quarantined=sum(1 for record in records if record.quarantined),
        ),
        quota=_quota(project.root, project.budget, sum(record.bytes for record in records)),
    )


def _quota(root: Path, budget: Budget, staged_bytes: int) -> Quota:
    project_bytes = _project_bytes(root)
    return Quota(
        max_asset_bytes=budget.max_asset_bytes,
        max_project_bytes=budget.max_project_bytes,
        staged_bytes=staged_bytes,
        project_bytes=project_bytes,
        ok=project_bytes <= budget.max_project_bytes,
    )


def render_credits(index: AssetIndex) -> str:
    """Render ``CREDITS.md`` from an index."""

    credited = [record for record in index.assets if record.attribution_required]
    quarantined = [record for record in index.assets if record.quarantined]
    lines = [
        "# Credits",
        "",
        LICENCE_FOOTER,
        "`ms assets index` instead.",
        "",
        f"Project: {index.title} ({index.slug})",
        f"Assets: {index.totals.count} staged, {len(credited)} requiring attribution",
        f"Generated: {index.generated_at}",
        "",
    ]
    if credited:
        lines.extend(["## Attribution required", ""])
        for record in credited:
            credit = record.attribution or "author not recorded"
            lines.append(f"- {record.path}")
            lines.append(f"  - Credit: {credit}")
            lines.append(f"  - Licence: {record.spdx}")
            if record.source_url:
                lines.append(f"  - Source: {record.source_url}")
        lines.append("")
    else:
        lines.extend(["No staged asset requires attribution.", ""])

    if quarantined:
        lines.extend(
            [
                "## Quarantined: licence unknown",
                "",
                "These files are not referenced by any deliverable. Confirm the licence, then",
                "stage the same bytes again with the correct licence string; the quarantined",
                "copy is replaced automatically.",
                "",
            ]
        )
        lines.extend(f"- {record.path}" for record in quarantined)
        lines.append("")

    if index.unlicensed:
        lines.extend(
            [
                "## Files without a sidecar",
                "",
                "A file with no sidecar is invisible to the ledger. Restage it through",
                "`ms assets stage`.",
                "",
            ]
        )
        lines.extend(f"- {path}" for path in index.unlicensed)
        lines.append("")

    if index.duplicates:
        lines.extend(["## Duplicate files", ""])
        for digest, paths in sorted(index.duplicates.items()):
            lines.append(f"- {digest[:12]}: {', '.join(paths)}")
        lines.append("")
    return "\n".join(lines)


def write_index_and_credits(project: Project) -> tuple[Path, Path, AssetIndex]:
    """Write both derived files, returning their paths and the index."""

    index = build_index(project)
    assets_dir = project.root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    index_path = assets_dir / INDEX_NAME
    credits_path = assets_dir / CREDITS_NAME
    index_path.write_text(json.dumps(index.to_dict(), indent=2) + "\n", encoding="utf-8")
    credits_path.write_text(render_credits(index), encoding="utf-8")
    return index_path, credits_path, index
