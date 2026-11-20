#!/usr/bin/env bash
# Restore the server data from a backup archive made by the backup service.
# Usage: scripts/restore.sh <backup.tar.gz | latest> [--yes]
# The current data folder is kept as <data>.before-restore-<UTC timestamp> - nothing is deleted.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
envval() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }
DATA="$(envval MC_DATA_DIR)"; DATA="${DATA:-./data/minecraft}"
BACKUPS="$(envval BACKUP_DIR)"; BACKUPS="${BACKUPS:-./backups}"
src="${1:?usage: scripts/restore.sh <backup.tar.gz|latest> [--yes]}"; yes="${2:-}"
if [[ "$src" == latest ]]; then
  # shellcheck disable=SC2012  # backup names are generated (no spaces); ls -t is portable (GNU/BSD)
  src="$(ls -t "$BACKUPS"/*.tar.gz "$BACKUPS"/*.tgz 2>/dev/null | head -1 || true)"; [[ -n "$src" ]] || { echo "No backups in $BACKUPS" >&2; exit 1; }
fi
[[ -f "$src" ]] || { echo "No such file: $src" >&2; exit 1; }
tar tzf "$src" >/dev/null || { echo "$src is not a readable gzip tar archive" >&2; exit 1; }
# refuse archives with absolute paths or .. components (path traversal). Not 'grep -q': it exits at the first
# match, tar dies of SIGPIPE, and with pipefail the pipeline would fail, which reads as "nothing unsafe".
if tar tzf "$src" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null; then echo "Unsafe paths in $src - refusing." >&2; exit 1; fi
echo "Restore $src into $DATA? The server is stopped while restoring; the current data is kept as a copy."
[[ "$yes" == --yes ]] || { read -r -p "Type 'restore' to continue: " a; [[ "$a" == restore ]] || { echo "Cancelled."; exit 1; }; }
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
docker compose stop backup minecraft
mv "$DATA" "$DATA.before-restore-$stamp"
mkdir -p "$DATA"
tar xzf "$src" -C "$DATA" --no-same-owner
echo "Restored. Previous data: $DATA.before-restore-$stamp"
docker compose up -d
scripts/wait-healthy.sh minecraft 900
