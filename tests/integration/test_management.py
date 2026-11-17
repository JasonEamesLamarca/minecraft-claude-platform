"""Management API through the real gateway HTTP server: SSH key -> account -> role -> command, identity
(players by UUID, owner bootstrap), role escalation attempts, audit chain, RCON-backed commands (fake RCON
server), link codes, heartbeats, runtime AI settings and undo-snapshot pruning."""
import json
import socket
import struct
import subprocess
import threading

import pytest

from mcai.rcon import Rcon
from test_build_flow import CTX, mk, proposed, wait_event
from test_gateway_flow import ALEX, MOD_TOKEN, STEVE, WORKER_TOKEN

MGMT_TOKEN = "g" * 40
OWNER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def keypair(tmp_path, name):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(tmp_path / name)], check=True)
    return (tmp_path / f"{name}.pub").read_text().strip()


class FakeRcon(threading.Thread):
    """Speaks just enough RCON: auth with the right password, echo commands back."""

    def __init__(self, password="rconpassword"):
        super().__init__(daemon=True)
        self.password, self.commands = password, []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.start()

    @staticmethod
    def packet(rid, kind, body):
        payload = struct.pack("<ii", rid, kind) + body.encode() + b"\0\0"
        return struct.pack("<i", len(payload)) + payload

    def run(self):
        while True:
            conn, _ = self.sock.accept()
            with conn:
                authed = False
                while True:
                    head = conn.recv(4)
                    if not head:
                        break
                    data = b""
                    while len(data) < struct.unpack("<i", head)[0]:
                        data += conn.recv(4096)
                    rid, kind = struct.unpack("<ii", data[:8])
                    body = data[8:-2].decode()
                    if kind == 3:
                        authed = body == self.password
                        conn.sendall(self.packet(rid if authed else -1, 2, ""))
                    elif authed:
                        self.commands.append(body)
                        reply = ("There are 2 whitelisted player(s): Steve, Alex" if body == "whitelist list"
                                 else f"ok: {body}")
                        conn.sendall(self.packet(rid, 0, reply))


@pytest.fixture
def env(tmp_path):
    rcon = FakeRcon()
    logs = tmp_path / "mc-logs"
    logs.mkdir()
    (logs / "latest.log").write_text("[12:00:00] [Server thread/INFO]: Steve[/203.0.113.7:5555] logged in\n"
                                     "[12:00:01] [Server thread/INFO]: Alex joined the game\n")
    (tmp_path / "gw" / "bootstrap").mkdir(parents=True)
    owner_key = keypair(tmp_path, "owner")
    (tmp_path / "gw" / "bootstrap" / "owner.pub").write_text(owner_key + "\n")
    s = mk(tmp_path / "gw", management_token=MGMT_TOKEN, owner_name="Boss", rcon_host="127.0.0.1",
           rcon_port=rcon.port, rcon_password=rcon.password, mc_logs_dir=logs)
    s.gw.rcon = Rcon("127.0.0.1", rcon.port, rcon.password)
    s.keys = {"owner": owner_key}
    s.rcon = rcon
    s.tmp = tmp_path

    def run(who, *argv, ip="192.0.2.10"):
        st, r = s.call("POST", "/v1/mgmt/run", {"key": s.keys[who], "source_ip": ip, "session": "t", "argv": list(argv)},
                       token=MGMT_TOKEN)
        assert st == 200
        return r
    s.run = run

    def account(name, role):
        s.keys[name] = keypair(tmp_path, name)
        r = run("owner", "accounts", "add", name, role, *s.keys[name].split())
        assert r["ok"], r
    s.account = account
    yield s
    s.close()


def events(s, prefix):
    return [a for a in s.audit() if a["event"].startswith(prefix)]


# ------------------------------------------------------------------ authentication
def test_owner_key_bootstrapped_once(env):
    r = env.run("owner", "whoami")
    assert r["ok"] and r["data"]["account"] == "owner" and r["data"]["role"] == "owner"
    assert r["data"]["source_ip"] == "192.0.2.10"
    assert not (env.tmp / "gw" / "bootstrap" / "owner.pub").exists(), "bootstrap file is consumed"
    assert events(env, "account.key_added")[0]["by"] == "host:bootstrap"


