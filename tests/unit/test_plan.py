"""Build plan validation (action schema v1): the gate between model output and the world."""
import json
import os
import subprocess
import sys

import pytest

from mcai.plan import Limits, PlanError, box_count, validate

O = (100, 64, 200)
DIM = "minecraft:overworld"


def plan(*actions, title="Test"):
    return {"title": title, "actions": list(actions)}


def fill(a, b, block="minecraft:stone", mode="replace"):
    return {"op": "fill", "from": list(a), "to": list(b), "block": block, "mode": mode}


def test_valid_plan_summary():
    p, s = validate(plan(fill((102, 64, 202), (104, 67, 204), "minecraft:stone_bricks", "hollow"),
                         {"op": "setblock", "pos": [103, 68, 203], "block": "minecraft:torch"}), O, DIM)
    assert s.size == (3, 5, 3) and s.block_count == 37
    assert s.materials == {"minecraft:stone_bricks": 36, "minecraft:torch": 1}
    assert p["dimension"] == DIM and p["actions"][0]["from"] == [102, 64, 202]
    assert "37 blocks" in s.describe()


def test_coordinates_are_normalized():
    p, _ = validate(plan(fill((104, 70, 204), (102, 64, 202))), O, DIM)
    assert p["actions"][0]["from"] == [102, 64, 202] and p["actions"][0]["to"] == [104, 70, 204]


@pytest.mark.parametrize("mode, n", [("replace", 27), ("hollow", 27), ("outline", 26)])
def test_box_count(mode, n):
    assert box_count((0, 0, 0), (2, 2, 2), mode) == n


@pytest.mark.parametrize("block", [
    "minecraft:tnt", "minecraft:command_block", "minecraft:repeating_command_block", "minecraft:lava",
    "minecraft:bedrock", "minecraft:spawner", "minecraft:structure_block", "minecraft:nether_portal",
    "minecraft:barrier", "minecraft:end_gateway",
])
def test_denied_blocks(block):
    with pytest.raises(PlanError, match="not allowed"):
        validate(plan(fill((101, 64, 201), (101, 64, 201), block)), O, DIM)


@pytest.mark.parametrize("block", [
    'minecraft:chest{Items:[{id:"minecraft:diamond",Count:64}]}',          # NBT
    'minecraft:command_block{Command:"op @a"}',
    "minecraft:stone run op Steve",                                           # command injection attempt
    "minecraft:stone\nop Steve",
    "othermod:block",                                                         # other namespaces
    "stone",                                                                  # missing namespace
    "minecraft:oak_stairs[facing=east;op=1]",
    "minecraft:Stone",
    "",
    None,
    42,
])
def test_malformed_blocks(block):
    with pytest.raises(PlanError):
        validate(plan(fill((101, 64, 201), (101, 64, 201), block)), O, DIM)


def test_block_states_allowed():
    validate(plan({"op": "setblock", "pos": [101, 64, 201], "block": "minecraft:oak_stairs[facing=east,half=bottom]"}), O, DIM)


@pytest.mark.parametrize("bad", [
    {"op": "kill", "pos": [1, 1, 1], "block": "minecraft:stone"},
    {"op": "setblock", "pos": [1, 1], "block": "minecraft:stone"},
    {"op": "setblock", "pos": [1.5, 64, 1], "block": "minecraft:stone"},
    {"op": "setblock", "pos": ["1", 64, 1], "block": "minecraft:stone"},
    {"op": "setblock", "pos": [True, 64, 1], "block": "minecraft:stone"},
    {"op": "setblock", "pos": [101, 400, 201], "block": "minecraft:stone"},
    {"op": "setblock", "pos": [101, -65, 201], "block": "minecraft:stone"},
    {"op": "fill", "from": [101, 64, 201], "to": [102, 64, 202], "block": "minecraft:stone", "mode": "destroy"},
    {"op": "setblock", "pos": [101, 64, 201], "block": "minecraft:stone", "command": "op @a"},
    "setblock 1 2 3 stone",
])
def test_malformed_actions(bad):
    with pytest.raises(PlanError):
        validate(plan(bad), O, DIM)


@pytest.mark.parametrize("p", [None, [], {"actions": []}, {"title": "x" * 61, "actions": [fill((101, 64, 201), (101, 64, 201))]},
                               {"title": "t", "version": 2, "actions": [fill((101, 64, 201), (101, 64, 201))]}])
def test_malformed_plans(p):
    with pytest.raises(PlanError):
        validate(p, O, DIM)


def test_title_is_cleaned():
    p, _ = validate(plan(fill((101, 64, 201), (101, 64, 201)), title="Tower §c<script>"), O, DIM)
    assert p["title"] == "Tower cscript"


