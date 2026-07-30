#!/usr/bin/env bash
# Backup Android Agent durable state (data / workspaces / builds).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-$ROOT/backups/agent-backup-$STAMP.tar.gz}"
mkdir -p "$(dirname "$OUT")"

echo "Backing up to $OUT"
tar -czf "$OUT" \
  -C "$ROOT" \
  --exclude='data/*.db-shm' \
  --exclude='data/*.db-wal' \
  data workspaces builds config.yaml.example 2>/dev/null || \
tar -czf "$OUT" -C "$ROOT" data workspaces builds

# Also dump SQLite consistency check when agent.db exists.
if [[ -f "$ROOT/data/agent.db" ]]; then
  python3 - <<PY
import sqlite3
from pathlib import Path
db = Path("$ROOT/data/agent.db")
con = sqlite3.connect(db)
print("integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0])
con.close()
PY
fi

echo "OK: $OUT"
ls -lh "$OUT"
