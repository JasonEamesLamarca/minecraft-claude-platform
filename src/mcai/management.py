"""Management commands, executed by the gateway for SSH users (the `mcai` CLI and the TUI).

The SSH gateway container only authenticates keys and forwards argv here, with the key that logged in. The
account and its role are looked up from the key on every command, every command checks its permission here,
and changes are audited. Clients never decide what someone may do.

Output: {"ok": bool, "code": str, "text": human-readable, "data": machine-readable (for --json and the TUI)}.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, rbac
from .automation import AutomationError, Definition, DefinitionError, parse_definition, parse_text
from .cron import timezone
from .rbac import Actor
from .rcon import RconError, clean_command
from .sanitize import clean_name
from .sshkeys import KeyError_, parse_public_key

NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")              # Minecraft names
ACCOUNT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\[?[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){3,7}\]?")
MAX_ARGS, MAX_ARG_LEN = 64, 1000


class MgmtError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class _Help(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


class Parser(argparse.ArgumentParser):
    """argparse that never prints or exits: errors and help come back as exceptions."""

    def error(self, message):
        raise MgmtError("usage", f"{message}\n{self.format_usage()}")

    def print_help(self, file=None):
        raise _Help(self.format_help())

    def exit(self, status=0, message=None):
        raise MgmtError("usage", message or "")


@dataclass
class Result:
    text: str
    data: object = None
    ok: bool = True
    code: str = "ok"


@dataclass
class Command:
    path: tuple
    permission: str
    func: object
    help: str
    args: list = field(default_factory=list)
    audited: bool = False            # changes and sensitive reads are always audited; denials too


COMMANDS: list[Command] = []


def command(path: str, permission: str, help: str, *args, audited: bool = False):
    def deco(fn):
        COMMANDS.append(Command(tuple(path.split()), permission, fn, help, list(args), audited))
        return fn
    return deco


def arg(*names, **kw):
    return names, kw


def build_parser() -> Parser:
    root = Parser(prog="mcai", description="Minecraft AI Platform management. Your role decides what you may run "
                                           "(see 'mcai roles').")
    subs = root.add_subparsers(dest="cmd", metavar="<command>")
    groups: dict[str, argparse._SubParsersAction] = {}
    for c in COMMANDS:
        if len(c.path) == 1:
            p = subs.add_parser(c.path[0], help=c.help, description=f"{c.help} (needs: {c.permission})")
        else:
            if c.path[0] not in groups:
                g = subs.add_parser(c.path[0], help=GROUP_HELP.get(c.path[0], ""),
                                    aliases=GROUP_ALIASES.get(c.path[0], []))
                groups[c.path[0]] = g.add_subparsers(dest="sub", metavar="<subcommand>")
            p = groups[c.path[0]].add_parser(c.path[1], help=c.help, description=f"{c.help} (needs: {c.permission})")
        for names, kw in c.args:
            p.add_argument(*names, **kw)
        p.set_defaults(_cmd=c)
    return root


GROUP_HELP = {"players": "players, roles and sessions", "whitelist": "server whitelist", "server": "save, restart, console",
              "ai": "AI requests, approvals, limits", "audit": "audit history", "accounts": "management accounts and SSH keys",
              "backups": "world backups", "automations": "event and scheduled automations (alias: auto)"}
GROUP_ALIASES = {"automations": ["auto"]}


# ---------------------------------------------------------------- helpers
def table(rows: list[list], headers: list[str]) -> str:
    rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join([fmt.format(*headers), fmt.format(*("-" * w for w in widths))] + [fmt.format(*r) for r in rows])


def since_iso(value: str) -> str:
    m = re.fullmatch(r"(\d{1,5})([mhd])", value or "")
    if m:
        delta = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(**{delta: int(m.group(1))})
        return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?", value or ""):
        return value
    raise MgmtError("usage", "--since takes 30m, 24h, 7d or a date like 2026-11-01")


def require_rcon(gw):
    if gw.rcon is None:
        raise MgmtError("unavailable", "The gateway has no RCON access (secrets/rcon_password missing).")
    return gw.rcon


def rcon(gw, cmd: str) -> str:
    try:
        return require_rcon(gw).command(cmd)
    except (RconError, OSError) as e:
        raise MgmtError("unavailable", f"The Minecraft server didn't answer ({e.__class__.__name__}). Is it running?") \
            from None


def find_player(gw, target: str):
    t = target.strip()
    row = gw.store.player(t.lower()) if UUID_RE.match(t.lower()) else gw.store.player_by_name(t)
    if row is None:
        raise MgmtError("not_found", f"No player '{target}' has joined this server yet (players are identified by "
                                     "UUID once they join; whitelist them and let them log in first).")
    return row


def check_assign(actor: Actor, current: str, new: str, what: str) -> None:
    if not rbac.valid_role(new):
        raise MgmtError("usage", f"Unknown role '{new}'. Roles: {', '.join(rbac.ROLES)}")
    if not rbac.may_assign(actor.role, current, new):
        raise MgmtError("forbidden", f"As {actor.role} you can't change {what} from {current} to {new}: you can only "
                                     "change roles below your own, to roles below your own.")


# ---------------------------------------------------------------- general
@command("whoami", "status.read", "your account, role and permissions")
def c_whoami(gw, actor, a, ctx):
    acc = gw.store.account(actor.id)
    linked = gw.store.player(acc["player_uuid"]) if acc["player_uuid"] else None
    perms = sorted(p for p, roles in rbac.PERMISSIONS.items() if actor.role in roles)
    data = {"account": actor.id, "role": actor.role, "key": ctx["fingerprint"], "source_ip": ctx["source_ip"],
            "linked_player": ({"uuid": linked["uuid"], "name": linked["name"], "role": linked["role"]} if linked else None),
            "permissions": perms}
    text = (f"Account {actor.id} ({actor.role}), key {ctx['fingerprint']}, from {ctx['source_ip'] or '?'}\n"
            f"Linked player: {linked['name'] + ' (' + linked['role'] + ')' if linked else 'none (mcai link)'}\n"
            f"Permissions: {', '.join(perms)}")
    return Result(text, data)


@command("roles", "status.read", "which role may do what")
def c_roles(gw, actor, a, ctx):
    rows = [[p, ", ".join(r)] for p, r in rbac.matrix()]
    return Result("Roles, highest first: " + " > ".join(rbac.ROLES) + "\n\n" + table(rows, ["permission", "roles"]),
                  {"roles": list(rbac.ROLES), "permissions": dict(rbac.matrix())})


@command("status", "status.read", "server, players, AI and backups at a glance")
def c_status(gw, actor, a, ctx):
    hb = dict(gw.heartbeat) if gw.mod_online() else {}
    spent = gw.store.spend_today()
    pending = gw.store.with_status(("queued", "running", "proposed", "approved", "rolling_back"))
    data = {
        "version": __version__,
        "server": {"online": bool(hb), **hb, "tps": round(min(20.0, 1000 / hb["mspt"]), 1) if hb.get("mspt") else None},
        "ai": {"enabled": gw.ai_enabled(), "worker_online": gw.worker_online(), "spent_today_usd": round(spent, 4),
               "budget_usd": gw.daily_budget(), "queue": len(gw.jobs), "active": len(pending)},
        "backups": _backup_summary(gw),
    }
    s, ai = data["server"], data["ai"]
    lines = [f"mcai {__version__}"]
    if s["online"]:
        lines.append(f"Server: online, {s.get('tps')} TPS ({s.get('mspt')} ms/tick), heap {s.get('heap_used_mb', 0):.0f}"
                     f"/{s.get('heap_max_mb', 0):.0f} MB, up {int(s.get('uptime_s', 0)) // 60} min")
        lines.append(f"Players online ({len(s['players'])}): {', '.join(s['players']) or '-'}")
    else:
        lines.append("Server: no heartbeat from the mod in the last 90 s (starting, stopped or mod missing)")
    lines.append(f"AI: {'on' if ai['enabled'] else 'OFF'}, worker {'online' if ai['worker_online'] else 'offline'}, "
                 f"${ai['spent_today_usd']:.2f} of ${ai['budget_usd']:.2f} today, {ai['active']} active request(s)")
    b = data["backups"]
    lines.append(f"Backups: {b['count']}, latest {b['latest'] or '-'}" if b else "Backups: folder not mounted")
    return Result("\n".join(lines), data)


def _backup_summary(gw):
    files = _backups(gw)
    if files is None:
        return None
    return {"count": len(files), "latest": files[0]["modified"] if files else None,
            "total_mb": round(sum(f["size_mb"] for f in files), 1)}


def _backups(gw):
    d = gw.s.backups_dir
    if not d or not Path(d).is_dir():
        return None
    out = []
    for p in Path(d).iterdir():
        if p.is_file() and p.name.endswith((".tar.gz", ".tgz", ".tar.zst", ".zip")):
            st = p.stat()
            out.append({"name": p.name, "size_mb": round(st.st_size / 1e6, 1),
                        "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")})
    return sorted(out, key=lambda f: f["modified"], reverse=True)


@command("link", "status.read", "get a code to link your Minecraft player to this account", audited=True)
def c_link(gw, actor, a, ctx):
    code = gw.store.new_link_code(actor.id)
    return Result(f"In game, type: /ai link {code}   (valid 10 minutes). Linking only records who is who; it grants "
                  "no rights.", {"code": code})


# ---------------------------------------------------------------- players
@command("players list", "players.read", "known players with role and last seen")
def c_players(gw, actor, a, ctx):
    rows = gw.store.players()
    data = [{k: r[k] for k in ("uuid", "name", "role", "online", "first_seen", "last_seen")} for r in rows]
    return Result(table([[r["name"], r["role"], "online" if r["online"] else r["last_seen"][:16].replace("T", " "),
                          r["uuid"]] for r in rows], ["name", "role", "last seen", "uuid"]) if rows
                  else "No player has joined yet.", data)


@command("players info", "players.read", "one player's role, sessions and AI use",
         arg("player", help="name or UUID"))
def c_player_info(gw, actor, a, ctx):
    r = find_player(gw, a.player)
    sess = gw.store.sessions(r["uuid"], 5)
    reqs = gw.store.recent_requests(5, r["uuid"])
    data = {"player": dict(r), "sessions": [dict(x) for x in sess], "requests": [_req(x) for x in reqs]}
    text = (f"{r['name']} ({r['uuid']}): role {r['role']}, {'online' if r['online'] else 'offline'}, first seen "
            f"{r['first_seen'][:10]}, last seen {r['last_seen'][:16].replace('T', ' ')}\n"
            f"Recent sessions: " + (", ".join(f"{x['joined_at'][:16].replace('T', ' ')}" for x in sess) or "-") + "\n"
            f"Recent AI requests: " + (", ".join(f"{x['id']} {x['kind']} {x['status']}" for x in reqs) or "-"))
    return Result(text, data)


@command("players sessions", "players.read", "join/leave history",
         arg("player", nargs="?", help="name or UUID (default: everyone)"), arg("-n", type=int, default=30))
def c_sessions(gw, actor, a, ctx):
    uuid_ = find_player(gw, a.player)["uuid"] if a.player else None
    rows = gw.store.sessions(uuid_, max(1, min(a.n, 1000)))

    def dur(r):
        if not r["left_at"]:
            return "online"
        t = (dt.datetime.fromisoformat(r["left_at"].replace("Z", "+00:00"))
             - dt.datetime.fromisoformat(r["joined_at"].replace("Z", "+00:00"))).total_seconds()
        return f"{int(t // 3600)}h{int(t % 3600 // 60):02d}m"
    return Result(table([[r["name"], r["joined_at"][:19].replace("T", " "), (r["left_at"] or "")[:19].replace("T", " "),
                          dur(r)] for r in rows], ["player", "joined (UTC)", "left (UTC)", "duration"]) if rows
                  else "No sessions recorded.", [dict(r) for r in rows])


@command("players role", "roles.assign", "set a player's role (AI and gameplay rights)",
         arg("player", help="name or UUID"), arg("role", choices=rbac.ROLES), audited=True)
def c_player_role(gw, actor, a, ctx):
    r = find_player(gw, a.player)
    if r["role"] == a.role:
        return Result(f"{r['name']} already has the role {a.role}.")
    check_assign(actor, r["role"], a.role, f"{r['name']}'s role")
    gw.store.set_player_role(r["uuid"], a.role)
    gw.store.audit("role.changed", target="player", player_uuid=r["uuid"], player_name=r["name"], old_role=r["role"],
                   new_role=a.role, by=actor.label)
    gw.notify(r["uuid"], f"Your role on this server is now {a.role}.", "info")
    return Result(f"{r['name']}: {r['role']} -> {a.role}", {"uuid": r["uuid"], "role": a.role})


@command("players kick", "players.moderate", "disconnect a player",
         arg("player", help="name"), arg("reason", nargs="*"), audited=True)
def c_kick(gw, actor, a, ctx):
    r = find_player(gw, a.player)
    if rbac.RANK[r["role"]] >= rbac.RANK[actor.role]:
        raise MgmtError("forbidden", f"You can't kick {r['name']} ({r['role']}).")
    reason = clean_command(" ".join(a.reason) or "Kicked by an administrator", 100)
    out = rcon(gw, f"kick {r['name']} {reason}")
    return Result(out or f"Kicked {r['name']}.")


@command("say", "players.moderate", "broadcast a chat message", arg("message", nargs="+"), audited=True)
def c_say(gw, actor, a, ctx):
    rcon(gw, "say " + clean_command(" ".join(a.message), 200))
    return Result("Sent.")


# ---------------------------------------------------------------- whitelist
@command("whitelist list", "players.read", "names on the whitelist")
def c_wl_list(gw, actor, a, ctx):
    out = rcon(gw, "whitelist list")
    names = sorted(n.strip() for n in out.split(":", 1)[1].split(",") if n.strip()) if ":" in out else []
    return Result(out, {"names": names})


def _wl_name(name):
    if not NAME_RE.match(name):
        raise MgmtError("usage", f"'{name}' is not a valid Minecraft name.")
    return name


@command("whitelist add", "players.moderate", "allow a Minecraft account to join", arg("name"), audited=True)
def c_wl_add(gw, actor, a, ctx):
    return Result(rcon(gw, f"whitelist add {_wl_name(a.name)}"))


@command("whitelist remove", "players.moderate", "remove a name from the whitelist", arg("name"), audited=True)
def c_wl_remove(gw, actor, a, ctx):
    row = gw.store.player_by_name(_wl_name(a.name))
    if row and rbac.RANK[row["role"]] >= rbac.RANK[actor.role]:
        raise MgmtError("forbidden", f"You can't remove {row['name']} ({row['role']}) from the whitelist.")
    return Result(rcon(gw, f"whitelist remove {a.name}"))


# ---------------------------------------------------------------- server
@command("server save", "server.control", "flush the world to disk", audited=True)
def c_save(gw, actor, a, ctx):
    return Result(rcon(gw, "save-all flush") or "Saved.")


@command("server restart", "server.control", "restart the Minecraft server (players are warned)",
         arg("--yes", action="store_true", help="required: confirms the restart"),
         arg("--delay", type=int, default=10, help="seconds of warning (0-300)"), audited=True)
def c_restart(gw, actor, a, ctx):
    if not a.yes:
        raise MgmtError("confirm", "This disconnects everyone. Re-run with --yes to restart.")
    delay = max(0, min(a.delay, 300))
    rcon(gw, f"say The server restarts in {delay} seconds.")

    def stop():
        try:
            rcon(gw, "stop")       # the container exits; Docker starts it again (restart: unless-stopped)
        except MgmtError as e:
            gw.store.audit("server.restart_failed", by=actor.label, error=e.message)
    threading.Timer(delay, stop).start()
    return Result(f"Restarting in {delay} s. Docker starts the server again; 'mcai status' shows when it's back.")


@command("server command", "server.console", "run a raw server console command (owner only)",
         arg("command", nargs="+"), audited=True)
def c_console(gw, actor, a, ctx):
    return Result(rcon(gw, clean_command(" ".join(a.command))) or "(no output)")


@command("logs", "logs.read", "recent server log lines",
         arg("-n", type=int, default=100, help="lines (max 2000)"), arg("--grep", default="", help="only lines with this text"),
         arg("--after", default="", help=argparse.SUPPRESS))
def c_logs(gw, actor, a, ctx):
    d = gw.s.mc_logs_dir
    path = Path(d) / "latest.log" if d else None
    if not path or not path.exists():
        raise MgmtError("unavailable", "The server log isn't available to the gateway yet.")
    st = path.stat()
    n = max(1, min(a.n, 2000))
    start = 0
    m = re.fullmatch(r"(\d+):(\d+)", a.after)
    if m and int(m.group(1)) == st.st_ino and int(m.group(2)) <= st.st_size:
        start = int(m.group(2))           # follow mode: only what was written since
    elif st.st_size > 2_000_000:
        start = st.st_size - 2_000_000
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    if start and not m:
        lines = lines[1:]                 # first line may be cut
    if a.grep:
        lines = [x for x in lines if a.grep.lower() in x.lower()]
    lines = lines[-n:] if not m else lines
    if not actor.can("ips.read"):
        lines = [IP_RE.sub("<ip>", x) for x in lines]
    cursor = f"{st.st_ino}:{start + len(data)}"
    return Result("\n".join(lines), {"lines": lines, "cursor": cursor})


@command("backups list", "backups.read", "world backups made by the backup service")
def c_backups(gw, actor, a, ctx):
    files = _backups(gw)
    if files is None:
        raise MgmtError("unavailable", "The backup folder isn't mounted into the gateway.")
    note = "\nBackups and restores run on the host: scripts/backup.sh, scripts/restore.sh (see the admin guide)."
    return Result((table([[f["name"], f["size_mb"], f["modified"]] for f in files[:50]], ["file", "MB", "modified"])
                   if files else "No backups yet.") + note, files)


# ---------------------------------------------------------------- AI
def _req(r) -> dict:
    d = {k: r[k] for k in ("id", "player_uuid", "player_name", "kind", "status", "created_at", "cost_usd", "error",
                           "decided_by")}
    d["title"] = json.loads(r["summary"])["title"] if r["summary"] else None
    d["snapshot"] = r["snapshot"]
    return d


def _req_rows(rows):
    return table([[r["id"], r["created_at"][:16].replace("T", " "), r["player_name"], r["kind"], r["status"],
                   f"${r['cost_usd']:.3f}", (json.loads(r["summary"])["title"] if r["summary"] else "")[:30]]
                  for r in rows], ["id", "created (UTC)", "player", "kind", "status", "cost", "title"])


@command("ai history", "ai.read", "recent AI requests of all players",
         arg("-n", type=int, default=20), arg("--player", help="name or UUID"))
def c_ai_history(gw, actor, a, ctx):
    uuid_ = find_player(gw, a.player)["uuid"] if a.player else None
    rows = gw.store.recent_requests(max(1, min(a.n, 500)), uuid_)
    return Result(_req_rows(rows) if rows else "No AI requests yet.", [_req(r) for r in rows])


@command("ai pending", "ai.read", "requests in progress and builds waiting for approval")
def c_ai_pending(gw, actor, a, ctx):
    rows = gw.store.recent_requests(200, None, ("queued", "running", "proposed", "approved", "rolling_back"))
    return Result(_req_rows(rows) if rows else "Nothing pending.", [_req(r) for r in rows])


@command("ai show", "ai.read", "details of one request, including the build summary", arg("id"))
def c_ai_show(gw, actor, a, ctx):
    r = gw.store.get(a.id.lower()) if re.fullmatch(r"[0-9a-f]{6,12}", a.id.lower()) else None
    if r is None:
        raise MgmtError("not_found", f"No request {a.id}.")
    d = _req(r)
    d.update(summary=json.loads(r["summary"]) if r["summary"] else None, reply=r["reply"], placed=r["placed"],
             text=r["text"])
    lines = [f"{r['id']} {r['kind']} by {r['player_name']}: {r['status']} (created {r['created_at']})",
             f"Request: {r['text']}"]
    if d["summary"]:
        s = d["summary"]
        lines.append(f"Build: {s['title']}, {'x'.join(map(str, s['size']))} at {s['from']}..{s['to']}, "
                     f"{s['block_count']} blocks, placed {r['placed']}, undo snapshot {r['snapshot'] or '-'}")
    if r["reply"]:
        lines.append(f"Reply: {r['reply']}")
    if r["error"]:
        lines.append(f"Error: {r['error']}")
    lines.append(f"Cost ${r['cost_usd']:.4f}" + (f", decided by {r['decided_by']}" if r["decided_by"] else ""))
    return Result("\n".join(lines), d)


def _decision(name):
    def fn(gw, actor, a, ctx):
        r = getattr(gw, name)(actor, a.id.lower())
        return Result(r["message"])
    return fn


command("ai approve", "ai.decide_any", "approve a proposed build (it is built in game)", arg("id"),
        audited=True)(_decision("approve"))
command("ai cancel", "ai.decide_any", "cancel a queued request or proposal", arg("id"), audited=True)(_decision("cancel"))
command("ai undo", "ai.decide_any", "undo a finished build", arg("id"), audited=True)(_decision("undo"))


@command("ai enable", "ai.configure", "switch the AI on", audited=True)
def c_ai_on(gw, actor, a, ctx):
    gw.store.set_setting("ai.enabled", None, actor.label)
    return Result("AI switched on.")


@command("ai disable", "ai.configure", "switch the AI off for everyone (requests are refused)", audited=True)
def c_ai_off(gw, actor, a, ctx):
    gw.store.set_setting("ai.enabled", "false", actor.label)
    return Result("AI switched off. 'mcai ai enable' switches it back on.")


@command("ai budget", "ai.configure", "show or change the daily AI budget (USD)",
         arg("amount", nargs="?", help="e.g. 5.00, or 'default' for AI_DAILY_BUDGET_USD"), audited=True)
def c_ai_budget(gw, actor, a, ctx):
    if a.amount is None:
        return Result(f"Daily budget ${gw.daily_budget():.2f} (default ${gw.s.daily_budget_usd:.2f}), spent today "
                      f"${gw.store.spend_today():.2f}.", {"budget_usd": gw.daily_budget()})
    if a.amount == "default":
        gw.store.set_setting("ai.daily_budget_usd", None, actor.label)
    else:
        try:
            v = float(a.amount)
        except ValueError:
            v = -1
        if not 0 <= v <= 1000:
            raise MgmtError("usage", "The budget must be between 0 and 1000 (USD per UTC day).")
        gw.store.set_setting("ai.daily_budget_usd", f"{v:.2f}", actor.label)
    return Result(f"Daily AI budget is now ${gw.daily_budget():.2f}.")


# ---------------------------------------------------------------- audit
@command("audit show", "audit.read", "audit records (newest last)",
         arg("--since", default="24h", help="30m, 24h, 7d or a date (default 24h)"),
         arg("--event", default="", help="event name prefix, e.g. role. or ssh."),
         arg("--actor", default="", help="only records mentioning this account, UUID or name"),
         arg("-n", type=int, default=200, help="max records (10000)"),
         arg("--format", choices=("text", "jsonl", "csv"), default="text"), audited=True)
def c_audit(gw, actor, a, ctx):
    recs = gw.store.audit_records(since_iso(a.since), a.event, a.actor, max(1, min(a.n, 10_000)))
    if a.format == "jsonl":
        text = "\n".join(json.dumps(r, sort_keys=True) for r in recs)
    elif a.format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ts", "event", "details"])
        for r in recs:
            w.writerow([r.get("ts"), r.get("event"), json.dumps({k: v for k, v in r.items()
                                                                  if k not in ("ts", "event", "prev")}, sort_keys=True)])
        text = buf.getvalue().rstrip("\n")
    else:
        text = "\n".join(f"{r.get('ts', '')[:19].replace('T', ' ')}  {r.get('event', ''):<24} " +
                         " ".join(f"{k}={v}" for k, v in sorted(r.items()) if k not in ("ts", "event", "prev"))
                         for r in recs) or "No matching records."
    return Result(text, recs)


@command("audit verify", "audit.read", "check that no audit record was changed or removed", audited=True)
def c_audit_verify(gw, actor, a, ctx):
    n, problem = gw.store.audit_verify()
    if problem:
        return Result(f"Audit chain BROKEN after {n} good records: {problem}", {"records": n, "problem": problem},
                      ok=False, code="tampered")
    return Result(f"Audit chain intact: {n} records checked.", {"records": n, "problem": None})


# ---------------------------------------------------------------- accounts
def _account(gw, name):
    acc = gw.store.account(name)
    if acc is None:
        raise MgmtError("not_found", f"No account '{name}'.")
    return acc


def _outranks(actor, acc, what):
    if actor.id == acc["name"]:
        return
    if rbac.RANK[actor.role] <= rbac.RANK[acc["role"]] or not actor.can("accounts.manage"):
        raise MgmtError("forbidden", f"You can't {what} account {acc['name']} ({acc['role']}).")


@command("accounts list", "accounts.read", "management accounts")
def c_accounts(gw, actor, a, ctx):
    rows = gw.store.accounts()
    data = [{k: r[k] for k in ("name", "role", "created_at", "created_by", "disabled", "player_uuid", "keys")} for r in rows]
    return Result(table([[r["name"], r["role"], "disabled" if r["disabled"] else "active", r["keys"],
                          r["created_at"][:10], r["created_by"]] for r in rows],
                        ["account", "role", "state", "keys", "created", "by"]), data)


@command("accounts add", "accounts.manage", "create an account with one SSH public key",
         arg("name", help="lowercase letters, digits, - and _"), arg("role", choices=rbac.ROLES),
         arg("key", nargs="+", help="the public key line, e.g. ssh-ed25519 AAAA... alice@laptop"), audited=True)
def c_acc_add(gw, actor, a, ctx):
    if not ACCOUNT_RE.match(a.name):
        raise MgmtError("usage", "Account names: 2-32 lowercase letters, digits, - or _, starting with a letter.")
    if rbac.RANK[a.role] >= rbac.RANK[actor.role]:
        raise MgmtError("forbidden", "You can only create accounts with a role below your own.")
    key = _key(a.key)
    if not gw.store.create_account(a.name, a.role, actor.label):
        raise MgmtError("exists", f"Account {a.name} already exists.")
    if not gw.store.add_key(a.name, key.fingerprint, key.line, key.comment, actor.label):
        gw.store.update_account(a.name, disabled=1)
        raise MgmtError("exists", "That key is already registered (keys can't be reused); the account was created "
                                  "disabled.")
    gw.store.audit("account.created", account=a.name, role=a.role, fingerprint=key.fingerprint, by=actor.label)
    return Result(f"Created {a.name} ({a.role}) with key {key.fingerprint}.", {"account": a.name,
                                                                                  "fingerprint": key.fingerprint})


def _key(parts):
    try:
        return parse_public_key(" ".join(parts))
    except KeyError_ as e:
        raise MgmtError("usage", str(e)) from None


@command("accounts role", "accounts.manage", "change an account's management role",
         arg("name"), arg("role", choices=rbac.ROLES), audited=True)
def c_acc_role(gw, actor, a, ctx):
    acc = _account(gw, a.name)
    if acc["role"] == a.role:
        return Result(f"{a.name} already has the role {a.role}.")
    check_assign(actor, acc["role"], a.role, f"{a.name}'s role")
    gw.store.update_account(a.name, role=a.role)
    gw.store.audit("role.changed", target="account", account=a.name, old_role=acc["role"], new_role=a.role,
                   by=actor.label)
    return Result(f"{a.name}: {acc['role']} -> {a.role}")


def _set_disabled(flag):
    def fn(gw, actor, a, ctx):
        acc = _account(gw, a.name)
        if acc["name"] == actor.id:
            raise MgmtError("forbidden", "You can't disable or enable your own account.")
        _outranks(actor, acc, "change")
        gw.store.update_account(a.name, disabled=int(flag))
        gw.store.audit("account.disabled" if flag else "account.enabled", account=a.name, by=actor.label)
        return Result(f"Account {a.name} {'disabled: its keys stop working within seconds' if flag else 'enabled'}.")
    return fn


command("accounts disable", "accounts.manage", "block an account (keys stay registered)", arg("name"),
        audited=True)(_set_disabled(True))
command("accounts enable", "accounts.manage", "unblock an account", arg("name"), audited=True)(_set_disabled(False))


@command("accounts keys", "status.read", "SSH keys of an account (default: yours)", arg("name", nargs="?"),
         arg("--all", action="store_true", help="include revoked keys"))
def c_keys(gw, actor, a, ctx):
    name = a.name or actor.id
    if name != actor.id and not actor.can("accounts.read"):
        raise MgmtError("forbidden", "You can only list your own keys.")
    _account(gw, name)
    rows = gw.store.keys(name, active_only=not a.all)
    return Result(table([[r["fingerprint"], r["comment"], r["added_at"][:10], r["added_by"],
                          (r["revoked_at"] or "")[:10]] for r in rows], ["fingerprint", "comment", "added", "by", "revoked"]),
                  [{k: r[k] for k in ("fingerprint", "comment", "added_at", "added_by", "revoked_at")} for r in rows])


@command("accounts key-add", "status.read", "add an SSH key (to your account, or to one below your role)",
         arg("name"), arg("key", nargs="+"), audited=True)
def c_key_add(gw, actor, a, ctx):
    acc = _account(gw, a.name)
    _outranks(actor, acc, "add keys to")
    key = _key(a.key)
    if not gw.store.add_key(acc["name"], key.fingerprint, key.line, key.comment, actor.label):
        raise MgmtError("exists", "That key is already registered (or was revoked: revoked keys can't come back).")
    gw.store.audit("account.key_added", account=acc["name"], fingerprint=key.fingerprint, by=actor.label)
    return Result(f"Added {key.fingerprint} to {acc['name']}.")


@command("accounts key-revoke", "status.read", "revoke an SSH key (yours, or of an account below your role)",
         arg("fingerprint", help="SHA256:... (see 'mcai accounts keys')"), audited=True)
def c_key_revoke(gw, actor, a, ctx):
    rows = [r for r in gw.store.keys(None) if r["fingerprint"] == a.fingerprint]
    if not rows:
        raise MgmtError("not_found", "No active key with that fingerprint.")
    acc = _account(gw, rows[0]["account"])
    _outranks(actor, acc, "revoke keys of")
    if acc["name"] == actor.id and a.fingerprint == ctx["fingerprint"] and len(gw.store.keys(acc["name"])) == 1:
        raise MgmtError("forbidden", "That is your only key: add another one first, or you would lock yourself out.")
    gw.store.revoke_key(a.fingerprint)
    gw.store.audit("account.key_revoked", account=acc["name"], fingerprint=a.fingerprint, by=actor.label)
    return Result(f"Revoked {a.fingerprint} ({acc['name']}). It stops working within seconds.")


# ---------------------------------------------------------------- automations
def _when(ts, tz_name: str) -> str:
    if not ts:
        return "-"
    local = dt.datetime.fromtimestamp(ts, timezone(tz_name))
    return local.strftime("%Y-%m-%d %H:%M %Z")


def _definition_input(gw, ctx) -> Definition:
    text = ctx.get("input") or ""
    if not text.strip():
        raise MgmtError("usage", "Give a definition file: 'mcai automations import FILE' (the mcai client sends it). "
                                 "With plain ssh: ssh ... automations import --stdin < FILE")
    try:
        return parse_definition(parse_text(text), gw.automation.default_tz)
    except DefinitionError as e:
        raise MgmtError("invalid", f"Invalid definition: {e}") from None


def _describe(gw, d: Definition, row=None) -> list[str]:
    lines = [f"{d.id}: {d.name}" + (f" - {d.description}" if d.description else ""),
             f"Trigger: {d.trigger_text()}"]
    if d.conditions:
        lines.append("Conditions: " + ", ".join(f"{k}={v}" for k, v in d.conditions.items()))
    for i, a in enumerate(d.actions, 1):
        detail = a.get("text") or a.get("prompt") or " ".join(f"{k}={v}" for k, v in a.items() if k != "type")
        lines.append(f"  {i}. {a['type']}: {str(detail)[:100]}")
    lim = d.limits
    lines.append(f"Limits: timeout {lim['timeout_s']}s, retries {lim['retries']}, on_error {lim['on_error']}, "
                 f"max {lim['max_runs_per_hour']}/h" + (f", cooldown {lim['cooldown_s']}s" if lim["cooldown_s"] else "")
                 + (f", AI ${lim['ai_max_usd_per_day']:.2f}/day" if lim["ai_max_usd_per_day"] else ""))
    lines.append("Needs: " + ", ".join(d.required_permissions)
                 + ("  (PRIVILEGED: the owner must approve it)" if d.privileged else ""))
    if d.schedule:
        nxt = row["next_run_at"] if row is not None and row["state"] == "enabled" else None
        upcoming = [nxt] if nxt else d.schedule.upcoming(gw.automation.now(), 3)
        lines.append(("Next run: " if nxt else "Would run at: ") + ", ".join(_when(t, d.timezone) for t in upcoming))
    return lines


def _auto_rows(gw, rows):
    out = []
    for r in rows:
        try:
            d = gw.automation.definition(r)
            trig = d.trigger_text()
        except (DefinitionError, ValueError):
            d, trig = None, "(invalid)"
        last = gw.store.runs_list(r["id"], 1)
        out.append({"id": r["id"], "name": r["name"], "state": r["state"], "trigger": trig, "owner": r["owner"],
                    "privileged": bool(r["privileged"]), "version": r["version"], "source": r["source"],
                    "next_run": _when(r["next_run_at"], d.timezone) if d and r["next_run_at"] else None,
                    "last_status": last[0]["status"] if last else None,
                    "last_run": _when(last[0]["created"], "UTC") if last else None, "note": r["note"]})
    return out


@command("automations list", "automations.read", "automations, their state and last run",
         arg("--all", action="store_true", help="include rejected and deleted ones"))
def c_auto_list(gw, actor, a, ctx):
    rows = [r for r in gw.store.automations() if a.all or r["state"] not in ("rejected", "deleted")]
    data = _auto_rows(gw, rows)
    if not data:
        return Result("No automations yet. See 'mcai automations examples'.", [])
    return Result(table([[x["id"], x["state"] + ("*" if x["privileged"] else ""), x["trigger"][:40],
                          f"{x['last_status'] or '-'} {x['last_run'] or ''}".strip(), x["next_run"] or "",
                          x["owner"]] for x in data], ["id", "state", "trigger", "last run", "next run", "owner"])
                  + "\n(* privileged: changes roles or the world; the owner approves it)", data)


@command("automations show", "automations.read", "one automation: trigger, conditions, actions, limits, runs",
         arg("id"))
def c_auto_show(gw, actor, a, ctx):
    row = gw.automation._row(a.id)
    d = gw.automation.definition(row)
    lines = [f"State: {row['state']} (version {row['version']}, {row['source']}, owner {row['owner']}"
             + (f", approved by {row['approved_by']}" if row["approved_by"] else "") + ")"]
    if row["note"]:
        lines.append(f"Note: {row['note']}")
    lines += _describe(gw, d, row)
    runs = gw.store.runs_list(row["id"], 5)
    if runs:
        lines.append("Recent runs: " + ", ".join(f"{r['id']} {r['status']} ({_when(r['created'], 'UTC')})" for r in runs))
    return Result("\n".join(lines), {"automation": dict(row), "definition": d.as_dict(),
                                     "permissions": d.required_permissions, "privileged": d.privileged})


@command("automations runs", "automations.read", "execution history", arg("id", nargs="?"),
         arg("-n", type=int, default=20))
def c_auto_runs(gw, actor, a, ctx):
    if a.id:
        gw.automation._row(a.id)
    rows = gw.store.runs_list(a.id, max(1, min(a.n, 500)))
    data = [{k: r[k] for k in ("id", "automation_id", "trigger", "status", "attempt", "created", "finished", "error")}
            | {"output": json.loads(r["output"] or "[]")} for r in rows]
    if not rows:
        return Result("No runs yet.", [])
    return Result(table([[r["id"], r["automation_id"], r["trigger"], r["status"], r["attempt"],
                          _when(r["created"], "UTC"), (r["error"] or "")[:50]] for r in rows],
                        ["run", "automation", "trigger", "status", "try", "created", "error"]), data)


@command("automations export", "automations.read", "the definition as JSON (edit it and import --replace)", arg("id"))
def c_auto_export(gw, actor, a, ctx):
    d = gw.automation.definition(gw.automation._row(a.id))
    return Result(json.dumps(d.as_dict(), indent=2), d.as_dict())


@command("automations validate", "automations.manage", "check a definition file without saving it",
         arg("--stdin", action="store_true", help=argparse.SUPPRESS))
def c_auto_validate(gw, actor, a, ctx):
    d = _definition_input(gw, ctx)
    return Result("Valid.\n" + "\n".join(_describe(gw, d)), {"definition": d.as_dict(),
                                                              "permissions": d.required_permissions,
                                                              "privileged": d.privileged})


@command("automations import", "automations.manage", "create (or with --replace update) an automation from a file",
         arg("--stdin", action="store_true", help=argparse.SUPPRESS),
         arg("--replace", action="store_true", help="replace an existing automation with the same id"), audited=True)
def c_auto_import(gw, actor, a, ctx):
    d = _definition_input(gw, ctx)
    r = gw.automation.save(actor, d, "manual", replace=a.replace)
    hint = {"pending_approval": f" It is privileged: the owner approves it with 'mcai automations approve {d.id}'.",
            "disabled": f" Enable it with 'mcai automations enable {d.id}'."}.get(r["state"], "")
    return Result(f"Saved '{d.id}' (version {r['version']}): {r['state']}.{hint}", r)


@command("automations examples", "automations.read", "example automations shipped with the platform")
def c_auto_examples(gw, actor, a, ctx):
    ex = gw.automation.examples()
    if not ex:
        return Result("No examples found (AUTOMATION_EXAMPLES_DIR).", [])
    rows = [[e.get("id", e["file"]), e.get("trigger", "invalid: " + e.get("error", ""))[:40],
             "yes" if e.get("privileged") else "", e.get("name", "")] for e in ex]
    return Result(table(rows, ["example", "trigger", "privileged", "name"])
                  + "\nInstall one (disabled) with 'mcai automations install <example>'.",
                  [{k: v for k, v in e.items() if k != "definition"} for e in ex])


@command("automations install", "automations.manage", "install an example (disabled until you enable it)",
         arg("example"), arg("--replace", action="store_true"), audited=True)
def c_auto_install(gw, actor, a, ctx):
    ex = {e["id"]: e for e in gw.automation.examples() if "id" in e}
    if a.example not in ex:
        raise MgmtError("not_found", f"No example '{a.example}'. See 'mcai automations examples'.")
    d = ex[a.example]["definition"]
    r = gw.automation.save(actor, d, "example", replace=a.replace)
    return Result(f"Installed '{d.id}': {r['state']}." + (f" Enable it with 'mcai automations enable {d.id}'."
                                                         if r["state"] == "disabled" else
                                                         f" The owner approves it with 'mcai automations approve {d.id}'."
                                                         if r["state"] == "pending_approval" else ""), r)


def _auto_simple(method, **kw):
    def fn(gw, actor, a, ctx):
        return Result(getattr(gw.automation, method)(actor, a.id, **kw))
    return fn


command("automations enable", "automations.manage", "start running an automation", arg("id"),
        audited=True)(_auto_simple("set_enabled", enabled=True))
command("automations disable", "automations.manage", "stop an automation", arg("id"),
        audited=True)(_auto_simple("set_enabled", enabled=False))
command("automations approve", "automations.manage", "approve a proposed or privileged automation (enables it)",
        arg("id"), audited=True)(_auto_simple("approve"))


@command("automations reject", "automations.manage", "reject a proposal", arg("id"), arg("reason", nargs="*"),
         audited=True)
def c_auto_reject(gw, actor, a, ctx):
    return Result(gw.automation.reject(actor, a.id, " ".join(a.reason)))


@command("automations delete", "automations.manage", "delete an automation (history is kept)", arg("id"),
         arg("--yes", action="store_true", help="required: confirms"), audited=True)
def c_auto_delete(gw, actor, a, ctx):
    if not a.yes:
        raise MgmtError("confirm", f"Re-run with --yes to delete '{a.id}'.")
    return Result(gw.automation.delete(actor, a.id))


@command("automations run", "automations.manage", "run an automation now (also a disabled one, to test it)",
         arg("id"), arg("--player", help="name or UUID, for automations that act on a player"), audited=True)
def c_auto_run(gw, actor, a, ctx):
    player = None
    if a.player:
        p = find_player(gw, a.player)
        player = {"player_uuid": p["uuid"], "player_name": p["name"]}
    rid = gw.automation.run_now(actor, a.id, player)
    return Result(f"Started run {rid}. See 'mcai automations runs {a.id}'.", {"run": rid})


@command("automations propose", "automations.manage", "ask the AI to write an automation (you review it first)",
         arg("description", nargs="+"), audited=True)
def c_auto_propose(gw, actor, a, ctx):
    if not actor.can("ai.configure"):
        raise MgmtError("forbidden", "Proposals spend AI budget: they need ai.configure.")
    if not gw.ai_enabled():
        raise MgmtError("unavailable", "The AI is switched off ('mcai ai enable').")
    budget = gw.daily_budget()
    if budget <= 0 or gw.store.spend_today() >= budget:
        raise MgmtError("limited", "The AI has reached today's budget.")
    text = " ".join(a.description).strip()
    if len(text) > 1000:
        raise MgmtError("usage", "Keep the description under 1000 characters.")
    rid = gw.automation.propose(actor, text)
    return Result(f"Asked the AI (request {rid}). In a minute: 'mcai ai show {rid}', then "
                  "'mcai automations list' shows the proposal; nothing runs until you approve it.", {"request_id": rid})


# ---------------------------------------------------------------- entry point
def _params(ns) -> dict:
    out = {}
    for k, v in vars(ns).items():
        if k.startswith("_") or k in ("cmd", "sub", "key"):
            continue
        out[k] = (" ".join(v) if isinstance(v, list) else v)
        if isinstance(out[k], str):
            out[k] = out[k][:200]
    return out


def run(gw, body: dict) -> dict:
    from .gateway import Rejected
    ctx = {"source_ip": str(body.get("source_ip", ""))[:64], "session": str(body.get("session", ""))[:40],
           "fingerprint": "", "input": str(body.get("input", ""))[:65536]}
    try:
        key = parse_public_key(str(body.get("key", "")))
    except KeyError_:
        key = None
    acc = gw.store.key_owner(key.fingerprint) if key else None
    if acc is None:
        gw.store.audit("mgmt.denied", reason="unknown or revoked key", fingerprint=key.fingerprint if key else None,
                       source_ip=ctx["source_ip"])
        return {"ok": False, "code": "unknown_key", "text": "This SSH key is not registered, or it was revoked."}
    ctx["fingerprint"] = key.fingerprint
    actor = Actor("account", acc["name"], acc["name"], acc["role"])
    argv = body.get("argv")
    if not isinstance(argv, list) or len(argv) > MAX_ARGS or not all(isinstance(x, str) and len(x) <= MAX_ARG_LEN
                                                                       for x in argv):
        return {"ok": False, "code": "usage", "text": "Invalid command."}
    if not argv or argv[0] == "help":
        argv = ["--help"] if len(argv) <= 1 else argv[1:] + ["--help"]
    try:
        ns = build_parser().parse_args(argv)
    except _Help as h:
        return {"ok": True, "code": "help", "text": h.text.rstrip()}
    except MgmtError as e:
        return {"ok": False, "code": e.code, "text": e.message.rstrip()}
    cmd: Command | None = getattr(ns, "_cmd", None)
    if cmd is None:              # a group without subcommand, e.g. "mcai players"
        try:
            build_parser().parse_args(argv[:1] + ["--help"])
        except _Help as h:
            return {"ok": True, "code": "help", "text": h.text.rstrip()}
        return {"ok": False, "code": "usage", "text": "Unknown command. Try: mcai help"}
    name = " ".join(cmd.path)
    audit_base = {"account": actor.id, "role": actor.role, "command": name, "session": ctx["session"],
                  "source_ip": ctx["source_ip"]}
    if not actor.can(cmd.permission):
        gw.store.audit("mgmt.denied", **audit_base, permission=cmd.permission)
        return {"ok": False, "code": "forbidden",
                "text": f"'{name}' needs the permission {cmd.permission} (roles: "
                        f"{', '.join(r for r in rbac.ROLES if r in rbac.PERMISSIONS[cmd.permission])}); "
                        f"you are {actor.role}."}
    try:
        res = cmd.func(gw, actor, ns, ctx)
    except MgmtError as e:
        res = Result(e.message, None, False, e.code)
    except Rejected as e:
        res = Result(e.message, None, False, e.code)
    except AutomationError as e:
        res = Result(e.message, None, False, e.code)
    except Exception:
        import logging
        logging.getLogger("mcai.management").exception("management command %s failed", name)
        res = Result("Internal error (see the gateway log).", None, False, "internal")
    if cmd.audited or res.code == "forbidden":
        gw.store.audit("mgmt.command", **audit_base, params=_params(ns), ok=res.ok, code=res.code)
    return {"ok": res.ok, "code": res.code, "text": res.text, "data": res.data}


def authorized_keys(gw) -> list[str]:
    """Lines for the SSH gateway's authorized_keys (all keys run the same forced command; restrict = no PTY,
    no forwarding)."""
    return [f'restrict {r["public_key"]} {r["account"]}' for r in gw.store.active_keys()]


def ssh_event(gw, body: dict) -> dict:
    result = str(body.get("result", ""))
    if result not in ("accepted", "key_rejected", "invalid_user", "closed_preauth", "disconnected", "too_many"):
        return {"ok": False}
    fp = str(body.get("fingerprint", ""))[:80]
    acc = gw.store.key_owner(fp) if fp else None
    gw.store.audit("ssh.auth" if result != "disconnected" else "ssh.disconnect", result=result,
                   user=clean_name(str(body.get("user", "")))[:32], fingerprint=fp or None,
                   account=acc["name"] if acc else None, source_ip=str(body.get("source_ip", ""))[:64])
    return {"ok": True}


def session_event(gw, body: dict) -> dict:
    try:
        key = parse_public_key(str(body.get("key", "")))
        acc = gw.store.key_owner(key.fingerprint)
    except KeyError_:
        key, acc = None, None
    event = "ssh.session_start" if body.get("event") == "start" else "ssh.session_end"
    extra = {}
    if event == "ssh.session_end" and isinstance(body.get("duration_s"), (int, float)):
        extra["duration_s"] = round(float(body["duration_s"]), 1)
    gw.store.audit(event, account=acc["name"] if acc else None, fingerprint=key.fingerprint if key else None,
                   source_ip=str(body.get("source_ip", ""))[:64], session=str(body.get("session", ""))[:40],
                   mode=str(body.get("mode", ""))[:10], **extra)
    return {"ok": True}

