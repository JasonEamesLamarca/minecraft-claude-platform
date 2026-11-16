#!/usr/bin/env bash
# Operational test: first-time deployment, backup, restore and crash recovery, end to end.
# Runs in a throw-away copy of the repo, so the real .env, secrets and data are never touched.
# Needs Docker + internet (downloads Minecraft and mods). Takes ~3-6 minutes.
# Usage: tests/operational/first_start.sh [workdir]     (workdir defaults to a new temp dir; it is left behind for inspection)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:-$(mktemp -d)}"
PORT="${MCAI_TEST_PORT:-25599}"
OWNER="${MCAI_TEST_OWNER:-Notch}"   # any real Java account name: the image resolves it to a UUID for the whitelist
export COMPOSE_PROJECT_NAME="mcai-optest"
step() { printf '\n### %s\n' "$*"; }
cleanup() { (cd "$WORK" && docker compose down --timeout 60 >/dev/null 2>&1) || true; }
trap cleanup EXIT

step "copy deployment files to $WORK"
cp "$REPO/compose.yaml" "$REPO/.env.example" "$WORK/"; cp -r "$REPO/scripts" "$WORK/"
cd "$WORK"

step "setup wizard (non-interactive) + first start"
scripts/setup.sh --non-interactive --accept-eula --owner "$OWNER" --port "$PORT" --memory 1G --ai-auth none --start

R() { docker compose exec -T minecraft rcon-cli "$@"; }
has_marker() { R "execute if block 0 -60 0 minecraft:diamond_block run forceload query 0 0" | grep -q "marked for force loading"; }

step "whitelist + ops contain the owner by UUID"
grep -q '"uuid"' data/minecraft/whitelist.json && grep -q '"level": 4' data/minecraft/ops.json

step "RCON is not reachable from the host's published ports"
docker compose port minecraft 25575 >/dev/null 2>&1 && { echo "RCON published!"; exit 1; } || echo "ok: 25575 not published"

step "place marker, back up"
R "forceload add 0 0"; sleep 2; R "setblock 0 -60 0 minecraft:diamond_block"; has_marker
scripts/backup.sh

step "break marker, restore latest backup, marker is back"
R "setblock 0 -60 0 minecraft:air"
if has_marker; then echo "marker still present after breaking it"; exit 1; fi
scripts/restore.sh latest --yes
has_marker

step "crash recovery: kill the server process, Docker restarts it, world persists"
docker compose exec -T minecraft sh -c 'kill -9 $(pgrep java)' || true
sleep 10
scripts/wait-healthy.sh minecraft 600
has_marker

step "doctor"
scripts/doctor.sh

printf '\nOPERATIONAL TEST PASSED\n'
