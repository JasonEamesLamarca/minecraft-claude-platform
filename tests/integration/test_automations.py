"""Automation engine with a fake wall clock: schedules, restarts without duplicate runs, events and their
deduplication, guards (loops, rates, AI budgets), retries and failure policies, owner permissions, approval of
privileged and AI-proposed automations, game events over HTTP, and the `mcai automations` commands."""
import datetime as dt
import json
import time
from pathlib import Path

import pytest

from mcai.automation import parse_definition
from mcai.gateway import Gateway
from mcai.rbac import Actor
from mcai.store import Store
from test_build_flow import mk
from test_gateway_flow import ALEX, MOD_TOKEN, STEVE, settings
from test_management import MGMT_TOKEN, env, keypair  # noqa: F401  (env is a fixture)

REPO = Path(__file__).resolve().parents[2]
T0 = dt.datetime(2026, 11, 18, 10, 0, 20, tzinfo=dt.timezone.utc).timestamp()
ADMIN = Actor("account", "ann", "ann", "admin")
OWNER = Actor("account", "boss", "boss", "owner")


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def gateway(path, clock, **kw):
    path.mkdir(parents=True, exist_ok=True)
    gw = Gateway(settings(path, **kw), Store(path, False), wallclock=clock)
    for name, role in (("ann", "admin"), ("boss", "owner"), ("otto", "operator")):
        gw.store.create_account(name, role, "test")
    return gw


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def gw(tmp_path, clock):
    g = gateway(tmp_path / "gw", clock)
    yield g
    g.stop()


def defn(**kw):
    d = {"id": "t1", "name": "Test", "enabled": True, "trigger": {"every": "1h"},
         "actions": [{"type": "log", "text": "hello"}]}
    d.update(kw)
    return parse_definition(d)


def save(gw, actor=ADMIN, **kw):
    return gw.automation.save(actor, defn(**kw))


def online(gw, *names):
    gw.heartbeat = {"players": list(names)}
    gw.heartbeat_at = gw.clock()


def runs(gw, aid="t1"):
    return gw.store.runs_list(aid, 1000)


def audit(gw, prefix):
    return [a for a in gw.store.audit_records(limit=100000) if a["event"].startswith(prefix)]


def events(gw, typ):
    return [e for e in gw.events if e.get("type") == typ]


def next_slot(gw, aid="t1"):
    return gw.store.automation(aid)["next_run_at"]


# ------------------------------------------------------------------ schedules and restarts
def test_schedule_runs_once_per_slot(gw, clock):
    online(gw, "Steve")
    save(gw, actions=[{"type": "broadcast", "text": "on the hour, {players_online} online"}])
    slot = next_slot(gw)
    assert slot == T0 - 20 + 3600, "aligned to the hour"
    gw.automation.tick()
    assert not runs(gw)
    clock.t = slot
    gw.automation.tick()
    gw.automation.tick()
    assert [r["status"] for r in runs(gw)] == ["succeeded"]
    assert events(gw, "broadcast")[0]["text"] == "on the hour, 1 online"
    clock.advance(3600)
    gw.automation.tick()
    assert len(runs(gw)) == 2


def test_restart_never_repeats_a_slot(tmp_path, clock):
    gw1 = gateway(tmp_path, clock)
    save(gw1)
    slot = next_slot(gw1)
    clock.t = slot + 1
    gw1.automation.tick()
    assert len(runs(gw1)) == 1
    # crash between creating the run and moving to the next slot: the slot comes round again after the restart
    gw1.store.automation_update("t1", next_run_at=slot)
    gw1.stop()
    gw2 = gateway(tmp_path, clock)
    gw2.automation.tick()
    assert len(runs(gw2)) == 1, "the slot's idempotency key prevents a second run"
    assert next_slot(gw2) == slot + 3600
    gw2.stop()


def test_run_interrupted_by_a_restart_is_not_repeated(tmp_path, clock):
    gw1 = gateway(tmp_path, clock)
    save(gw1, trigger={"event": "server.started"})
    rid = gw1.automation.emit("server.started", {}, key="start:1")[0]
    assert gw1.store.run_claim(rid, clock())                # executing when the gateway dies
    gw1.stop()
    gw2 = gateway(tmp_path, clock)
    assert gw2.store.run(rid)["status"] == "interrupted"
    gw2.automation.tick()
    assert gw2.store.run(rid)["status"] == "interrupted" and not audit(gw2, "automation.log")
    assert gw2.automation.emit("server.started", {}, key="start:1") == [], "the event is not taken again either"
    gw2.stop()