def test_unknown_key_denied_and_audited(env, tmp_path):
    env.keys["stranger"] = keypair(tmp_path, "stranger")
    r = env.run("stranger", "status")
    assert not r["ok"] and r["code"] == "unknown_key"
    assert events(env, "mgmt.denied")[-1]["reason"] == "unknown or revoked key"


def test_tokens_are_separate(env):
    body = {"key": env.keys["owner"], "argv": ["whoami"]}
    assert env.call("POST", "/v1/mgmt/run", body, token=MOD_TOKEN)[0] == 401
    assert env.call("POST", "/v1/mgmt/run", body, token=WORKER_TOKEN)[0] == 401
    assert env.call("GET", "/v1/mgmt/authorized-keys", token=MOD_TOKEN)[0] == 401
    assert env.call("POST", "/v1/mod/requests", {}, token=MGMT_TOKEN)[0] == 401


def test_management_disabled_without_token(tmp_path):
    s = mk(tmp_path)
    try:
        assert s.call("POST", "/v1/mgmt/run", {"argv": ["whoami"]}, token="")[0] == 401
    finally:
        s.close()


def test_authorized_keys_restricted_and_follow_revocation(env):
    env.account("ann", "auditor")
    keys = env.call("GET", "/v1/mgmt/authorized-keys", token=MGMT_TOKEN)[1]["keys"]
    assert len(keys) == 2 and all(k.startswith("restrict ssh-ed25519 ") for k in keys)
    fp = env.run("ann", "whoami")["data"]["key"]
    assert env.run("owner", "accounts", "key-revoke", fp)["ok"]
    assert env.run("ann", "whoami")["code"] == "unknown_key"           # immediately, before the file sync
    assert len(env.call("GET", "/v1/mgmt/authorized-keys", token=MGMT_TOKEN)[1]["keys"]) == 1


def test_disabled_account_cannot_log_in(env):
    env.account("ops", "operator")
    assert env.run("owner", "accounts", "disable", "ops")["ok"]
    assert env.run("ops", "status")["code"] == "unknown_key"
    assert env.run("owner", "accounts", "enable", "ops")["ok"]
    assert env.run("ops", "status")["ok"]


def test_revoked_key_cannot_be_re_added(env):
    env.account("bob", "player")
    fp = env.run("bob", "whoami")["data"]["key"]
    env.run("owner", "accounts", "key-revoke", fp)
    r = env.run("owner", "accounts", "key-add", "bob", *env.keys["bob"].split())
    assert not r["ok"] and r["code"] == "exists"


def test_cannot_revoke_own_last_key(env):
    fp = env.run("owner", "whoami")["data"]["key"]
    r = env.run("owner", "accounts", "key-revoke", fp)
    assert r["code"] == "forbidden" and "lock yourself out" in r["text"]


# ------------------------------------------------------------------ authorization
def test_auditor_is_read_only(env):
    env.account("ann", "auditor")
    for argv in (["status"], ["players", "list"], ["audit", "show"], ["ai", "history"], ["logs", "-n", "5"],
                 ["accounts", "list"], ["audit", "verify"]):
        assert env.run("ann", *argv)["ok"], argv
    for argv in (["players", "role", "Steve", "builder"], ["whitelist", "add", "Eve"], ["say", "hi"],
                 ["server", "restart", "--yes"], ["ai", "disable"], ["ai", "approve", "abcdef"],
                 ["accounts", "add", "x1", "player", *env.keys["owner"].split()], ["server", "command", "op", "Eve"]):
        r = env.run("ann", *argv)
        assert not r["ok"] and r["code"] == "forbidden", argv
    assert env.rcon.commands == [], "nothing reached the server console"
    assert {e["command"] for e in events(env, "mgmt.denied")} >= {"players role", "server restart", "ai disable"}


