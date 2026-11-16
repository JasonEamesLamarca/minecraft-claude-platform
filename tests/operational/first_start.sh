#!/usr/bin/env bash
# Operational test: first-time deployment, backup, restore and crash recovery, end to end.
# Runs in a throw-away copy of the repo, so the real .env, secrets and data are never touched.
# Needs Docker + internet (downloads Minecraft and mods). Takes ~3-6 minutes.
# Usage: tests/operational/first_start.sh [workdir]     (workdir defaults to a new temp dir; it is left behind for inspection)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK"
PORT="${MCAI_TEST_PORT:-25599}"
OWNER="${MCAI_TEST_OWNER:-Notch}"   # any real Java account name: the image resolves it to a UUID for the whitelist
export COMPOSE_PROJECT_NAME="mcai-optest"
step() { printf '\n### %s\n' "$*"; }
cleanup() {
  local rc=$?
  if (( rc != 0 )); then
    echo "### FAILED (exit $rc) - recent service logs:"
    (cd "$WORK" && docker compose ps -a && docker compose logs --no-color --tail 25 gateway ai-worker minecraft) || true
  fi
  (cd "$WORK" && docker compose down --timeout 60 >/dev/null 2>&1) || true
}
trap cleanup EXIT

step "copy the repository (tracked + new, non-ignored files) to $WORK"
(cd "$REPO" && git ls-files -z --cached --others --exclude-standard \
   | while IFS= read -r -d '' f; do [[ -e "$f" ]] && printf '%s\0' "$f"; done \
   | tar --null -T - -cf -) | tar -xf - -C "$WORK"
