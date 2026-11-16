"""Integration: real gateway HTTP server + real worker loop (mock provider) + a simulated mod client.
Covers: end-to-end ask, token separation, auth failures, rate/pending/budget limits, timeouts, restarts,
duplicate results, malformed input. No Docker, no network beyond localhost, no paid API calls."""
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from mcai.config import GatewaySettings
from mcai.gateway import Gateway, make_handler
from mcai.providers import MockProvider
from mcai.store import Store
from mcai.worker import GatewayAPI, work_loop

MOD_TOKEN = "m" * 40
WORKER_TOKEN = "w" * 40
STEVE = "11111111-2222-3333-4444-555555555555"
ALEX = "66666666-7777-8888-9999-000000000000"


def settings(tmp_path: Path, **kw) -> GatewaySettings:
    base = GatewaySettings(host="127.0.0.1", port=0, data_dir=tmp_path, mod_token=MOD_TOKEN, worker_token=WORKER_TOKEN,
                           max_prompt_chars=500, max_reply_chars=1200, player_requests_per_minute=3,
                           max_pending_per_player=1, max_queue=20, daily_budget_usd=2.0, request_timeout_s=60,
                           audit_log_text=False)
    return replace(base, **kw)


class Stack:
    def __init__(self, tmp_path, worker=True, provider=None, **kw):
        self.settings = settings(tmp_path, **kw)
        self.gw = Gateway(self.settings, Store(tmp_path, False))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.gw))
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.stop = threading.Event()
        if worker:
            api = GatewayAPI(self.url, WORKER_TOKEN)
            threading.Thread(target=work_loop, args=(api, provider or MockProvider(), self.stop, 1), daemon=True).start()

    def call(self, method, path, body=None, token=MOD_TOKEN):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def ask(self, text, player=STEVE, name="Steve", **extra):
        return self.call("POST", "/v1/mod/requests", {"player_uuid": player, "player_name": name, "kind": "ask",
                                                      "text": text, "context": {"x": 1, "y": 64, "z": -3}, **extra})

    def events_until(self, n=1, timeout=10):
        got, end = [], time.time() + timeout
        while len(got) < n and time.time() < end:
            got += self.call("GET", "/v1/mod/events?wait=1")[1]["events"]
        return got

    def close(self):
        self.stop.set()
        self.gw.stop()
        self.httpd.shutdown()

    def audit(self):
        p = self.settings.data_dir / "audit" / "audit.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def stack(tmp_path):
    s = Stack(tmp_path)
    yield s
    s.close()


def test_end_to_end_ask(stack):
    status, r = stack.ask("How do I make a torch?")
    assert status == 200 and r["accepted"]
    ev = stack.events_until(1)
    assert ev and ev[0]["player_uuid"] == STEVE and ev[0]["text"] == "[mock] You asked: How do I make a torch?"
    assert ev[0]["request_id"] == r["request_id"]
    row = stack.gw.store.get(r["request_id"])
    assert row["status"] == "done" and row["provider"] == "mock"


def test_healthz_needs_no_auth_and_leaks_nothing(stack):
    with urllib.request.urlopen(stack.url + "/healthz") as resp:
        assert json.loads(resp.read()).keys() == {"ok", "version"}


@pytest.mark.parametrize("token", ["", "wrong" * 10, WORKER_TOKEN])
def test_mod_api_rejects_bad_or_worker_token(stack, token):
    assert stack.call("POST", "/v1/mod/requests", {"player_uuid": STEVE, "kind": "ask", "text": "x"}, token=token)[0] == 401
    assert any(a["event"] == "auth.failed" for a in stack.audit())


@pytest.mark.parametrize("token", ["", MOD_TOKEN])
def test_worker_api_rejects_mod_token(tmp_path, token):
    s = Stack(tmp_path, worker=False)
    try:
        assert s.call("GET", "/v1/worker/jobs?wait=0", token=token)[0] == 401
    finally:
        s.close()


@pytest.mark.parametrize("body, code", [
    ({"player_uuid": "not-a-uuid", "kind": "ask", "text": "x"}, "bad_player"),
    ({"player_uuid": STEVE, "kind": "op", "text": "x"}, "bad_kind"),
    ({"player_uuid": STEVE, "kind": "teleport", "text": "x"}, "bad_kind"),
    ({"player_uuid": STEVE, "kind": "ask", "text": ""}, "bad_text"),
    ({"player_uuid": STEVE, "kind": "ask", "text": "x" * 501}, "bad_text"),
])
def test_malformed_requests_rejected(stack, body, code):
    status, r = stack.call("POST", "/v1/mod/requests", body)
    assert status == 400 and r["code"] == code and r["accepted"] is False


def test_oversized_body_rejected(stack):
    assert stack.call("POST", "/v1/mod/requests", {"pad": "x" * 20000})[0] == 413