def test_missed_slots_skip_by_default(gw, clock):
    save(gw)
    gw.store.automation_update("t1", next_run_at=clock() - 7200)
    gw.automation.tick()
    assert not runs(gw) and audit(gw, "automation.misfire")
    assert next_slot(gw) > clock()


def test_missed_slots_run_once_when_asked(gw, clock):
    save(gw, limits={"misfire": "run"})
    gw.store.automation_update("t1", next_run_at=clock() - 3 * 3600)      # three slots missed
    gw.automation.tick()
    gw.automation.tick()
    assert len(runs(gw)) == 1


def test_short_delay_is_not_a_misfire(gw, clock):
    save(gw)
    clock.t = next_slot(gw) + 120                    # gateway busy/restarting for 2 minutes (grace 300 s)
    gw.automation.tick()
    assert [r["status"] for r in runs(gw)] == ["succeeded"]


def test_schedule_conditions_record_a_skip(gw, clock):
    save(gw, conditions={"players_online_min": 1}, actions=[{"type": "broadcast", "text": "x"}])
    clock.t = next_slot(gw)
    gw.automation.tick()
    r = runs(gw)[0]
    assert r["status"] == "skipped" and "0 player(s) online" in r["error"]
    assert not events(gw, "broadcast")


def test_time_window_in_the_automation_timezone(gw, clock):
    # 10:00 UTC is 11:00 in Berlin
    save(gw, timezone="Europe/Berlin", trigger={"event": "server.started"},
         conditions={"time_between": ["11:00", "11:30"], "days": ["wed"]})
    assert gw.automation.emit("server.started", {}, key="a")
    gw.automation.save(ADMIN, defn(timezone="UTC", trigger={"event": "server.started"},
                                   conditions={"time_between": ["11:00", "11:30"]}), replace=True)
    assert gw.automation.emit("server.started", {}, key="b") == []


# ------------------------------------------------------------------ events
def test_player_events_and_dedup(gw, clock):
    save(gw, trigger={"event": "player.join"}, actions=[{"type": "log", "text": "{player} joined"}])
    gw.automation.save(ADMIN, defn(id="t2", trigger={"event": "player.first_join"}))
    join = {"event": "join", "player_uuid": STEVE, "player_name": "Steve"}
    gw.player_event(join)
    gw.player_event({**join, "event": "leave"})
    gw.player_event(join)
    assert len(runs(gw)) == 2 and len(runs(gw, "t2")) == 1, "two sessions, one first join"
    assert gw.automation.emit("player.join", {"player_uuid": STEVE, "player_name": "Steve"}, key="session:1") == []
    gw.automation.tick()
    assert sorted(a["message"] for a in audit(gw, "automation.log")) == ["Steve joined", "Steve joined", "hello"]


def test_loop_and_chain_guards(gw, clock):
    save(gw, trigger={"event": "build.completed"}, actions=[{"type": "log", "text": "{title}"}])
    data = {"player_uuid": STEVE, "player_name": "Steve", "title": "Tower"}
    assert gw.automation.emit("build.completed", data, key="k1", chain=("t1",)) == []
    assert gw.automation.emit("build.completed", data, key="k2", chain=("a", "b", "c")) == []
    assert gw.automation.emit("build.completed", data, key="k3", chain=("other",))
    reasons = [a["reason"] for a in audit(gw, "automation.guard")]
    assert any("loop" in r for r in reasons) and any("deeper" in r for r in reasons)


def test_build_started_by_an_automation_cannot_retrigger_it(gw, clock):
    save(gw, trigger={"event": "build.completed"})
    run_id = gw.store.run_create("t1", 1, "x", "manual", "", {}, [], clock())
    rid = gw.store.create_request("00000000-0000-0000-0000-000000000000", "Automation", "build", "a tower", {},
                                  automation_run=run_id)
    gw.store.transition(rid, ("queued",), "approved", summary=json.dumps({"title": "Tower"}))
    assert gw.report(rid, {"status": "built", "placed": 9})["recorded"]
    assert len(runs(gw)) == 1, "the build.completed caused by t1 does not start t1 again"


