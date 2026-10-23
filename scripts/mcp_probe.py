#!/usr/bin/env python3
"""Probe a stdio MCP server: initialize, list tools, print a JSON summary.

Used by ``scripts/selftest.sh`` to prove the 3D asset server answers over stdio.
Exit status is 0 only when the expected tools are present.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading

EXPECTED_TOOLS = {"search_assets", "get_asset", "download_asset", "list_providers"}
TIMEOUT_SECONDS = 25


def probe(command: list[str]) -> dict[str, object]:
    """Start ``command``, perform an MCP handshake, and return what it advertised."""

    # The command is supplied by the local operator, never by remote input.
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "media-studio-selftest", "version": "0.1.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    for request in requests:
        process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()

    lines: list[str] = []

    def reader() -> None:
        for line in process.stdout:
            stripped = line.strip()
            if stripped:
                lines.append(stripped)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(timeout=TIMEOUT_SECONDS)

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()

    summary: dict[str, object] = {"tools": []}
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("id") == 1:
            result = payload.get("result", {})
            summary["server"] = result.get("serverInfo", {})
            summary["protocol"] = result.get("protocolVersion")
        elif payload.get("id") == 2:
            summary["tools"] = [
                tool.get("name") for tool in payload.get("result", {}).get("tools", [])
            ]

    if not lines:
        stderr = process.stderr.read() if process.stderr else ""
        summary["error"] = f"no responses: {stderr.strip()[:400]}"
    return summary


def main(argv: list[str]) -> int:
    """Run the probe and print its summary."""

    if len(argv) < 2:
        print("usage: mcp_probe.py <command> [args...]", file=sys.stderr)
        return 2
    summary = probe(argv[1:])
    print(json.dumps(summary, indent=2))
    tools = {name for name in summary.get("tools", []) if isinstance(name, str)}
    missing = EXPECTED_TOOLS - tools
    if missing:
        print(f"missing expected tools: {sorted(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