def test_operator_moderates_but_cannot_assign_roles(env):
    env.account("op", "operator")
    assert env.run("op", "whitelist", "add", "Eve")["ok"]
    assert env.rcon.commands[-1] == "whitelist add Eve"
    assert env.run("op", "players", "role", "Alex", "builder")["code"] == "forbidden"
    assert env.run("op", "server", "command", "op", "Alex")["code"] == "forbidden"


@pytest.mark.parametrize("argv", [
    ["players", "role", "Boss", "player"],          # demote the owner
    ["accounts", "role", "admin2", "owner"],        # promote a peer to owner
    ["accounts", "role", "admin", "player"],        # change own role
    ["accounts", "add", "evil", "admin"],           # create an equal
    ["accounts", "disable", "admin2"],              # disable a peer
])
def test_admin_escalation_attempts_fail(env, argv):
    env.account("admin", "admin")
    env.account("admin2", "admin")
    env.gw.identify(OWNER_UUID, "Boss")
    if argv[:2] == ["accounts", "add"]:
        argv = argv + env.keys["owner"].split()
    r = env.run("admin", *argv)
    assert not r["ok"] and r["code"] == "forbidden", r
    assert env.gw.store.account("admin2")["role"] == "admin" and env.gw.store.player(OWNER_UUID)["role"] == "owner"


def test_admin_assigns_roles_below_and_player_is_told(env):
    env.account("admin", "admin")
    r = env.run("admin", "players", "role", "Alex", "builder")
    assert r["ok"] and env.gw.store.player(ALEX)["role"] == "builder"
    assert "now builder" in wait_event(env, "message")["text"]
    ev = events(env, "role.changed")[-1]
    assert ev["old_role"] == "player" and ev["new_role"] == "builder" and ev["by"] == "account:admin"


def test_roles_by_uuid_not_name(env):
    r = env.run("owner", "players", "role", ALEX, "builder")
    assert r["ok"]
    env.call("POST", "/v1/mod/players", {"event": "join", "player_uuid": ALEX, "player_name": "AlexRenamed"})
    assert env.gw.store.player(ALEX)["role"] == "builder"          # a name change keeps the role
    assert env.run("owner", "players", "role", "Nobody", "builder")["code"] == "not_found"


def test_operator_cannot_kick_higher_role(env):
    env.account("op", "operator")
    env.gw.identify(OWNER_UUID, "Boss")
    assert env.run("op", "players", "kick", "Boss")["code"] == "forbidden"
    assert env.run("op", "players", "kick", "Alex", "too", "loud")["ok"]
    assert env.rcon.commands[-1] == "kick Alex too loud"


def test_console_command_is_owner_only_and_single_line(env):
    r = env.run("owner", "server", "command", "time set day\nop Eve")
    assert r["ok"] and env.rcon.commands[-1] == "time set dayop Eve"      # newline removed: one command only
    assert events(env, "mgmt.command")[-1]["params"]["command"].startswith("time set day")


def test_restart_needs_confirmation(env):
    assert env.run("owner", "server", "restart")["code"] == "confirm"
    assert env.run("owner", "server", "restart", "--yes", "--delay", "0")["ok"]
    import time
    time.sleep(0.5)
    assert env.rcon.commands[-1] == "stop"


# ------------------------------------------------------------------ players, owner bootstrap, link
def test_owner_bootstrap_by_name_once(env):
    env.call("POST", "/v1/mod/players", {"event": "join", "player_uuid": OWNER_UUID, "player_name": "Boss"})
    assert env.gw.store.player(OWNER_UUID)["role"] == "owner"
    impostor = "12345678-1234-1234-1234-123456789abc"
    env.call("POST", "/v1/mod/players", {"event": "join", "player_uuid": impostor, "player_name": "boss"})
    assert env.gw.store.player(impostor)["role"] == "player", "only the first UUID with that name becomes owner"
    assert len(events(env, "role.bootstrap")) == 1


def test_no_owner_bootstrap_in_offline_mode(tmp_path):
    s = mk(tmp_path, owner_name="Boss", online_mode=False)
    try:
        s.call("POST", "/v1/mod/players", {"event": "join", "player_uuid": OWNER_UUID, "player_name": "Boss"})
        assert s.gw.store.player(OWNER_UUID)["role"] == "player"
    finally:
        s.close()