def test_rate_limits_and_cooldown(gw, clock):
    save(gw, trigger={"event": "player.join"}, limits={"max_runs_per_hour": 2})
    for i in range(3):
        gw.automation.emit("player.join", {"player_uuid": STEVE, "player_name": "Steve"}, key=f"s{i}")
    assert len(runs(gw)) == 2 and audit(gw, "automation.guard")
    gw.automation.save(ADMIN, defn(id="t2", trigger={"event": "player.join"}, limits={"cooldown_s": 60}))
    emit = gw.automation.emit
    assert emit("player.join", {"player_uuid": STEVE, "player_name": "Steve"}, key="c1", only={"t2"})
    assert not emit("player.join", {"player_uuid": STEVE, "player_name": "Steve"}, key="c2", only={"t2"})
    assert emit("player.join", {"player_uuid": ALEX, "player_name": "Alex"}, key="c3", only={"t2"}), "per player"
    clock.advance(61)
    assert emit("player.join", {"player_uuid": STEVE, "player_name": "Steve"}, key="c4", only={"t2"})


def test_global_run_rate(gw, clock):
    gw.automation.max_runs_per_minute = 3
    save(gw, trigger={"event": "server.started"}, limits={"max_runs_per_hour": 100})
    created = [gw.automation.emit("server.started", {}, key=f"k{i}") for i in range(5)]
    assert sum(len(c) for c in created) == 3
    clock.advance(61)
    assert gw.automation.emit("server.started", {}, key="k9")


# ------------------------------------------------------------------ execution
def test_retry_resumes_at_the_failed_action(gw, clock):
    save(gw, trigger={"event": "server.started"}, limits={"retries": 2, "retry_delay_s": 10},
         actions=[{"type": "log", "text": "one"}, {"type": "broadcast", "text": "two"}])
    rid = gw.automation.emit("server.started", {}, key="a")[0]
    gw.automation.tick()                                    # server offline: the broadcast fails
    r = gw.store.run(rid)
    assert r["status"] == "retry_wait" and r["attempt"] == 1 and r["step"] == 1
    clock.advance(5)
    gw.automation.tick()
    assert gw.store.run(rid)["status"] == "retry_wait"
    online(gw)
    clock.advance(6)
    gw.automation.tick()
    r = gw.store.run(rid)
    assert r["status"] == "succeeded" and len(json.loads(r["output"])) == 3
    assert len(audit(gw, "automation.log")) == 1, "the finished first action is not repeated"


def test_retries_exhausted_then_failed(gw, clock):
    save(gw, trigger={"event": "server.started"}, limits={"retries": 1, "retry_delay_s": 5},
         actions=[{"type": "broadcast", "text": "x"}])
    rid = gw.automation.emit("server.started", {}, key="a")[0]
    gw.automation.tick()
    clock.advance(6)
    gw.automation.tick()
    assert gw.store.run(rid)["status"] == "failed" and gw.store.automation("t1")["failures"] == 1


def test_on_error_disable_and_circuit_breaker(gw, clock):
    save(gw, trigger={"event": "server.started"}, limits={"on_error": "disable"}, actions=[{"type": "save"}])
    gw.automation.emit("server.started", {}, key="a")
    gw.automation.tick()                                     # no RCON: not retryable
    assert gw.store.automation("t1")["state"] == "suspended" and audit(gw, "automation.suspended")
    gw.automation.save(ADMIN, defn(id="t2", trigger={"event": "server.started"}, actions=[{"type": "save"}]))
    for i in range(5):
        gw.automation.emit("server.started", {}, key=f"b{i}", only={"t2"})
        gw.automation.tick()
    row = gw.store.automation("t2")
    assert row["state"] == "suspended" and "5 failed" in row["note"]


def test_on_error_continue(gw, clock):
    save(gw, trigger={"event": "server.started"}, limits={"on_error": "continue"},
         actions=[{"type": "save"}, {"type": "log", "text": "after"}])
    rid = gw.automation.emit("server.started", {}, key="a")[0]
    gw.automation.tick()
    assert gw.store.run(rid)["status"] == "partial" and audit(gw, "automation.log")


