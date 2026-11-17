"""Schedules for automations: 5-field cron expressions in an IANA timezone, or fixed intervals.

Cron follows the usual (Vixie) rules: minute hour day-of-month month day-of-week, with `*`, lists, ranges,
steps and names (jan..dec, sun..sat; 0 and 7 are Sunday). When both day fields are restricted, a day matches
if either matches. Daylight saving: a local time that doesn't exist (spring forward) is skipped that day; a
local time that happens twice (fall back) runs once, at its first occurrence.
"""
from __future__ import annotations

import datetime as dt
import re
import zoneinfo

ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
           "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
MONTHS = {n: i for i, n in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
DOWS = {n: i for i, n in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
TZ_RE = re.compile(r"^(UTC|[A-Za-z][A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2})$")
EVERY_RE = re.compile(r"^(\d{1,4})([mhd])$")


class ScheduleError(ValueError):
    pass


def timezone(name: str) -> zoneinfo.ZoneInfo:
    if not isinstance(name, str) or not TZ_RE.match(name):
        raise ScheduleError(f"unknown timezone {name!r} (use an IANA name like Europe/Berlin or UTC)")
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"unknown timezone {name!r}") from None


def _value(token: str, names: dict, lo: int, hi: int) -> int:
    token = token.lower()
    if token in names:
        return names[token]
    if not token.isdigit():
        raise ScheduleError(f"bad cron value {token!r}")
    v = int(token)
    if not lo <= v <= hi:
        raise ScheduleError(f"cron value {v} outside {lo}-{hi}")
    return v


def _field(spec: str, lo: int, hi: int, names: dict) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or not 1 <= int(s) <= hi:
                raise ScheduleError(f"bad cron step {s!r}")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = _value(x, names, lo, hi), _value(y, names, lo, hi)
            if a > b:
                raise ScheduleError(f"bad cron range {part!r}")
        else:
            a = _value(part, names, lo, hi)
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out


class Cron:
    def __init__(self, expr: str):
        if not isinstance(expr, str) or len(expr) > 100:
            raise ScheduleError("cron must be a short string like '0 18 * * *'")
        expr = ALIASES.get(expr.strip().lower(), expr.strip())
        parts = expr.split()
        if len(parts) != 5:
            raise ScheduleError("cron needs 5 fields: minute hour day-of-month month day-of-week")
        self.expr = expr
        self.minutes = sorted(_field(parts[0], 0, 59, {}))
        self.hours = sorted(_field(parts[1], 0, 23, {}))
        self.doms = _field(parts[2], 1, 31, {})
        self.months = _field(parts[3], 1, 12, MONTHS)
        self.dows = {d % 7 for d in _field(parts[4], 0, 7, DOWS)}
        self.dom_any, self.dow_any = parts[2] == "*", parts[4] == "*"

    def day_matches(self, d: dt.date) -> bool:
        if d.month not in self.months:
            return False
        dom, dow = d.day in self.doms, (d.isoweekday() % 7) in self.dows
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow

    def next_after(self, after: float, tz: dt.tzinfo) -> float | None:
        """First matching instant strictly after `after` (epoch seconds), or None within 5 years."""
        start = (dt.datetime.fromtimestamp(after, tz).replace(tzinfo=None, second=0, microsecond=0)
                 + dt.timedelta(minutes=1))
        day = start.date()
        for _ in range(366 * 5):
            if self.day_matches(day):
                for h in self.hours:
                    for m in self.minutes:
                        naive = dt.datetime.combine(day, dt.time(h, m))
                        if naive < start:
                            continue
                        aware = naive.replace(tzinfo=tz)                # fold=0: first occurrence
                        if aware.astimezone(dt.timezone.utc).astimezone(tz).replace(tzinfo=None) != naive:
                            continue                                    # doesn't exist today (DST gap)
                        ts = aware.timestamp()
                        if ts > after:
                            return ts
            day += dt.timedelta(days=1)
        return None


class Schedule:
    """Either a cron expression (in a timezone) or a fixed interval aligned to the epoch (restart-proof)."""

    def __init__(self, cron: str | None = None, every: str | None = None, tz: str = "UTC"):
        if (cron is None) == (every is None):
            raise ScheduleError("give either schedule (cron) or every, not both")
        self.tz_name = tz
        self.tz = timezone(tz)
        self.cron = Cron(cron) if cron is not None else None
        self.period = None
        if every is not None:
            m = EVERY_RE.match(str(every))
            if not m:
                raise ScheduleError("every takes a number and m/h/d, like 15m, 6h or 1d")
            self.period = int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
            if not 60 <= self.period <= 31 * 86400:
                raise ScheduleError("every must be between 1m and 31d")

    def next_after(self, after: float) -> float | None:
        if self.period:
            return (int(after) // self.period + 1) * self.period
        return self.cron.next_after(after, self.tz)

    def upcoming(self, after: float, n: int = 3) -> list[float]:
        out, t = [], after
        for _ in range(n):
            t = self.next_after(t)
            if t is None:
                break
            out.append(t)
        return out

    def describe(self) -> str:
        if self.period:
            p = self.period
            return f"every {p // 86400}d" if p % 86400 == 0 else f"every {p // 3600}h" if p % 3600 == 0 \
                else f"every {p // 60}m"
        return f"cron '{self.cron.expr}' ({self.tz_name})"
