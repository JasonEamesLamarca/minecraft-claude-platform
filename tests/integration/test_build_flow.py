"""Build lifecycle through a real gateway + worker (mock provider) + simulated mod:
request -> proposal -> preview -> approve -> execute order -> report -> undo, plus every refusal path."""
import json

import pytest

from test_gateway_flow import ALEX, STEVE, WORKER_TOKEN, Stack

CTX = {"x": 100, "y": 64, "z": 200, "dimension": "minecraft:overworld", "yaw": 0}


def build(s, text="a small tower", player=STEVE, name="Steve", op_level=4):
    return s.call("POST", "/v1/mod/requests", {"player_uuid": player, "player_name": name, "kind": "build",
                                               "text": text, "context": CTX, "op_level": op_level})


def wait_event(s, etype, timeout=10):
    for e in s.events_until(1, timeout=timeout):
        if e.get("type") == etype:
            return e
    raise AssertionError(f"no {etype} event")


def proposed(s, **kw):
    status, r = build(s, **kw)
    assert status == 200, r
    ev = wait_event(s, "preview")
    assert ev["request_id"] == r["request_id"]
    return r["request_id"], ev


@pytest.fixture
def stack(tmp_path):
    s = Stack(tmp_path)
    yield s
    s.close()


def test_full_lifecycle(stack):
    rid, preview = proposed(stack)
    assert "/ai approve " + rid in preview["text"] and preview["summary"]["block_count"] == 37
    assert preview["blocks"] == ["minecraft:stone_bricks", "minecraft:torch"]
    assert stack.gw.store.get(rid)["status"] == "proposed"

    st, r = stack.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})
    assert st == 200 and r["ok"]
    ex = wait_event(stack, "execute")
    assert ex["request_id"] == rid and ex["plan"]["actions"][0]["op"] == "fill"
    assert stack.gw.store.get(rid)["status"] == "approved"

    assert stack.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "built", "placed": 37})[1]["recorded"]
    msg = wait_event(stack, "message")
    assert "37 blocks placed" in msg["text"] and f"/ai undo {rid}" in msg["text"]
    assert stack.gw.store.get(rid)["status"] == "built"

    st, r = stack.call("POST", "/v1/mod/undo", {"player_uuid": STEVE, "op_level": 4})     # no id: latest build
    assert st == 200
    rb = wait_event(stack, "rollback")
    assert rb["request_id"] == rid
    stack.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "rolled_back"})
    assert stack.gw.store.get(rid)["status"] == "rolled_back"
    events = [a["event"] for a in stack.audit()]
    for e in ("request.accepted", "build.proposed", "build.approved", "build.built", "build.undo_requested", "build.rolled_back"):
        assert e in events


def test_build_needs_op_level(stack):
    st, r = build(stack, op_level=2)
    assert st == 403 and r["code"] == "forbidden"


def test_build_needs_position(stack):
    st, r = stack.call("POST", "/v1/mod/requests", {"player_uuid": STEVE, "player_name": "S", "kind": "build",
                                                    "text": "x", "op_level": 4, "context": {"x": 1}})
    assert st == 400 and r["code"] == "no_position"


@pytest.mark.parametrize("level", [-1, 5, "4", True, None])
def test_forged_op_levels_are_treated_as_zero(stack, level):
    st, r = build(stack, op_level=level)
    assert st == 403


def test_invalid_ai_plan_is_rejected_by_gateway(stack):
    build(stack, text="MOCK_BAD_BUILD please")
    msg = wait_event(stack, "message")
    assert "rejected" in msg["text"] and "tnt" in msg["text"]
    assert stack.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4})[0] == 404   # nothing to approve


def test_other_player_cannot_approve_or_undo(stack):
    rid, _ = proposed(stack)
    st, r = stack.call("POST", "/v1/mod/approve", {"player_uuid": ALEX, "op_level": 0, "request_id": rid})
    assert st == 403
    st, r = stack.call("POST", "/v1/mod/approve", {"player_uuid": ALEX, "op_level": 4, "request_id": rid})  # an admin may
    assert st == 200


def test_requester_who_lost_rights_cannot_approve(stack):
    rid, _ = proposed(stack)
    st, _ = stack.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 0, "request_id": rid})
    assert st == 403


def test_approve_twice_and_wrong_states(stack):
    rid, _ = proposed(stack)
    assert stack.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})[0] == 200
    assert stack.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})[0] == 409
    assert stack.call("POST", "/v1/mod/undo", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})[0] == 409  # not built yet
    # a late/duplicate report for a state that doesn't match is ignored
    assert stack.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "rolled_back"})[1]["recorded"] is False


