#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KSCH_BIN="${KSCH_BIN:-ksch}"

if ! command -v "$KSCH_BIN" >/dev/null 2>&1; then
  echo "ksch executable not found. Install kicad-schema or set KSCH_BIN=/path/to/ksch." >&2
  exit 127
fi

"$KSCH_BIN" gen --config "$ROOT/ksch.toml"