def test_timeout_between_actions(gw, clock, monkeypatch):
    save(gw, trigger={"event": "server.started"}, limits={"timeout_s": 2},
         actions=[{"type": "log", "text": "a"}, {"type": "log", "text": "b"}])
    real = gw.automation._do

    def slow(*a, **k):
        out = real(*a, **k)
        clock.advance(5)
        return out
    monkeypatch.setattr(gw.automation, "_do", slow)
    rid = gw.automation.emit("server.started", {}, key="a")[0]
    gw.automation.tick()
    r = gw.store.run(rid)
    assert r["status"] == "failed" and "timeout" in r["error"] and len(audit(gw, "automation.log")) == 1


def test_owner_losing_a_permission_suspends(gw, clock):
    save(gw, trigger={"event": "server.started"}, actions=[{"type": "broadcast", "text": "x"}])
    gw.store.update_account("ann", role="operator")
    rid = gw.automation.emit("server.started", {}, key="a")[0]
    gw.automation.tick()
    assert gw.store.run(rid)["status"] == "denied"
    assert gw.store.automation("t1")["state"] == "suspended"
    with pytest.raises(Exception, match="no longer has"):
        gw.automation.set_enabled(OWNER, "t1", True)
    gw.store.update_account("ann", role="admin")
    assert "enabled" in gw.automation.set_enabled(OWNER, "t1", True)


def test_privileged_automation_needs_the_owner(gw, clock):
    promote = dict(id="promote", trigger={"event": "player.join"},
                   conditions={"player_roles": ["player"], "playtime_hours_min": 1},
                   actions=[{"type": "set_role", "role": "builder"}])
    r = gw.automation.save(ADMIN, defn(**promote))
    assert r["state"] == "pending_approval"
    with pytest.raises(Exception, match="approval"):
        gw.automation.set_enabled(ADMIN, "promote", True)
    with pytest.raises(Exception, match="only the owner"):
        gw.automation.approve(ADMIN, "promote")
    assert "Approved" in gw.automation.approve(OWNER, "promote")
    gw.store.player_seen(STEVE, "Steve")
    gw.store.player_seen(ALEX, "Alex")
    gw.store.set_player_role(ALEX, "admin")
    gw.store.db.execute("INSERT INTO sessions(player_uuid, name, joined_at, left_at) VALUES (?,?,?,?)",
                        (STEVE, "Steve", "2026-11-01T10:00:00.000Z", "2026-11-01T12:00:00.000Z"))
    gw.player_event({"event": "join", "player_uuid": STEVE, "player_name": "Steve"})
    gw.player_event({"event": "join", "player_uuid": ALEX, "player_name": "Alex"})   # admin: condition fails
    gw.automation.tick()
    assert gw.store.player(STEVE)["role"] == "builder" and gw.store.player(ALEX)["role"] == "admin"
    change = audit(gw, "role.changed")[-1]
    assert change["by"] == "automation:promote" and change["new_role"] == "builder"


def test_owner_created_privileged_is_active(gw):
    r = gw.automation.save(OWNER, defn(trigger={"event": "player.join"},
                                        actions=[{"type": "set_role", "role": "builder"}]))
    assert r["state"] == "enabled"


def test_creator_needs_the_permissions(gw):
    with pytest.raises(Exception, match="your role"):
        gw.automation.save(Actor("account", "otto", "otto", "operator"), defn())


def test_manual_run_of_a_disabled_automation(gw, clock):
    save(gw, enabled=False)
    rid = gw.automation.run_now(ADMIN, "t1")
    gw.automation.tick()
    assert gw.store.run(rid)["status"] == "succeeded" and gw.store.automation("t1")["state"] == "disabled"


