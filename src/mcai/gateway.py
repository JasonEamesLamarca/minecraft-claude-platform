"""Integration gateway: the only component that talks to both the Minecraft mod and the AI worker.

Mod API (Bearer gateway_mod_token):
  POST /v1/mod/requests            {player_uuid, player_name, kind:"ask"|"build", text, context}
  POST /v1/mod/approve             {player_uuid, player_name, request_id?}  build a proposed plan
  POST /v1/mod/cancel              {player_uuid, player_name, request_id?}  drop a queued/proposed request
  POST /v1/mod/undo                {player_uuid, player_name, request_id?}  restore the snapshot of a build
  POST /v1/mod/link                {player_uuid, player_name, code}         link a management account
  POST /v1/mod/players             {event: join|leave, player_uuid, player_name}
  POST /v1/mod/heartbeat           {players: [{uuid, name}], mspt, heap_used_mb, heap_max_mb, uptime_s, version,
                                    world: {time, raining, thundering}}  -> {areas: [...]} to watch for automations
  POST /v1/mod/game-events         {type: area_enter|area_leave|advancement|trigger, event_id, player_uuid, ...}
  POST /v1/mod/builds/<id>/report  {status: invalid|built|failed|rolled_back|rollback_failed|pruned, placed, error}
  GET  /v1/mod/events?wait=25      long-poll: messages, previews, execute/rollback/prune orders for the mod
  GET  /v1/mod/status?player_uuid=...
  GET  /v1/mod/history?player_uuid=...
Worker API (Bearer worker_token):
  GET  /v1/worker/jobs?wait=25     long-poll: next job
  POST /v1/worker/jobs/<id>/result {ok, text, plan?, error, error_code, cost_usd, input_tokens, output_tokens, provider}
Management API (Bearer management_token, used only by the SSH gateway container; see management.py):
  POST /v1/mgmt/run                {key, source_ip, session, argv}         one management command
  GET  /v1/mgmt/authorized-keys                                            active SSH keys
  POST /v1/mgmt/ssh-event          {result, user, fingerprint, source_ip}  sshd authentication results
  POST /v1/mgmt/session            {event: start|end, key, source_ip, session, duration_s}
Health: GET /healthz (no auth, no data)

Roles (rbac.py) are looked up by player UUID or account; the mod's own claims are never trusted.
Each token only opens its own API. The gateway re-validates every plan (it never trusts the worker) and only
orders a build after an authorized approval. The mod places blocks itself through the game's API: no commands.
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import management
from .automation import Engine
from .config import GatewaySettings, setup_logging
from .plan import Limits, PlanError, validate
from .ratelimit import RateLimiter
from .rbac import Actor
from .sanitize import clean_name, clean_player_text, clean_reply
from .store import Store, utcnow

log = logging.getLogger("mcai.gateway")
MAX_BODY = 16 * 1024
KINDS = {"ask", "build"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RID_RE = re.compile(r"^[0-9a-f]{6,12}$")
CONSOLE_UUID = "00000000-0000-0000-0000-000000000000"
CONTEXT_KEYS = {"dimension": str, "x": int, "y": int, "z": int, "yaw": int, "game_mode": str}
GENERIC_FAILURE = "Sorry, the AI could not answer that right now."
REPORT_STATUSES = {"invalid", "built", "failed", "rolled_back", "rollback_failed", "pruned"}
CONSOLE = Actor("console", CONSOLE_UUID, "Server", "owner")   # the console already has full host access


class Rejected(Exception):
    def __init__(self, status: int, message: str, code: str):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


def clean_context(ctx) -> dict:
    """Keep only known, typed, bounded context fields (they reach the prompt)."""
    out = {}
    if isinstance(ctx, dict):
        for k, typ in CONTEXT_KEYS.items():
            v = ctx.get(k)
            if typ is int and isinstance(v, int) and not isinstance(v, bool) and -30_000_000 <= v <= 30_000_000:
                out[k] = v
            elif typ is str and isinstance(v, str) and re.fullmatch(r"[a-z0-9_:./-]{1,64}", v):
                out[k] = v
    return out


def _age_s(iso: str | None) -> float:
    if not iso:
        return 0.0
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds()


class Gateway:
    def __init__(self, settings: GatewaySettings, store: Store, clock=time.monotonic, wallclock=time.time):
        self.s = settings
        self.store = store
        self.clock = clock
        self.limiter = RateLimiter(settings.player_requests_per_minute, clock=clock)
        self.cond = threading.Condition()
        self.jobs: deque[str] = deque()
        self.inflight: dict[str, float] = {}           # AI jobs: request id -> deadline (monotonic)
        self.executing: dict[str, float] = {}          # builds/rollbacks ordered from the mod -> deadline
        self.events: deque[dict] = deque(maxlen=1000)  # consumed by the mod
        self.last_worker_seen = 0.0
        self.heartbeat: dict = {}                      # latest server stats from the mod
        self.heartbeat_at = 0.0
        self.pruning: dict[str, float] = {}            # snapshot prune orders -> re-send after (monotonic)
        self.next_retention = 0.0
        self.rcon = None
        if settings.rcon_password:
            from .rcon import Rcon
            self.rcon = Rcon(settings.rcon_host, settings.rcon_port, settings.rcon_password)
        self._stop = threading.Event()
        self.import_bootstrap_keys()
        for row in store.fail_unfinished("gateway restarted"):
            store.audit("request.interrupted", request_id=row["id"], player_uuid=row["player_uuid"],
                        previous_status=row["status"], reason="gateway restarted")
        from .examples import Examples
        self.examples = Examples(self, Path(settings.data_dir) / "examples", settings.examples_catalog,
                                 blocks_per_tick=settings.examples_blocks_per_tick,
                                 max_file_bytes=settings.examples_max_file_mb * 1024 * 1024,
                                 max_install_blocks=settings.examples_max_install_blocks, clock=clock)
        self.automation = Engine(self, now=wallclock, default_tz=settings.automation_timezone,
                                 max_runs_per_minute=settings.automation_max_runs_per_minute,
                                 ai_calls_per_hour=settings.automation_ai_calls_per_hour,
                                 examples_dir=settings.automation_examples_dir,
                                 extra_example_dirs=sorted(settings.examples_catalog.parent.glob("*/automations"))
                                 if settings.examples_catalog else ())

    # ---------- policy ----------
    def limits(self) -> Limits:
        return Limits(max_actions=self.s.build_max_actions, max_blocks=self.s.build_max_blocks,
                      max_size=self.s.build_max_size, max_distance=self.s.build_max_distance,
                      protected=tuple(self.protected_areas()))

    def protected_areas(self) -> list:
        """Optional <data>/protected-areas.json: [{"name", "dimension", "from": [x,y,z], "to": [x,y,z]}]"""
        p = Path(self.s.data_dir) / "protected-areas.json"
        try:
            items = json.loads(p.read_text())
            return [(str(a["name"])[:40], str(a.get("dimension", "minecraft:overworld")),
                     tuple(int(c) for c in a["from"]), tuple(int(c) for c in a["to"])) for a in items]
        except FileNotFoundError:
            return []
        except (ValueError, KeyError, TypeError):
            log.error("protected-areas.json is invalid: refusing all builds until it is fixed")
            return [("invalid protected-areas.json", "*", (-30_000_000, -64, -30_000_000), (30_000_000, 319, 30_000_000))]

    # ---------- identity ----------
    def identify(self, player_uuid: str, name: str) -> Actor:
        """The player's role from the players table (UUID is authoritative; names only label)."""
        if player_uuid == CONSOLE_UUID:
            return CONSOLE
        row = self.store.player(player_uuid)
        if row is None or (name and row["name"] != name):
            self.store.player_seen(player_uuid, name or (row["name"] if row else "?"))
            self._bootstrap_owner(player_uuid, name)
            row = self.store.player(player_uuid)
        return Actor("player", player_uuid, row["name"], row["role"])

    def _bootstrap_owner(self, player_uuid: str, name: str) -> None:
        """MC_OWNER becomes the owner at its first appearance, then the UUID is what counts. Only in online mode,
        where Mojang authenticates names; otherwise make the owner from the host (docs/administration)."""
        if not (self.s.owner_name and name and name.lower() == self.s.owner_name.lower()):
            return
        if not self.s.online_mode:
            log.warning("MC_ONLINE_MODE is false: not trusting the name %s for the owner role", name)
            return
        if self.store.owner_player() is None:
            self.store.set_player_role(player_uuid, "owner")
            self.store.audit("role.bootstrap", player_uuid=player_uuid, player_name=name, role="owner",
                             reason="MC_OWNER first seen")

    def mod_actor(self, body: dict) -> Actor:
        raw = body.get("player_name")
        return self.identify(self._player(body), clean_name(raw) if isinstance(raw, str) and raw.strip() else "")

    def ai_enabled(self) -> bool:
        return self.store.setting("ai.enabled") != "false"

    def daily_budget(self) -> float:
        v = self.store.setting("ai.daily_budget_usd")
        try:
            return float(v) if v is not None else self.s.daily_budget_usd
        except ValueError:
            return self.s.daily_budget_usd

    def import_bootstrap_keys(self) -> None:
        """<data>/bootstrap/owner.pub (written by setup.sh on the host) adds an SSH key to the 'owner' account.
        Whoever can write the gateway's data directory already controls the host, so this grants nothing new;
        it is also how a lost owner key is replaced."""
        from .sshkeys import KeyError_, parse_public_key
        path = Path(self.s.data_dir) / "bootstrap" / "owner.pub"
        if not path.exists():
            return
        try:
            key = parse_public_key(path.read_text())
        except KeyError_ as e:
            log.error("bootstrap/owner.pub ignored: %s", e)
            return
        if self.store.account("owner") is None:
            self.store.create_account("owner", "owner", "host")
            self.store.audit("account.created", account="owner", role="owner", by="host:bootstrap")
        self.store.update_account("owner", role="owner", disabled=0)
        if self.store.add_key("owner", key.fingerprint, key.line, key.comment, "host:bootstrap"):
            self.store.audit("account.key_added", account="owner", fingerprint=key.fingerprint, by="host:bootstrap")
        path.rename(path.with_name(f"owner.pub.imported-{int(time.time())}"))

    @staticmethod
    def _player(body: dict) -> str:
        player_uuid = str(body.get("player_uuid", "")).lower()
        if not UUID_RE.match(player_uuid):
            raise Rejected(400, "Invalid player.", "bad_player")
        return player_uuid

    # ---------- mod side: requests ----------
    def submit(self, body: dict) -> dict:
        player_uuid = self._player(body)
        name = clean_name(body.get("player_name", ""))
        kind = body.get("kind")
        if kind not in KINDS:
            raise Rejected(400, "Unknown request type.", "bad_kind")
        actor = self.mod_actor(body)
        try:
            text = clean_player_text(body.get("text", ""), self.s.max_prompt_chars)
        except ValueError as e:
            raise Rejected(400, str(e), "bad_text") from None
        context = clean_context(body.get("context"))

        def reject(status, msg, code):
            self.store.audit("request.rejected", player_uuid=player_uuid, player_name=name, kind=kind, reason=code)
            raise Rejected(status, msg, code)

        if not actor.can("ai." + kind):
            reject(403, "You are not allowed to use the AI." if kind != "build"
                   else "Building with the AI needs the builder role. Ask the server owner.", "forbidden")
        if not self.ai_enabled():
            reject(503, "The AI is switched off on this server right now.", "disabled")
        if kind == "build" and not all(k in context for k in ("x", "y", "z", "dimension")):
            reject(400, "Building needs your position: use /ai build in game.", "no_position")
        budget = self.daily_budget()
        if budget <= 0 or self.store.spend_today() >= budget:
            reject(429, "The AI has reached today's budget. Try again tomorrow.", "budget_exhausted")
        with self.cond:   # checks + enqueue under one lock, so two quick requests can't both pass
            if len(self.store.pending_for(player_uuid)) >= self.s.max_pending_per_player:
                reject(429, "You already have a request in progress (see /ai status, /ai cancel).", "pending_limit")
            allowed, wait_s = self.limiter.allow(player_uuid)
            if not allowed:
                reject(429, f"Too many requests. Try again in {int(wait_s) + 1} s.", "rate_limited")
            if len(self.jobs) >= self.s.max_queue:
                reject(503, "The AI is busy. Try again in a minute.", "queue_full")
            rid = self.store.create_request(player_uuid, name, kind, text, context)
            self.jobs.append(rid)
            self.cond.notify_all()
        self.store.audit("request.accepted", request_id=rid, player_uuid=player_uuid, player_name=name, kind=kind,
                         role=actor.role, text=text)
        msg = "Thinking..." if kind == "ask" else f"Designing your build (request {rid})... this can take a minute."
        return {"accepted": True, "request_id": rid, "message": msg}

    def _find(self, actor: Actor, rid, statuses: tuple, kind: str | None = "build"):
        """The request named by id (any owner), or the actor's latest one in one of `statuses`."""
        if rid:
            if not isinstance(rid, str) or not RID_RE.match(rid.lower()):
                raise Rejected(400, "Invalid request id.", "bad_id")
            row = self.store.get(rid.lower())
            if row is None:
                raise Rejected(404, f"No request {rid}.", "unknown")
            return row
        row = self.store.latest(actor.id, statuses, kind) if actor.kind != "account" else None
        if row is None:
            raise Rejected(404, "You have no request that can be used with this command." if actor.kind != "account"
                           else "Give the request id.", "none")
        return row

    @staticmethod
    def _may_decide(actor: Actor, row, own_permission: str = "ai.build") -> bool:
        """Staff may decide on anyone's request; others only on their own, while they still hold the right."""
        if actor.can("ai.decide_any"):
            return True
        return actor.kind == "player" and row["player_uuid"] == actor.id and actor.can(own_permission)

    def approve(self, actor: Actor, rid=None) -> dict:
        row = self._find(actor, rid, ("proposed",))
        rid = row["id"]
        if row["kind"] != "build" or row["status"] != "proposed":
            raise Rejected(409, f"Request {rid} is {row['status']}, not waiting for approval.", "bad_state")
        if not self._may_decide(actor, row):
            self.store.audit("build.approve_denied", request_id=rid, actor=actor.label, role=actor.role)
            raise Rejected(403, "You can't approve this build.", "forbidden")
        if _age_s(row["proposed_at"]) > self.s.approve_ttl_s:
            self.store.transition(rid, ("proposed",), "expired")
            raise Rejected(410, f"Proposal {rid} has expired. Ask again with /ai build.", "expired")
        if not self.store.transition(rid, ("proposed",), "approved", decided_by=actor.label, decided_at=utcnow(),
                                     snapshot="present"):
            raise Rejected(409, f"Request {rid} was already decided.", "bad_state")
        plan = json.loads(row["plan"])
        summary = json.loads(row["summary"])
        self.store.audit("build.approved", request_id=rid, player_uuid=row["player_uuid"], approved_by=actor.label,
                         role=actor.role, block_count=summary["block_count"], bbox_from=summary["from"],
                         bbox_to=summary["to"], dimension=plan["dimension"])
        with self.cond:
            self.executing[rid] = self.clock() + self.s.exec_timeout_s
            self.events.append({"id": uuid.uuid4().hex[:8], "type": "execute", "request_id": rid,
                                "player_uuid": row["player_uuid"], "plan": plan, "summary": summary})
            self.cond.notify_all()
        if actor.kind == "account" or row["player_uuid"] != actor.id:
            self.notify(row["player_uuid"], f"Your build {rid} was approved by {actor.name}.", "info", rid)
        return {"ok": True, "message": f"Approved. Building {summary['title']} ({summary['block_count']} blocks)..."}

    def cancel(self, actor: Actor, rid=None) -> dict:
        row = self._find(actor, rid, ("queued", "running", "proposed"), kind=None)
        rid = row["id"]
        if not self._may_decide(actor, row, own_permission="ai.ask"):
            raise Rejected(403, "You can't cancel someone else's request.", "forbidden")
        if not self.store.transition(rid, ("queued", "running", "proposed"), "cancelled",
                                     decided_by=actor.label, decided_at=utcnow()):
            raise Rejected(409, f"Request {rid} is {row['status']} and can't be cancelled.", "bad_state")
        with self.cond:
            if rid in self.jobs:
                self.jobs.remove(rid)
        self.store.audit("request.cancelled", request_id=rid, player_uuid=row["player_uuid"], cancelled_by=actor.label)
        return {"ok": True, "message": f"Cancelled request {rid}."}

    def undo(self, actor: Actor, rid=None) -> dict:
        row = self._find(actor, rid, ("built", "interrupted"))
        rid = row["id"]
        if row["kind"] != "build" or row["status"] not in ("built", "interrupted"):
            raise Rejected(409, f"Request {rid} is {row['status']}; only finished builds can be undone.", "bad_state")
        if not self._may_decide(actor, row):
            raise Rejected(403, "You can't undo this build.", "forbidden")
        if row["snapshot"] == "pruned":
            raise Rejected(410, f"Build {rid} is older than the undo window ({self.s.undo_keep_days} days).", "expired")
        if not self.store.transition(rid, ("built", "interrupted"), "rolling_back", decided_by=actor.label,
                                     decided_at=utcnow()):
            raise Rejected(409, f"Request {rid} changed state, try again.", "bad_state")
        self.store.audit("build.undo_requested", request_id=rid, player_uuid=row["player_uuid"], by=actor.label)
        plan = json.loads(row["plan"])
        with self.cond:
            self.executing[rid] = self.clock() + self.s.exec_timeout_s
            self.events.append({"id": uuid.uuid4().hex[:8], "type": "rollback", "request_id": rid,
                                "player_uuid": row["player_uuid"], "dimension": plan["dimension"],
                                "summary": json.loads(row["summary"])})
            self.cond.notify_all()
        return {"ok": True, "message": f"Restoring the area of build {rid}..."}

    # ---------- mod side: players, link, heartbeat ----------
    def player_event(self, body: dict) -> dict:
        player_uuid = self._player(body)
        if player_uuid == CONSOLE_UUID:
            raise Rejected(400, "Invalid player.", "bad_player")
        name = clean_name(body.get("player_name", ""))
        event = body.get("event")
        data = {"player_uuid": player_uuid, "player_name": name}
        if event == "join":
            is_new, old_name = self.store.player_seen(player_uuid, name, online=True)
            self._bootstrap_owner(player_uuid, name)
            sid = self.store.session_open(player_uuid, name)
            extra = {"first_join": True} if is_new else ({"previous_name": old_name} if old_name != name else {})
            self.store.audit("player.join", player_uuid=player_uuid, player_name=name, **extra)
            if is_new:
                self.automation.emit("player.first_join", data, key=f"first:{player_uuid}")
            self.automation.emit("player.join", data, key=f"session:{sid}")
        elif event == "leave":
            sid = self.store.session_close(player_uuid)
            if sid:
                self.store.audit("player.leave", player_uuid=player_uuid, player_name=name)
                self.automation.emit("player.leave", data, key=f"leave:{sid}")
        else:
            raise Rejected(400, "bad event", "bad_event")
        return {"ok": True}

    def heartbeat_in(self, body: dict) -> dict:
        players = body.get("players") if isinstance(body.get("players"), list) else []
        online = {}
        for p in players[:500]:
            if isinstance(p, dict) and UUID_RE.match(str(p.get("uuid", "")).lower()):
                online[str(p["uuid"]).lower()] = clean_name(p.get("name", ""))
        for gone in self.store.online_uuids() - set(online):        # missed leave events (gateway restarts)
            sid = self.store.session_close(gone)
            if sid:
                self.store.audit("player.leave", player_uuid=gone, reason="not online at heartbeat")
                row = self.store.player(gone)
                self.automation.emit("player.leave", {"player_uuid": gone, "player_name": row["name"] if row else "?"},
                                     key=f"leave:{sid}")
        known = self.store.online_uuids()
        for u, n in online.items():
            if u not in known:
                self.player_event({"event": "join", "player_uuid": u, "player_name": n})
        stats = {}
        for k in ("mspt", "heap_used_mb", "heap_max_mb", "uptime_s"):
            v = body.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e9:
                stats[k] = round(float(v), 2)
        stats["version"] = clean_name(str(body.get("version", "")))[:20]
        stats["players"] = sorted(online.values())
        with self.cond:
            self.heartbeat, self.heartbeat_at = stats, self.clock()
        world = body.get("world") if isinstance(body.get("world"), dict) else {}
        self.examples.on_heartbeat(body.get("example_job"))
        self.automation.on_heartbeat({**stats, "players": list(online)}, world)
        return {"ok": True, "areas": self.automation.areas()}

    def game_event(self, body: dict) -> dict:
        """Area enter/leave, advancements and /ai trigger, reported by the mod (player events for automations)."""
        typ = body.get("type")
        event_id = str(body.get("event_id", "")).lower()
        if not re.fullmatch(r"[0-9a-f-]{8,36}", event_id):
            raise Rejected(400, "bad event id", "bad_event")
        actor = self.mod_actor(body)
        if actor.kind != "player":
            raise Rejected(400, "Only players can do that.", "bad_player")
        data = {"player_uuid": actor.id, "player_name": actor.name}
        if typ in ("area_enter", "area_leave"):
            aid = str(body.get("area", ""))
            name = self.automation.area_name(aid)
            if name is None:
                return {"ok": True, "started": 0}
            event = "player.enter_area" if typ == "area_enter" else "player.leave_area"
            runs = self.automation.emit(event, {**data, "area": name}, key=f"area:{event_id}", only={aid})
            return {"ok": True, "started": len(runs)}
        if typ == "advancement":
            adv = str(body.get("advancement", ""))
            if not re.fullmatch(r"[a-z0-9_.-]{1,32}:[a-z0-9_./-]{1,100}", adv):
                raise Rejected(400, "bad advancement", "bad_event")
            title = clean_reply(str(body.get("title", "")), 64) if body.get("title") else adv
            runs = self.automation.emit("player.advancement", {**data, "advancement": title, "advancement_id": adv},
                                        key=f"adv:{actor.id}:{adv}")
            return {"ok": True, "started": len(runs)}
        if typ == "trigger":
            name = str(body.get("name", "")).lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,32}", name) or name not in self.automation.trigger_names():
                raise Rejected(404, f"There is nothing called '{name[:32]}' to start here.", "unknown")
            runs = self.automation.emit("player.trigger", {**data, "trigger": name}, key=f"trigger:{event_id}")
            if not runs:
                return {"ok": False, "message": f"You can't start '{name}' right now."}
            return {"ok": True, "message": f"Started '{name}'.", "started": len(runs)}
        raise Rejected(400, "bad event type", "bad_event")

    def link(self, body: dict) -> dict:
        actor = self.mod_actor(body)
        code = str(body.get("code", "")).strip().lower()
        if actor.kind != "player":
            raise Rejected(400, "Only players can link an account.", "bad_player")
        account = self.store.use_link_code(code) if re.fullmatch(r"[0-9a-f]{8}", code) else None
        if account is None:
            self.store.audit("account.link_failed", player_uuid=actor.id)
            raise Rejected(404, "That code is unknown or expired. Run 'mcai account link' again.", "bad_code")
        self.store.update_account(account, player_uuid=actor.id)
        self.store.audit("account.linked", account=account, player_uuid=actor.id, player_name=actor.name)
        return {"ok": True, "message": f"Linked to management account '{account}'. This doesn't change your "
                                      "in-game rights."}

    def report(self, rid: str, body: dict) -> dict:
        """Outcome of a preview check, a build or a rollback, reported by the mod."""
        row = self.store.get(rid)
        if row is None or row["kind"] != "build":
            raise Rejected(404, "unknown build", "unknown")
        status = body.get("status")
        if status not in REPORT_STATUSES:
            raise Rejected(400, "bad status", "bad_status")
        if status == "pruned":
            with self.cond:
                self.pruning.pop(rid, None)
            self.store.set_snapshot(rid, "pruned")
            self.store.audit("build.snapshot_pruned", request_id=rid)
            return {"recorded": True}
        placed = body.get("placed") if isinstance(body.get("placed"), int) else 0
        error = str(body.get("error", ""))[:300] or None
        owner = row["player_uuid"]
        transitions = {
            "invalid": (("proposed",), "failed"),
            "built": (("approved",), "built"),
            "failed": (("approved",), "interrupted"),
            "rolled_back": (("rolling_back",), "rolled_back"),
            "rollback_failed": (("rolling_back",), "built"),
        }
        frm, to = transitions[status]
        extra = {"error": error}
        if status in ("built", "failed"):
            extra.update(built_at=utcnow(), placed=max(0, placed))
        if not self.store.transition(rid, frm, to, **extra):
            return {"recorded": False}
        with self.cond:
            self.executing.pop(rid, None)
        self.store.audit(f"build.{status}", request_id=rid, player_uuid=owner, placed=placed, error=error)
        event = {"built": "build.completed", "failed": "build.failed", "rolled_back": "build.undone"}.get(status)
        if event:
            chain: tuple = ()
            if row["automation_run"]:
                run = self.store.run(row["automation_run"])
                if run:
                    chain = tuple(json.loads(run["chain"])) + (run["automation_id"],)
            title = json.loads(row["summary"])["title"] if row["summary"] else ""
            self.automation.emit(event, {"player_uuid": owner, "player_name": row["player_name"], "title": title,
                                         "request": rid, "blocks": max(0, placed)}, key=f"{event}:{rid}", chain=chain)
        messages = {
            "invalid": (f"The AI's plan {rid} used blocks this server doesn't accept ({error}). Try /ai build again.", "error"),
            "built": (f"Build {rid} finished: {placed} blocks placed. Not happy? /ai undo {rid}", "info"),
            "failed": (f"Build {rid} stopped early ({error}). /ai undo {rid} restores the area.", "error"),
            "rolled_back": (f"Build {rid} undone: the area is back as it was.", "info"),
            "rollback_failed": (f"Could not undo build {rid} ({error}).", "error"),
        }
        self.notify(owner, *messages[status], request_id=rid)
        return {"recorded": True}

    # ---------- mod side: info ----------
    def notify(self, player_uuid: str, text: str, level: str = "info", request_id: str | None = None) -> None:
        with self.cond:
            self.events.append({"id": uuid.uuid4().hex[:8], "type": "message", "player_uuid": player_uuid,
                                "text": text, "level": level, "request_id": request_id})
            self.cond.notify_all()

    def broadcast(self, text: str) -> None:
        with self.cond:
            self.events.append({"id": uuid.uuid4().hex[:8], "type": "broadcast", "text": text})
            self.cond.notify_all()

    def enqueue_internal(self, kind: str, text: str, automation_run: str | None, context: dict | None = None,
                         max_usd: float | None = None) -> str:
        """A request made by the platform itself (automations, automation proposals), not by a player. It counts
        against the same queue and daily budget. Results go back to the automation engine, not to a player."""
        with self.cond:
            if len(self.jobs) >= self.s.max_queue:
                raise Rejected(503, "The AI queue is full.", "queue_full")
            rid = self.store.create_request(CONSOLE_UUID, "Automation", kind, text[:2000], context or {},
                                            automation_run=automation_run, max_usd=max_usd)
            self.jobs.append(rid)
            self.cond.notify_all()
        self.store.audit("request.accepted", request_id=rid, player_uuid=CONSOLE_UUID, kind=kind, automation_run=automation_run,
                         text=text)
        return rid

    def cancel_internal(self, rid: str) -> None:
        if self.store.transition(rid, ("queued", "running"), "cancelled", decided_by="automation", decided_at=utcnow()):
            with self.cond:
                if rid in self.jobs:
                    self.jobs.remove(rid)
            self.store.audit("request.cancelled", request_id=rid, cancelled_by="automation timeout")

    def status_line(self) -> str:
        hb = dict(self.heartbeat) if self.mod_online() else {}
        parts = []
        if hb:
            tps = round(min(20.0, 1000 / hb["mspt"]), 1) if hb.get("mspt") else "?"
            players = hb.get("players", [])
            parts.append(f"Server online, {tps} TPS, {len(players)} player(s) online"
                         + (f": {', '.join(players[:10])}" if players else ""))
        else:
            parts.append("Server: no heartbeat")
        parts.append(f"AI ${self.store.spend_today():.2f} of ${self.daily_budget():.2f} today"
                     + ("" if self.ai_enabled() else " (AI off)"))
        return " | ".join(parts)

    def take_events(self, wait: float) -> list[dict]:
        deadline = self.clock() + wait
        with self.cond:
            while not self.events and not self._stop.is_set():
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                self.cond.wait(min(remaining, 1.0))
            out = list(self.events)
            self.events.clear()
            return out

    def status(self, player_uuid: str) -> dict:
        pending = self.store.pending_for(player_uuid) if UUID_RE.match(player_uuid or "") else []
        spent = self.store.spend_today()
        worker_ok = self.worker_online()
        parts = [("Active: " + ", ".join(f"{r['id']} {r['kind']} {r['status']}" for r in pending)) if pending
                 else "No requests in progress",
                 f"AI budget today: ${spent:.2f} of ${self.daily_budget():.2f}",
                 ("AI worker: online" if worker_ok else "AI worker: offline") + ("" if self.ai_enabled() else " (AI off)")]
        return {"message": " | ".join(parts), "pending": [dict(r) for r in pending], "spent_today_usd": round(spent, 4),
                "budget_usd": self.daily_budget(), "worker_online": worker_ok}

    def worker_online(self) -> bool:
        return self.last_worker_seen > 0 and self.clock() - self.last_worker_seen < 90

    def mod_online(self) -> bool:
        return self.heartbeat_at > 0 and self.clock() - self.heartbeat_at < 90

    def history(self, player_uuid: str) -> dict:
        rows = self.store.history(player_uuid, 5) if UUID_RE.match(player_uuid or "") else []
        items = []
        for r in rows:
            label = json.loads(r["summary"])["title"] if r["summary"] else r["text"][:40]
            items.append(f"{r['id']} {r['kind']} {r['status']}: {label}")
        return {"message": " | ".join(items) if items else "No requests yet.", "items": [dict(r) for r in rows]}

    # ---------- worker side ----------
    def next_job(self, wait: float) -> dict | None:
        self.last_worker_seen = self.clock()
        deadline = self.clock() + wait
        with self.cond:
            while True:
                while self.jobs:
                    rid = self.jobs.popleft()
                    if not self.store.mark_running(rid):
                        continue
                    row = self.store.get(rid)
                    timeout = self.s.build_timeout_s if row["kind"] == "build" else self.s.request_timeout_s
                    self.inflight[rid] = self.clock() + timeout
                    ctx = json.loads(row["context"])
                    job = {"request_id": rid, "kind": row["kind"], "text": row["text"], "player_name": row["player_name"],
                           "context": ctx if row["kind"] != "automation" else {}, "timeout_s": timeout}
                    if row["max_usd"]:
                        job["max_usd"] = row["max_usd"]
                    if row["kind"] == "build":
                        lim = self.limits()
                        job["build"] = {
                            "origin": [ctx["x"], ctx["y"], ctx["z"]], "dimension": ctx["dimension"],
                            "limits": {"max_actions": lim.max_actions, "max_blocks": lim.max_blocks,
                                       "max_size": list(lim.max_size), "max_distance": lim.max_distance},
                            "protected": [{"name": n, "dimension": d, "from": list(a), "to": list(b)}
                                          for n, d, a, b in lim.protected]}
                    return job
                remaining = deadline - self.clock()
                if remaining <= 0 or self._stop.is_set():
                    return None
                self.cond.wait(min(remaining, 1.0))

    def complete(self, rid: str, result: dict) -> bool:
        self.last_worker_seen = self.clock()
        row = self.store.get(rid)
        if row is None:
            raise Rejected(404, "unknown request", "unknown")
        ok = bool(result.get("ok"))
        cost = float(result.get("cost_usd") or 0)
        usage = (cost, int(result.get("input_tokens") or 0), int(result.get("output_tokens") or 0),
                 str(result.get("provider", ""))[:40])
        code = str(result.get("error_code", ""))[:40]
        error = str(result.get("error", ""))[:500] or None
        with self.cond:
            self.inflight.pop(rid, None)

        if row["kind"] == "automation":
            accepted, message = (self.automation.proposal_result(row, str(result.get("text", ""))) if ok
                                 else (False, error or "failed"))
            if not self.store.finish(rid, accepted, message if accepted else None, None if accepted else message,
                                     *usage):
                return False
            self.store.audit("request.done" if accepted else "request.failed", request_id=rid, kind="automation",
                             cost_usd=round(cost, 6), error=None if accepted else message[:200])
            return True

        if row["kind"] == "build" and ok:
            ctx = json.loads(row["context"])
            try:
                plan, summary = validate(result.get("plan"), (ctx["x"], ctx["y"], ctx["z"]), ctx["dimension"],
                                         self.limits())
            except PlanError as e:
                if not self.store.finish(rid, False, None, f"invalid plan: {e}", *usage):
                    return False
                self.store.audit("build.plan_rejected", request_id=rid, player_uuid=row["player_uuid"], reason=str(e)[:200])
                self.notify(row["player_uuid"], f"The AI's plan was rejected: {e}", "error", rid)
                return True
            reply = clean_reply(result.get("text", ""), self.s.max_reply_chars)
            if not self.store.finish(rid, True, reply, None, *usage, status="proposed"):
                return False
            self.store.transition(rid, ("proposed",), "proposed", plan=json.dumps(plan),
                                  summary=json.dumps(summary.as_dict()), proposed_at=utcnow())
            self.store.audit("build.proposed", request_id=rid, player_uuid=row["player_uuid"], cost_usd=round(cost, 6),
                             block_count=summary.block_count, bbox_from=list(summary.bbox_from),
                             bbox_to=list(summary.bbox_to))
            blocks = sorted({a["block"] for a in plan["actions"]})
            minutes = self.s.approve_ttl_s // 60
            with self.cond:
                self.events.append({
                    "id": uuid.uuid4().hex[:8], "type": "preview", "request_id": rid, "player_uuid": row["player_uuid"],
                    "summary": summary.as_dict(), "blocks": blocks, "dimension": plan["dimension"],
                    "text": f"Proposal {rid}: {summary.describe()}. {reply} Type /ai approve {rid} to build it or "
                            f"/ai cancel {rid} (expires in {minutes} min)."})
                self.cond.notify_all()
            return True

        reply = clean_reply(result.get("text", ""), self.s.max_reply_chars) if ok else None
        if not self.store.finish(rid, ok, reply, error, *usage):
            return False   # duplicate, late (timed out) or cancelled: never deliver twice
        self.store.audit("request.done" if ok else "request.failed", request_id=rid, player_uuid=row["player_uuid"],
                         cost_usd=round(cost, 6), provider=result.get("provider"), error_code=code or None)
        if row["automation_run"]:
            self.automation.ai_done(rid)       # the automation delivers the text itself
            return True
        if ok:
            self.notify(row["player_uuid"], reply, "info", rid)
        elif code == "not_configured":
            self.notify(row["player_uuid"], "The AI is not set up on this server yet. Ask the server owner.", "error", rid)
        elif code == "no_plan":
            self.notify(row["player_uuid"], "The AI didn't come up with a plan for that. Try describing it differently.",
                        "error", rid)
        else:
            self.notify(row["player_uuid"], GENERIC_FAILURE, "error", rid)
        return True

    def sweep(self) -> None:
        """Time out AI jobs and builds; expire proposals nobody approved."""
        now = self.clock()
        with self.cond:
            expired = [rid for rid, dl in self.inflight.items() if dl < now]
            for rid in expired:
                self.inflight.pop(rid, None)
            stuck = [rid for rid, dl in self.executing.items() if dl < now]
            for rid in stuck:
                self.executing.pop(rid, None)
        for rid in expired:
            row = self.store.get(rid)
            if row and self.store.finish(rid, False, None, "timeout", 0.0, 0, 0, ""):
                self.store.audit("request.failed", request_id=rid, player_uuid=row["player_uuid"], reason="timeout")
                self.notify(row["player_uuid"], "Sorry, that took too long and was cancelled.", "error", rid)
        for rid in stuck:
            row = self.store.get(rid)
            if row and self.store.transition(rid, ("approved", "rolling_back"), "interrupted", error="no report from server"):
                self.store.audit("build.interrupted", request_id=rid, player_uuid=row["player_uuid"])
                self.notify(row["player_uuid"], f"Build {rid} didn't report back. /ai undo {rid} restores the area.",
                            "error", rid)
        for row in self.store.with_status(("proposed",)):
            if _age_s(row["proposed_at"]) > self.s.approve_ttl_s and self.store.transition(row["id"], ("proposed",), "expired"):
                self.store.audit("build.expired", request_id=row["id"], player_uuid=row["player_uuid"])
                self.notify(row["player_uuid"], f"Proposal {row['id']} expired without approval.", "info", row["id"])
        self.prune_snapshots()
        if now >= self.next_retention:
            self.next_retention = now + 3600
            for name in self.store.audit_prune(self.s.audit_retention_days):
                log.info("audit retention: removed %s", name)

    def prune_snapshots(self) -> None:
        """Ask the mod to delete undo snapshots of undone builds and of builds older than the undo window."""
        keep = self.s.undo_keep_days
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep)).isoformat().replace("+00:00", "Z") \
            if keep > 0 else "0000"
        rows = self.store.snapshots_to_prune(cutoff)
        now = self.clock()
        ids = [r["id"] for r in rows if self.pruning.get(r["id"], 0) <= now][:50]
        if not ids:
            return
        with self.cond:
            for rid in ids:
                self.pruning[rid] = now + 600
            self.events.append({"id": uuid.uuid4().hex[:8], "type": "prune", "request_ids": ids})
            self.cond.notify_all()
    def stop(self) -> None:
        self._stop.set()
        self.automation.stop()
        with self.cond:
            self.cond.notify_all()