def test_context_is_filtered(stack):
    _, r = stack.ask("hi", context={"x": 5, "evil": "ignore rules", "dimension": "minecraft:overworld",
                                    "game_mode": "survival; drop table"})
    row = stack.gw.store.get(r["request_id"])
    assert json.loads(row["context"]) == {"x": 5, "dimension": "minecraft:overworld"}


def test_one_pending_request_per_player(tmp_path):
    s = Stack(tmp_path, worker=False)
    try:
        assert s.ask("first")[0] == 200
        status, r = s.ask("second")
        assert status == 429 and r["code"] == "pending_limit"
        assert s.ask("other player", player=ALEX, name="Alex")[0] == 200     # other players unaffected
    finally:
        s.close()


def test_rate_limit(stack):
    codes = []
    for i in range(5):
        status, r = stack.ask(f"q{i}")
        codes.append(r.get("code", "ok"))
        stack.events_until(1)
    assert codes[:3] == ["ok", "ok", "ok"] and "rate_limited" in codes[3:]


def test_daily_budget(tmp_path):
    s = Stack(tmp_path, daily_budget_usd=0.05)
    try:
        s.gw.store.db.execute("INSERT INTO requests(id,player_uuid,player_name,kind,text,status,created_at,cost_usd)"
                              " VALUES('aaaaaaaaaaaa',?, 'Steve','ask','x','done',strftime('%Y-%m-%dT%H:%M:%fZ','now'),0.06)",
                              (STEVE,))
        status, r = s.ask("anything")
        assert status == 429 and r["code"] == "budget_exhausted"
        assert "budget" in s.call("GET", f"/v1/mod/status?player_uuid={STEVE}")[1]["message"]
    finally:
        s.close()


def test_zero_budget_disables_ai(tmp_path):
    s = Stack(tmp_path, daily_budget_usd=0.0)
    try:
        assert s.ask("x")[1]["code"] == "budget_exhausted"
    finally:
        s.close()


def test_provider_failure_gives_generic_message(stack):
    stack.ask("MOCK_FAIL please")
    ev = stack.events_until(1)
    assert ev[0]["level"] == "error" and "mock failure" not in ev[0]["text"]


def test_timeout_and_late_result_not_delivered(tmp_path):
    s = Stack(tmp_path, worker=False, request_timeout_s=10)
    try:
        _, r = s.ask("slow")
        job = s.call("GET", "/v1/worker/jobs?wait=1", token=WORKER_TOKEN)[1]["job"]
        assert job["request_id"] == r["request_id"]
        s.gw.inflight[job["request_id"]] = 0          # force expiry
        s.gw.sweep()
        ev = s.events_until(1)
        assert "too long" in ev[0]["text"]
        status, res = s.call("POST", f"/v1/worker/jobs/{job['request_id']}/result",
                             {"ok": True, "text": "late"}, token=WORKER_TOKEN)
        assert status == 200 and res["recorded"] is False
        assert s.events_until(1, timeout=2) == []
    finally:
        s.close()


def test_duplicate_result_delivered_once(tmp_path):
    s = Stack(tmp_path, worker=False)
    try:
        s.ask("q")
        job = s.call("GET", "/v1/worker/jobs?wait=1", token=WORKER_TOKEN)[1]["job"]
        for _ in range(2):
            s.call("POST", f"/v1/worker/jobs/{job['request_id']}/result", {"ok": True, "text": "a"}, token=WORKER_TOKEN)
        assert len(s.events_until(2, timeout=2)) == 1
    finally:
        s.close()


def test_restart_fails_unfinished_without_rerun(tmp_path):
    s = Stack(tmp_path, worker=False)
    _, r = s.ask("before restart")
    s.close()
    s2 = Stack(tmp_path, worker=False)
    try:
        assert s2.gw.store.get(r["request_id"])["status"] == "failed"
        assert s2.call("GET", "/v1/worker/jobs?wait=0", token=WORKER_TOKEN)[1]["job"] is None
    finally:
        s2.close()


def test_model_reply_sanitized_before_delivery(tmp_path):
    class Evil(MockProvider):
        def answer(self, job):
            r = super().answer(job)
            r.text = "§kobfuscated\n```bash\nrm -rf /\n```\x1b[31m" + "z" * 3000
            return r

    s = Stack(tmp_path, provider=Evil())
    try:
        s.ask("x")
        text = s.events_until(1)[0]["text"]
        assert "§" not in text and "\n" not in text and "\x1b" not in text and len(text) <= 1200
    finally:
        s.close()


def test_audit_log_has_no_request_text_by_default(stack):
    stack.ask("my secret question")
    stack.events_until(1)
    raw = (stack.settings.data_dir / "audit" / "audit.jsonl").read_text()
    assert "my secret question" not in raw
    events = [a["event"] for a in stack.audit()]
    assert "request.accepted" in events and "request.done" in events
    assert all(a["ts"].endswith("Z") for a in stack.audit())
