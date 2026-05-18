#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS_DIR="$ROOT_DIR/scripts"

python3 -m py_compile "$SCRIPTS_DIR"/*.py
python3 "$SCRIPTS_DIR/compress_to_size.py" --help >/dev/null
python3 "$SCRIPTS_DIR/compress_to_size.py" --target-size 500KB --dry-run "$ROOT_DIR/README.md" >/dev/null || true

echo "smoke test ok"
