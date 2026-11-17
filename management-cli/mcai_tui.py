"""mcai tui - full-screen administration console (Textual), over one SSH session in RPC mode.

Ported from the author's earlier `mc-admin` console, keeping its layout (live log + command line, player list,
status bar, menus by key) but none of its privileges: it no longer opens a shell on the host or runs Docker. Every
action is an `mcai` command that the server checks against your role, exactly as on the command line.

    pip install textual          # once
    mcai tui                     # or: python management-cli/mcai_cli.py tui
"""
from __future__ import annotations

import shlex
import threading
import time

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from mcai_cli import Config, Session, file_argument

POLL_SECONDS = 5
LOG_SECONDS = 2
ROLES = ("owner", "admin", "operator", "builder", "player", "auditor")


# ------------------------------------------------------------------ dialogs
class Choose(ModalScreen):
    """A small menu; returns the chosen option id or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, title: str, options: list[tuple[str, str]]):
        super().__init__()
        self.title_text, self.options = title, options

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Label(self.title_text)
            yield OptionList(*[Option(label, id=key) for key, label in self.options], id="choices")

    @on(OptionList.OptionSelected)
    def chosen(self, event):
        self.dismiss(event.option.id)


class Ask(ModalScreen):
    """A one-line question; returns the text or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, question: str, placeholder: str = ""):
        super().__init__()
        self.question, self.placeholder = question, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Label(self.question)
            yield Input(placeholder=self.placeholder, id="answer")

    @on(Input.Submitted)
    def done(self, event):
        self.dismiss(event.value.strip() or None)


class Confirm(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(False)", show=False), Binding("n", "dismiss(False)", show=False),
                Binding("y", "dismiss(True)", show=False)]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Label(self.message)
            with Horizontal():
                yield Button("No", id="no")
                yield Button("Yes", id="yes", variant="warning")

    @on(Button.Pressed)
    def pressed(self, event):
        self.dismiss(event.button.id == "yes")


