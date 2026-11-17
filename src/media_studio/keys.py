"""Provider credentials.

The key wizard writes ``~/.config/opencode/secrets.env`` and nothing exports it into a login
shell, so the toolkit checks the environment first and then reads that file directly. Values are
never logged, printed, or written into a report: callers get the value, and anything user-facing
gets :func:`mask`.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR_VARIABLE = "OPENCODE_CONFIG_DIR"
SECRETS_NAME = "secrets.env"
DEFAULT_CONFIG_DIR = "~/.config/opencode"

# Which credential belongs to which capability, and whether the toolkit needs it.
REQUIRED_KEYS = ("PIXABAY_API_KEY", "FREESOUND_API_KEY", "JAMENDO_CLIENT_ID", "SKETCHFAB_API_TOKEN")
OPTIONAL_KEYS = ("UNSPLASH_ACCESS_KEY", "BLENDERKIT_API_KEY", "GEMINI_API_KEY", "HF_TOKEN")
DEFERRED_KEYS = ("PEXELS_API_KEY",)


@dataclass(frozen=True)
class KeyStatus:
    """Presence of one credential, without its value."""

    name: str
    present: bool
    source: str
    masked: str = ""

    @property
    def requirement(self) -> str:
        if self.name in DEFERRED_KEYS:
            return "deferred"
        if self.name in OPTIONAL_KEYS:
            return "optional"
        return "required"


def config_directory() -> Path:
    """The OpenCode config directory, honouring the same override the wizard uses."""

    override = os.environ.get(CONFIG_DIR_VARIABLE)
    return Path(override).expanduser() if override else Path(DEFAULT_CONFIG_DIR).expanduser()


def secrets_file() -> Path:
    """Path of the secrets file, which may not exist."""

    return config_directory() / SECRETS_NAME


def parse_secrets(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines, ignoring comments, blanks, and surrounding quotes."""

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value:
            values[name] = value
    return values


def load_secrets() -> dict[str, str]:
    """Read the secrets file, returning an empty mapping when it is absent or unreadable."""

    path = secrets_file()
    try:
        return parse_secrets(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def key_for(name: str) -> str | None:
    """The value of one credential: environment first, then the secrets file."""

    from_environment = os.environ.get(name, "").strip()
    if from_environment:
        return from_environment
    return load_secrets().get(name)


def mask(value: str) -> str:
    """A shape-only rendering of a secret: never enough to reconstruct it."""

    if not value:
        return ""
    if len(value) <= 8:
        return f"set ({len(value)} chars)"
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)"


def key_status(names: Sequence[str] = ()) -> list[KeyStatus]:
    """Presence of each credential, for the doctor report and the selftest."""

    secrets = load_secrets()
    wanted = list(names) if names else [*REQUIRED_KEYS, *OPTIONAL_KEYS, *DEFERRED_KEYS]
    statuses: list[KeyStatus] = []
    for name in wanted:
        from_environment = os.environ.get(name, "").strip()
        value = from_environment or secrets.get(name, "")
        source = "environment" if from_environment else ("secrets.env" if value else "absent")
        statuses.append(
            KeyStatus(
                name=name,
                present=bool(value),
                source=source,
                masked=mask(value) if value else "",
            )
        )
    return statuses


def shadowed_by_environment(name: str) -> bool:
    """True when the environment supplies a value and the secrets file holds a different one.

    A stale export silently wins over a freshly pasted value, which is how a valid credential can
    keep being reported as refused while the file looks correct. Neither value is returned.
    """

    from_environment = os.environ.get(name, "").strip()
    if not from_environment:
        return False
    from_file = load_secrets().get(name, "").strip()
    return bool(from_file) and from_file != from_environment


def missing_required_keys(names: Sequence[str] = REQUIRED_KEYS) -> list[str]:
    """Required credentials that are not available."""

    return [status.name for status in key_status(names) if not status.present]