def test_sessions_and_heartbeat(env):
    env.call("POST", "/v1/mod/players", {"event": "join", "player_uuid": ALEX, "player_name": "Alex"})
    env.call("POST", "/v1/mod/heartbeat", {"players": [{"uuid": STEVE, "name": "Steve"}], "mspt": 12.5,
                                           "heap_used_mb": 900, "heap_max_mb": 2048, "uptime_s": 600, "version": "1.21.1"})
    assert env.gw.store.online_uuids() == {STEVE}                  # Alex's missed leave repaired, Steve's join added
    st = env.run("owner", "status")
    assert st["data"]["server"]["online"] and st["data"]["server"]["tps"] == 20.0
    assert st["data"]["server"]["players"] == ["Steve"]
    sess = env.run("owner", "players", "sessions", "Alex")
    assert sess["ok"] and sess["data"][0]["left_at"]
    assert {e["event"] for e in env.audit()} >= {"player.join", "player.leave"}


def test_link_code(env):
    code = env.run("owner", "link")["data"]["code"]
    st, r = env.call("POST", "/v1/mod/link", {"player_uuid": STEVE, "player_name": "Steve", "code": code})
    assert st == 200 and r["ok"]
    assert env.run("owner", "whoami")["data"]["linked_player"]["uuid"] == STEVE
    assert env.call("POST", "/v1/mod/link", {"player_uuid": ALEX, "player_name": "Alex", "code": code})[0] == 404
    assert env.gw.store.player(STEVE)["role"] == "builder", "linking grants nothing"


# ------------------------------------------------------------------ logs, audit
def test_logs_mask_ips_except_for_admins(env):
    env.account("ann", "auditor")
    assert "203.0.113.7" not in env.run("ann", "logs")["text"] and "<ip>" in env.run("ann", "logs")["text"]
    assert "203.0.113.7" in env.run("owner", "logs")["text"]
    r = env.run("owner", "logs", "--grep", "joined")
    assert r["data"]["lines"] == ["[12:00:01] [Server thread/INFO]: Alex joined the game"]


def test_logs_follow_cursor(env):
    cur = env.run("owner", "logs")["data"]["cursor"]
    with open(env.tmp / "mc-logs" / "latest.log", "a") as f:
        f.write("new line\n")
    r = env.run("owner", "logs", "--after", cur)
    assert r["data"]["lines"] == ["new line"]


def test_audit_chain_detects_tampering(env):
    env.run("owner", "say", "hello")
    assert env.run("owner", "audit", "verify")["ok"]
    f = env.gw.store.audit_files()[-1]
    lines = f.read_text().splitlines()
    lines[1] = lines[1].replace('"event": "', '"event": "x')
    f.write_text("\n".join(lines) + "\n")
    r = env.run("owner", "audit", "verify")
    assert not r["ok"] and r["code"] == "tampered"


def test_audit_export_formats(env):
    env.run("owner", "say", "hello")
    jl = env.run("owner", "audit", "show", "--format", "jsonl", "--event", "mgmt.")["text"].splitlines()
    assert jl and all(json.loads(x)["event"].startswith("mgmt.") for x in jl)
    csv_text = env.run("owner", "audit", "show", "--format", "csv")["text"]
    assert csv_text.startswith("ts,event,details")
    assert env.run("owner", "audit", "show", "--since", "yesterday")["code"] == "usage"


def test_audit_never_contains_keys_or_tokens(env):
    env.account("ann", "auditor")
    raw = "".join(p.read_text() for p in env.gw.store.audit_files())
    assert env.keys["ann"].split()[1] not in raw and MGMT_TOKEN not in raw and "rconpassword" not in raw