def test_heartbeat_thresholds_world_and_start(tmp_path, clock):
    gw = gateway(tmp_path, clock)
    gw.automation.save(ADMIN, defn(id="lag", trigger={"event": "server.threshold", "metric": "mspt", "above": 45,
                                                       "for_s": 60}))
    gw.automation.save(ADMIN, defn(id="night", trigger={"event": "world.night_start"}))
    gw.automation.save(ADMIN, defn(id="boot", trigger={"event": "server.started"}))

    def hb(mspt, time_=1000, up=30):
        gw.heartbeat_in({"players": [], "mspt": mspt, "uptime_s": up, "world": {"time": time_, "raining": False}})
    hb(60)
    clock.advance(30)
    hb(60, up=60)
    assert not runs(gw, "lag")
    clock.advance(31)
    hb(60, up=91)
    clock.advance(30)
    hb(60, up=121)
    assert len(runs(gw, "lag")) == 1, "fires once while the condition holds"
    gw.stop()
    gw = gateway(tmp_path, clock)                         # restart while still lagging: no second alert
    hb(60, up=151)
    assert len(runs(gw, "lag")) == 1
    hb(10, up=181)
    hb(60, up=211)
    clock.advance(61)
    hb(60, up=241)
    assert len(runs(gw, "lag")) == 2, "re-armed after recovering"
    hb(10, time_=13500, up=271)
    hb(10, time_=13600, up=301)
    assert len(runs(gw, "night")) == 1
    assert len(runs(gw, "boot")) == 1
    hb(10, time_=13700, up=20)                             # server restarted
    assert len(runs(gw, "boot")) == 2
    gw.stop()


# ------------------------------------------------------------------ HTTP: mod game events
@pytest.fixture
def stack(tmp_path, clock):
    s = mk(tmp_path)
    s.gw.automation.now = clock
    for name, role in (("ann", "admin"), ("boss", "owner")):
        s.gw.store.create_account(name, role, "test")
    yield s
    s.close()


def game(s, **body):
    body.setdefault("event_id", "0a1b2c3d-0000-0000-0000-000000000001")
    body.setdefault("player_uuid", STEVE)
    body.setdefault("player_name", "Steve")
    return s.call("POST", "/v1/mod/game-events", body)


def test_areas_served_and_entered(stack):
    gw = stack.gw
    gw.automation.save(ADMIN, defn(id="plaza", trigger={"event": "player.enter_area", "area": {
        "name": "plaza", "from": [0, 60, 0], "to": [10, 80, 10]}}, actions=[{"type": "log", "text": "{player} {area}"}]))
    st, r = stack.call("POST", "/v1/mod/heartbeat", {"players": [], "mspt": 5})
    assert st == 200 and r["areas"] == [{"id": "plaza", "on": "enter", "dimension": "minecraft:overworld",
                                         "from": [0, 60, 0], "to": [10, 80, 10]}]
    assert game(stack, type="area_enter", area="plaza")[1]["started"] == 1
    assert game(stack, type="area_enter", area="plaza")[1]["started"] == 0, "same event id: no second run"
    assert game(stack, type="area_enter", area="nowhere")[1]["started"] == 0
    assert game(stack, type="area_enter", area="plaza", event_id="x")[0] == 400
    gw.automation.tick()
    assert audit(gw, "automation.log")[0]["message"] == "Steve plaza"


def test_trigger_command(stack):
    gw = stack.gw
    gw.automation.save(ADMIN, defn(id="tour", trigger={"event": "player.trigger", "name": "tour"},
                                   conditions={"player_role_min": "builder"}))
    st, r = game(stack, type="trigger", name="nope")
    assert st == 404 and "nothing called" in r["message"]
    st, r = game(stack, type="trigger", name="tour", player_uuid=ALEX, player_name="Alex")
    assert st == 200 and not r["ok"] and "can't start" in r["message"]
    st, r = game(stack, type="trigger", name="tour", event_id="0a1b2c3d-0000-0000-0000-000000000002")
    assert r["ok"] and r["message"] == "Started 'tour'."
    assert game(stack, type="trigger", name="tour", player_uuid="00000000-0000-0000-0000-000000000000")[0] == 400


def test_advancement_glob(stack):
    gw = stack.gw
    gw.automation.save(ADMIN, defn(id="adv", trigger={"event": "player.advancement",
                                                     "advancement": "minecraft:story/*"},
                                   actions=[{"type": "log", "text": "{player}: {advancement}"}]))
    assert game(stack, type="advancement", advancement="minecraft:story/mine_stone", title="Stone Age")[1]["started"] == 1
    assert game(stack, type="advancement", advancement="minecraft:story/mine_stone", title="Stone Age",
                event_id="0a1b2c3d-0000-0000-0000-000000000009")[1]["started"] == 0, "once per player"
    assert game(stack, type="advancement", advancement="minecraft:nether/root")[1]["started"] == 0
    assert game(stack, type="advancement", advancement="NOT VALID")[0] == 400
    gw.automation.tick()
    assert audit(gw, "automation.log")[0]["message"] == "Steve: Stone Age"