def test_cancel_proposal(stack):
    rid, _ = proposed(stack)
    st, r = stack.call("POST", "/v1/mod/cancel", {"player_uuid": STEVE, "op_level": 4})
    assert st == 200 and rid in r["message"]
    assert stack.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})[0] == 409


def test_cannot_cancel_someone_elses(stack):
    rid, _ = proposed(stack)
    assert stack.call("POST", "/v1/mod/cancel", {"player_uuid": ALEX, "request_id": rid})[0] == 403


def test_proposal_expires(tmp_path):
    s = Stack(tmp_path, approve_ttl_s=30)
    try:
        rid, _ = proposed(s)
        s.gw.store.db.execute("UPDATE requests SET proposed_at='2000-01-01T00:00:00.000Z' WHERE id=?", (rid,))
        st, r = s.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})
        assert st == 410 and r["code"] == "expired"
    finally:
        s.close()


def test_sweep_expires_and_notifies(tmp_path):
    s = Stack(tmp_path, approve_ttl_s=30)
    try:
        rid, _ = proposed(s)
        s.gw.store.db.execute("UPDATE requests SET proposed_at='2000-01-01T00:00:00.000Z' WHERE id=?", (rid,))
        s.gw.sweep()
        assert s.gw.store.get(rid)["status"] == "expired"
        assert "expired" in wait_event(s, "message")["text"]
    finally:
        s.close()


def test_execution_without_report_becomes_interrupted_and_undoable(stack):
    rid, _ = proposed(stack)
    stack.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})
    wait_event(stack, "execute")
    stack.gw.executing[rid] = 0
    stack.gw.sweep()
    assert stack.gw.store.get(rid)["status"] == "interrupted"
    assert stack.call("POST", "/v1/mod/undo", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})[0] == 200


def test_mod_reports_invalid_block_states(stack):
    rid, _ = proposed(stack)
    stack.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "invalid", "error": "minecraft:torch[facing=up]"})
    assert stack.gw.store.get(rid)["status"] == "failed"
    assert "doesn't accept" in wait_event(stack, "message")["text"]


def test_restart_interrupts_running_builds(tmp_path):
    s = Stack(tmp_path)
    rid, _ = proposed(s)
    s.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "op_level": 4, "request_id": rid})
    s.close()
    s2 = Stack(tmp_path, worker=False)
    try:
        assert s2.gw.store.get(rid)["status"] == "interrupted"
        assert s2.call("GET", "/v1/mod/events?wait=0")[1]["events"] == []          # nothing re-executed
    finally:
        s2.close()


def test_worker_cannot_bypass_validation(tmp_path):
    """A compromised or buggy worker posting a dangerous plan directly is still refused by the gateway."""
    s = Stack(tmp_path, worker=False)
    try:
        build(s)
        job = s.call("GET", "/v1/worker/jobs?wait=1", token=WORKER_TOKEN)[1]["job"]
        assert job["build"]["origin"] == [100, 64, 200] and job["build"]["limits"]["max_blocks"] == 40000
        evil = {"title": "x", "actions": [{"op": "setblock", "pos": [101, 64, 201],
                                           "block": 'minecraft:command_block{Command:"op @a"}'}]}
        s.call("POST", f"/v1/worker/jobs/{job['request_id']}/result", {"ok": True, "text": "ok", "plan": evil},
               token=WORKER_TOKEN)
        assert s.gw.store.get(job["request_id"])["status"] == "failed"
        assert all(e.get("type") != "preview" for e in s.events_until(1, timeout=2))
    finally:
        s.close()


def test_protected_area_file(tmp_path):
    (tmp_path / "protected-areas.json").write_text(json.dumps(
        [{"name": "spawn", "dimension": "minecraft:overworld", "from": [100, 60, 200], "to": [110, 80, 210]}]))
    s = Stack(tmp_path)
    try:
        build(s)
        assert "protected area 'spawn'" in wait_event(s, "message")["text"]
    finally:
        s.close()


def test_broken_protected_area_file_blocks_all_builds(tmp_path):
    (tmp_path / "protected-areas.json").write_text("{not json")
    s = Stack(tmp_path)
    try:
        build(s)
        assert "protected area" in wait_event(s, "message")["text"]
    finally:
        s.close()


def test_history(stack):
    rid, _ = proposed(stack)
    msg = stack.call("GET", f"/v1/mod/history?player_uuid={STEVE}")[1]["message"]
    assert rid in msg and "Mock pillar" in msg
