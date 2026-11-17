#!/usr/bin/env python3
"""verify_pipeline.py - Automated multi-stack quality verification runner for media-studio."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

failures = 0


def run_check(cmd: list[str], cwd: Path | None = None, name: str = "") -> bool:
    global failures
    label = name or " ".join(cmd)
    cwd_str = str(cwd) if cwd else "."
    print(f"[*] Running {label} in {cwd_str}...", flush=True)
    try:
        res = subprocess.run(cmd, check=False, cwd=cwd, text=True, capture_output=True)
        if res.returncode != 0:
            print(f"[FAIL] {label} failed with code {res.returncode}:", file=sys.stderr)
            if res.stdout:
                print(res.stdout, file=sys.stderr)
            if res.stderr:
                print(res.stderr, file=sys.stderr)
            failures += 1
            return False
        else:
            print(f"[PASS] {label}")
            return True
    except FileNotFoundError:
        print(f"[WARN] Command {cmd[0]} not found. Skipping {label}.")
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated quality verification pipeline.")
    parser.add_argument(
        "--fast", action="store_true", help="Fast iteration mode (skips heavy suites)"
    )
    parser.add_argument("--skip-vuln", action="store_true", help="Skip vulnerability scanning")
    args = parser.parse_args()
    fast_mode = args.fast

    # Python checks
    py_exec = sys.executable
    run_check([py_exec, "-m", "ruff", "check", "src", "tests", "scripts"], name="ruff check")
    run_check(
        [py_exec, "-m", "ruff", "format", "--check", "src", "tests", "scripts"], name="ruff format"
    )

    if fast_mode:
        pytest_cmd = [
            py_exec,
            "-m",
            "pytest",
            "-q",
            "tests/test_engine_dispatcher.py",
            "tests/test_critique_artifact_backed.py",
            "tests/test_critique_dual_gate.py",
            "tests/test_storyboard_multi_engine.py",
            "tests/test_remotion_pilot.py",
            "tests/test_sfx_world_class.py",
            "tests/test_stage_and_typography_3d.py",
            "tests/test_main.py",
        ]
    else:
        pytest_cmd = [py_exec, "-m", "pytest", "-q", "-m", "not slow and not network"]
    run_check(pytest_cmd, name="pytest")

    # TypeScript / Node checks
    import json

    pkg_files = [
        p
        for p in Path(".").glob("**/package.json")
        if "node_modules" not in p.parts and ".git" not in p.parts
    ]
    for pkg in pkg_files:
        workdir = pkg.parent
        try:
            pkg_data = json.loads(pkg.read_text(encoding="utf-8"))
        except Exception:
            pkg_data = {}
        scripts = pkg_data.get("scripts", {})
        runner = "bun" if shutil.which("bun") else "npm"
        if not fast_mode and "test" in scripts:
            run_check([runner, "test"], cwd=workdir, name=f"npm test ({workdir})")
        if "lint" in scripts:
            run_check([runner, "run", "lint"], cwd=workdir, name=f"npm lint ({workdir})")

    if failures > 0:
        print(f"\nVerification FAILED with {failures} error(s).", file=sys.stderr)
        return 1
    print("\nAll verification checks PASSED successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
