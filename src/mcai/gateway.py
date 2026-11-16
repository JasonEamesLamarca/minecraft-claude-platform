"""Integration gateway: the only component that talks to both the Minecraft mod and the AI worker.

Mod API (Bearer gateway_mod_token):
  POST /v1/mod/requests        {player_uuid, player_name, kind:"ask", text, context}
  GET  /v1/mod/events?wait=25  long-poll: messages to show to players
  GET  /v1/mod/status?player_uuid=...
Worker API (Bearer worker_token):
  GET  /v1/worker/jobs?wait=25 long-poll: next job
  POST /v1/worker/jobs/<id>/result  {ok, text, error, error_code, cost_usd, input_tokens, output_tokens, provider}
Health: GET /healthz (no auth, no data)

Each token only opens its own API: the mod can't fetch jobs, the worker can't submit requests.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import GatewaySettings, setup_logging
from .ratelimit import RateLimiter
from .sanitize import clean_name, clean_player_text, clean_reply
from .store import Store

log = logging.getLogger("mcai.gateway")
MAX_BODY = 16 * 1024
KINDS = {"ask"}                         # Phase 2: questions only. Build actions arrive in Phase 3.
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CONTEXT_KEYS = {"dimension": str, "x": int, "y": int, "z": int, "yaw": int, "game_mode": str}
GENERIC_FAILURE = "Sorry, the AI could not answer that right now."


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


class Gateway:
    def __init__(self, settings: GatewaySettings, store: Store, clock=time.monotonic):
        self.s = settings
        self.store = store
        self.clock = clock
        self.limiter = RateLimiter(settings.player_requests_per_minute, clock=clock)
        self.cond = threading.Condition()
        self.jobs: deque[str] = deque()
        self.inflight: dict[str, float] = {}           # request id -> deadline (monotonic)
        self.events: deque[dict] = deque(maxlen=1000)  # messages for players, consumed by the mod
        self.last_worker_seen = 0.0
        self._stop = threading.Event()
        for row in store.fail_unfinished("gateway restarted"):
            store.audit("request.failed", request_id=row["id"], player_uuid=row["player_uuid"], reason="gateway restarted")

    # ---------- mod side ----------
    def submit(self, body: dict) -> dict:
        player_uuid = str(body.get("player_uuid", "")).lower()
        if not UUID_RE.match(player_uuid):
            raise Rejected(400, "Invalid player.", "bad_player")
        name = clean_name(body.get("player_name", ""))
        kind = body.get("kind")
        if kind not in KINDS:
            raise Rejected(400, "Unknown request type.", "bad_kind")
        try:
            text = clean_player_text(body.get("text", ""), self.s.max_prompt_chars)
        except ValueError as e:
            raise Rejected(400, str(e), "bad_text") from None
        context = clean_context(body.get("context"))

        def reject(status, msg, code):
            self.store.audit("request.rejected", player_uuid=player_uuid, player_name=name, kind=kind, reason=code)
            raise Rejected(status, msg, code)

        if not self.authorize(player_uuid, kind):
            reject(403, "You are not allowed to use the AI.", "forbidden")
        if self.s.daily_budget_usd <= 0 or self.store.spend_today() >= self.s.daily_budget_usd:
            reject(429, "The AI has reached today's budget. Try again tomorrow.", "budget_exhausted")
        with self.cond:   # checks + enqueue under one lock, so two quick requests can't both pass
            if len(self.store.pending_for(player_uuid)) >= self.s.max_pending_per_player:
                reject(429, "You already have a request in progress. Wait for the answer first.", "pending_limit")
            allowed, wait_s = self.limiter.allow(player_uuid)
            if not allowed:
                reject(429, f"Too many requests. Try again in {int(wait_s) + 1} s.", "rate_limited")
            if len(self.jobs) >= self.s.max_queue:
                reject(503, "The AI is busy. Try again in a minute.", "queue_full")
            rid = self.store.create_request(player_uuid, name, kind, text, context)
            self.jobs.append(rid)
            self.cond.notify_all()
        self.store.audit("request.accepted", request_id=rid, player_uuid=player_uuid, player_name=name, kind=kind,
                         text=text)
        return {"accepted": True, "request_id": rid, "message": "Thinking..."}

    def authorize(self, player_uuid: str, kind: str) -> bool:
        """Phase 2 policy: every player who could join (whitelist enforced by the server) may ask questions.
        Role-based access control replaces this in Phase 4."""
        return kind == "ask"

    def notify(self, player_uuid: str, text: str, level: str = "info", request_id: str | None = None) -> None:
        with self.cond:
            self.events.append({"id": uuid.uuid4().hex[:8], "player_uuid": player_uuid, "text": text,
                                "level": level, "request_id": request_id})
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
        parts = [f"{len(pending)} request(s) in progress" if pending else "No requests in progress",
                 f"AI budget today: ${spent:.2f} of ${self.s.daily_budget_usd:.2f}",
                 "AI worker: online" if worker_ok else "AI worker: offline"]
        return {"message": " | ".join(parts), "pending": [dict(r) for r in pending], "spent_today_usd": round(spent, 4),
                "budget_usd": self.s.daily_budget_usd, "worker_online": worker_ok}

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
                    self.inflight[rid] = self.clock() + self.s.request_timeout_s
                    row = self.store.get(rid)
                    return {"request_id": rid, "kind": row["kind"], "text": row["text"],
                            "player_name": row["player_name"], "context": json.loads(row["context"]),
                            "timeout_s": self.s.request_timeout_s}
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
        reply = clean_reply(result.get("text", ""), self.s.max_reply_chars) if ok else None
        error = str(result.get("error", ""))[:500] or None
        code = str(result.get("error_code", ""))[:40]
        cost = float(result.get("cost_usd") or 0)
        first = self.store.finish(rid, ok, reply, error, cost, int(result.get("input_tokens") or 0),
                                  int(result.get("output_tokens") or 0), str(result.get("provider", ""))[:40])
        with self.cond:
            self.inflight.pop(rid, None)
        if not first:
            return False   # duplicate or late (already timed out): ignore, never deliver twice
        self.store.audit("request.done" if ok else "request.failed", request_id=rid, player_uuid=row["player_uuid"],
                         cost_usd=round(cost, 6), provider=result.get("provider"), error_code=code or None)
        if ok:
            self.notify(row["player_uuid"], reply, "info", rid)
        elif code == "not_configured":
            self.notify(row["player_uuid"], "The AI is not set up on this server yet. Ask the server owner.", "error", rid)
        else:
            self.notify(row["player_uuid"], GENERIC_FAILURE, "error", rid)
        return True

    def sweep(self) -> None:
        """Fail requests whose worker never answered in time."""
        now = self.clock()
        with self.cond:
            expired = [rid for rid, dl in self.inflight.items() if dl < now]
            for rid in expired:
                self.inflight.pop(rid, None)
        for rid in expired:
            row = self.store.get(rid)
            if row and self.store.finish(rid, False, None, "timeout", 0.0, 0, 0, ""):
                self.store.audit("request.failed", request_id=rid, player_uuid=row["player_uuid"], reason="timeout")
                self.notify(row["player_uuid"], "Sorry, that took too long and was cancelled.", "error", rid)

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
            if n < 0 or n > MAX_BODY:
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
            if u.path == "/healthz":
                return self._send(200, {"ok": True, "version": __version__})
            if u.path == "/v1/mod/events":
                if self._auth("mod"):
                    self._send(200, {"events": gw.take_events(self._wait(q))})
                return
            if u.path == "/v1/mod/status":
                if self._auth("mod"):
                    self._send(200, gw.status(q.get("player_uuid", [""])[0].lower()))
                return
            if u.path == "/v1/worker/jobs":
                if self._auth("worker"):
                    job = gw.next_job(self._wait(q))
                    self._send(200, {"job": job})
                return
            self._send(404, {"message": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            try:
                if u.path == "/v1/mod/requests":
                    if self._auth("mod") and (body := self._body()) is not None:
                        self._send(200, gw.submit(body))
                    return
                m = re.fullmatch(r"/v1/worker/jobs/([0-9a-f]{12})/result", u.path)
                if m:
                    if self._auth("worker") and (body := self._body()) is not None:
                        self._send(200, {"recorded": gw.complete(m.group(1), body)})
                    return
                self._send(404, {"message": "not found"})
            except Rejected as e:
                self._send(e.status, {"accepted": False, "message": e.message, "code": e.code})
            except Exception:                         # never leak internals to the caller
                log.exception("unhandled error on %s", u.path)
                self._send(500, {"accepted": False, "message": "internal error"})

    return Handler


def serve(settings: GatewaySettings | None = None) -> None:
    setup_logging("gateway")
    settings = settings or GatewaySettings.from_env()
    gw = Gateway(settings, Store(settings.data_dir, settings.audit_log_text))
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(gw))
    httpd.daemon_threads = True

    def sweeper():
        while not gw._stop.wait(5):
            gw.sweep()

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