# ------------------------------------------------------------------ AI from the console
def test_ai_disable_and_budget(env):
    assert env.run("owner", "ai", "disable")["ok"]
    st, r = env.call("POST", "/v1/mod/requests", {"player_uuid": STEVE, "player_name": "Steve", "kind": "ask",
                                                  "text": "hi", "context": CTX})
    assert st == 503 and r["code"] == "disabled"
    env.run("owner", "ai", "enable")
    assert env.run("owner", "ai", "budget", "0")["ok"]
    st, r = env.call("POST", "/v1/mod/requests", {"player_uuid": STEVE, "player_name": "Steve", "kind": "ask",
                                                  "text": "hi", "context": CTX})
    assert r["code"] == "budget_exhausted"
    assert env.run("owner", "ai", "budget", "5000")["code"] == "usage"
    env.run("owner", "ai", "budget", "default")
    assert env.gw.daily_budget() == env.settings.daily_budget_usd


def test_account_approves_and_undoes_a_build(env):
    rid, _ = proposed(env)
    env.account("op", "operator")
    pend = env.run("op", "ai", "pending")
    assert pend["data"][0]["id"] == rid
    assert env.run("op", "ai", "approve", rid)["ok"]
    assert wait_event(env, "execute")["request_id"] == rid
    assert env.gw.store.get(rid)["decided_by"] == "account:op"
    env.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "built", "placed": 37})
    assert env.run("op", "ai", "undo", rid)["ok"]
    assert wait_event(env, "rollback")["request_id"] == rid
    assert "37 blocks" in env.run("op", "ai", "show", rid)["text"]


def test_builder_account_cannot_approve_from_console(env):
    rid, _ = proposed(env)
    env.account("bld", "builder")
    assert env.run("bld", "ai", "approve", rid)["code"] == "forbidden"


# ------------------------------------------------------------------ snapshot pruning
def test_snapshot_pruned_after_undo_and_undo_window(env):
    rid, _ = proposed(env)
    env.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "request_id": rid})
    env.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "built", "placed": 37})
    env.gw.prune_snapshots()
    assert env.gw.store.get(rid)["snapshot"] == "present"                     # within the window
    env.gw.store.db.execute("UPDATE requests SET built_at='2000-01-01T00:00:00.000Z' WHERE id=?", (rid,))
    env.gw.prune_snapshots()
    ev = wait_event(env, "prune")
    assert ev["request_ids"] == [rid]
    env.gw.prune_snapshots()                                                   # not re-sent while waiting
    assert env.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "pruned"})[1]["recorded"]
    st, r = env.call("POST", "/v1/mod/undo", {"player_uuid": STEVE, "request_id": rid})
    assert st == 410 and "undo window" in r["message"]


def test_snapshot_pruned_right_after_rollback(env):
    rid, _ = proposed(env)
    env.call("POST", "/v1/mod/approve", {"player_uuid": STEVE, "request_id": rid})
    env.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "built", "placed": 37})
    env.call("POST", "/v1/mod/undo", {"player_uuid": STEVE, "request_id": rid})
    env.call("POST", f"/v1/mod/builds/{rid}/report", {"status": "rolled_back"})
    env.gw.prune_snapshots()
    assert wait_event(env, "prune")["request_ids"] == [rid]


def test_host_admin_sets_owner(env, monkeypatch, capsys):
    from mcai import hostadmin
    from mcai.config import GatewaySettings
    env.gw.identify(ALEX, "Alex")
    monkeypatch.setattr(GatewaySettings, "from_env", classmethod(lambda cls: env.settings))
    assert hostadmin.main(["set-role", "player", "Alex", "owner"]) == 0
    assert env.gw.store.player(ALEX)["role"] == "owner"
    assert hostadmin.main(["set-role", "player", "Alex", "god"]) == 2
    assert [e for e in env.audit() if e["event"] == "role.changed"][-1]["by"] == "host"


def test_audit_chain_survives_a_second_writer(env):
    from mcai.store import Store
    other = Store(env.settings.data_dir)            # e.g. scripts/set-role.sh running next to the gateway
    for i in range(5):
        env.gw.store.audit("test.gateway", i=i)
        other.audit("test.host", i=i)
    assert env.run("owner", "audit", "verify")["ok"]
