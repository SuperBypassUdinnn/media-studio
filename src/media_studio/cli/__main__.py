"""Module execution entrypoint for ``python -m media_studio.cli``."""

from __future__ import annotations

from . import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
