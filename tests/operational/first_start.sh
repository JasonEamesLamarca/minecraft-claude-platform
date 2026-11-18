#!/usr/bin/env bash
# Operational test: first-time deployment, AI ask/build, SSH administration (roles, revocation, isolation),
# automations, backup, restore and crash recovery, end to end.
# Runs in a throw-away copy of the repo, so the real .env, secrets and data are never touched.
# Needs Docker + internet (downloads Minecraft and mods). Takes ~3-6 minutes.
# Usage: tests/operational/first_start.sh [workdir]     (workdir defaults to a new temp dir; it is left behind for inspection)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK"
PORT="${MCAI_TEST_PORT:-25599}"
OWNER="${MCAI_TEST_OWNER:-Notch}"   # any real Java account name: the image resolves it to a UUID for the whitelist
SSH_PORT="${MCAI_TEST_SSH_PORT:-22299}"
export COMPOSE_PROJECT_NAME="mcai-optest"
step() { printf '\n### %s\n' "$*"; }
cleanup() {
  local rc=$?
  if (( rc != 0 )); then
    echo "### FAILED (exit $rc) - recent service logs:"
    (cd "$WORK" && docker compose ps -a && docker compose logs --no-color --tail 25 gateway ai-worker ssh-gateway minecraft) || true
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

step "setup wizard (non-interactive; builds the server mod) with the mock AI provider and an owner SSH key"
mkdir -p .optest-keys && chmod 700 .optest-keys
for k in owner ann; do ssh-keygen -q -t ed25519 -N '' -C "optest-$k" -f ".optest-keys/$k"; done
scripts/setup.sh --non-interactive --accept-eula --owner "$OWNER" --port "$PORT" --memory 1G --ai-auth none \
  --ssh-key .optest-keys/owner.pub
sed -i.bak "s/^AI_PROVIDER=.*/AI_PROVIDER=mock/; s/^MCAI_SSH_PORT=.*/MCAI_SSH_PORT=$SSH_PORT/" .env && rm -f .env.bak
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
AUDIT() { cat data/gateway/audit/audit-*.jsonl; }
AUDIT | grep -q '"event": "request.done"'
if AUDIT | grep -q 'optest question'; then echo "request text leaked into audit log"; exit 1; fi

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
AUDIT | grep -q '"event": "build.rolled_back"'
echo "ok: build $rid placed, verified and undone"
for _ in $(seq 1 20); do [[ -f "data/minecraft/world/generated/mcai/structures/undo/$rid.nbt" ]] || break; sleep 1; done
if [[ -f "data/minecraft/world/generated/mcai/structures/undo/$rid.nbt" ]]; then echo "undo snapshot not pruned"; exit 1; fi
echo "ok: undo snapshot of $rid deleted after the undo"

step "AI build: the gateway rejects a forbidden block from the AI (mock TNT plan)"
sleep 21   # per-player rate limit (3/min) for the console identity
R "ai build MOCK_BAD_BUILD"
for _ in $(seq 1 20); do docker compose logs --since 1m minecraft | grep -q "plan was rejected: action 0: tnt is not allowed" && break; sleep 1; done
docker compose logs --since 1m minecraft | grep -q "plan was rejected: action 0: tnt is not allowed"
echo "ok: forbidden block rejected"

step "SSH administration: owner over the mcai CLI, auditor is read-only, escalation refused, revocation works"
export XDG_CONFIG_HOME="$WORK/.optest-cli"
M() { management-cli/mcai "$@"; }   # pipe into grep >/dev/null, not grep -q: an early exit would SIGPIPE ssh (255)
hostkey="$(docker compose exec -T ssh-gateway python3 -m mcai.sshgw hostkey)"
M remote add owner localhost --port "$SSH_PORT" --identity .optest-keys/owner --host-key "$hostkey"
M remote add ann localhost --port "$SSH_PORT" --identity .optest-keys/ann --host-key "$hostkey"
M whoami | grep >/dev/null '^Account owner (owner)'
for _ in $(seq 1 40); do M status | grep >/dev/null '^Server: online' && break; sleep 3; done   # heartbeat every 30 s
M status | grep >/dev/null '^Server: online'
M whitelist list | grep >/dev/null "$OWNER"
M logs -n 20 | grep >/dev/null 'mcai'
M accounts add ann auditor "$(cat .optest-keys/ann.pub)"
for _ in $(seq 1 15); do M -r ann status >/dev/null 2>&1 && break; sleep 1; done   # keys reach sshd within 5 s
M -r ann status >/dev/null
M -r ann audit show --since 1h >/dev/null
for cmd in "whitelist add Eve" "players role $OWNER player" "server restart --yes" "ai disable" "server command op Eve"; do
  set +e; out="$(M -r ann $cmd 2>&1)"; rc=$?; set -e
  [[ $rc -eq 4 && "$out" == *"needs the permission"* ]] || { echo "auditor could run '$cmd' (rc=$rc): $out"; exit 1; }
done
echo "ok: auditor refused for every change"
SSHO=(-F /dev/null -i .optest-keys/owner -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes
      -o "UserKnownHostsFile=$XDG_CONFIG_HOME/mcai/known_hosts" -o HostKeyAlias=mcai-owner -p "$SSH_PORT")
out="$(ssh "${SSHO[@]}" -tt mcai@localhost bash -i </dev/null 2>&1 || true)"   # ssh exits 255: capture first
[[ "$out" == *"PTY allocation request failed"* ]] || { echo "PTY allowed! $out"; exit 1; }
echo "ok: no PTY"
ssh "${SSHO[@]}" -N -L 18099:gateway:8080 mcai@localhost & fwd=$!; sleep 3
if curl -s -m 3 http://127.0.0.1:18099/healthz >/dev/null; then echo "port forwarding works!"; exit 1; fi
kill $fwd 2>/dev/null || true; wait $fwd 2>/dev/null || true; echo "ok: no port forwarding"
out="$(ssh "${SSHO[@]}" mcai@localhost 'whoami; id' 2>&1 || true)"
[[ "$out" == *"invalid choice"* ]] || { echo "unexpected: $out"; exit 1; }
echo "ok: no shell, commands are only mcai arguments"
fp="$(M -r ann whoami | grep -oE 'SHA256:[A-Za-z0-9+/]+' | head -1)"
M accounts key-revoke "$fp"
for _ in $(seq 1 15); do set +e; M -r ann status >/dev/null 2>&1; rc=$?; set -e; [[ $rc -eq 255 ]] && break; sleep 1; done
[[ $rc -eq 255 ]] || { echo "revoked key still accepted by sshd (rc=$rc)"; exit 1; }
echo "ok: revoked key refused by sshd"
M audit verify | grep >/dev/null 'Audit chain intact'
AUDIT | grep -q '"result": "accepted"'
AUDIT | grep -q '"event": "account.key_revoked"'
AUDIT | grep -q '"event": "mgmt.denied"'

step "automations: definition file over SSH, manual run reaches the game, world event from the heartbeat"
cat > .optest-auto.toml <<'TOML'
id = "optest-hello"
name = "Optest hello"
enabled = true
[trigger]
event = "server.started"
[[actions]]
type = "broadcast"
text = "optest automation ran"
TOML
M automations validate .optest-auto.toml | grep >/dev/null '^Valid'
M automations import .optest-auto.toml | grep >/dev/null "Saved 'optest-hello' (version 1): enabled"
M automations run optest-hello >/dev/null
for _ in $(seq 1 15); do docker compose logs --since 1m minecraft | grep -q '(broadcast) optest automation ran' && break; sleep 1; done
docker compose logs --since 1m minecraft | grep -q '(broadcast) optest automation ran'
printf 'id = "optest-bad"\nname = "Bad"\n[trigger]\nevery = "1h"\n[[actions]]\ntype = "command"\ncommand = "op Eve"\n' > .optest-bad.toml
set +e; out="$(M automations import .optest-bad.toml 2>&1)"; set -e
[[ "$out" == *"can't run commands"* ]] || { echo "forbidden action accepted: $out"; exit 1; }
M automations examples | grep >/dev/null 'promote-builders'
M automations install night-reminder >/dev/null
printf 'id = "optest-night"\nname = "Night"\nenabled = true\n[trigger]\nevent = "world.night_start"\n[[actions]]\ntype = "log"\ntext = "night fell"\n' > .optest-night.toml
M automations import .optest-night.toml >/dev/null
R "time set 1000"; sleep 35; R "time set 13500"            # the heartbeat (every 30 s) carries the world time
for _ in $(seq 1 25); do AUDIT | grep -q '"message": "night fell"' && break; sleep 2; done
AUDIT | grep -q '"message": "night fell"' || { echo "night automation did not run"; exit 1; }
M automations runs | grep >/dev/null 'optest-night'
if docker compose logs minecraft | grep -qi 'mixin.*\(error\|fail\)'; then echo "mixin problem in server log"; exit 1; fi
echo "ok: automations imported, run, refused and triggered by a world event"

step "examples: Atlantis Lite through the mcai client (local build), checked, installed, rolled back"
scripts/build-examples.sh lite >/dev/null            # deterministic: matches examples/catalog.json -> official
M examples download atlantis-lite --from dist/examples | grep >/dev/null 'ready to install'
M examples list | grep -E >/dev/null 'atlantis-lite +1\.0\.0 +ready '
M automations examples | grep >/dev/null 'atlantis-tour'
AX=2000; AY=-56; AZ=2000                               # far from spawn, recommended floor height
# probe blocks: a few distinctive cells of the pack, as world coordinates "x y z block"
probes="$(python3 - "$AX" "$AY" "$AZ" <<'PY'
import json, sys
sys.path.insert(0, "examples/atlantis/generator")
from atlantis import schem
ax, ay, az = map(int, sys.argv[1:])
m = json.load(open("dist/examples/atlantis-lite/manifest.json"))
want = {"minecraft:sea_lantern", "minecraft:quartz_block", "minecraft:chiseled_quartz_block"}
out = []
for sec in m["sections"][::2]:
    s, states, cells = schem.decode(open("dist/examples/atlantis-lite/" + sec["file"], "rb").read())
    w, h, l = sec["size"]
    for i in range(0, len(cells), 9973):
        st = states[cells[i]]
        if st in want:
            x, z, y = i % w, (i // w) % l, i // (w * l)
            ox, oy, oz = sec["offset"]
            out.append(f"{ax + ox + x} {ay + oy + y} {az + oz + z} {st}")
            break
print("\n".join(out))
PY
)"
[[ $(wc -l <<<"$probes") -ge 2 ]] || { echo "no probe blocks found: $probes"; exit 1; }
probe_count() { local n=0; while read -r x y z b; do is_block "$x" "$y" "$z" "$b" </dev/null && n=$((n+1)); done <<<"$probes"; echo $n; }
while read -r x y z b; do R "forceload add $x $z" </dev/null >/dev/null; done <<<"$probes"; sleep 3   # exec -T would eat the loop input
[[ $(probe_count) -eq 0 ]] || { echo "probe blocks already there before installing"; exit 1; }
M examples install atlantis-lite --at $AX $AY $AZ | grep >/dev/null 'Re-run with --yes'
M examples install atlantis-lite --at $AX $AY $AZ --yes | grep >/dev/null 'Started install'
iid="$(M --json examples installs | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')"
set +e; out="$(M examples install atlantis-lite --at $AX $AY $AZ --yes 2>&1)"; set -e
[[ "$out" == *"is placing"* || "$out" == *"overlap"* ]] || { echo "second install not refused: $out"; exit 1; }
for _ in $(seq 1 180); do M examples status "$iid" | grep >/dev/null "atlantis-lite 1.0.0, placed" && break; sleep 5; done
M examples status "$iid"
M examples status "$iid" | grep >/dev/null "atlantis-lite 1.0.0, placed" || { echo "install did not finish"; exit 1; }
[[ $(probe_count) -eq $(wc -l <<<"$probes") ]] || { echo "pack blocks missing after install"; exit 1; }
echo "ok: Atlantis Lite installed ($(wc -l <<<"$probes") probe blocks present)"
M examples rollback "$iid" --yes | grep >/dev/null 'Rolling back'
for _ in $(seq 1 120); do M examples status "$iid" | grep >/dev/null ", rolled_back" && break; sleep 5; done
M examples status "$iid" | grep >/dev/null ", rolled_back" || { echo "rollback did not finish"; exit 1; }
[[ $(probe_count) -eq 0 ]] || { echo "pack blocks still there after rollback"; exit 1; }
AUDIT | grep -q '"event": "examples.rolled_back"'
while read -r x y z b; do R "forceload remove $x $z" </dev/null >/dev/null; done <<<"$probes"
echo "ok: rolled back, the area is as before"

step "isolation: only the game port and the SSH gateway (localhost) are published; the AI worker has no host mounts"
bindings ssh-gateway | grep -q "\"HostIp\":\"127.0.0.1\",\"HostPort\":\"$SSH_PORT\"" \
  || { echo "ssh-gateway binding: $(bindings ssh-gateway)"; exit 1; }
smounts="$(docker inspect "$(docker compose ps -q ssh-gateway)" --format '{{range .Mounts}}{{.Destination}};{{end}}')"
[[ "$smounts" == *"/data;"* && "$smounts" != *docker.sock* && "$smounts" != *ai_credential* ]] || { echo "ssh-gateway mounts: $smounts"; exit 1; }
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
