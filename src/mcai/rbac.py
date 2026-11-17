"""Roles and permissions (one vocabulary for players and management accounts; assignments are separate).

Players are identified by their Minecraft UUID; management accounts by name + SSH key fingerprints. A linked
account and player share nothing but the link: gameplay, AI and infrastructure rights are granted separately.
The gateway checks every permission itself; clients only display what they are told.
"""
from __future__ import annotations

from dataclasses import dataclass

ROLES = ("owner", "admin", "operator", "builder", "player", "auditor")
RANK = {"owner": 100, "admin": 80, "operator": 60, "builder": 40, "player": 20, "auditor": 10}
DEFAULT_PLAYER_ROLE = "player"

_ALL = set(ROLES)
_STAFF = {"owner", "admin", "operator"}
_ADMINS = {"owner", "admin"}
_READERS = _STAFF | {"auditor"}

PERMISSIONS: dict[str, set[str]] = {
    # gameplay / AI (players in game, or accounts acting on players' requests)
    "ai.ask": _ALL - {"auditor"},
    "ai.build": _STAFF | {"builder"},
    "ai.decide_any": set(_STAFF),          # approve / cancel / undo other players' requests
    # management (SSH)
    "status.read": set(_ALL),
    "players.read": set(_READERS),
    "players.moderate": set(_STAFF),       # whitelist, kick, say
    "roles.assign": set(_ADMINS),
    "logs.read": set(_READERS),
    "ai.read": set(_READERS),              # all players' AI history and pending requests
    "ai.configure": set(_ADMINS),          # enable/disable, daily budget
    "audit.read": _ADMINS | {"auditor"},
    "backups.read": set(_READERS),
    "server.control": set(_ADMINS),        # save, restart
    "server.console": {"owner"},           # raw server console commands
    "accounts.read": _ADMINS | {"auditor"},
    "accounts.manage": set(_ADMINS),
    "ips.read": set(_ADMINS),              # unmasked IP addresses in logs
}


def valid_role(role: str) -> bool:
    return role in RANK


def can(role: str | None, permission: str) -> bool:
    if permission not in PERMISSIONS:
        raise KeyError(f"unknown permission {permission}")
    return role in PERMISSIONS[permission]


def may_assign(actor_role: str, current_role: str, new_role: str) -> bool:
    """Only to someone strictly below you, and only to a role strictly below yours. So nobody changes their own
    role, nobody can demote an equal, and owners are made on the host (see docs), never through the API."""
    if not (valid_role(new_role) and valid_role(current_role) and can(actor_role, "roles.assign")):
        return False
    return RANK[actor_role] > RANK[current_role] and RANK[actor_role] > RANK[new_role]


@dataclass(frozen=True)
class Actor:
    """Who is asking: a player (by UUID), the server console, or a management account (by name)."""
    kind: str          # "player" | "console" | "account"
    id: str            # player UUID or account name
    name: str
    role: str

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.name}" if self.kind != "player" else self.id

    def can(self, permission: str) -> bool:
        return can(self.role, permission)


def matrix() -> list[tuple[str, list[str]]]:
    """(permission, roles in rank order) for display."""
    return [(p, [r for r in ROLES if r in roles]) for p, roles in PERMISSIONS.items()]
