"""Host-only administration (scripts/set-role.sh): actions the API deliberately refuses, such as making someone
owner or recovering a locked-out account. Runs inside the gateway container via `docker compose exec`, so only
people with control of the Docker host can use it. Every change is audited as by "host"."""
from __future__ import annotations

import sys
from pathlib import Path

from .config import GatewaySettings
from .rbac import ROLES
from .store import Store

USAGE = "usage: set-role player <name|uuid> <role> | set-role account <name> <role>"


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] != "set-role" or argv[1] not in ("player", "account") or argv[3] not in ROLES:
        print(USAGE + "\nroles: " + ", ".join(ROLES), file=sys.stderr)
        return 2
    _, target, who, role = argv
    store = Store(Path(GatewaySettings.from_env().data_dir))
    if target == "player":
        row = store.player(who.lower()) or store.player_by_name(who)
        if row is None:
            print(f"No player '{who}' has joined yet.", file=sys.stderr)
            return 1
        store.set_player_role(row["uuid"], role)
        store.audit("role.changed", target="player", player_uuid=row["uuid"], player_name=row["name"],
                    old_role=row["role"], new_role=role, by="host")
        print(f"{row['name']}: {row['role']} -> {role}")
    else:
        acc = store.account(who)
        if acc is None:
            print(f"No account '{who}'.", file=sys.stderr)
            return 1
        store.update_account(who, role=role, disabled=0)
        store.audit("role.changed", target="account", account=who, old_role=acc["role"], new_role=role, by="host")
        print(f"{who}: {acc['role']} -> {role} (enabled)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
