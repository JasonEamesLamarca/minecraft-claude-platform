"""Automation definitions: the examples, strict validation, permissions and privilege, AI answer parsing."""
import json
from pathlib import Path

import pytest

from mcai.automation import DefinitionError, extract_json, parse_definition, parse_text, render

EXAMPLES = sorted((Path(__file__).resolve().parents[2] / "automation-examples").glob("*.toml"))


def base(**kw):
    d = {"id": "t1", "name": "Test", "trigger": {"every": "1h"}, "actions": [{"type": "log", "text": "hi"}]}
    d.update(kw)
    return d


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_examples_are_valid_and_disabled(path):
    d = parse_definition(parse_text(path.read_text()))
    assert d.id == path.stem and not d.enabled, "examples are installed disabled"
    assert "automations.manage" in d.required_permissions


def test_enough_examples():
    assert len(EXAMPLES) >= 5


def test_privileged_and_permissions():
    d = parse_definition(base(trigger={"event": "player.join"}, actions=[{"type": "set_role", "role": "builder"}]))
    assert d.privileged and "roles.assign" in d.required_permissions
    d = parse_definition(base(actions=[{"type": "save"}, {"type": "broadcast", "text": "x"}]))
    assert not d.privileged and {"server.control", "players.moderate"} <= set(d.required_permissions)


@pytest.mark.parametrize("change,msg", [
    ({"id": "Bad Id"}, "invalid format"),
    ({"extra": 1}, "unknown key"),
    ({"trigger": {}}, "exactly one"),
    ({"trigger": {"every": "1h", "event": "player.join"}}, "exactly one"),
    ({"trigger": {"event": "player.explode"}}, "unknown event"),
    ({"trigger": {"schedule": "61 * * * *"}}, "outside"),
    ({"timezone": "Nowhere/City"}, "timezone"),
    ({"actions": []}, "1 to 10"),
    ({"actions": [{"type": "log", "text": "x"}] * 11}, "1 to 10"),
    ({"actions": [{"type": "command", "command": "op Steve"}]}, "can't run commands"),
    ({"actions": [{"type": "log", "text": "x", "command": "y"}]}, "unknown key"),
    ({"actions": [{"type": "log", "text": "x" * 300}]}, "at most 256"),
    ({"actions": [{"type": "log", "text": "§cred"}]}, "formatting"),
    ({"actions": [{"type": "log", "text": "hi {player}"}]}, "unknown placeholder"),
    ({"actions": [{"type": "message", "text": "hi"}]}, "needs a trigger with a player"),
    ({"actions": [{"type": "set_role", "role": "admin"}], "trigger": {"event": "player.join"}}, "only give"),
    ({"actions": [{"type": "world", "set": "gamerule", "value": "keepInventory"}]}, "set must be"),
    ({"actions": [{"type": "ai_message", "prompt": "hello"}]}, "ai_max_usd_per_day"),
    ({"limits": {"retries": 99}}, "between"),
    ({"limits": {"on_error": "explode"}}, "on_error"),
    ({"conditions": {"player_role_min": "builder"}}, "need a trigger with a player"),
    ({"conditions": {"time_between": ["8:00", "22:00"]}}, "HH:MM"),
    ({"trigger": {"event": "player.trigger"}}, "trigger.name"),
    ({"trigger": {"event": "server.threshold", "metric": "mspt"}}, "above / below"),
    ({"trigger": {"event": "player.enter_area", "area": {"from": [0, 0, 0], "to": [600, 10, 10]}}}, "512"),
    ({"enabled": "yes"}, "true or false"),
])
def test_invalid(change, msg):
    with pytest.raises(DefinitionError, match=msg):
        parse_definition(base(**change))


def test_defaults_and_roundtrip():
    d = parse_definition(base())
    assert d.limits["timeout_s"] == 60 and d.limits["misfire"] == "skip" and d.timezone == "UTC"
    again = parse_definition(json.loads(json.dumps(d.as_dict())))
    assert again.as_dict() == d.as_dict()


def test_placeholders_follow_the_trigger():
    d = parse_definition(base(trigger={"event": "player.enter_area", "area": {"name": "plaza", "from": [0, 60, 0],
                                                                                 "to": [5, 70, 5]}},
                              actions=[{"type": "message", "text": "{player} at {area}, {players_online} online"}]))
    assert render(d.actions[0]["text"], {"player": "Alex", "area": "plaza", "players_online": 3}) == \
        "Alex at plaza, 3 online"


def test_render_cleans_values():
    assert render("hi {player}", {"player": "§4evil‮\x07name"}) == "hi 4evilname"


def test_parse_text_toml_and_json():
    toml = 'id = "t1"\nname = "T"\n[trigger]\nevery = "1h"\n[[actions]]\ntype = "log"\ntext = "x"\n'
    assert parse_definition(parse_text(toml)).id == "t1"
    assert parse_definition(parse_text(json.dumps(base()))).id == "t1"
    with pytest.raises(DefinitionError):
        parse_text("id = ")
    with pytest.raises(DefinitionError):
        parse_text("x" * 70000)


def test_extract_json_from_ai_answer():
    assert extract_json('Sure!\n```json\n{"id": "a"}\n```')["id"] == "a"
    for bad in ("no json here", "{broken", "[1, 2]"):
        with pytest.raises(DefinitionError):
            extract_json(bad)