def make_handler(gw: Gateway):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mcai-gateway/" + __version__
        sys_version = ""

        def log_message(self, fmt, *args):          # no request lines with query strings in logs
            pass

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_bytes(self, data: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _auth(self, which: str) -> bool:
            expected = {"mod": gw.s.mod_token, "worker": gw.s.worker_token, "mgmt": gw.s.management_token}[which]
            got = self.headers.get("Authorization", "")
            if (not expected or not got.startswith("Bearer ")
                    or not hmac.compare_digest(got[7:].encode(), expected.encode())):
                gw.store.audit("auth.failed", api=which, source=self.client_address[0], path=urlparse(self.path).path)
                self._send(401, {"message": "unauthorized"})
                return False
            return True

        def _body(self) -> dict | None:
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            limit = gw.s.examples_max_file_mb * 1400 * 1024 + 65536 if self.path.startswith("/v1/mgmt/upload") else \
                MAX_BODY * 64 if self.path.startswith(("/v1/worker/", "/v1/mod/heartbeat")) else \
                MAX_BODY * 8 if self.path.startswith("/v1/mgmt/run") else MAX_BODY
            if n < 0 or n > limit:
                self._send(413, {"message": "request too large"})
                return None
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, UnicodeDecodeError):
                self._send(400, {"message": "invalid JSON"})
                return None
            if not isinstance(body, dict):
                self._send(400, {"message": "invalid JSON"})
                return None
            return body

        @staticmethod
        def _wait(q: dict) -> float:
            try:
                return max(0.0, min(30.0, float(q.get("wait", ["0"])[0])))
            except ValueError:
                return 0.0

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            player = q.get("player_uuid", [""])[0].lower()
            if u.path == "/healthz":
                return self._send(200, {"ok": True, "version": __version__})
            routes = {"/v1/mod/events": ("mod", lambda: {"events": gw.take_events(self._wait(q))}),
                      "/v1/mod/status": ("mod", lambda: gw.status(player)),
                      "/v1/mod/history": ("mod", lambda: gw.history(player)),
                      "/v1/worker/jobs": ("worker", lambda: {"job": gw.next_job(self._wait(q))}),
                      "/v1/mgmt/authorized-keys": ("mgmt", lambda: {"keys": management.authorized_keys(gw)})}
            if u.path in routes:
                api, fn = routes[u.path]
                if self._auth(api):
                    self._send(200, fn())
                return
            m = re.fullmatch(r"/v1/mod/examples/([0-9a-f]{8})/([a-z0-9][a-z0-9-]{0,63}\.schem)", u.path)
            if m:
                if self._auth("mod"):
                    data = gw.examples.file_for_mod(m.group(1), m.group(2))
                    if data is None:
                        self._send(404, {"message": "not found"})
                    else:
                        self._send_bytes(data)
                return
            self._send(404, {"message": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            routes = {
                "/v1/mod/requests": ("mod", gw.submit),
                "/v1/mod/approve": ("mod", lambda b: gw.approve(gw.mod_actor(b), b.get("request_id"))),
                "/v1/mod/cancel": ("mod", lambda b: gw.cancel(gw.mod_actor(b), b.get("request_id"))),
                "/v1/mod/undo": ("mod", lambda b: gw.undo(gw.mod_actor(b), b.get("request_id"))),
                "/v1/mod/link": ("mod", gw.link),
                "/v1/mod/players": ("mod", gw.player_event),
                "/v1/mod/heartbeat": ("mod", gw.heartbeat_in),
                "/v1/mod/game-events": ("mod", gw.game_event),
                "/v1/mgmt/run": ("mgmt", lambda b: management.run(gw, b)),
                "/v1/mgmt/upload": ("mgmt", lambda b: management.run(gw, b, upload=True)),
                "/v1/mgmt/ssh-event": ("mgmt", lambda b: management.ssh_event(gw, b)),
                "/v1/mgmt/session": ("mgmt", lambda b: management.session_event(gw, b)),
            }
            try:
                if u.path in routes:
                    api, fn = routes[u.path]
                    if self._auth(api) and (body := self._body()) is not None:
                        self._send(200, fn(body))
                    return
                m = re.fullmatch(r"/v1/mod/builds/([0-9a-f]{6,12})/report", u.path)
                if m:
                    if self._auth("mod") and (body := self._body()) is not None:
                        self._send(200, gw.report(m.group(1), body))
                    return
                m = re.fullmatch(r"/v1/mod/examples/([0-9a-f]{8})/report", u.path)
                if m:
                    if self._auth("mod") and (body := self._body()) is not None:
                        self._send(200, gw.examples.report(m.group(1), body))
                    return
                m = re.fullmatch(r"/v1/worker/jobs/([0-9a-f]{6,12})/result", u.path)
                if m:
                    if self._auth("worker") and (body := self._body()) is not None:
                        self._send(200, {"recorded": gw.complete(m.group(1), body)})
                    return
                self._send(404, {"message": "not found"})
            except Rejected as e:
                self._send(e.status, {"accepted": False, "ok": False, "message": e.message, "code": e.code})
            except Exception:                         # never leak internals to the caller
                log.exception("unhandled error on %s", u.path)
                self._send(500, {"accepted": False, "ok": False, "message": "internal error"})

    return Handler


def serve(settings: GatewaySettings | None = None) -> None:
    setup_logging("gateway")
    settings = settings or GatewaySettings.from_env()
    gw = Gateway(settings, Store(settings.data_dir, settings.audit_log_text))
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(gw))
    httpd.daemon_threads = True

    def sweeper():
        while not gw._stop.wait(5):
            try:
                gw.sweep()
            except Exception:
                log.exception("sweep failed")

    threading.Thread(target=sweeper, daemon=True).start()
    gw.automation.start(settings.automation_workers)
    gw.store.audit("gateway.started", version=__version__)
    log.info("gateway %s listening on %s:%d", __version__, settings.host, settings.port)
    import signal

    def shutdown(*_):
        gw.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        httpd.serve_forever()
    finally:
        gw.store.audit("gateway.stopped")


if __name__ == "__main__":
    serve()
