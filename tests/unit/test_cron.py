"""Cron expressions, intervals and timezones (including both daylight-saving transitions)."""
import datetime as dt
import zoneinfo

import pytest

from mcai.cron import Cron, Schedule, ScheduleError, timezone

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
UTC = dt.timezone.utc


def ts(y, mo, d, h=0, mi=0, tz=UTC):
    return dt.datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


def local(t, tz=BERLIN):
    return dt.datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d %H:%M %Z")


@pytest.mark.parametrize("expr", ["", "* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *",
                                  "*/0 * * * *", "5-1 * * * *", "a * * * *", "* * * * 8", "1,,2 * * * *",
                                  "* * * * * *"])
def test_bad_expressions(expr):
    with pytest.raises(ScheduleError):
        Cron(expr)


def test_fields_names_steps_aliases():
    c = Cron("*/15 9-17 * jan,dec mon-fri")
    assert c.minutes == [0, 15, 30, 45] and c.hours == list(range(9, 18))
    assert c.months == {1, 12} and c.dows == {1, 2, 3, 4, 5}
    assert Cron("0 0 * * 7").dows == {0}, "7 is Sunday too"
    assert Cron("@daily").expr == "0 0 * * *"
    assert Cron("5/20 * * * *").minutes == [5, 25, 45]


def test_next_after_basic():
    c = Cron("30 18 * * *")
    t = c.next_after(ts(2026, 11, 17, 10, 0), UTC)
    assert t == ts(2026, 11, 17, 18, 30)
    assert c.next_after(t, UTC) == ts(2026, 11, 18, 18, 30), "strictly after"


def test_dom_or_dow_when_both_restricted():
    c = Cron("0 12 1 * mon")                      # the 1st of the month OR any Monday
    t = c.next_after(ts(2026, 11, 17), UTC)       # Tue 17 Nov
    assert t == ts(2026, 11, 23, 12)              # next Monday comes before Dec 1
    assert c.next_after(ts(2026, 11, 30, 13), UTC) == ts(2026, 12, 1, 12)


def test_timezone_is_applied():
    t = Cron("0 18 * * *").next_after(ts(2026, 11, 17, 0, 0), BERLIN)
    assert local(t) == "2026-11-17 18:00 CET" and t == ts(2026, 11, 17, 17, 0)


def test_spring_forward_gap_is_skipped():
    # 29 Mar 2026: Berlin clocks jump from 02:00 to 03:00, so 02:30 doesn't exist that day.
    c = Cron("30 2 * * *")
    t = c.next_after(ts(2026, 3, 28, 12, 0), BERLIN)
    assert local(t) == "2026-03-30 02:30 CEST"


def test_fall_back_runs_once():
    # 25 Oct 2026: 02:00-03:00 happens twice in Berlin; the job runs at the first 02:30 only.
    c = Cron("30 2 * * *")
    first = c.next_after(ts(2026, 10, 24, 12, 0), BERLIN)
    assert local(first) == "2026-10-25 02:30 CEST"
    assert local(c.next_after(first, BERLIN)) == "2026-10-26 02:30 CET"


def test_interval_aligned_and_restart_proof():
    s = Schedule(every="15m")
    a = s.next_after(ts(2026, 11, 17, 10, 7))
    assert a == ts(2026, 11, 17, 10, 15)
    assert s.next_after(a) == a + 900
    assert s.next_after(a - 0.5) == a, "the same slot is computed after a restart"


@pytest.mark.parametrize("every", ["0m", "59s", "32d", "1w", "", "15"])
def test_bad_intervals(every):
    with pytest.raises(ScheduleError):
        Schedule(every=every)


def test_schedule_needs_exactly_one():
    with pytest.raises(ScheduleError):
        Schedule()
    with pytest.raises(ScheduleError):
        Schedule(cron="* * * * *", every="5m")


@pytest.mark.parametrize("name", ["Mars/Olympus", "../etc/passwd", "/etc/localtime", "Europe/../UTC", ""])
def test_bad_timezones(name):
    with pytest.raises(ScheduleError):
        timezone(name)


def test_upcoming_and_describe():
    s = Schedule(cron="0 9 * * mon", tz="Europe/Berlin")
    up = s.upcoming(ts(2026, 11, 17), 3)
    assert [local(t)[:10] for t in up] == ["2026-11-23", "2026-11-30", "2026-12-07"]
    assert s.describe() == "cron '0 9 * * mon' (Europe/Berlin)"
    assert Schedule(every="6h").describe() == "every 6h"
