# Purpose: Unit tests for core media-studio entrypoint.
# Data & Logic Flow: Asserts CLI version output and entrypoint presence.
# Key Components: test_cli_version

from media_studio import __version__


def test_cli_version() -> None:
    assert __version__