def test_broadcast_reaches_the_mod(stack):
    gw = stack.gw
    online(gw, "Steve")
    gw.automation.save(ADMIN, defn(trigger={"event": "server.started"}, actions=[{"type": "broadcast", "text": "hi all"}]))
    gw.automation.emit("server.started", {}, key="a")
    gw.automation.tick()
    assert any(e["type"] == "broadcast" and e["text"] == "hi all" for e in stack.events_until(1))


def test_ai_message_and_its_limits(stack, clock):
    gw = stack.gw
    online(gw, "Steve")
    gw.automation.save(ADMIN, defn(trigger={"event": "server.started"},
                                   limits={"ai_max_usd_per_day": 0.2, "ai_max_tokens_per_day": 5},
                                   actions=[{"type": "ai_message", "prompt": "a challenge for {date}", "to": "broadcast"}]))
    rid = gw.automation.emit("server.started", {}, key="a")[0]
    gw.automation.tick()
    run = gw.store.run(rid)
    assert run["status"] == "succeeded", run["error"]
    msgs = [e for e in stack.events_until(1) if e["type"] == "broadcast"]
    assert msgs and msgs[0]["text"].startswith("[mock] You asked: a challenge for 2026-11-18")
    req = [r for r in gw.store.recent_requests(10) if r["automation_run"] == rid][0]
    assert req["max_usd"] == 0.05 and req["status"] == "done"
    rid2 = gw.automation.emit("server.started", {}, key="b")[0]
    gw.automation.tick()
    assert "token limit" in gw.store.run(rid2)["error"], "mock usage already exceeds 5 tokens today"


def test_ai_off_and_global_ai_rate(stack):
    gw = stack.gw
    online(gw, "Steve")
    gw.automation.save(ADMIN, defn(trigger={"event": "server.started"}, limits={"ai_max_usd_per_day": 1.0},
                                   actions=[{"type": "ai_message", "prompt": "x"}]))
    gw.automation.ai_calls_per_hour = 0
    rid = gw.automation.emit("server.started", {}, key="a")[0]
    gw.automation.tick()
    assert "AI calls in the last hour" in gw.store.run(rid)["error"]
    gw.store.set_setting("ai.enabled", "false", "test")
    gw.automation.ai_calls_per_hour = 10
    rid = gw.automation.emit("server.started", {}, key="b")[0]
    gw.automation.tick()
    assert "switched off" in gw.store.run(rid)["error"]


# ------------------------------------------------------------------ management commands and AI proposals
def wait_request(gw, rid, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        r = gw.store.get(rid)
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.05)
    raise AssertionError("request not finished")


WELCOME = (REPO / "automation-examples" / "welcome.toml").read_text()


def run_input(env, who, text, *argv):
    st, r = env.call("POST", "/v1/mgmt/run", {"key": env.keys[who], "source_ip": "192.0.2.1", "session": "t",
                                               "argv": list(argv), "input": text}, token=MGMT_TOKEN)
    assert st == 200
    return r


def test_management_permissions(env):
    env.account("audrey", "auditor")
    env.account("olga", "operator")
    env.account("adam", "admin")
    assert env.run("audrey", "automations", "list")["ok"]
    r = run_input(env, "olga", WELCOME, "automations", "import", "--stdin")
    assert r["code"] == "forbidden" and "automations.manage" in r["text"]
    r = run_input(env, "adam", WELCOME, "automations", "import", "--stdin")
    assert r["ok"] and r["data"]["state"] == "disabled", r
    assert not run_input(env, "adam", WELCOME, "automations", "import", "--stdin")["ok"], "exists"
    assert env.run("audrey", "auto", "show", "welcome")["data"]["definition"]["id"] == "welcome"
    assert env.run("audrey", "automations", "enable", "welcome")["code"] == "forbidden"
    assert env.run("adam", "automations", "enable", "welcome")["ok"]
    assert env.run("adam", "automations", "list")["data"][0]["state"] == "enabled"
    cmds = [a for a in env.audit() if a["event"] == "mgmt.command" and a["command"].startswith("automations")]
    assert {c["command"] for c in cmds} >= {"automations import", "automations enable"}


