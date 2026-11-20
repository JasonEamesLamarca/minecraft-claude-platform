#!/usr/bin/env bash
# Clean-machine install: follow docs/installation/README.md on a fresh Ubuntu 24.04 host, as a new user would.
#
# The "host" is a privileged Ubuntu container with its own Docker Engine (installed from Docker's apt repository,
# as the guide links to), so nothing from this machine leaks in: no images, no .env, no secrets, no Java, no build
# cache. A non-root user with the docker group runs every step, and the interactive wizard is answered through a
# terminal. The repository arrives as a git bundle of the commit under test (a stand-in for `git clone` while the
# repo is private).
#
# Usage: tests/operational/clean_machine.sh [git-ref]      (default HEAD; needs Docker and internet, ~15 min)
# Keep the host for inspection: KEEP=1 tests/operational/clean_machine.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REF="${1:-HEAD}"
OWNER="${MCAI_TEST_OWNER:-Notch}"   # a real Java account name: the server looks the owner up at Mojang
UBUNTU="ubuntu@sha256:ab18ecc70c55c703eae968948f9f1dbbe662aa8d9badac7c982e6a6237b9d74c"   # 24.04
NAME="mcai-clean-$$"
WORK="$(mktemp -d)"; chmod 755 "$WORK"          # the host's non-root user reads it
cleanup() {
  if [[ "${KEEP:-}" != 1 ]]; then
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$NAME-docker" >/dev/null 2>&1 || true
  else
    echo "kept: docker exec -it $NAME bash   (remove: docker rm -f $NAME; docker volume rm $NAME-docker)"
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

git -C "$ROOT" bundle create "$WORK/repo.bundle" "$REF" 2>/dev/null
COMMIT="$(git -C "$ROOT" rev-parse --short "$REF")"
cat > "$WORK/host.sh" <<'HOST'
#!/bin/bash
# Runs inside the fresh host as root: what the owner of a new Ubuntu machine has, plus Docker per docs.docker.com.
set -euo pipefail
step() { printf '\n==> [%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
export DEBIAN_FRONTEND=noninteractive

step "base system (an Ubuntu Server install already has these)"
apt-get update -qq
apt-get install -y -qq ca-certificates curl git python3 openssh-client sudo expect >/dev/null

step "1. Install Docker (docs.docker.com/engine/install/ubuntu, apt repository)"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
# Nested Docker on cgroup v2: move this host's processes into a child cgroup so dockerd can delegate controllers
# (what the docker:dind image does at start). Not needed on a real machine.
if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then
  mkdir -p /sys/fs/cgroup/init
  xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || true
  sed -e 's/ / +/g' -e 's/^/+/' < /sys/fs/cgroup/cgroup.controllers > /sys/fs/cgroup/cgroup.subtree_control
fi
(dockerd > /var/log/dockerd.log 2>&1 &)          # no systemd in this host; the package would start it
for _ in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
useradd -m -s /bin/bash alex
usermod -aG docker alex                          # the guide's `sudo usermod -aG docker $USER`
su - alex -c 'docker run --rm hello-world >/dev/null && echo "hello-world OK"; docker compose version'

su - alex -c "OWNER=$OWNER bash /in/user.sh"
HOST

cat > "$WORK/user.sh" <<'USER'
#!/bin/bash
# Runs as the new user, following the installation guide step by step.
set -euo pipefail
step() { printf '\n==> [%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/id_ed25519          # the guide: "create one with ssh-keygen"

step "2. Get the platform"
git clone -q /in/repo.bundle minecraft-claude-platform
cd minecraft-claude-platform
git log --oneline -1

step "3. Run the setup wizard (interactive, answered through a terminal)"
cat > /tmp/wizard.exp <<'EXP'
set timeout 1800
log_user 1
spawn scripts/setup.sh
expect "accept the Minecraft EULA"       { send "yes\r" }
expect "Minecraft Java username"         { send "$env(OWNER)\r" }
expect "Game port"                       { send "\r" }
expect "Java heap"                       { send "2G\r" }
expect "Choose 1, 2 or 3"                { send "3\r" }
expect "other computers on your network" { send "\r" }
expect "Public key file"                 { send "\r" }
expect "Start the server now"            { send "\r" }
expect {
  timeout { puts "\nwizard timed out"; exit 1 }
  eof
}
catch wait result
exit [lindex $result 3]
EXP
OWNER="$OWNER" expect /tmp/wizard.exp | tee /tmp/wizard.log

step "4. Start and check"
scripts/wait-healthy.sh minecraft 900
scripts/doctor.sh
running="$(docker compose ps --services --status running | sort | tr '\n' ' ')"
echo "running: $running"
for s in ai-worker backup egress-proxy gateway minecraft ssh-gateway; do
  [[ " $running " == *" $s "* ]] || { echo "FAIL: $s is not running"; exit 1; }
done

step "Test administration: the mcai remote add line the wizard printed, then mcai status"
add="$(tr -d '\r' < /tmp/wizard.log | sed 's/\x1b\[[0-9;]*m//g' | grep -A1 'mcai remote add' | tr -d '\\\n' | sed 's/^ *//')"
echo "$add"
eval "$add"
management-cli/mcai status
management-cli/mcai players list

step "Versions"
docker version --format 'Docker Engine {{.Server.Version}}'
docker compose version
docker compose images --format json 2>/dev/null | python3 -c 'import json,sys; [print(i["Repository"]+":"+i["Tag"]) for i in json.load(sys.stdin)]' || docker compose images
echo "CLEAN MACHINE INSTALL PASSED"
USER

chmod 755 "$WORK"/*.sh; chmod 644 "$WORK/repo.bundle"
echo "clean-machine install of $COMMIT in a fresh Ubuntu 24.04 host ($NAME)"
docker volume create "$NAME-docker" >/dev/null
docker run -d --privileged --cgroupns=private --name "$NAME" --hostname cleanhost -v "$NAME-docker:/var/lib/docker" \
  -v "$WORK:/in:ro" "$UBUNTU" sleep infinity >/dev/null
start=$(date +%s)
docker exec -e OWNER="$OWNER" "$NAME" bash /in/host.sh
echo "total: $(( ($(date +%s) - start) / 60 )) min"
