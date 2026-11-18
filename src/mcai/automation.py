"""Automation engine: event-driven and scheduled automations, run inside the gateway.

An automation is a declarative definition (TOML or JSON, see automation-examples/): a trigger, conditions,
a list of actions from a fixed allowlist, and limits. There is no code and no raw console command: every action
type is implemented here and needs a named permission. The definition's owner must still hold those permissions
when it runs; otherwise the automation is suspended.

Lifecycle (state):
  proposed           written by the AI; never runs until approved
  pending_approval   privileged (changes roles or the world) and not created by the owner; the owner approves
  enabled / disabled
  suspended          stopped by the engine (too many failures, or the owner lost a permission)
  rejected / deleted

Guarantees:
  - Each run has an idempotency key (schedule slot, event id, session id, ...) with a UNIQUE index, so the same
    slot or event never runs twice, also across restarts. A schedule slot missed while the gateway was down runs
    once if it is less than `misfire_grace_s` late, otherwise according to `misfire` (skip by default).
  - A run that was executing when the gateway stopped is marked interrupted and is not repeated (at most once).
    Finished actions of a run waiting for a retry are not repeated either: the retry resumes at the failed one.
  - Loops: events caused by an automation carry its id; a chain that would reach the same automation again, or
    grows deeper than MAX_CHAIN, is refused.
  - Rates: per automation (max_runs_per_hour, cooldown_s), global (runs per minute), AI spend per automation per
    day (USD and tokens), global automation AI calls per hour, and the shared daily AI budget.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import logging
import re
import threading
import time
import tomllib
import unicodedata
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import rbac
from .cron import Schedule, ScheduleError, timezone

log = logging.getLogger("mcai.automation")

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
WORD_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
DIM_RE = re.compile(r"^[a-z0-9_.-]{1,32}:[a-z0-9_./-]{1,64}$")
ADV_RE = re.compile(r"^[a-z0-9_:/.*-]{1,100}$")
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
MAX_CHAIN = 3
MAX_CONSECUTIVE_FAILURES = 5
MAX_TEXT = 256
MAX_PROMPT = 500
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
STATES = ("proposed", "pending_approval", "enabled", "disabled", "suspended", "rejected", "deleted")
RUN_FINAL = ("succeeded", "partial", "failed", "skipped", "denied", "interrupted", "expired")

COMMON = {"players_online", "time", "date", "automation"}
PLAYER = {"player"}
# event -> (has a player subject, allowed extra trigger keys, placeholders)
EVENTS: dict[str, tuple[bool, set, set]] = {
    "player.join": (True, set(), PLAYER),
    "player.first_join": (True, set(), PLAYER),
    "player.leave": (True, set(), PLAYER),
    "player.enter_area": (True, {"area"}, PLAYER | {"area"}),
    "player.leave_area": (True, {"area"}, PLAYER | {"area"}),
    "player.advancement": (True, {"advancement"}, PLAYER | {"advancement"}),
    "player.trigger": (True, {"name"}, PLAYER | {"trigger"}),
    "build.completed": (True, set(), PLAYER | {"title", "request", "blocks"}),
    "build.failed": (True, set(), PLAYER | {"title", "request"}),
    "build.undone": (True, set(), PLAYER | {"title", "request"}),
    "server.started": (False, set(), set()),
    "server.threshold": (False, {"metric", "above", "below", "for_s"}, {"metric", "value"}),
    "world.day_start": (False, set(), set()),
    "world.night_start": (False, set(), set()),
    "world.rain_start": (False, set(), set()),
    "world.rain_stop": (False, set(), set()),
}
METRICS = ("mspt", "tps", "players_online", "heap_pct")
WORLD_VALUES = {"time": ("day", "noon", "night", "midnight"), "weather": ("clear", "rain", "thunder")}
SET_ROLES = ("player", "builder")          # an automation never grants more than builder

LIMITS = {   # name: (default, min, max)
    "timeout_s": (60, 1, 900),
    "retries": (0, 0, 5),
    "retry_delay_s": (30, 5, 3600),
    "max_runs_per_hour": (12, 1, 120),
    "cooldown_s": (0, 0, 86400),
    "ai_max_usd_per_day": (0.0, 0.0, 5.0),
    "ai_max_tokens_per_day": (50_000, 0, 1_000_000),
    "misfire_grace_s": (300, 0, 86400),
}
LIMIT_CHOICES = {"on_error": ("stop", "continue", "disable"), "misfire": ("skip", "run")}


class DefinitionError(ValueError):
    pass


class AutomationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ActionError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


# ====================================================================== definitions
@dataclass
class Definition:
    id: str
    name: str
    description: str
    timezone: str
    enabled: bool
    trigger: dict
    conditions: dict
    actions: list
    limits: dict
    schedule: Schedule | None = None
    event: str | None = None
    required_permissions: list = field(default_factory=list)
    privileged: bool = False

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description, "timezone": self.timezone,
                "enabled": self.enabled, "trigger": self.trigger, "conditions": self.conditions,
                "actions": self.actions, "limits": self.limits}

    @property
    def has_subject(self) -> bool:
        return bool(self.event and EVENTS[self.event][0])

    def trigger_text(self) -> str:
        if self.schedule:
            return self.schedule.describe()
        extra = ""
        t = self.trigger
        if self.event == "player.trigger":
            extra = f" '{t['name']}'"
        elif self.event in ("player.enter_area", "player.leave_area"):
            extra = f" '{t['area']['name']}'"
        elif self.event == "player.advancement":
            extra = f" {t['advancement']}"
        elif self.event == "server.threshold":
            extra = f" {t['metric']} {'>' if 'above' in t else '<'} {t.get('above', t.get('below'))} for {t['for_s']}s"
        return f"on {self.event}{extra}"


def parse_text(text: str) -> dict:
    """A definition from TOML or JSON text."""
    if not isinstance(text, str) or not text.strip():
        raise DefinitionError("empty definition")
    if len(text) > 64 * 1024:
        raise DefinitionError("definition larger than 64 KB")
    try:
        if text.lstrip().startswith("{"):
            data = json.loads(text)
        else:
            data = tomllib.loads(text)
    except (ValueError, tomllib.TOMLDecodeError) as e:
        raise DefinitionError(f"not valid TOML or JSON: {e}") from None
    if not isinstance(data, dict):
        raise DefinitionError("a definition is a table/object")
    return data


def _keys(obj, allowed: set, where: str, required: set = frozenset()) -> None:
    if not isinstance(obj, dict):
        raise DefinitionError(f"{where} must be a table/object")
    unknown = set(obj) - allowed
    if unknown:
        raise DefinitionError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    missing = set(required) - set(obj)
    if missing:
        raise DefinitionError(f"{where}: missing {', '.join(sorted(missing))}")


def _str(v, where: str, max_len: int, pattern: re.Pattern | None = None, allow_empty=False) -> str:
    if not isinstance(v, str) or (not v.strip() and not allow_empty) or len(v) > max_len:
        raise DefinitionError(f"{where} must be text of at most {max_len} characters")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in v) or "§" in v:
        raise DefinitionError(f"{where} contains control or formatting characters")
    if pattern and not pattern.match(v):
        raise DefinitionError(f"{where}: {v!r} has an invalid format")
    return v


def _num(v, where: str, lo, hi, integer=True):
    ok = isinstance(v, int) if integer else isinstance(v, (int, float))
    if isinstance(v, bool) or not ok or not lo <= v <= hi:
        raise DefinitionError(f"{where} must be a{'n integer' if integer else ' number'} between {lo} and {hi}")
    return v


def _template(v, where: str, placeholders: set, max_len: int = MAX_TEXT) -> str:
    _str(v, where, max_len)
    for name in PLACEHOLDER_RE.findall(v):
        if name not in placeholders:
            raise DefinitionError(f"{where}: unknown placeholder {{{name}}} for this trigger "
                                  f"(available: {', '.join('{' + p + '}' for p in sorted(placeholders))})")
    return v


def _coords(v, where: str) -> list:
    if not isinstance(v, list) or len(v) != 3:
        raise DefinitionError(f"{where} must be [x, y, z]")
    x, y, z = v
    return [_num(x, where, -30_000_000, 30_000_000), _num(y, where, -64, 320), _num(z, where, -30_000_000, 30_000_000)]


def _area(v, where: str) -> dict:
    if isinstance(v, dict) and "example" in v:
        # the area of an installed example pack (its latest placed install), resolved when the mod asks
        _keys(v, {"name", "example"}, where, {"example"})
        return {"name": _str(v.get("name", v["example"]), where + ".name", 32, WORD_RE),
                "example": _str(v["example"], where + ".example", 40, ID_RE)}
    _keys(v, {"name", "dimension", "from", "to"}, where, {"from", "to"})
    a = {"name": _str(v.get("name", "area"), where + ".name", 32, WORD_RE),
         "dimension": _str(v.get("dimension", "minecraft:overworld"), where + ".dimension", 100, DIM_RE),
         "from": _coords(v["from"], where + ".from"), "to": _coords(v["to"], where + ".to")}
    if any(abs(a["from"][i] - a["to"][i]) > 512 for i in range(3)):
        raise DefinitionError(f"{where}: an area may be at most 512 blocks along each axis")
    return a


def _trigger(t: dict, tz: str) -> tuple[dict, Schedule | None, str | None]:
    if not isinstance(t, dict):
        raise DefinitionError("trigger must be a table/object")
    kinds = [k for k in ("schedule", "every", "event") if k in t]
    if len(kinds) != 1:
        raise DefinitionError("trigger needs exactly one of: schedule (cron), every (interval), event")
    if kinds[0] in ("schedule", "every"):
        _keys(t, {kinds[0]}, "trigger")
        try:
            sched = Schedule(cron=t.get("schedule"), every=t.get("every"), tz=tz)
        except ScheduleError as e:
            raise DefinitionError(f"trigger: {e}") from None
        return dict(t), sched, None
    event = t["event"]
    if event not in EVENTS:
        raise DefinitionError(f"trigger.event: unknown event {event!r} (known: {', '.join(EVENTS)})")
    extra = EVENTS[event][1]
    _keys(t, {"event"} | extra, "trigger")
    out = {"event": event}
    if event in ("player.enter_area", "player.leave_area"):
        if "area" not in t:
            raise DefinitionError("trigger.area is required for area events")
        out["area"] = _area(t["area"], "trigger.area")
    elif event == "player.advancement":
        out["advancement"] = _str(t.get("advancement", "*"), "trigger.advancement", 100, ADV_RE)
    elif event == "player.trigger":
        if "name" not in t:
            raise DefinitionError("trigger.name is required: players start it with /ai trigger <name>")
        out["name"] = _str(t["name"], "trigger.name", 32, WORD_RE)
    elif event == "server.threshold":
        if t.get("metric") not in METRICS:
            raise DefinitionError(f"trigger.metric must be one of {', '.join(METRICS)}")
        if ("above" in t) == ("below" in t):
            raise DefinitionError("trigger needs exactly one of above / below")
        out["metric"] = t["metric"]
        k = "above" if "above" in t else "below"
        out[k] = _num(t[k], f"trigger.{k}", 0, 1_000_000, integer=False)
        out["for_s"] = _num(t.get("for_s", 60), "trigger.for_s", 0, 3600)
    return out, None, event


def _conditions(c: dict, has_subject: bool) -> dict:
    allowed = {"players_online_min", "players_online_max", "time_between", "days", "player_role_min", "player_roles",
               "playtime_hours_min"}
    _keys(c, allowed, "conditions")
    out = {}
    for k in ("players_online_min", "players_online_max"):
        if k in c:
            out[k] = _num(c[k], f"conditions.{k}", 0, 1000)
    if "time_between" in c:
        v = c["time_between"]
        if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) and HHMM_RE.match(x) for x in v)):
            raise DefinitionError('conditions.time_between must be ["HH:MM", "HH:MM"] (may wrap past midnight)')
        out["time_between"] = list(v)
    if "days" in c:
        v = c["days"]
        if not (isinstance(v, list) and v and all(d in DAYS for d in v)):
            raise DefinitionError(f"conditions.days must be a list of {', '.join(DAYS)}")
        out["days"] = list(dict.fromkeys(v))
    subject_keys = {"player_role_min", "player_roles", "playtime_hours_min"} & set(c)
    if subject_keys and not has_subject:
        raise DefinitionError(f"conditions {', '.join(sorted(subject_keys))} need a trigger with a player")
    if "player_role_min" in c:
        if not rbac.valid_role(c["player_role_min"]):
            raise DefinitionError(f"conditions.player_role_min must be one of {', '.join(rbac.ROLES)}")
        out["player_role_min"] = c["player_role_min"]
    if "player_roles" in c:
        v = c["player_roles"]
        if not (isinstance(v, list) and v and all(rbac.valid_role(r) for r in v)):
            raise DefinitionError(f"conditions.player_roles must be a list of {', '.join(rbac.ROLES)}")
        out["player_roles"] = list(dict.fromkeys(v))
    if "playtime_hours_min" in c:
        out["playtime_hours_min"] = _num(c["playtime_hours_min"], "conditions.playtime_hours_min", 0, 100_000,
                                         integer=False)
    return out


def _action(a: dict, i: int, has_subject: bool, placeholders: set, ai_allowed: bool) -> tuple[dict, set, bool]:
    """(normalized action, permissions it needs, privileged)."""
    where = f"actions[{i}]"
    if not isinstance(a, dict) or not isinstance(a.get("type"), str):
        raise DefinitionError(f"{where} needs a type")
    t = a["type"]

    def need_subject():
        if not has_subject:
            raise DefinitionError(f"{where}: '{t}' needs a trigger with a player (player.* or build.* events)")

    if t in ("message", "broadcast", "log"):
        _keys(a, {"type", "text"}, where, {"text"})
        if t == "message":
            need_subject()
        perms = {"status.read"} if t == "log" else {"players.moderate"}
        return {"type": t, "text": _template(a["text"], where + ".text", placeholders)}, perms, False
    if t == "notify_role":
        _keys(a, {"type", "text", "min_role"}, where, {"text"})
        role = a.get("min_role", "operator")
        if not rbac.valid_role(role):
            raise DefinitionError(f"{where}.min_role must be one of {', '.join(rbac.ROLES)}")
        return ({"type": t, "text": _template(a["text"], where + ".text", placeholders), "min_role": role},
                {"players.moderate"}, False)
    if t == "status_report":
        _keys(a, {"type", "to"}, where)
        to = a.get("to", "log")
        if to not in ("log", "staff", "broadcast"):
            raise DefinitionError(f"{where}.to must be log, staff or broadcast")
        return {"type": t, "to": to}, {"status.read"} | ({"players.moderate"} if to != "log" else set()), False
    if t == "save":
        _keys(a, {"type"}, where)
        return {"type": t}, {"server.control"}, False
    if t == "world":
        _keys(a, {"type", "set", "value"}, where, {"set", "value"})
        if a["set"] not in WORLD_VALUES or a["value"] not in WORLD_VALUES[a["set"]]:
            raise DefinitionError(f"{where}: set must be time ({', '.join(WORLD_VALUES['time'])}) or weather "
                                  f"({', '.join(WORLD_VALUES['weather'])})")
        return {"type": t, "set": a["set"], "value": a["value"]}, {"server.control"}, False
    if t == "set_role":
        _keys(a, {"type", "role"}, where, {"role"})
        need_subject()
        if a["role"] not in SET_ROLES:
            raise DefinitionError(f"{where}.role: automations can only give the roles {', '.join(SET_ROLES)}")
        return {"type": t, "role": a["role"]}, {"roles.assign"}, True
    if t == "ai_message":
        _keys(a, {"type", "prompt", "to", "max_usd"}, where, {"prompt"})
        to = a.get("to", "broadcast")
        if to not in ("broadcast", "player", "staff"):
            raise DefinitionError(f"{where}.to must be broadcast, player or staff")
        if to == "player":
            need_subject()
        if not ai_allowed:
            raise DefinitionError(f"{where}: AI actions need limits.ai_max_usd_per_day above 0")
        return ({"type": t, "prompt": _template(a["prompt"], where + ".prompt", placeholders, MAX_PROMPT), "to": to,
                 "max_usd": _num(a.get("max_usd", 0.05), where + ".max_usd", 0.01, 0.5, integer=False)},
                {"ai.configure", "players.moderate"}, False)
    if t == "ai_build":
        _keys(a, {"type", "prompt", "dimension", "at"}, where, {"prompt", "at"})
        if not ai_allowed:
            raise DefinitionError(f"{where}: AI actions need limits.ai_max_usd_per_day above 0")
        return ({"type": t, "prompt": _template(a["prompt"], where + ".prompt", placeholders, MAX_PROMPT),
                 "dimension": _str(a.get("dimension", "minecraft:overworld"), where + ".dimension", 100, DIM_RE),
                 "at": _coords(a["at"], where + ".at")}, {"ai.build"}, True)
    if t == "example_tour":
        _keys(a, {"type", "example"}, where, {"example"})
        need_subject()
        return {"type": t, "example": _str(a["example"], where + ".example", 40, ID_RE)}, {"players.moderate"}, False
    if t == "example_restore":
        _keys(a, {"type", "example"}, where, {"example"})
        return ({"type": t, "example": _str(a["example"], where + ".example", 40, ID_RE)}, {"examples.install"},
                True)
    raise DefinitionError(f"{where}: unknown action type {t!r} (allowed: message, broadcast, notify_role, log, "
                          "status_report, save, world, set_role, ai_message, ai_build, example_tour, "
                          "example_restore). Automations can't run commands or code.")


def parse_definition(data: dict, default_tz: str = "UTC") -> Definition:
    if not isinstance(data, dict):
        raise DefinitionError("a definition is a table/object")
    _keys(data, {"id", "name", "description", "timezone", "enabled", "trigger", "conditions", "actions", "limits"},
          "definition", {"id", "name", "trigger", "actions"})
    aid = _str(data["id"], "id", 40, ID_RE)
    name = _str(data["name"], "name", 60)
    desc = _str(data.get("description", ""), "description", 300, allow_empty=True)
    tz = data.get("timezone", default_tz)
    try:
        timezone(tz)
    except ScheduleError as e:
        raise DefinitionError(f"timezone: {e}") from None
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise DefinitionError("enabled must be true or false")
    trigger, sched, event = _trigger(data["trigger"], tz)
    has_subject = bool(event and EVENTS[event][0])
    conditions = _conditions(data.get("conditions", {}), has_subject)
    lim_in = data.get("limits", {})
    _keys(lim_in, set(LIMITS) | set(LIMIT_CHOICES), "limits")
    limits = {}
    for k, (default, lo, hi) in LIMITS.items():
        limits[k] = _num(lim_in.get(k, default), f"limits.{k}", lo, hi, integer=not isinstance(default, float))
    for k, choices in LIMIT_CHOICES.items():
        v = lim_in.get(k, choices[0])
        if v not in choices:
            raise DefinitionError(f"limits.{k} must be one of {', '.join(choices)}")
        limits[k] = v
    acts = data["actions"]
    if not isinstance(acts, list) or not 1 <= len(acts) <= 10:
        raise DefinitionError("actions must be a list of 1 to 10 actions")
    placeholders = COMMON | (EVENTS[event][2] if event else set())
    actions, perms, privileged = [], {"automations.manage"}, False
    for i, a in enumerate(acts):
        norm, p, priv = _action(a, i, has_subject, placeholders, limits["ai_max_usd_per_day"] > 0)
        actions.append(norm)
        perms |= p
        privileged = privileged or priv
    return Definition(aid, name, desc, tz, enabled, trigger, conditions, actions, limits, sched, event,
                      sorted(perms), privileged)


def extract_json(text: str) -> dict:
    """The JSON object in an AI answer (it may be wrapped in a code fence or a sentence)."""
    if not isinstance(text, str):
        raise DefinitionError("no definition in the answer")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise DefinitionError("no JSON object in the answer")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as e:
        raise DefinitionError(f"the answer is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise DefinitionError("the answer is not a JSON object")
    return data


def definition_hash(d: Definition) -> str:
    return hashlib.sha256(json.dumps(d.as_dict(), sort_keys=True).encode()).hexdigest()[:16]


def _clean_value(v) -> str:
    s = "".join(c for c in str(v) if unicodedata.category(c) not in ("Cc", "Cf", "Co", "Cs")).replace("§", "")
    return s[:64]


def render(template: str, values: dict) -> str:
    out = PLACEHOLDER_RE.sub(lambda m: _clean_value(values.get(m.group(1), "?")), template)
    return out[:MAX_TEXT * 2]


# ====================================================================== engine
class Engine:
    def __init__(self, gw, now=time.time, default_tz: str = "UTC", max_runs_per_minute: int = 30,
                 ai_calls_per_hour: int = 10, examples_dir: Path | None = None, extra_example_dirs=()):
        self.gw, self.store = gw, gw.store
        self.now = now
        self.default_tz = default_tz
        self.max_runs_per_minute = max_runs_per_minute
        self.ai_calls_per_hour = ai_calls_per_hour
        self.examples_dir = examples_dir
        self.extra_example_dirs = list(extra_example_dirs)
        self.lock = threading.RLock()
        self.defs: dict[str, Definition] = {}
        self.recent: deque[float] = deque()
        self.ai_waiters: dict[str, threading.Event] = {}
        self.hb: dict = {}
        self.throttle: dict[str, float] = {}
        self.wake = threading.Event()
        self.pool: ThreadPoolExecutor | None = None
        self._stop = threading.Event()
        self._recover()
        self.reload()

    # ------------------------------------------------------------ state
    def _recover(self) -> None:
        now = self.now()
        for r in self.store.runs_with_status(("running",)):
            self.store.run_update(r["id"], status="interrupted", finished=now,
                                  error="the gateway stopped during this run; it is not repeated")
            self.store.audit("automation.run", automation=r["automation_id"], run=r["id"], status="interrupted")
        for r in self.store.runs_with_status(("queued",)):
            if now - r["created"] > 600:
                self.store.run_update(r["id"], status="expired", finished=now, error="queued for more than 10 minutes")

    def reload(self) -> None:
        defs = {}
        for row in self.store.automations(("enabled",)):
            try:
                defs[row["id"]] = parse_definition(json.loads(row["definition"]), self.default_tz)
            except (DefinitionError, ValueError) as e:
                log.error("automation %s has an invalid stored definition: %s", row["id"], e)
        with self.lock:
            self.defs = defs

    def definition(self, row) -> Definition:
        return parse_definition(json.loads(row["definition"]), self.default_tz)

    def _audit_throttled(self, key: str, event: str, **fields) -> None:
        now = self.now()
        if self.throttle.get(key, 0) > now:
            return
        self.throttle[key] = now + 600
        self.store.audit(event, **fields)

    # ------------------------------------------------------------ management operations
    def save(self, actor: rbac.Actor, d: Definition, source: str = "manual", replace: bool = False) -> dict:
        existing = self.store.automation(d.id)
        if existing and existing["state"] != "deleted" and not replace:
            raise AutomationError("exists", f"An automation '{d.id}' already exists (use --replace to update it).")
        missing = [p for p in d.required_permissions if not actor.can(p)]
        if missing:
            raise AutomationError("forbidden", f"Its actions need {', '.join(missing)}, which your role "
                                               f"({actor.role}) doesn't have.")
        if source == "ai":
            state = "proposed"
        elif d.privileged and actor.role != "owner":
            state = "pending_approval"
        else:
            state = "enabled" if d.enabled else "disabled"
        now = self.now()
        fields = dict(name=d.name, definition=json.dumps(d.as_dict(), sort_keys=True), state=state, owner=actor.label,
                      source=source, privileged=int(d.privileged), updated_at=_iso(now), note=None, failures=0,
                      runtime="{}", approved_by=actor.label if state == "enabled" and d.privileged else None,
                      approved_at=_iso(now) if state == "enabled" and d.privileged else None,
                      next_run_at=d.schedule.next_after(now) if (d.schedule and state == "enabled") else None)
        version = self.store.automation_upsert(d.id, actor.label, _iso(now), **fields)
        self.store.audit("automation.updated" if existing else "automation.created", automation=d.id, version=version,
                         state=state, privileged=d.privileged, source=source, by=actor.label,
                         permissions=d.required_permissions, sha256=definition_hash(d))
        self.reload()
        return {"id": d.id, "state": state, "version": version}

    def _row(self, aid: str):
        row = self.store.automation(aid) if isinstance(aid, str) and ID_RE.match(aid) else None
        if row is None or row["state"] == "deleted":
            raise AutomationError("not_found", f"No automation '{aid}'.")
        return row

    def approve(self, actor: rbac.Actor, aid: str) -> str:
        row = self._row(aid)
        if row["state"] not in ("proposed", "pending_approval"):
            raise AutomationError("bad_state", f"'{aid}' is {row['state']}, not waiting for approval.")
        d = self.definition(row)
        if d.privileged and not actor.can("automations.approve"):
            raise AutomationError("forbidden", f"'{aid}' changes roles or the world: only the owner can approve it.")
        missing = [p for p in d.required_permissions if not actor.can(p)]
        if missing:
            raise AutomationError("forbidden", f"You don't hold {', '.join(missing)}, which its actions need.")
        now = self.now()
        self.store.automation_update(aid, state="enabled", approved_by=actor.label, approved_at=_iso(now),
                                     updated_at=_iso(now), failures=0, note=None,
                                     next_run_at=d.schedule.next_after(now) if d.schedule else None)
        self.store.audit("automation.approved", automation=aid, version=row["version"], by=actor.label,
                         privileged=d.privileged, sha256=definition_hash(d))
        self.reload()
        return f"Approved and enabled '{aid}'."

    def reject(self, actor: rbac.Actor, aid: str, reason: str = "") -> str:
        row = self._row(aid)
        if row["state"] not in ("proposed", "pending_approval"):
            raise AutomationError("bad_state", f"'{aid}' is {row['state']}, not waiting for approval.")
        self.store.automation_update(aid, state="rejected", note=reason[:200] or None, updated_at=_iso(self.now()))
        self.store.audit("automation.rejected", automation=aid, by=actor.label, reason=reason[:200] or None)
        return f"Rejected '{aid}'."

    def set_enabled(self, actor: rbac.Actor, aid: str, enabled: bool) -> str:
        row = self._row(aid)
        now = self.now()
        if enabled:
            if row["state"] in ("proposed", "pending_approval"):
                raise AutomationError("bad_state", f"'{aid}' needs approval first (mcai automations approve {aid}).")
            if row["state"] == "rejected":
                raise AutomationError("bad_state", f"'{aid}' was rejected; import it again to change it.")
            if row["state"] == "enabled":
                return f"'{aid}' is already enabled."
            d = self.definition(row)
            ok, why = self.owner_ok(row, d)
            if not ok:
                raise AutomationError("forbidden", f"Can't enable '{aid}': {why}. Re-import it under an account "
                                                   "that holds the permissions.")
            self.store.automation_update(aid, state="enabled", failures=0, note=None, updated_at=_iso(now),
                                         runtime="{}", next_run_at=d.schedule.next_after(now) if d.schedule else None)
        else:
            if row["state"] != "enabled":
                return f"'{aid}' is not enabled ({row['state']})."
            self.store.automation_update(aid, state="disabled", next_run_at=None, updated_at=_iso(now))
        self.store.audit("automation.enabled" if enabled else "automation.disabled", automation=aid, by=actor.label)
        self.reload()
        return f"'{aid}' {'enabled' if enabled else 'disabled'}."

    def delete(self, actor: rbac.Actor, aid: str) -> str:
        self._row(aid)
        self.store.automation_update(aid, state="deleted", next_run_at=None, updated_at=_iso(self.now()))
        self.store.audit("automation.deleted", automation=aid, by=actor.label)
        self.reload()
        return f"Deleted '{aid}' (its run history is kept)."

    def run_now(self, actor: rbac.Actor, aid: str, player: dict | None = None) -> str:
        row = self._row(aid)
        if row["state"] not in ("enabled", "disabled"):
            raise AutomationError("bad_state", f"'{aid}' is {row['state']}: it can't run.")
        d = self.definition(row)
        if d.has_subject and not player and any(a["type"] in ("message", "set_role") or a.get("to") == "player"
                                                for a in d.actions):
            raise AutomationError("usage", f"'{aid}' acts on a player: give one with --player NAME.")
        data = dict(player or {})
        data["manual_by"] = actor.label
        rid = self._create_run(aid, d, row["version"], "manual", f"manual:{uuid.uuid4().hex}",
                               data.get("player_uuid"), data, (), check_rates=True)
        if rid is None:
            raise AutomationError("limited", "Refused by a rate limit (see 'mcai audit show --event automation.').")
        self.store.audit("automation.manual_run", automation=aid, run=rid, by=actor.label)
        self.wake.set()
        return rid

    def propose(self, actor: rbac.Actor, text: str) -> str:
        return self.gw.enqueue_internal("automation", text, None, context={"proposer": actor.label})

    def proposal_result(self, row, raw: str) -> tuple[bool, str]:
        """Store the AI's definition as a 'proposed' automation (never enabled without approval)."""
        proposer = json.loads(row["context"] or "{}").get("proposer", "host")
        try:
            data = extract_json(raw)
            d = parse_definition(data, self.default_tz)
        except DefinitionError as e:
            return False, f"invalid automation: {e}"
        base = d.id
        for n in range(2, 100):
            if self.store.automation(d.id) is None:
                break
            data["id"] = f"{base[:36]}-{n}"
            d = parse_definition(data, self.default_tz)
        now = self.now()
        self.store.automation_upsert(d.id, proposer, _iso(now), name=d.name, definition=json.dumps(d.as_dict(),
                                     sort_keys=True), state="proposed", owner=proposer, source="ai",
                                     privileged=int(d.privileged), updated_at=_iso(now), note=f"request {row['id']}",
                                     failures=0, runtime="{}", approved_by=None, approved_at=None, next_run_at=None)
        self.store.audit("automation.proposed", automation=d.id, request_id=row["id"], by=proposer,
                         privileged=d.privileged, permissions=d.required_permissions, sha256=definition_hash(d))
        return True, f"Proposed automation '{d.id}' ({d.name}): {d.trigger_text()}, {len(d.actions)} action(s). " \
                     f"Review with 'mcai automations show {d.id}'."

    def examples(self) -> list[dict]:
        out = []
        paths = []
        for d in [self.examples_dir, *self.extra_example_dirs]:
            if d and Path(d).is_dir():
                paths += sorted(Path(d).glob("*.toml"))
        for p in paths:
            try:
                defn = parse_definition(parse_text(p.read_text(encoding="utf-8")), self.default_tz)
                out.append({"file": p.name, "id": defn.id, "name": defn.name, "description": defn.description,
                            "trigger": defn.trigger_text(), "privileged": defn.privileged, "definition": defn})
            except (DefinitionError, OSError) as e:
                out.append({"file": p.name, "error": str(e)})
        return out

    # ------------------------------------------------------------ guards and conditions
    def owner_ok(self, row, d: Definition) -> tuple[bool, str]:
        owner = row["owner"]
        if owner == "host":
            return True, ""
        kind, _, name = owner.partition(":")
        if kind != "account":
            return False, f"unknown owner {owner}"
        acc = self.store.account(name)
        if acc is None or acc["disabled"]:
            return False, f"its owner account '{name}' is disabled or gone"
        missing = [p for p in d.required_permissions if not rbac.can(acc["role"], p)]
        if missing:
            return False, f"its owner '{name}' ({acc['role']}) no longer has {', '.join(missing)}"
        return True, ""

    def players_online(self) -> list[str]:
        return list(self.gw.heartbeat.get("players", [])) if self.gw.mod_online() else []

    def conditions_ok(self, d: Definition, subject: str | None) -> tuple[bool, str]:
        c = d.conditions
        n = len(self.players_online())
        if "players_online_min" in c and n < c["players_online_min"]:
            return False, f"{n} player(s) online, needs {c['players_online_min']}"
        if "players_online_max" in c and n > c["players_online_max"]:
            return False, f"{n} player(s) online, max {c['players_online_max']}"
        local = dt.datetime.fromtimestamp(self.now(), timezone(d.timezone))
        if "days" in c and DAYS[local.weekday()] not in c["days"]:
            return False, "not on this day"
        if "time_between" in c:
            a, b = c["time_between"]
            hm = local.strftime("%H:%M")
            inside = a <= hm < b if a <= b else (hm >= a or hm < b)
            if not inside:
                return False, f"outside {a}-{b}"
        if subject and ({"player_role_min", "player_roles", "playtime_hours_min"} & set(c)):
            p = self.store.player(subject)
            role = p["role"] if p else "player"
            if "player_role_min" in c and rbac.RANK[role] < rbac.RANK[c["player_role_min"]]:
                return False, f"player role {role} is below {c['player_role_min']}"
            if "player_roles" in c and role not in c["player_roles"]:
                return False, f"player role {role} not in {', '.join(c['player_roles'])}"
            if "playtime_hours_min" in c and self.store.playtime_hours(subject) < c["playtime_hours_min"]:
                return False, "not enough playtime"
        return True, ""

    def _create_run(self, aid: str, d: Definition, version: int, trigger: str, key: str, subject: str | None,
                    event: dict, chain: tuple, check_rates: bool = True) -> str | None:
        now = self.now()
        if aid in chain or len(chain) >= MAX_CHAIN:
            why = "loop: the event was caused by this automation" if aid in chain else f"chain deeper than {MAX_CHAIN}"
            self._audit_throttled(f"{why[:4]}:{aid}", "automation.guard", automation=aid, reason=why,
                                  chain=list(chain))
            return None
        with self.lock:
            while self.recent and self.recent[0] < now - 60:
                self.recent.popleft()
            if check_rates:
                if len(self.recent) >= self.max_runs_per_minute:
                    self._audit_throttled("global-rate", "automation.guard", automation=aid,
                                          reason=f"more than {self.max_runs_per_minute} runs per minute")
                    return None
                if self.store.runs_since(aid, now - 3600) >= d.limits["max_runs_per_hour"]:
                    self._audit_throttled(f"hour:{aid}", "automation.guard", automation=aid,
                                          reason=f"max_runs_per_hour ({d.limits['max_runs_per_hour']})")
                    return None
                cd = d.limits["cooldown_s"]
                if cd and self.store.last_run_at(aid, subject or "") > now - cd:
                    return None
            rid = self.store.run_create(aid, version, key, trigger, subject or "", event, list(chain), now)
            if rid:
                self.recent.append(now)
        return rid

    # ------------------------------------------------------------ events
    def emit(self, event: str, data: dict, key: str, chain: tuple = (), only: set | None = None) -> list[str]:
        """An event happened. Starts every enabled automation that listens for it and whose conditions hold."""
        created = []
        with self.lock:
            defs = list(self.defs.items())
        for aid, d in defs:
            if d.event != event or (only is not None and aid not in only):
                continue
            if event == "player.trigger" and d.trigger["name"] != data.get("trigger"):
                continue
            adv_id = data.get("advancement_id", data.get("advancement", ""))
            if event == "player.advancement" and not fnmatch.fnmatchcase(adv_id, d.trigger["advancement"]):
                continue
            subject = data.get("player_uuid") if d.has_subject else None
            ok, why = self.conditions_ok(d, subject)
            if not ok:
                log.debug("automation %s skipped for %s: %s", aid, event, why)
                continue
            row = self.store.automation(aid)
            if row is None or row["state"] != "enabled":
                continue
            rid = self._create_run(aid, d, row["version"], event, f"evt:{aid}:{key}", subject, data, chain)
            if rid:
                created.append(rid)
        if created:
            self.wake.set()
        return created

    def trigger_names(self) -> set[str]:
        with self.lock:
            return {d.trigger["name"] for d in self.defs.values() if d.event == "player.trigger"}

    def areas(self) -> list[dict]:
        """Areas the mod must watch: one per enabled area automation (the id is the automation id). An area that
        names an example pack follows its latest placed install, and is left out while there is none."""
        with self.lock:
            items = [(aid, d) for aid, d in self.defs.items() if d.event in ("player.enter_area", "player.leave_area")]
        out = []
        for aid, d in items:
            a = d.trigger["area"]
            if "example" in a:
                ex = getattr(self.gw, "examples", None)
                a = ex.area(a["example"]) if ex else None
                if a is None:
                    continue
            out.append({"id": aid, "on": "enter" if d.event == "player.enter_area" else "leave",
                        "dimension": a["dimension"], "from": a["from"], "to": a["to"]})
        return out[:200]

    def area_name(self, aid: str) -> str | None:
        with self.lock:
            d = self.defs.get(aid)
        return d.trigger["area"]["name"] if d and d.event in ("player.enter_area", "player.leave_area") else None

    def on_heartbeat(self, stats: dict, world: dict) -> None:
        now = self.now()
        up = stats.get("uptime_s")
        if isinstance(up, (int, float)):
            prev = self.hb.get("uptime")
            if (prev is None and up < 180) or (prev is not None and up + 5 < prev):
                self.emit("server.started", {}, key=f"start:{int((now - up) // 300)}")
            self.hb["uptime"] = up
        t = world.get("time") if isinstance(world, dict) else None
        if isinstance(t, int) and not isinstance(t, bool) and t >= 0:
            phase = "night" if 13000 <= t % 24000 < 23000 else "day"
            if self.hb.get("phase") not in (None, phase):
                self.emit(f"world.{phase}_start", {}, key=f"{phase}:{t // 24000}")
            self.hb["phase"] = phase
        rain = world.get("raining") if isinstance(world, dict) else None
        if isinstance(rain, bool):
            if self.hb.get("rain") not in (None, rain):
                self.emit("world.rain_start" if rain else "world.rain_stop", {},
                          key=f"rain:{int(rain)}:{int(now // 60)}")
            self.hb["rain"] = rain
        mspt = stats.get("mspt")
        used, mx = stats.get("heap_used_mb"), stats.get("heap_max_mb")
        metrics = {"mspt": mspt, "tps": round(min(20.0, 1000 / mspt), 2) if mspt else None,
                   "players_online": len(stats.get("players", [])),
                   "heap_pct": round(100 * used / mx, 1) if used is not None and mx else None}
        with self.lock:
            thresholds = [(aid, d) for aid, d in self.defs.items() if d.event == "server.threshold"]
        for aid, d in thresholds:
            self._threshold(aid, d, metrics, now)

    def _threshold(self, aid: str, d: Definition, metrics: dict, now: float) -> None:
        t = d.trigger
        value = metrics.get(t["metric"])
        if value is None:
            return
        breached = value > t["above"] if "above" in t else value < t["below"]
        row = self.store.automation(aid)
        state = json.loads(row["runtime"] or "{}") if row else {}
        new = dict(state)
        if not breached:
            new = {}
        else:
            new.setdefault("since", now)
            if not new.get("fired") and now - new["since"] >= t["for_s"]:
                new["fired"] = True
                self.emit("server.threshold", {"metric": t["metric"], "value": value},
                          key=f"threshold:{int(new['since'])}", only={aid})
        if new != state:
            self.store.automation_update(aid, runtime=json.dumps(new))

    # ------------------------------------------------------------ scheduler
    def tick(self) -> None:
        """Create runs for due schedule slots, then execute due runs (inline unless a pool is running)."""
        now = self.now()
        with self.lock:
            scheduled = [(aid, d) for aid, d in self.defs.items() if d.schedule]
        for aid, d in scheduled:
            row = self.store.automation(aid)
            if row is None or row["state"] != "enabled":
                continue
            nxt = row["next_run_at"]
            if nxt is None:
                self.store.automation_update(aid, next_run_at=d.schedule.next_after(now))
                continue
            if now < nxt:
                continue
            late = now - nxt
            run_it = late <= d.limits["misfire_grace_s"] or d.limits["misfire"] == "run"
            if run_it:
                ok, why = self.conditions_ok(d, None)
                if ok:
                    self._create_run(aid, d, row["version"], "schedule", f"sched:{aid}:{int(nxt)}", None,
                                     {"slot": _iso(nxt)}, ())
                else:
                    self.store.run_create(aid, row["version"], f"sched:{aid}:{int(nxt)}", "schedule", "",
                                          {"slot": _iso(nxt)}, [], now, status="skipped", error=why)
            else:
                self.store.audit("automation.misfire", automation=aid, slot=_iso(nxt), late_s=int(late))
            self.store.automation_update(aid, next_run_at=d.schedule.next_after(max(now, nxt)))
        self.dispatch_due()

    def dispatch_due(self) -> None:
        for r in self.store.runs_due(self.now()):
            if self.store.run_claim(r["id"], self.now()):
                if self.pool:
                    self.pool.submit(self._safe_execute, r["id"])
                else:
                    self._safe_execute(r["id"])

    def _safe_execute(self, run_id: str) -> None:
        try:
            self.execute(run_id)
        except Exception as e:
            log.exception("automation run %s crashed", run_id)
            self._finish(run_id, "failed", f"internal error: {e.__class__.__name__}", None)

    # ------------------------------------------------------------ execution
    def _finish(self, run_id: str, status: str, error: str | None, outputs: list | None) -> None:
        fields = {"status": status, "finished": self.now(), "error": error}
        if outputs is not None:
            fields["output"] = json.dumps(outputs[-20:])
        self.store.run_update(run_id, **fields)
        r = self.store.run(run_id)
        self.store.audit("automation.run", automation=r["automation_id"], run=run_id, trigger=r["trigger"],
                         status=status, attempt=r["attempt"], error=error)

    def _failed(self, aid: str, d: Definition, error: str) -> None:
        row = self.store.automation(aid)
        failures = (row["failures"] or 0) + 1
        self.store.automation_update(aid, failures=failures)
        if d.limits["on_error"] == "disable" or failures >= MAX_CONSECUTIVE_FAILURES:
            self.suspend(aid, f"{failures} failed run(s) in a row; last error: {error}"[:200])

    def suspend(self, aid: str, why: str) -> None:
        self.store.automation_update(aid, state="suspended", note=why, next_run_at=None, updated_at=_iso(self.now()))
        self.store.audit("automation.suspended", automation=aid, reason=why)
        self.reload()

    def execute(self, run_id: str) -> None:
        run = self.store.run(run_id)
        aid = run["automation_id"]
        row = self.store.automation(aid)
        allowed = ("enabled", "disabled") if run["trigger"] == "manual" else ("enabled",)
        if row is None or row["state"] not in allowed:
            return self._finish(run_id, "skipped", f"automation is {row['state'] if row else 'gone'}", None)
        if row["version"] != run["version"]:
            return self._finish(run_id, "skipped", "the definition changed since this run was created", None)
        d = self.definition(row)
        ok, why = self.owner_ok(row, d)
        if not ok:
            self._finish(run_id, "denied", why, None)
            self.suspend(aid, why)
            return
        event = json.loads(run["event"] or "{}")
        values = self._values(d, event)
        outputs = json.loads(run["output"] or "[]")
        deadline = self.now() + d.limits["timeout_s"]
        errors = []
        for i in range(run["step"], len(d.actions)):
            a = d.actions[i]
            try:
                if self.now() > deadline:
                    raise ActionError(f"timeout after {d.limits['timeout_s']} s")
                out = self._do(aid, run, d, a, event, values, deadline)
                outputs.append(f"{i + 1}. {a['type']}: {out}")
            except ActionError as e:
                outputs.append(f"{i + 1}. {a['type']}: error: {e}")
                if e.retryable and run["attempt"] < d.limits["retries"]:
                    delay = min(3600, d.limits["retry_delay_s"] * 2 ** run["attempt"])
                    self.store.run_update(run_id, status="retry_wait", attempt=run["attempt"] + 1, step=i,
                                          next_attempt=self.now() + delay, error=str(e), output=json.dumps(outputs[-20:]))
                    return None
                if d.limits["on_error"] == "continue":
                    errors.append(str(e))
                    self.store.run_update(run_id, step=i + 1, output=json.dumps(outputs[-20:]))
                    continue
                self._finish(run_id, "failed", str(e), outputs)
                self._failed(aid, d, str(e))
                return None
            self.store.run_update(run_id, step=i + 1, output=json.dumps(outputs[-20:]))
        if errors:
            self._finish(run_id, "partial", "; ".join(errors)[:300], outputs)
            self._failed(aid, d, errors[-1])
        else:
            self._finish(run_id, "succeeded", None, outputs)
            self.store.automation_update(aid, failures=0)
        return None

    def _values(self, d: Definition, event: dict) -> dict:
        local = dt.datetime.fromtimestamp(self.now(), timezone(d.timezone))
        v = {"players_online": len(self.players_online()), "time": local.strftime("%H:%M"),
             "date": local.strftime("%Y-%m-%d"), "automation": d.name}
        for k in ("area", "advancement", "trigger", "title", "request", "blocks", "metric", "value"):
            if k in event:
                v[k] = event[k]
        if event.get("player_name"):
            v["player"] = event["player_name"]
        return v

    def _need_server(self) -> None:
        if not self.gw.mod_online():
            raise ActionError("the Minecraft server is not connected")

    def _rcon(self, cmd: str) -> str:
        if self.gw.rcon is None:
            raise ActionError("the gateway has no RCON access", retryable=False)
        from .rcon import RconError
        try:
            return self.gw.rcon.command(cmd)
        except (RconError, OSError) as e:
            raise ActionError(f"the server didn't answer RCON ({e.__class__.__name__})") from None

    def _staff(self, min_role: str) -> list[str]:
        out = []
        names = set(self.players_online())
        for p in self.store.players():
            if p["online"] and p["name"] in names and rbac.RANK[p["role"]] >= rbac.RANK[min_role]:
                out.append(p["uuid"])
        return out

    def _deliver(self, to: str, text: str, subject: str | None) -> str:
        self._need_server()
        if to == "broadcast":
            self.gw.broadcast(text)
            return "broadcast"
        if to == "player":
            if not subject:
                raise ActionError("no player for this run", retryable=False)
            self.gw.notify(subject, text, "info")
            return "sent to the player"
        targets = self._staff("operator")
        for u in targets:
            self.gw.notify(u, text, "info")
        return f"sent to {len(targets)} staff member(s)"

    def _do(self, aid, run, d: Definition, a: dict, event: dict, values: dict, deadline: float) -> str:
        t = a["type"]
        subject = run["subject"] or None
        if t in ("message", "broadcast", "notify_role", "log"):
            text = render(a["text"], values)
            if t == "log":
                self.store.audit("automation.log", automation=aid, run=run["id"], message=text[:MAX_TEXT])
                return "logged"
            if t == "message":
                return self._deliver("player", text, subject)
            if t == "broadcast":
                return self._deliver("broadcast", text, None)
            self._need_server()
            targets = self._staff(a["min_role"])
            for u in targets:
                self.gw.notify(u, text, "info")
            return f"sent to {len(targets)} player(s)"
        if t == "status_report":
            text = self.gw.status_line()
            if a["to"] == "log":
                self.store.audit("automation.log", automation=aid, run=run["id"], message=text)
                return text
            return self._deliver(a["to"], text, None)
        if t == "save":
            return self._rcon("save-all") or "saved"
        if t == "world":
            cmd = f"time set {a['value']}" if a["set"] == "time" else f"weather {a['value']}"
            return self._rcon(cmd) or cmd
        if t == "set_role":
            return self._set_role(aid, run, a["role"], subject)
        if t == "ai_message":
            return self._ai_message(aid, run, d, a, values, subject, deadline)
        if t == "ai_build":
            self._ai_allowed(aid, d, 0.0)
            x, y, z = a["at"]
            rid = self.gw.enqueue_internal("build", render(a["prompt"], values), run["id"],
                                           context={"dimension": a["dimension"], "x": x, "y": y, "z": z, "yaw": 0})
            return f"build request {rid} queued; staff approve it with 'mcai ai approve {rid}'"
        if t == "example_tour":
            stops = self.gw.examples.tour(a["example"])
            if not stops:
                raise ActionError(f"{a['example']} is not installed", retryable=False)
            self._need_server()
            for name, (x, y, z) in stops[:12]:
                self.gw.notify(subject, f"{name}: {x} {y} {z}", "info")
            return f"sent {len(stops[:12])} place(s) of {a['example']}"
        if t == "example_restore":
            from .examples import ExampleError
            row = self.gw.examples.latest(a["example"])
            if row is None:
                raise ActionError(f"{a['example']} is not installed", retryable=False)
            actor = rbac.Actor("account", f"automation:{aid}", f"automation:{aid}", "admin")
            try:
                return self.gw.examples.repair(actor, row["id"])
            except ExampleError as e:
                raise ActionError(e.message, retryable=e.code in ("busy", "unavailable")) from None
        raise ActionError(f"unknown action {t}", retryable=False)

    def _set_role(self, aid: str, run, role: str, subject: str | None) -> str:
        p = self.store.player(subject) if subject else None
        if p is None:
            raise ActionError("unknown player", retryable=False)
        if rbac.RANK[p["role"]] >= rbac.RANK[role]:
            return f"{p['name']} already has {p['role']} (automations never lower a role)"
        row = self.store.automation(aid)
        owner_role = "owner" if row["owner"] == "host" else \
            (self.store.account(row["owner"].partition(":")[2]) or {"role": "auditor"})["role"]
        if not rbac.may_assign(owner_role, p["role"], role):
            raise ActionError(f"the owner ({owner_role}) may not give {role}", retryable=False)
        self.store.set_player_role(p["uuid"], role)
        self.store.audit("role.changed", target="player", player_uuid=p["uuid"], player_name=p["name"],
                         old_role=p["role"], new_role=role, by=f"automation:{aid}", run=run["id"])
        if self.gw.mod_online():
            self.gw.notify(p["uuid"], f"Your role on this server is now {role}.", "info")
        return f"{p['name']}: {p['role']} -> {role}"

    def _ai_allowed(self, aid: str, d: Definition, max_usd: float) -> None:
        def refuse(why):
            self._audit_throttled(f"ai:{aid}:{why[:20]}", "automation.guard", automation=aid, reason=why)
            raise ActionError(why, retryable=False)
        if not self.gw.ai_enabled():
            refuse("the AI is switched off")
        budget = self.gw.daily_budget()
        if budget <= 0 or self.store.spend_today() >= budget:
            refuse("the daily AI budget is used up")
        usd, tokens = self.store.automation_ai_usage(aid)
        if usd + max_usd > d.limits["ai_max_usd_per_day"]:
            refuse(f"this automation's AI budget for today (${d.limits['ai_max_usd_per_day']:.2f}) is used up")
        if tokens >= d.limits["ai_max_tokens_per_day"]:
            refuse(f"this automation's AI token limit for today ({d.limits['ai_max_tokens_per_day']}) is reached")
        if self.store.automation_ai_calls(3600) >= self.ai_calls_per_hour:
            refuse(f"automations made {self.ai_calls_per_hour} AI calls in the last hour (AUTOMATION_AI_CALLS_PER_HOUR)")

    def _ai_message(self, aid, run, d: Definition, a: dict, values: dict, subject, deadline: float) -> str:
        if a["to"] != "player" or subject:
            self._need_server()
        self._ai_allowed(aid, d, a["max_usd"])
        waiter = threading.Event()
        try:
            rid = self.gw.enqueue_internal("ask", render(a["prompt"], values), run["id"], max_usd=a["max_usd"])
        except Exception as e:
            raise ActionError(getattr(e, "message", str(e))) from None
        with self.lock:
            self.ai_waiters[rid] = waiter
        try:
            if self.store.get(rid)["status"] in ("queued", "running"):
                waiter.wait(max(1.0, min(deadline - self.now(), self.gw.s.request_timeout_s + 5)))
            r = self.store.get(rid)
        finally:
            with self.lock:
                self.ai_waiters.pop(rid, None)
        if r["status"] == "done":
            out = self._deliver(a["to"], r["reply"] or "", subject)
            return f"AI request {rid} (${r['cost_usd']:.4f}), {out}"
        if r["status"] in ("queued", "running"):
            self.gw.cancel_internal(rid)
            raise ActionError(f"AI request {rid} took too long")
        retry = "configured" not in (r["error"] or "")
        raise ActionError(f"AI request {rid} failed: {r['error'] or r['status']}", retryable=retry)

    def ai_done(self, rid: str) -> None:
        with self.lock:
            w = self.ai_waiters.get(rid)
        if w:
            w.set()

    # ------------------------------------------------------------ threads
    def start(self, workers: int = 2) -> None:
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="automation")

        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("automation tick failed")
                self.wake.wait(1.0)
                self.wake.clear()
        threading.Thread(target=loop, daemon=True, name="automation-scheduler").start()

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
