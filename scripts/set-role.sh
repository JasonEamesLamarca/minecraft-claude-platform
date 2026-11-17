#!/usr/bin/env bash
# Host-only role changes that the management API refuses on purpose (e.g. making someone owner, or recovering an
# account). Audited as by "host".
#   scripts/set-role.sh player <name|uuid> <role>
#   scripts/set-role.sh account <name> <role>
# Roles: owner admin operator builder player auditor
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -eq 3 ]] || { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
cd "$ROOT" && exec docker compose exec -T gateway python3 -m mcai.hostadmin set-role "$1" "$2" "$3"
