#!/usr/bin/env bash
# Wait until a Compose service reports "healthy". Usage: scripts/wait-healthy.sh <service> [timeout-seconds]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
svc="${1:?service name}"; timeout="${2:-600}"; start=$SECONDS; last=""
while :; do
  cid="$(docker compose ps -q "$svc" 2>/dev/null || true)"
  state="$( [[ -n "$cid" ]] && docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo missing)"
  [[ "$state" != "$last" ]] && { printf '  %s: %s (%ss)\n' "$svc" "$state" "$((SECONDS - start))"; last="$state"; }
  case "$state" in
    running/healthy) exit 0 ;;
    exited/*|dead/*) echo "✘ $svc stopped. Last log lines:" >&2; docker compose logs --tail 40 "$svc" >&2; exit 1 ;;
  esac
  if (( SECONDS - start > timeout )); then
    echo "✘ $svc not healthy after ${timeout}s. Last log lines:" >&2; docker compose logs --tail 40 "$svc" >&2; exit 1
  fi
  sleep 5
done