# ------------------------------------------------------------------ app
class McaiTui(App):
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; background: $panel; padding: 0 1; }
    #main { height: 1fr; }
    #left { width: 3fr; }
    #right { width: 2fr; }
    #logbox, #playersbox, #aibox, #autobox { border: round $panel-lighten-2; border-title-color: $text-muted; }
    #logbox:focus-within, #playersbox:focus-within, #aibox:focus-within, #autobox:focus-within {
        border: round $accent; }
    #log { height: 1fr; }
    #cmd { height: 1; border: none; padding: 0 1; background: $boost; }
    #playersbox { height: 1fr; }
    #aibox { height: 1fr; }
    #autobox { height: 1fr; }
    #keys { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    Choose, Ask, Confirm { align: center middle; background: $background 60%; }
    #dlg { width: 70; max-width: 95%; height: auto; border: thick $accent; background: $panel; padding: 1 2; }
    #dlg Label { margin-bottom: 1; }
    #dlg Horizontal { height: auto; align: right middle; }
    #dlg Button { margin-left: 2; }
    #choices { height: auto; max-height: 16; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("f2", "focus('cmd')", priority=True),
        Binding("f4", "focus('players')", priority=True),
        Binding("f6", "focus('ai')", priority=True),
        Binding("f7", "focus('auto')", priority=True),
        Binding("f5", "refresh", priority=True),
        Binding("f1", "help", priority=True),
    ]

    def __init__(self, cfg: Config, remote: str | None):
        super().__init__()
        self.cfg, self.remote = cfg, remote
        self.session: Session | None = None
        self.lock = threading.Lock()
        self.cursor = ""
        self.role = "?"
        self.history: list[str] = []
        self.hpos = 0

    def compose(self) -> ComposeResult:
        yield Static("connecting…", id="status")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                with Vertical(id="logbox"):
                    yield RichLog(id="log", max_lines=5000, wrap=True, markup=False)
                    yield Input(id="cmd", placeholder="mcai command: players list · say hi · ai pending · help")
            with Vertical(id="right"):
                with Vertical(id="playersbox"):
                    yield DataTable(id="players", cursor_type="row")
                with Vertical(id="aibox"):
                    yield DataTable(id="ai", cursor_type="row")
                with Vertical(id="autobox"):
                    yield DataTable(id="auto", cursor_type="row")
        yield Static(Text.from_markup("[b]F2[/] Command  [b]F4[/] Players  [b]F6[/] AI  [b]F7[/] Automations  "
                                      "[b]Enter[/] Actions  "
                                      "[b]F5[/] Refresh  [b]F1[/] Help  [b]^Q[/] Quit"), id="keys")

    def on_mount(self):
        name, _ = self.cfg.remote(self.remote)
        self.title = f"mcai · {name}"
        self.query_one("#logbox").border_title = "Server log + command output (F2)"
        self.query_one("#playersbox").border_title = "Players (F4)"
        self.query_one("#aibox").border_title = "AI requests waiting (F6)"
        self.query_one("#autobox").border_title = "Automations (F7)"
        p = self.query_one("#players", DataTable)
        for key, label, w in (("on", "", 1), ("name", "Player", 16), ("role", "Role", 9), ("seen", "Last seen", 16)):
            p.add_column(label, key=key, width=w)
        a = self.query_one("#ai", DataTable)
        for key, label, w in (("id", "Id", 7), ("player", "Player", 12), ("kind", "Kind", 5), ("status", "Status", 9),
                              ("title", "Title", 24)):
            a.add_column(label, key=key, width=w)
        au = self.query_one("#auto", DataTable)
        for key, label, w in (("id", "Automation", 18), ("state", "State", 9), ("last", "Last run", 10),
                              ("trigger", "Trigger", 22)):
            au.add_column(label, key=key, width=w)
        self.query_one("#cmd").focus()
        self.say(f"Connecting to {name}…", "bold #5fafff")
        self.poller()

    # ---------- talking to the server ----------
    def call(self, argv: list[str], input: str | None = None) -> dict:
        """One command over the RPC session (reconnects once if the session dropped). Thread-safe."""
        with self.lock:
            for attempt in (1, 2):
                if self.session is None:
                    self.session = Session(self.cfg, self.remote)
                r = self.session.call(argv, input) if input is not None else self.session.call(argv)
                if r.get("code") != "disconnected" or attempt == 2:
                    if r.get("code") == "disconnected":
                        self.session = None
                    return r
                self.session.close()
                self.session = None
        return {"ok": False, "code": "disconnected", "text": "not connected"}

    def say(self, text: str, style: str = "") -> None:
        self.query_one("#log", RichLog).write(Text(text, style=style))

    @work(thread=True, exclusive=True, group="poll")
    def poller(self) -> None:
        who = self.call(["whoami"])
        if not who.get("ok"):
            self.call_from_thread(self.say, who.get("text", "Could not connect."), "bold red")
            self.call_from_thread(self.set_status, Text("not connected: " + who.get("text", "")[:80], style="red"))
        else:
            self.role = who["data"]["role"]
            self.call_from_thread(self.say, f"Connected as {who['data']['account']} ({self.role}).", "bold #5fafff")
        next_poll = 0.0
        while True:
            if time.time() >= next_poll:
                next_poll = time.time() + POLL_SECONDS
                self.refresh_tables()
            logs = self.call(["logs", "-n", "200"] + (["--after", self.cursor] if self.cursor else []))
            if logs.get("ok"):
                self.cursor = logs["data"]["cursor"]
                for line in logs["data"]["lines"]:
                    self.call_from_thread(self.say, line, "dim")
            time.sleep(LOG_SECONDS)

    def refresh_tables(self) -> None:
        st = self.call(["status"])
        if st.get("ok"):
            s, ai = st["data"]["server"], st["data"]["ai"]
            text = Text()
            text.append("● ", style="green" if s["online"] else "red")
            text.append(f"{s.get('tps', '-')} TPS · heap {s.get('heap_used_mb', 0):.0f}/{s.get('heap_max_mb', 0):.0f} MB · "
                        f"{len(s.get('players', []))} online   " if s["online"] else "server offline   ")
            text.append(f"AI {'on' if ai['enabled'] else 'OFF'} · ${ai['spent_today_usd']:.2f}/${ai['budget_usd']:.2f}"
                        f" · worker {'ok' if ai['worker_online'] else 'offline'}   ")
            text.append(f"you: {self.role}", style="bold")
            self.call_from_thread(self.set_status, text)
        else:
            self.call_from_thread(self.set_status, Text("● " + st.get("text", "no connection")[:100], style="red"))
        pl = self.call(["players", "list"])
        if pl.get("ok"):
            self.call_from_thread(self.fill_players, pl["data"])
        ai = self.call(["ai", "pending"])
        if ai.get("ok"):
            self.call_from_thread(self.fill_ai, ai["data"])
        au = self.call(["automations", "list"])
        if au.get("ok"):
            self.call_from_thread(self.fill_auto, au["data"])

    def set_status(self, text: Text) -> None:
        self.query_one("#status", Static).update(text)

    def fill_players(self, rows: list[dict]) -> None:
        t = self.query_one("#players", DataTable)
        keep = t.cursor_row
        t.clear()
        for r in rows:
            t.add_row(Text("●", style="green") if r["online"] else Text("○", style="dim"), r["name"], r["role"],
                      "online" if r["online"] else r["last_seen"][:16].replace("T", " "), key=r["uuid"])
        if rows:
            t.move_cursor(row=min(keep, len(rows) - 1))

    def fill_ai(self, rows: list[dict]) -> None:
        t = self.query_one("#ai", DataTable)
        t.clear()
        for r in rows:
            t.add_row(r["id"], r["player_name"], r["kind"], r["status"], r.get("title") or "", key=r["id"])

    def fill_auto(self, rows: list[dict]) -> None:
        t = self.query_one("#auto", DataTable)
        keep = t.cursor_row
        t.clear()
        colors = {"enabled": "green", "suspended": "red", "proposed": "yellow", "pending_approval": "yellow"}
        for r in rows:
            t.add_row(r["id"], Text(r["state"].replace("pending_approval", "pending"), style=colors.get(r["state"], "dim")),
                      r.get("last_status") or "-", r["trigger"], key=r["id"])
        if rows:
            t.move_cursor(row=min(keep, len(rows) - 1))

    @work(thread=True)
    def run_command(self, argv: list[str], echo: bool = True, input: str | None = None) -> None:
        if echo:
            self.call_from_thread(self.say, "› mcai " + " ".join(shlex.quote(a) for a in argv), "bold")
        r = self.call(argv, input)
        self.call_from_thread(self.say, r.get("text", "") or ("Done." if r.get("ok") else "Failed."),
                              "" if r.get("ok") else "red")
        self.refresh_tables()

    # ---------- input ----------
    @on(Input.Submitted, "#cmd")
    def command(self, event):
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        if not self.history or self.history[-1] != line:
            self.history.append(line)
        self.hpos = len(self.history)
        try:
            argv = shlex.split(line.removeprefix("mcai "))
        except ValueError as e:
            self.say(f"Can't parse that: {e}", "red")
            return
        if argv[:2] == ["logs", "--follow"] or argv == ["rpc"]:
            self.say("The log already follows here.", "yellow")
            return
        try:
            argv, text = file_argument(argv)          # automations import FILE: the file is read here
        except SystemExit as e:
            self.say(str(e), "red")
            return
        self.run_command(argv, input=text)

    def on_key(self, event):
        cmd = self.query_one("#cmd", Input)
        if self.focused is cmd and event.key in ("up", "down") and self.history:
            self.hpos = max(0, min(len(self.history), self.hpos + (-1 if event.key == "up" else 1)))
            cmd.value = self.history[self.hpos] if self.hpos < len(self.history) else ""
            cmd.cursor_position = len(cmd.value)
            event.stop()

    @on(DataTable.RowSelected, "#players")
    def player_menu(self, event):
        name = str(self.query_one("#players", DataTable).get_row(event.row_key)[1])
        options = [("info", "Info and recent sessions"), ("role", "Change role…"), ("kick", "Kick…"),
                   ("unwhitelist", "Remove from whitelist")]

        def picked(choice):
            if choice == "info":
                self.run_command(["players", "info", name])
            elif choice == "role":
                self.push_screen(Choose(f"New role for {name}", [(r, r) for r in ROLES if r != "owner"]),
                                 lambda role: role and self.run_command(["players", "role", name, role]))
            elif choice == "kick":
                self.push_screen(Ask(f"Kick {name}: reason (optional)", "Kicked by an administrator"),
                                 lambda reason: self.run_command(["players", "kick", name] +
                                                                 (reason.split() if reason else [])))
            elif choice == "unwhitelist":
                self.push_screen(Confirm(f"Remove {name} from the whitelist?"),
                                 lambda yes: yes and self.run_command(["whitelist", "remove", name]))
        self.push_screen(Choose(name, options), picked)

    @on(DataTable.RowSelected, "#ai")
    def ai_menu(self, event):
        rid = str(event.row_key.value)
        status = str(self.query_one("#ai", DataTable).get_row(event.row_key)[3])
        options = [("show", "Show details")]
        if status == "proposed":
            options.append(("approve", "Approve (build it)"))
        if status in ("queued", "running", "proposed"):
            options.append(("cancel", "Cancel"))

        def picked(choice):
            if choice == "show":
                self.run_command(["ai", "show", rid])
            elif choice in ("approve", "cancel"):
                self.push_screen(Confirm(f"{choice.capitalize()} request {rid}?"),
                                 lambda yes: yes and self.run_command(["ai", choice, rid]))
        self.push_screen(Choose(f"AI request {rid} ({status})", options), picked)

    @on(DataTable.RowSelected, "#auto")
    def auto_menu(self, event):
        aid = str(event.row_key.value)
        state = str(self.query_one("#auto", DataTable).get_row(event.row_key)[1])
        options = [("show", "Show definition and status"), ("runs", "Run history")]
        if state in ("disabled", "suspended"):
            options.append(("enable", "Enable"))
        if state == "enabled":
            options.append(("disable", "Disable"))
        if state in ("enabled", "disabled"):
            options.append(("run", "Run now (test)"))
        if state in ("proposed", "pending"):
            options += [("approve", "Approve (enables it)"), ("reject", "Reject")]

        def picked(choice):
            if choice in ("show", "runs"):
                self.run_command(["automations", choice, aid])
            elif choice == "run":
                self.push_screen(Ask(f"Run {aid} now. Player (only if it acts on one):", ""),
                                 lambda who: who is not None and self.run_command(
                                     ["automations", "run", aid] + (["--player", who] if who else [])))
            elif choice:
                self.push_screen(Confirm(f"{choice.capitalize()} automation {aid}?"),
                                 lambda yes: yes and self.run_command(["automations", choice, aid]))
        self.push_screen(Choose(f"Automation {aid} ({state})", options), picked)

    def action_focus(self, wid: str) -> None:
        self.query_one(f"#{wid}").focus()

    def action_refresh(self) -> None:
        threading.Thread(target=self.refresh_tables, daemon=True).start()

    def action_help(self) -> None:
        self.run_command(["help"])

    def on_unmount(self) -> None:
        if self.session:
            self.session.close()


def run(cfg: Config, remote: str | None) -> int:
    cfg.remote(remote)                     # fail early with a helpful message if nothing is configured
    McaiTui(cfg, remote).run()
    return 0