if compgen -G "$REPO/dist/mod/mcai-fabric-*.jar" >/dev/null; then
  mkdir -p "$WORK/dist/mod"; cp "$REPO"/dist/mod/mcai-fabric-*.jar "$WORK/dist/mod/"
  echo "using prebuilt mod: $(basename "$(ls "$WORK"/dist/mod/*.jar)")"
fi
cd "$WORK"

step "setup wizard (non-interactive; builds the server mod) with the mock AI provider"
scripts/setup.sh --non-interactive --accept-eula --owner "$OWNER" --port "$PORT" --memory 1G --ai-auth none
sed -i.bak 's/^AI_PROVIDER=.*/AI_PROVIDER=mock/' .env && rm -f .env.bak
docker compose up -d --build
scripts/wait-healthy.sh minecraft 900

R() { docker compose exec -T minecraft rcon-cli "$@"; }
# Host port bindings of a service ("{}" = nothing published). Note: `docker compose port` prints ":0" and
# succeeds for ports an image merely EXPOSEs, so it can't be used for this check.
bindings() { docker inspect "$(docker compose ps -q "$1")" --format '{{json .HostConfig.PortBindings}}'; }
has_marker() { R "execute if block 0 -60 0 minecraft:diamond_block run forceload query 0 0" | grep -q "marked for force loading"; }

step "whitelist + ops contain the owner by UUID"
grep -q '"uuid"' data/minecraft/whitelist.json && grep -q '"level": 4' data/minecraft/ops.json

step "RCON is not reachable from the host's published ports"
if bindings minecraft | grep -q '25575'; then echo "RCON published!"; exit 1; fi
bindings minecraft | grep -q '"25565/tcp"' && echo "ok: only the game port is published on minecraft"

step "AI round trip: /ai ask from the console -> mod -> gateway -> worker (mock) -> mod"
docker compose logs minecraft | grep -q '\[mcai\] ready: /ai'
R "ai ask optest question"
for _ in $(seq 1 20); do
  docker compose logs --since 2m minecraft | grep -q '\[AI\] \[mock\] You asked: optest question' && break; sleep 1
done
docker compose logs --since 2m minecraft | grep -q '\[AI\] \[mock\] You asked: optest question'
grep -q '"event": "request.done"' data/gateway/audit/audit.jsonl
if grep -q 'optest question' data/gateway/audit/audit.jsonl; then echo "request text leaked into audit log"; exit 1; fi

step "AI build: /ai build -> preview -> /ai approve -> blocks placed -> /ai undo -> area restored (mock plan at spawn)"
R "ai build a test pillar"
prev=""
for _ in $(seq 1 20); do
  prev="$(docker compose logs --since 2m minecraft | grep -o 'Proposal [0-9a-f]\{6\}: Mock pillar.*' | tail -1)"; [[ -n "$prev" ]] && break; sleep 1
done
[[ -n "$prev" ]] || { echo "no build preview"; exit 1; }
rid="$(echo "$prev" | grep -oE 'Proposal [0-9a-f]{6}' | cut -d' ' -f2)"
read -r x1 y1 z1 _ y2 _ < <(echo "$prev" | grep -oE 'at -?[0-9]+ -?[0-9]+ -?[0-9]+ \.\. -?[0-9]+ -?[0-9]+ -?[0-9]+' | sed 's/^at //; s/ \.\. / /')
is_block() { R "execute if block $1 $2 $3 $4 run seed" | grep -q Seed; }
if is_block "$x1" "$y1" "$z1" minecraft:stone_bricks; then echo "pillar present before approval"; exit 1; fi
R "ai approve $rid"
for _ in $(seq 1 20); do is_block "$x1" "$y1" "$z1" minecraft:stone_bricks && break; sleep 1; done
is_block "$x1" "$y1" "$z1" minecraft:stone_bricks && is_block "$((x1 + 1))" "$y2" "$((z1 + 1))" minecraft:torch
is_block "$((x1 + 1))" "$((y1 + 1))" "$((z1 + 1))" minecraft:air          # hollow inside
test -f "data/minecraft/world/generated/mcai/structures/undo/$rid.nbt"
R "ai undo $rid"
for _ in $(seq 1 20); do is_block "$x1" "$y1" "$z1" minecraft:stone_bricks || break; sleep 1; done
if is_block "$x1" "$y1" "$z1" minecraft:stone_bricks; then echo "undo did not restore the area"; exit 1; fi
grep -q '"event": "build.rolled_back"' data/gateway/audit/audit.jsonl
echo "ok: build $rid placed, verified and undone"

step "AI build: the gateway rejects a forbidden block from the AI (mock TNT plan)"
sleep 21   # per-player rate limit (3/min) for the console identity
R "ai build MOCK_BAD_BUILD"
for _ in $(seq 1 20); do docker compose logs --since 1m minecraft | grep -q "plan was rejected: action 0: tnt is not allowed" && break; sleep 1; done
docker compose logs --since 1m minecraft | grep -q "plan was rejected: action 0: tnt is not allowed"
echo "ok: forbidden block rejected"

step "isolation: only the game port is published; the AI worker has no host mounts"
for svc in gateway ai-worker backup; do
  [[ "$(bindings "$svc")" == "{}" ]] || { echo "$svc publishes ports: $(bindings "$svc")"; exit 1; }
done
echo "ok: gateway, ai-worker and backup publish nothing"
# Compose implements file secrets as read-only bind mounts; anything else (world data, docker.sock...) fails.
mounts="$(docker inspect "$(docker compose ps -q ai-worker)" --format '{{range .Mounts}}{{.Destination}} rw={{.RW}};{{end}}')"
[[ "$mounts" == "/run/secrets/worker_token rw=false;/run/secrets/ai_credential rw=false;" \
  || "$mounts" == "/run/secrets/ai_credential rw=false;/run/secrets/worker_token rw=false;" ]] \
  || { echo "unexpected ai-worker mounts: $mounts"; exit 1; }
echo "ok: ai-worker mounts only its two secrets, read-only"
docker compose exec -T ai-worker sh -c 'test ! -e /data && test ! -S /var/run/docker.sock' && echo "ok: no world data / docker socket in ai-worker"

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
