#!/usr/bin/env bash
# Snapshot the same AGENT_*_DIR paths as the service, including SQLite WALs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "${PYTHON:-python3}" "$ROOT/scripts/backup_data.py" "$@"
