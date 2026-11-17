"""The TUI renders server data and sends role-checked mcai commands (headless, fake session: no SSH)."""
import asyncio
import sys

import pytest

from conftest import REPO

textual = pytest.importorskip("textual")
sys.path.insert(0, str(REPO / "management-cli"))
import mcai_cli  # noqa: E402
import mcai_tui  # noqa: E402

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM0ZlbRxvJeha1Tyk7VWsHnGdq3m8m2Jw0zv1d8gB7cK"


class FakeSession:
    calls = []

    def __init__(self, *a, **k):
        pass

    def call(self, argv):
        FakeSession.calls.append(argv)
        if argv == ["whoami"]:
            return {"ok": True, "data": {"account": "ann", "role": "auditor"}}
        if argv == ["status"]:
            return {"ok": True, "data": {"server": {"online": True, "tps": 20.0, "heap_used_mb": 300, "heap_max_mb": 2048,
                                                    "players": ["Steve"]},
                                         "ai": {"enabled": True, "spent_today_usd": 0.1, "budget_usd": 2.0,
                                                "worker_online": True}}}
        if argv == ["players", "list"]:
            return {"ok": True, "data": [{"uuid": "u1", "name": "Steve", "role": "builder", "online": 1,
                                          "last_seen": "2026-11-17T10:00:00Z"}]}
        if argv == ["ai", "pending"]:
            return {"ok": True, "data": [{"id": "abc123", "player_name": "Steve", "kind": "build", "status": "proposed",
                                          "title": "Tower"}]}
        if argv[0] == "logs":
            return {"ok": True, "data": {"lines": ["[12:00] hello"] if "--after" not in argv else [], "cursor": "1:5"}}
        if argv[0] == "say":
            return {"ok": False, "code": "forbidden", "text": "'say' needs the permission players.moderate"}
        return {"ok": True, "text": "ok"}

    def close(self):
        pass


def test_tui_renders_and_reports_denials(tmp_path, monkeypatch):
    monkeypatch.setattr(mcai_cli, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mcai_tui, "Session", FakeSession)
    cfg = mcai_cli.Config()
    cfg.data["remotes"]["home"] = {"host": "h", "port": 2222, "host_key": KEY}
    app = mcai_tui.McaiTui(cfg, "home")

    async def scenario():
        async with app.run_test(size=(140, 40)) as pilot:
            await asyncio.sleep(1.5)
            assert "you: auditor" in str(app.query_one("#status").render())
            assert app.query_one("#players").row_count == 1 and app.query_one("#ai").row_count == 1
            await pilot.click("#cmd")
            await pilot.press(*"say hi", "enter")
            await asyncio.sleep(0.5)
            lines = [str(x.text) for x in app.query_one("#log").lines]
            assert any("needs the permission" in x for x in lines), lines
            app.exit()
    asyncio.run(scenario())
    assert ["say", "hi"] in FakeSession.calls
