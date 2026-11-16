"""Minimal MCP server (stdio, JSON-RPC 2.0, newline-delimited) exposing exactly two tools to Claude:

  validate_plan(plan)   check a build plan and return a summary or the problems to fix
  propose_build(plan)   record the final plan as this request's proposal (validated again)

It has no network access and writes nothing except the proposal file named by MCAI_PROPOSAL_FILE.
The request context (player position, dimension, limits, protected areas) comes from MCAI_BUILD_CONTEXT.
The gateway re-validates every proposal; this server only gives Claude fast feedback.
"""
from __future__ import annotations

import json
import os
import sys

from . import __version__
from .plan import Limits, PlanError, validate

PLAN_SCHEMA = {
    "type": "object",
    "required": ["title", "actions"],
    "properties": {
        "version": {"type": "integer", "const": 1},
        "title": {"type": "string", "maxLength": 60},
        "actions": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["op", "block"],
                "properties": {
                    "op": {"type": "string", "enum": ["fill", "setblock"]},
                    "from": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                    "to": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                    "pos": {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
                    "block": {"type": "string", "description": "e.g. minecraft:stone_bricks or minecraft:oak_stairs[facing=east,half=bottom]"},
                    "mode": {"type": "string", "enum": ["replace", "hollow", "outline"]},
                },
            },
        },
    },
}

TOOLS = [
    {"name": "validate_plan",
     "description": "Check a build plan against the server's rules without submitting it. Returns a summary "
                    "(bounding box, block count, materials) or a list of problems to fix.",
     "inputSchema": {"type": "object", "required": ["plan"], "properties": {"plan": PLAN_SCHEMA}}},
    {"name": "propose_build",
     "description": "Submit the final build plan. It is shown to the player as a preview and is only built after "
                    "they approve it. Call this exactly once, with a plan that validates.",
     "inputSchema": {"type": "object", "required": ["plan"], "properties": {"plan": PLAN_SCHEMA}}},
]


def load_context() -> tuple:
    ctx = json.loads(os.environ.get("MCAI_BUILD_CONTEXT", "{}"))
    origin = tuple(ctx.get("origin", [0, 64, 0]))
    lim = ctx.get("limits", {})
    limits = Limits(
        max_actions=int(lim.get("max_actions", Limits.max_actions)),
        max_blocks=int(lim.get("max_blocks", Limits.max_blocks)),
        max_size=tuple(lim.get("max_size", Limits.max_size)),
        max_distance=int(lim.get("max_distance", Limits.max_distance)),
        protected=tuple((p["name"], p["dimension"], tuple(p["from"]), tuple(p["to"])) for p in ctx.get("protected", [])),
    )
    return origin, ctx.get("dimension", "minecraft:overworld"), limits


def call_tool(name: str, args: dict) -> tuple[str, bool]:
    origin, dimension, limits = load_context()
    plan = args.get("plan") if isinstance(args, dict) else None
    try:
        normalized, summary = validate(plan, origin, dimension, limits)
    except PlanError as e:
        return f"INVALID: {e}. Fix the plan and validate again.", True
    if name == "validate_plan":
        return "VALID: " + summary.describe(), False
    out = os.environ.get("MCAI_PROPOSAL_FILE")
    if not out:
        return "INTERNAL: no proposal file configured", True
    with open(out, "w", encoding="utf-8") as f:
        json.dump(normalized, f)
    return ("PROPOSED: " + summary.describe() + ". The player will see a preview and must approve it. "
            "Now reply with one short sentence describing the build."), False


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:                       # notification (e.g. notifications/initialized): no reply
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
        result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "mcai", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in ("validate_plan", "propose_build"):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        text, is_error = call_tool(name, params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
