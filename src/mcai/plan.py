"""Build plans (action schema v1): validation and summaries.

A plan is pure data. It never contains commands, NBT or scripts:

    {"version": 1, "title": "Stone watchtower",
     "actions": [
        {"op": "fill", "from": [x, y, z], "to": [x, y, z], "block": "minecraft:stone_bricks", "mode": "hollow"},
        {"op": "setblock", "pos": [x, y, z], "block": "minecraft:oak_ladder[facing=north]"}
     ]}

Coordinates are absolute block coordinates in the requester's dimension. The same validator runs in the
AI worker's tool server (so Claude can fix mistakes) and, authoritatively, in the gateway (which never trusts
the worker). The mod checks block states again before showing the preview.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

BLOCK_RE = re.compile(
    r"^minecraft:[a-z0-9_]{1,64}"
    r"(\[[a-z0-9_]{1,32}=[a-z0-9_]{1,32}(,[a-z0-9_]{1,32}=[a-z0-9_]{1,32}){0,15}\])?$")

# Blocks the AI may never place: destructive, op-only, unbreakable, or able to run commands.
DENIED_BLOCKS = frozenset({
    "tnt", "command_block", "chain_command_block", "repeating_command_block", "structure_block",
    "structure_void", "jigsaw", "barrier", "light", "bedrock", "lava", "fire", "soul_fire",
    "end_portal", "end_portal_frame", "end_gateway", "nether_portal", "spawner", "trial_spawner",
    "vault", "respawn_anchor", "wither_skeleton_skull", "wither_skeleton_wall_skull", "dragon_egg",
    "reinforced_deepslate", "budding_amethyst", "sculk_shrieker", "test_block", "test_instance_block",
})
FILL_MODES = ("replace", "hollow", "outline")
Y_MIN, Y_MAX = -64, 319


@dataclass(frozen=True)
class Limits:
    max_actions: int = 2000
    max_blocks: int = 40_000            # blocks actually placed (sum over actions)
    max_size: tuple = (48, 64, 48)      # bounding box x, y, z
    max_distance: int = 64              # every block within this horizontal distance of the origin
    protected: tuple = ()               # ((name, dimension or "*", (x1,y1,z1), (x2,y2,z2)), ...)


@dataclass
class Summary:
    title: str
    bbox_from: tuple
    bbox_to: tuple
    size: tuple
    block_count: int
    materials: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"title": self.title, "from": list(self.bbox_from), "to": list(self.bbox_to), "size": list(self.size),
                "block_count": self.block_count, "materials": self.materials}

    def describe(self) -> str:
        mats = ", ".join(f"{n} {b.removeprefix('minecraft:')}" for b, n in list(self.materials.items())[:4])
        x, y, z = self.size
        return (f"{self.title}: {x}x{y}x{z} at {self.bbox_from[0]} {self.bbox_from[1]} {self.bbox_from[2]} .. "
                f"{self.bbox_to[0]} {self.bbox_to[1]} {self.bbox_to[2]}, {self.block_count} blocks ({mats})")


class PlanError(ValueError):
    pass


def _coord(v, what: str) -> tuple:
    if (not isinstance(v, list) or len(v) != 3
            or not all(isinstance(c, int) and not isinstance(c, bool) and -30_000_000 <= c <= 30_000_000 for c in v)):
        raise PlanError(f"{what} must be [x, y, z] with whole numbers")
    if not Y_MIN <= v[1] <= Y_MAX:
        raise PlanError(f"{what}: y must be between {Y_MIN} and {Y_MAX}")
    return tuple(v)


def _block(b, i: int) -> str:
    if not isinstance(b, str) or not BLOCK_RE.match(b):
        raise PlanError(f"action {i}: block must look like minecraft:stone or minecraft:oak_stairs[facing=north] "
                        f"(no NBT, no other namespaces)")
    name = b[len("minecraft:"):].split("[", 1)[0]
    if name in DENIED_BLOCKS:
        raise PlanError(f"action {i}: {name} is not allowed")
    return b


def box_count(a: tuple, b: tuple, mode: str) -> int:
    dx, dy, dz = (abs(a[k] - b[k]) + 1 for k in range(3))
    total = dx * dy * dz
    if mode == "replace":
        return total
    inner = max(dx - 2, 0) * max(dy - 2, 0) * max(dz - 2, 0)
    return total - inner if mode == "outline" else total   # hollow also places air inside


def _intersects(lo1, hi1, lo2, hi2) -> bool:
    return all(lo1[k] <= hi2[k] and lo2[k] <= hi1[k] for k in range(3))


def validate(plan, origin: tuple, dimension: str, limits: Limits = Limits()) -> tuple[dict, Summary]:
    """Return (normalized plan, summary) or raise PlanError with a message Claude/players can act on."""
    if not isinstance(plan, dict):
        raise PlanError("plan must be a JSON object")
    if plan.get("version", 1) != 1:
        raise PlanError("only plan version 1 is supported")
    title = plan.get("title")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60:
        raise PlanError("title must be 1-60 characters")
    title = re.sub(r"[^\w .,'()-]", "", title).strip() or "Build"
    actions = plan.get("actions")
    if not isinstance(actions, list) or not actions:
        raise PlanError("actions must be a non-empty list")
    if len(actions) > limits.max_actions:
        raise PlanError(f"too many actions ({len(actions)} > {limits.max_actions}); use larger fills")

    norm, lo, hi, total, mats = [], [None] * 3, [None] * 3, 0, Counter()
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            raise PlanError(f"action {i} must be an object")
        op = a.get("op")
        if op == "fill":
            p1, p2 = _coord(a.get("from"), f"action {i} from"), _coord(a.get("to"), f"action {i} to")
            mode = a.get("mode", "replace")
            if mode not in FILL_MODES:
                raise PlanError(f"action {i}: mode must be one of {', '.join(FILL_MODES)}")
            block = _block(a.get("block"), i)
            n = box_count(p1, p2, mode)
            a_lo = tuple(min(p1[k], p2[k]) for k in range(3))
            a_hi = tuple(max(p1[k], p2[k]) for k in range(3))
            norm.append({"op": "fill", "from": list(a_lo), "to": list(a_hi), "block": block, "mode": mode})
        elif op == "setblock":
            p = _coord(a.get("pos"), f"action {i} pos")
            block = _block(a.get("block"), i)
            n, a_lo, a_hi = 1, p, p
            norm.append({"op": "setblock", "pos": list(p), "block": block})
        else:
            raise PlanError(f"action {i}: op must be fill or setblock")
        extra = set(a) - {"op", "from", "to", "pos", "block", "mode"}
        if extra:
            raise PlanError(f"action {i}: unknown fields {sorted(extra)}")
        total += n
        mats[block.split("[", 1)[0]] += n
        if total > limits.max_blocks:
            raise PlanError(f"plan places more than {limits.max_blocks} blocks; make it smaller")
        for k in range(3):
            lo[k] = a_lo[k] if lo[k] is None else min(lo[k], a_lo[k])
            hi[k] = a_hi[k] if hi[k] is None else max(hi[k], a_hi[k])

    size = tuple(hi[k] - lo[k] + 1 for k in range(3))
    if any(size[k] > limits.max_size[k] for k in range(3)):
        raise PlanError(f"bounding box {size[0]}x{size[1]}x{size[2]} exceeds the limit "
                        f"{limits.max_size[0]}x{limits.max_size[1]}x{limits.max_size[2]}")
    ox, _, oz = origin
    far = max(abs(lo[0] - ox), abs(hi[0] - ox), abs(lo[2] - oz), abs(hi[2] - oz))
    if far > limits.max_distance:
        raise PlanError(f"the build reaches {far} blocks from the player; the limit is {limits.max_distance}")
    for name, dim, p1, p2 in limits.protected:
        if dim in (dimension, "*") and _intersects(tuple(lo), tuple(hi), tuple(map(min, p1, p2)), tuple(map(max, p1, p2))):
            raise PlanError(f"the build overlaps the protected area '{name}'")

    summary = Summary(title, tuple(lo), tuple(hi), size, total, dict(mats.most_common()))
    return {"version": 1, "title": title, "dimension": dimension, "actions": norm}, summary