def test_validate_export_replace_delete(env):
    r = run_input(env, "owner", WELCOME, "automations", "validate", "--stdin")
    assert r["ok"] and "Trigger: on player.first_join" in r["text"]
    assert not run_input(env, "owner", "id = ", "automations", "validate", "--stdin")["ok"]
    assert env.run("owner", "automations", "import", "--stdin")["code"] == "usage", "no input"
    run_input(env, "owner", WELCOME, "automations", "import", "--stdin")
    exported = env.run("owner", "automations", "export", "welcome")["text"]
    changed = exported.replace("Say hello!", "Wave!")
    r = run_input(env, "owner", changed, "automations", "import", "--stdin", "--replace")
    assert r["ok"] and r["data"]["version"] == 2
    assert env.run("owner", "automations", "delete", "welcome")["code"] == "confirm"
    assert env.run("owner", "automations", "delete", "welcome", "--yes")["ok"]
    assert env.run("owner", "automations", "show", "welcome")["code"] == "not_found"


def test_examples_install(env):
    env.gw.automation.examples_dir = REPO / "automation-examples"
    env.account("adam", "admin")
    r = env.run("adam", "automations", "examples")
    assert r["ok"] and len(r["data"]) >= 10 and all("error" not in e for e in r["data"])
    assert env.run("adam", "automations", "install", "spawn-tour")["data"]["state"] == "disabled"
    assert env.run("adam", "automations", "install", "promote-builders")["data"]["state"] == "pending_approval"
    assert env.run("adam", "automations", "approve", "promote-builders")["code"] == "forbidden"
    assert env.run("owner", "automations", "approve", "promote-builders")["ok"]
    assert env.run("adam", "automations", "install", "nope")["code"] == "not_found"


def test_run_now_and_history(env):
    run_input(env, "owner", WELCOME, "automations", "import", "--stdin")
    assert env.run("owner", "automations", "run", "welcome")["code"] == "usage", "acts on a player"
    env.gw.store.player_seen(STEVE, "Steve")
    r = env.run("owner", "automations", "run", "welcome", "--player", "Steve")
    assert r["ok"]
    env.gw.heartbeat_at = env.gw.clock()
    env.gw.automation.tick()
    h = env.run("owner", "automations", "runs", "welcome")
    assert h["data"][0]["status"] == "succeeded" and h["data"][0]["trigger"] == "manual"


def test_ai_proposals_need_approval(env):
    env.account("adam", "admin")
    r = env.run("adam", "automations", "propose", "greet", "everyone", "in", "the", "evening")
    assert r["ok"], r
    req = wait_request(env.gw, r["data"]["request_id"])
    assert req["status"] == "done" and "mock-daily" in req["reply"]
    row = env.gw.store.automation("mock-daily")
    assert row["state"] == "proposed" and row["owner"] == "account:adam" and row["source"] == "ai"
    assert env.run("adam", "automations", "enable", "mock-daily")["code"] == "bad_state"
    assert env.run("adam", "automations", "approve", "mock-daily")["ok"]
    assert env.gw.store.automation("mock-daily")["state"] == "enabled"

    bad = env.run("adam", "automations", "propose", "MOCK_BAD_AUTOMATION")
    req = wait_request(env.gw, bad["data"]["request_id"])
    assert req["status"] == "failed" and "invalid automation" in req["error"] and "can't run commands" in req["error"]

    priv = env.run("adam", "automations", "propose", "MOCK_PRIVILEGED")
    wait_request(env.gw, priv["data"]["request_id"])
    assert env.gw.store.automation("mock-promote")["state"] == "proposed"
    assert env.run("adam", "automations", "approve", "mock-promote")["code"] == "forbidden"
    assert env.run("owner", "automations", "reject", "mock-promote", "not", "now")["ok"]
    assert env.gw.store.automation("mock-promote")["state"] == "rejected"
    assert [a["event"] for a in env.audit() if a["event"].startswith("automation.")].count("automation.proposed") == 2


def test_proposal_needs_ai_rights(env):
    env.account("olga", "operator")
    assert env.run("olga", "automations", "propose", "x")["code"] == "forbidden"
    env.run("owner", "ai", "disable")
    assert env.run("owner", "automations", "propose", "x")["code"] == "unavailable"


def test_mod_token_cannot_manage_automations(stack):
    assert stack.call("POST", "/v1/mgmt/run", {"argv": ["automations", "list"]}, token=MOD_TOKEN)[0] == 401
