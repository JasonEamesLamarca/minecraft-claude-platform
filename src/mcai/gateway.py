"""Integration gateway: the only component that talks to both the Minecraft mod and the AI worker.

Mod API (Bearer gateway_mod_token):
  POST /v1/mod/requests            {player_uuid, player_name, op_level, kind:"ask"|"build", text, context}
  POST /v1/mod/approve             {player_uuid, op_level, request_id?}     build a proposed plan
  POST /v1/mod/cancel              {player_uuid, op_level, request_id?}     drop a queued/proposed request
  POST /v1/mod/undo                {player_uuid, op_level, request_id?}     restore the snapshot of a build
  POST /v1/mod/builds/<id>/report  {status: invalid|built|failed|rolled_back|rollback_failed, placed, error}
  GET  /v1/mod/events?wait=25      long-poll: messages, previews, execute/rollback orders for the mod
  GET  /v1/mod/status?player_uuid=...
  GET  /v1/mod/history?player_uuid=...
Worker API (Bearer worker_token):
  GET  /v1/worker/jobs?wait=25     long-poll: next job
  POST /v1/worker/jobs/<id>/result {ok, text, plan?, error, error_code, cost_usd, input_tokens, output_tokens, provider}
Health: GET /healthz (no auth, no data)

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
from .config import GatewaySettings, setup_logging
from .plan import Limits, PlanError, validate
from .ratelimit import RateLimiter
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
REPORT_STATUSES = {"invalid", "built", "failed", "rolled_back", "rollback_failed"}


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
    def __init__(self, settings: GatewaySettings, store: Store, clock=time.monotonic):
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
        self._stop = threading.Event()
        for row in store.fail_unfinished("gateway restarted"):
            store.audit("request.interrupted", request_id=row["id"], player_uuid=row["player_uuid"],
                        previous_status=row["status"], reason="gateway restarted")

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

    def authorize(self, player_uuid: str, kind: str, op_level: int) -> bool:
        """Phase 3 policy (RBAC replaces it in Phase 4): everyone may ask; building needs the vanilla
        permission level AI_BUILD_MIN_OP_LEVEL (default 4: the owner, the only operator by default)."""
        if kind == "ask":
            return True
        return kind == "build" and op_level >= self.s.build_min_op_level

    @staticmethod
    def _op_level(body: dict) -> int:
        v = body.get("op_level", 0)
        return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 4 else 0

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
        op_level = self._op_level(body)
        try:
            text = clean_player_text(body.get("text", ""), self.s.max_prompt_chars)
        except ValueError as e:
            raise Rejected(400, str(e), "bad_text") from None
        context = clean_context(body.get("context"))

        def reject(status, msg, code):
            self.store.audit("request.rejected", player_uuid=player_uuid, player_name=name, kind=kind, reason=code)
            raise Rejected(status, msg, code)

        if not self.authorize(player_uuid, kind, op_level):
            reject(403, "You are not allowed to do that." if kind != "build"
                   else "Building with the AI needs a higher permission level on this server.", "forbidden")
        if kind == "build" and not all(k in context for k in ("x", "y", "z", "dimension")):
            reject(400, "Building needs your position: use /ai build in game.", "no_position")
        if self.s.daily_budget_usd <= 0 or self.store.spend_today() >= self.s.daily_budget_usd:
            reject(429, "The AI has reached today's budget. Try again tomorrow.", "budget_exhausted")
        with self.cond:   # checks + enqueue under one lock, so two quick requests can't both pass
            if len(self.store.pending_for(player_uuid)) >= self.s.max_pending_per_player:
                reject(429, "You already have a request in progress (see /ai status, /ai cancel).", "pending_limit")
            allowed, wait_s = self.limiter.allow(player_uuid)
            if not allowed:
                reject(429, f"Too many requests. Try again in {int(wait_s) + 1} s.", "rate_limited")
            if len(self.jobs) >= self.s.max_queue:
                reject(503, "The AI is busy. Try again in a minute.", "queue_full")
            rid = self.store.create_request(player_uuid, name, kind, text, context, op_level)
            self.jobs.append(rid)
            self.cond.notify_all()
        self.store.audit("request.accepted", request_id=rid, player_uuid=player_uuid, player_name=name, kind=kind,
                         op_level=op_level, text=text)
        msg = "Thinking..." if kind == "ask" else f"Designing your build (request {rid})... this can take a minute."
        return {"accepted": True, "request_id": rid, "message": msg}

    def _find(self, body: dict, statuses: tuple, kind: str | None = "build"):
        """The request named in body (any owner), or the player's latest one in one of `statuses`."""
        player_uuid = self._player(body)
        rid = body.get("request_id")
        if rid:
            if not isinstance(rid, str) or not RID_RE.match(rid.lower()):
                raise Rejected(400, "Invalid request id.", "bad_id")
            row = self.store.get(rid.lower())
            if row is None:
                raise Rejected(404, f"No request {rid}.", "unknown")
        else:
            row = self.store.latest(player_uuid, statuses, kind)
            if row is None:
                raise Rejected(404, "You have no request that can be used with this command.", "none")
        return player_uuid, row

    def _may_decide(self, player_uuid: str, op_level: int, row) -> bool:
        """The requester (still allowed to build) or an operator of the highest level."""
        if op_level >= 4:
            return True
        return row["player_uuid"] == player_uuid and self.authorize(player_uuid, "build", op_level)

    def approve(self, body: dict) -> dict:
        player_uuid, row = self._find(body, ("proposed",))
        op_level = self._op_level(body)
        rid = row["id"]
        if row["kind"] != "build" or row["status"] != "proposed":
            raise Rejected(409, f"Request {rid} is {row['status']}, not waiting for approval.", "bad_state")
        if not self._may_decide(player_uuid, op_level, row):
            self.store.audit("build.approve_denied", request_id=rid, player_uuid=player_uuid)
            raise Rejected(403, "You can't approve this build.", "forbidden")
        if _age_s(row["proposed_at"]) > self.s.approve_ttl_s:
            self.store.transition(rid, ("proposed",), "expired")
            raise Rejected(410, f"Proposal {rid} has expired. Ask again with /ai build.", "expired")
        if not self.store.transition(rid, ("proposed",), "approved", decided_by=player_uuid, decided_at=utcnow()):
            raise Rejected(409, f"Request {rid} was already decided.", "bad_state")
        plan = json.loads(row["plan"])
        summary = json.loads(row["summary"])
        self.store.audit("build.approved", request_id=rid, player_uuid=row["player_uuid"], approved_by=player_uuid,
                         block_count=summary["block_count"], bbox_from=summary["from"], bbox_to=summary["to"],
                         dimension=plan["dimension"])
        with self.cond:
            self.executing[rid] = self.clock() + self.s.exec_timeout_s
            self.events.append({"id": uuid.uuid4().hex[:8], "type": "execute", "request_id": rid,
                                "player_uuid": row["player_uuid"], "plan": plan, "summary": summary})
            self.cond.notify_all()
        return {"ok": True, "message": f"Approved. Building {summary['title']} ({summary['block_count']} blocks)..."}

    def cancel(self, body: dict) -> dict:
        player_uuid, row = self._find(body, ("queued", "running", "proposed"), kind=None)
        rid = row["id"]
        if not (row["player_uuid"] == player_uuid or self._op_level(body) >= 4):
            raise Rejected(403, "You can't cancel someone else's request.", "forbidden")
        if not self.store.transition(rid, ("queued", "running", "proposed"), "cancelled",
                                     decided_by=player_uuid, decided_at=utcnow()):
            raise Rejected(409, f"Request {rid} is {row['status']} and can't be cancelled.", "bad_state")
        with self.cond:
            if rid in self.jobs:
                self.jobs.remove(rid)
        self.store.audit("request.cancelled", request_id=rid, player_uuid=row["player_uuid"], cancelled_by=player_uuid)
        return {"ok": True, "message": f"Cancelled request {rid}."}

    def undo(self, body: dict) -> dict:
        player_uuid, row = self._find(body, ("built", "interrupted"))
        rid = row["id"]
        if row["kind"] != "build" or row["status"] not in ("built", "interrupted"):
            raise Rejected(409, f"Request {rid} is {row['status']}; only finished builds can be undone.", "bad_state")
        if not self._may_decide(player_uuid, self._op_level(body), row):
            raise Rejected(403, "You can't undo this build.", "forbidden")
        if not self.store.transition(rid, ("built", "interrupted"), "rolling_back", decided_by=player_uuid,
                                     decided_at=utcnow()):
            raise Rejected(409, f"Request {rid} changed state, try again.", "bad_state")
        self.store.audit("build.undo_requested", request_id=rid, player_uuid=row["player_uuid"], by=player_uuid)
        plan = json.loads(row["plan"])
        with self.cond:
            self.executing[rid] = self.clock() + self.s.exec_timeout_s
            self.events.append({"id": uuid.uuid4().hex[:8], "type": "rollback", "request_id": rid,
                                "player_uuid": row["player_uuid"], "dimension": plan["dimension"],
                                "summary": json.loads(row["summary"])})
            self.cond.notify_all()
        return {"ok": True, "message": f"Restoring the area of build {rid}..."}

    def report(self, rid: str, body: dict) -> dict:
        """Outcome of a preview check, a build or a rollback, reported by the mod."""
        row = self.store.get(rid)
        if row is None or row["kind"] != "build":
            raise Rejected(404, "unknown build", "unknown")
        status = body.get("status")
        if status not in REPORT_STATUSES:
            raise Rejected(400, "bad status", "bad_status")
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
        worker_ok = self.clock() - self.last_worker_seen < 90
        parts = [("Active: " + ", ".join(f"{r['id']} {r['kind']} {r['status']}" for r in pending)) if pending
                 else "No requests in progress",
                 f"AI budget today: ${spent:.2f} of ${self.s.daily_budget_usd:.2f}",
                 "AI worker: online" if worker_ok else "AI worker: offline"]
        return {"message": " | ".join(parts), "pending": [dict(r) for r in pending], "spent_today_usd": round(spent, 4),
                "budget_usd": self.s.daily_budget_usd, "worker_online": worker_ok}

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
                           "context": ctx, "timeout_s": timeout}
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

    def stop(self) -> None:
        self._stop.set()
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

        def _auth(self, which: str) -> bool:
            expected = gw.s.mod_token if which == "mod" else gw.s.worker_token
            got = self.headers.get("Authorization", "")
            if not got.startswith("Bearer ") or not hmac.compare_digest(got[7:].encode(), expected.encode()):
                gw.store.audit("auth.failed", api=which, source=self.client_address[0], path=urlparse(self.path).path)
                self._send(401, {"message": "unauthorized"})
                return False
            return True

        def _body(self) -> dict | None:
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            limit = MAX_BODY * 64 if self.path.startswith("/v1/worker/") else MAX_BODY   # plans can be large
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
                      "/v1/worker/jobs": ("worker", lambda: {"job": gw.next_job(self._wait(q))})}
            if u.path in routes:
                api, fn = routes[u.path]
                if self._auth(api):
                    self._send(200, fn())
                return
            self._send(404, {"message": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            mod_routes = {"/v1/mod/requests": gw.submit, "/v1/mod/approve": gw.approve,
                          "/v1/mod/cancel": gw.cancel, "/v1/mod/undo": gw.undo}
            try:
                if u.path in mod_routes:
                    if self._auth("mod") and (body := self._body()) is not None:
                        self._send(200, mod_routes[u.path](body))
                    return
                m = re.fullmatch(r"/v1/mod/builds/([0-9a-f]{6,12})/report", u.path)
                if m:
                    if self._auth("mod") and (body := self._body()) is not None:
                        self._send(200, gw.report(m.group(1), body))
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