def test_size_limit():
    with pytest.raises(PlanError, match="bounding box"):
        validate(plan(fill((100, 64, 200), (149, 64, 200))), O, DIM)        # 50 wide > 48


def test_block_budget():
    with pytest.raises(PlanError, match="more than"):
        validate(plan(fill((90, 64, 190), (130, 90, 230))), O, DIM, Limits(max_size=(64, 64, 64)))


def test_overlapping_fills_count_toward_budget():
    actions = [fill((100, 64, 200), (109, 73, 209))] * 41          # 41 x 1000 > 40000
    with pytest.raises(PlanError, match="more than"):
        validate(plan(*actions), O, DIM)


def test_distance_limit():
    with pytest.raises(PlanError, match="from the player"):
        validate(plan(fill((170, 64, 200), (170, 64, 200))), O, DIM)


def test_action_count_limit():
    actions = [{"op": "setblock", "pos": [101, 64, 201], "block": "minecraft:stone"}] * 5
    with pytest.raises(PlanError, match="too many actions"):
        validate(plan(*actions), O, DIM, Limits(max_actions=4))


def test_protected_area():
    lim = Limits(protected=(("spawn", DIM, (95, 60, 195), (99, 70, 199)),))
    with pytest.raises(PlanError, match="protected area 'spawn'"):
        validate(plan(fill((98, 64, 198), (101, 64, 201))), O, DIM, lim)
    validate(plan(fill((98, 64, 198), (101, 64, 201))), O, "minecraft:the_nether", lim)   # other dimension: fine


# ---------- MCP tool server (stdio JSON-RPC) ----------
def mcp(messages, tmp_path, context=None):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), MCAI_PROPOSAL_FILE=str(tmp_path / "proposal.json"),
               MCAI_BUILD_CONTEXT=json.dumps(context or {"origin": list(O), "dimension": DIM}))
    out = subprocess.run([sys.executable, "-m", "mcai.mcp_build"], input="\n".join(json.dumps(m) for m in messages) + "\n",
                         capture_output=True, text=True, env=env, timeout=30)
    return [json.loads(line) for line in out.stdout.splitlines()]


def test_mcp_handshake_and_tools(tmp_path):
    r = mcp([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "nope"}], tmp_path)
    assert len(r) == 3                                   # no reply to the notification
    assert r[0]["result"]["protocolVersion"] == "2025-06-18"
    assert [t["name"] for t in r[1]["result"]["tools"]] == ["validate_plan", "propose_build"]
    assert r[2]["error"]["code"] == -32601


def test_mcp_validate_and_propose(tmp_path):
    good = plan(fill((102, 64, 202), (103, 65, 203)))
    bad = plan(fill((102, 64, 202), (102, 64, 202), "minecraft:tnt"))
    r = mcp([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "validate_plan", "arguments": {"plan": bad}}},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "propose_build", "arguments": {"plan": bad}}},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "validate_plan", "arguments": {"plan": good}}}],
            tmp_path)
    assert r[0]["result"]["isError"] and "not allowed" in r[0]["result"]["content"][0]["text"]
    assert r[1]["result"]["isError"] and not (tmp_path / "proposal.json").exists()   # invalid plans are never recorded
    assert not r[2]["result"]["isError"] and r[2]["result"]["content"][0]["text"].startswith("VALID")
    assert not (tmp_path / "proposal.json").exists()                                   # validate doesn't propose
    r = mcp([{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "propose_build", "arguments": {"plan": good}}}],
            tmp_path)
    assert r[0]["result"]["content"][0]["text"].startswith("PROPOSED")
    assert json.loads((tmp_path / "proposal.json").read_text())["actions"][0]["block"] == "minecraft:stone"


def test_mcp_unknown_tool_and_garbage(tmp_path):
    env_ok = mcp([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_command", "arguments": {}}}], tmp_path)
    assert env_ok[0]["error"]["code"] == -32602
    out = subprocess.run([sys.executable, "-m", "mcai.mcp_build"], input="not json\n", capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)), timeout=30)
    assert json.loads(out.stdout)["error"]["code"] == -32700


def test_wildcard_protected_area_covers_every_dimension():
    lim = Limits(protected=(("everything", "*", (-30_000_000, -64, -30_000_000), (30_000_000, 319, 30_000_000)),))
    for dim in (DIM, "minecraft:the_nether", "minecraft:the_end"):
        with pytest.raises(PlanError, match="protected area"):
            validate(plan(fill((101, 64, 201), (101, 64, 201))), O, dim, lim)
