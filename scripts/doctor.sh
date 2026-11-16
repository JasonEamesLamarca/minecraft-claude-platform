#!/usr/bin/env bash
# Diagnostics: prerequisites, configuration, service health, recent errors. Never prints secret values.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
rc=0
section() { printf '\n== %s ==\n' "$*"; }
fail() { printf '✘ %s\n' "$*"; rc=1; }
section "Prerequisites"; scripts/setup.sh --check || rc=1
section "Configuration"
[[ -f .env ]] && echo "✔ .env present (mode $(stat -c %a .env 2>/dev/null || stat -f %Lp .env))" || fail ".env missing - run scripts/setup.sh"
for s in rcon_password gateway_mod_token; do
  [[ -s secrets/$s ]] && echo "✔ secrets/$s present" || fail "secrets/$s missing - run scripts/setup.sh"
done
m="$(grep -E '^AI_AUTH_METHOD=' .env 2>/dev/null | cut -d= -f2)"; echo "  AI auth method: ${m:-not set}"
docker compose config --quiet && echo "✔ compose.yaml valid" || fail "compose.yaml invalid with current .env"
section "Services"; docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}\t{{.Ports}}' || rc=1
section "Resource usage"
mapfile -t ids < <(docker compose ps -q)
if (( ${#ids[@]} )); then docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' "${ids[@]}"; fi
section "Recent warnings/errors (minecraft)"
docker compose logs --no-color --tail 300 minecraft 2>/dev/null | grep -E 'WARN|ERROR|Exception' | tail -15 || echo "(none)"
exit $rc
