#!/usr/bin/env bash
# Headless selftest for the media-studio capability.
#
#   scripts/selftest.sh          run every implemented check
#   scripts/selftest.sh --list   show the full acceptance matrix, run nothing
#   scripts/selftest.sh --ledger run only the asset ledger roundtrip
#
# Exit status is 0 only when no executed check failed. Phase 1 checks are
# listed as pending and never counted as passes.
set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ASSET_SERVER_ENTRY="${HOME}/.clone/Github/3d-asset-server/dist/cli.js"
LOG_DIR="${TMPDIR:-/tmp}/media-studio-selftest"
FAILED=0
SKIPPED=0
mkdir -p "$LOG_DIR"

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

relay_failure() {
  local log=$1
  echo
  echo "  --- last 25 lines of $log ---"
  tail -n 25 "$log" | sed 's/^/  /'
}

check() {
  local name=$1
  shift
  local log="$LOG_DIR/${name// /-}.log"
  printf '%-30s' "$name"
  if "$@" >"$log" 2>&1; then
    echo "ok"
    return 0
  fi
  echo "FAIL"
  relay_failure "$log"
  FAILED=$((FAILED + 1))
  return 0
}

skip() {
  printf '%-30s SKIP (%s)\n' "$1" "$2"
  SKIPPED=$((SKIPPED + 1))
}

ledger_roundtrip() {
  local work="$LOG_DIR/ledger"
  rm -rf "$work"
  mkdir -p "$work/incoming"
  printf 'glTF-payload' >"$work/incoming/tree.glb"
  "$PYTHON" -m media_studio.cli project init selftest-ledger --root "$work" >/dev/null
  "$PYTHON" -m media_studio.cli assets stage "$work/selftest-ledger" --class 3d \
    --file "$work/incoming/tree.glb" --license "CC BY 4.0" \
    --attribution "Selftest Author" --source-url "https://example.org/tree" >/dev/null
  "$PYTHON" -m media_studio.cli project validate "$work/selftest-ledger" >/dev/null
  "$PYTHON" -m media_studio.cli assets index "$work/selftest-ledger" >/dev/null
  grep -q "Selftest Author" "$work/selftest-ledger/assets/CREDITS.md"
  grep -q '"spdx": "CC-BY-4.0"' "$work/selftest-ledger/assets/index.json"
  grep -q "SPDX-License-Identifier: CC-BY-4.0" "$work/selftest-ledger/assets/3d/tree.glb.license"
}

matrix() {
  cat <<'MATRIX'
Acceptance matrix
  phase 0  python tests                                 implemented
  phase 0  ruff lint and format                          implemented
  phase 0  shellcheck on scripts                         implemented
  phase 0  toolchain report (ms doctor)                  implemented
  phase 0  3D asset MCP stdio handshake                  implemented
  phase 1  manifest validation (ms project validate)     implemented
  phase 1  asset ledger: stage, index, CREDITS           implemented
  phase 1  asset search: keyless plus keyed sources      implemented (offline tests; live run 2026-10-24, Jamendo 2026-10-26)
  phase 1  video render: MLT, melt, loudness, ducking    implemented (offline tests; live render 2026-10-24)
  phase 1  Kdenlive handoff                              failed  (measured 2026-10-26: Incorrect project file)
  phase 1  3D render through Blender CLI                 implemented (offline tests; live render 2026-10-25)
  phase 1  material and HDRI binding                     implemented (offline tests; live bind 2026-10-25)
  phase 1  footage triage funnel                         implemented (offline tests; live index 2026-10-25)
  phase 1  preset registry                               implemented (offline tests; 12 recipes, 2026-10-26)
  phase 1  design render through Inkscape                implemented (offline tests; live render 2026-10-25)
  phase 1  scr2026 baseline reproduction                 pending
MATRIX
}

if [[ ${1:-} == "--list" ]]; then
  matrix
  exit 0
fi

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  usage
  exit 0
fi

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "error: no python3 found" >&2
  exit 1
fi

cd "$REPO_ROOT" || exit 1

if [[ ${1:-} == "--ledger" ]]; then
  if ledger_roundtrip; then
    echo "asset ledger roundtrip ok"
    exit 0
  fi
  echo "asset ledger roundtrip FAILED" >&2
  exit 1
fi

check "python tests" "$PYTHON" -m pytest
check "ruff check" ruff check .
check "ruff format --check" ruff format --check .
check "shellcheck" shellcheck scripts/*.sh
check "toolchain report" "$PYTHON" -m media_studio.cli doctor --json
check "asset ledger roundtrip" ledger_roundtrip

if [[ -f "$ASSET_SERVER_ENTRY" ]]; then
  check "3D asset MCP handshake" "$PYTHON" scripts/mcp_probe.py node "$ASSET_SERVER_ENTRY" mcp
else
  skip "3D asset MCP handshake" "build $ASSET_SERVER_ENTRY first"
fi

echo
matrix
echo
if [[ $FAILED -eq 0 ]]; then
  echo "result: all executed checks passed, ${SKIPPED} skipped"
  exit 0
fi
echo "result: $FAILED check(s) failed, ${SKIPPED} skipped"
exit 1
