#!/usr/bin/env bash
# Take a backup now (world saving is paused and flushed around the copy). Usage: scripts/backup.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose exec -T backup backup now
dir="$(grep -E '^BACKUP_DIR=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
ls -lht "${dir:-./backups}" | head -6
