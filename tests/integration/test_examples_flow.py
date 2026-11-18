"""Example packs through the real gateway: upload (official / unofficial), validation, install preview and site
checks, the mod's file download and reports, repair, rollback, cancel, forget, interruption, roles, and the
automation hooks (example areas, example_tour, example_restore)."""
import base64
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mcai.gateway import Gateway
from mcai.store import Store
from test_build_flow import mk
from test_gateway_flow import MOD_TOKEN, STEVE
from test_management import MGMT_TOKEN, FakeRcon, keypair  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples" / "atlantis" / "generator"))
from atlantis import schem  # noqa: E402

PID, VER = "tiny-town", "1.0.0"


def make_pack(blocks=("minecraft:stone", "minecraft:glass")):
    files, secs = {}, []
    for i in range(2):
        data = schem.encode(4, 3, 5, list(blocks), [(k + i) % len(blocks) for k in range(60)])
        name = f"tiny-town-s{i:03d}.schem"
        files[name] = data
        secs.append({"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                     "offset": [-4 + 4 * i, -1, -2], "size": [4, 3, 5], "blocks": 60})
    m = {"format": 1, "id": PID, "version": VER, "title": "Tiny town", "description": "Two little sections.",
         "license": "Apache-2.0", "minecraft": "1.21.1", "data_version": 3955, "anchor": "centre, floor level",
         "origin": [-4, -1, -2], "size": [8, 3, 5], "sections": secs, "total_blocks": 120,
         "total_bytes": sum(s["bytes"] for s in secs),
         "points_of_interest": [{"name": "Square", "at": [0, 1, 0]}, {"name": "Tower", "at": [2, 1, 1]}]}
    return json.dumps(m).encode(), files


@pytest.fixture
def ex(tmp_path):
    manifest, files = make_pack()
    cat = tmp_path / "catalog.json"
    cat.write_text(json.dumps({"format": 1, "version": VER, "packs": [{
        "id": PID, "version": VER, "title": "Tiny town", "description": "test",
        "manifest_url": "https://github.com/o/r/releases/download/examples-1.0.0/tiny-town-manifest.json",
        "manifest_sha256": hashlib.sha256(manifest).hexdigest()}]}))
    (tmp_path / "gw" / "bootstrap").mkdir(parents=True)
    owner_key = keypair(tmp_path, "owner")
    (tmp_path / "gw" / "bootstrap" / "owner.pub").write_text(owner_key + "\n")
    s = mk(tmp_path / "gw", management_token=MGMT_TOKEN, owner_name="Boss", examples_catalog=cat)
    s.keys, s.tmp, s.manifest, s.files = {"owner": owner_key}, tmp_path, manifest, files

    def run(who, *argv):
        st, r = s.call("POST", "/v1/mgmt/run", {"key": s.keys[who], "session": "t", "argv": list(argv)},
                       token=MGMT_TOKEN)
        assert st == 200
        return r

    def upload(who, name, data, pid=PID, ver=VER):
        st, r = s.call("POST", "/v1/mgmt/upload", {"key": s.keys[who], "session": "t",
                                                    "argv": ["examples", "upload", pid, ver, name, "--stdin"],
                                                    "input": base64.b64encode(data).decode()}, token=MGMT_TOKEN)
        assert st == 200
        return r

    def account(name, role):
        s.keys[name] = keypair(tmp_path, name)
        assert run("owner", "accounts", "add", name, role, *s.keys[name].split())["ok"]

    def heartbeat(job=None):
        st, r = s.call("POST", "/v1/mod/heartbeat", {"players": [], "mspt": 10, "example_job": job})
        assert st == 200
        return r

    def report(iid, **body):
        st, r = s.call("POST", f"/v1/mod/examples/{iid}/report", body)
        assert st == 200
        return r

    s.run, s.upload, s.account, s.heartbeat, s.report = run, upload, account, heartbeat, report
    heartbeat()
    yield s
    s.close()


def events_of(s, typ):
    return [e for e in s.events_until(1, timeout=2) if e.get("type") == typ]


def ready(s):
    assert s.upload("owner", "manifest.json", s.manifest)["ok"]
    for name, data in s.files.items():
        r = s.upload("owner", name, data)
        assert r["ok"], r
    return r


def get_file(s, iid, name, token=MOD_TOKEN):
    req = urllib.request.Request(f"{s.url}/v1/mod/examples/{iid}/{name}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def placed(s, at=(100, 64, 200)):
    r = s.run("owner", "examples", "install", PID, "--at", *map(str, at), "--yes")
    assert r["ok"], r
    iid = r["data"]["install"]
    assert s.report(iid, status="placed", done=120, total=120, changed=90)["ok"]
    return iid


# ------------------------------------------------------------------ upload and validation
def test_official_upload_then_install(ex):
    r = ex.upload("owner", "manifest.json", ex.manifest)
    assert r["ok"] and "official" in r["text"] and "unofficial" not in r["text"]
    names = list(ex.files)
    assert "1/2" in ex.upload("owner", names[0], ex.files[names[0]])["text"]
    assert "ready to install" in ex.upload("owner", names[1], ex.files[names[1]])["text"]
    lst = ex.run("owner", "examples", "list")
    assert lst["ok"] and "ready" in lst["text"] and lst["data"]["packs"][0]["official"]
    info = ex.run("owner", "examples", "info", PID)
    assert "Square: [0, 1, 0]" in info["text"] and "120 blocks" in info["text"]

    pre = ex.run("owner", "examples", "install", PID, "--at", "100", "64", "200")
    assert pre["ok"] and pre["code"] == "confirm" and "--yes" in pre["text"]
    assert pre["data"]["bbox"] == {"from": [96, 63, 198], "to": [103, 65, 202]}
    assert not ex.gw.store.example_installs(), "a preview changes nothing"

    r = ex.run("owner", "examples", "install", PID, "--at", "100", "64", "200", "--yes")
    assert r["ok"], r
    iid = r["data"]["install"]
    ev = events_of(ex, "example_place")[0]
    assert ev["install_id"] == iid and ev["anchor"] == [100, 64, 200] and len(ev["sections"]) == 2
    assert ev["blocks_per_tick"] == 4000 and set(ev["sections"][0]) == {"file", "sha256", "bytes", "offset", "size"}

    st, data = get_file(ex, iid, names[0])
    assert st == 200 and data == ex.files[names[0]]
    assert get_file(ex, iid, "other.schem")[0] == 404
    assert get_file(ex, "deadbeef", names[0])[0] == 404
    assert get_file(ex, iid, names[0], token=MGMT_TOKEN)[0] == 401

    assert ex.report(iid, status="progress", done=60, total=120, changed=40)["ok"]
    assert "50%" in ex.run("owner", "examples", "status", iid)["text"]
    assert ex.report(iid, status="placed", done=120, total=120, changed=90)["ok"]
    st = ex.run("owner", "examples", "status", iid)
    assert "placed" in st["text"] and "Square: 100 65 200" in st["text"] and "Tower: 102 65 201" in st["text"]
    assert get_file(ex, iid, names[0])[0] == 404, "files are served only while placing"
    kinds = [a["event"] for a in ex.audit() if a["event"].startswith("examples.")]
    assert kinds[:4] == ["examples.manifest", "examples.ready", "examples.install_started", "examples.placed"]


def test_unofficial_and_rejected_uploads(ex):
    manifest, files = make_pack(blocks=("minecraft:stone", "minecraft:oak_planks"))
    m = json.loads(manifest)
    m["version"] = "2.0.0"
    manifest = json.dumps(m).encode()
    r = ex.upload("owner", "manifest.json", manifest, ver="2.0.0")
    assert r["ok"] and "unofficial" in r["text"]
    assert ex.upload("owner", "evil.schem", b"x", ver="2.0.0")["code"] == "invalid"
    name = list(files)[0]
    bad = ex.upload("owner", name, files[name] + b"\x00", ver="2.0.0")
    assert bad["code"] == "invalid" and "SHA-256" in bad["text"]
    assert ex.gw.store.example_pack(PID, "2.0.0")["status"] == "invalid"
    assert any(a["event"] == "examples.rejected" for a in ex.audit())
    # a section with a command block, listed honestly in its manifest, is still refused
    m2, f2 = make_pack(blocks=("minecraft:stone", "minecraft:command_block"))
    j = json.loads(m2)
    j["version"] = "3.0.0"
    assert ex.upload("owner", "manifest.json", json.dumps(j).encode(), ver="3.0.0")["ok"]
    r = ex.upload("owner", list(f2)[0], f2[list(f2)[0]], ver="3.0.0")
    assert r["code"] == "invalid" and "command_block" in r["text"]
    # manifest for another pack, garbage base64, other commands on the upload channel
    assert ex.upload("owner", "manifest.json", ex.manifest, ver="9.9.9")["code"] == "invalid"
    st, r = ex.call("POST", "/v1/mgmt/upload", {"key": ex.keys["owner"], "argv": ["examples", "upload", PID, VER,
                                                                                    "manifest.json", "--stdin"],
                                                 "input": "not base64!"}, token=MGMT_TOKEN)
    assert r["code"] == "usage"
    st, r = ex.call("POST", "/v1/mgmt/upload", {"key": ex.keys["owner"], "argv": ["whoami"]}, token=MGMT_TOKEN)
    assert r["code"] == "usage"
    assert ex.run("owner", "examples", "download", PID)["code"] == "usage"


def test_validate_and_delete(ex):
    ready(ex)
    assert ex.run("owner", "examples", "validate", PID)["ok"]
    path = ex.tmp / "gw" / "examples" / PID / VER / list(ex.files)[0]
    path.write_bytes(path.read_bytes()[:-1] + b"\x01")              # damaged on disk
    r = ex.run("owner", "examples", "validate", PID)
    assert r["code"] == "invalid" and ex.gw.store.example_pack(PID, VER)["status"] == "invalid"
    assert ex.run("owner", "examples", "delete", PID, VER)["code"] == "confirm"
    assert ex.run("owner", "examples", "delete", PID, VER, "--yes")["ok"]
    assert not (ex.tmp / "gw" / "examples" / PID / VER).exists() and not ex.gw.store.example_packs()


# ------------------------------------------------------------------ site checks
def test_site_checks(ex):
    ready(ex)
    r = ex.run("owner", "examples", "install", PID, "--at", "0", "-64", "0", "--yes")
    assert r["code"] == "invalid" and "height" in r["text"]
    (ex.tmp / "gw" / "protected-areas.json").write_text(json.dumps([{"name": "spawn", "from": [-10, 0, -10],
                                                                     "to": [10, 100, 10]}]))
    r = ex.run("owner", "examples", "install", PID, "--at", "5", "64", "5", "--yes")
    assert r["code"] == "protected" and "spawn" in r["text"]
    iid = placed(ex)
    r = ex.run("owner", "examples", "install", PID, "--at", "103", "64", "200", "--yes")
    assert r["code"] == "overlap" and iid in r["text"]
    assert ex.run("owner", "examples", "install", PID, "--at", "103", "64", "200", "--dimension",
                  "minecraft:the_nether", "--yes")["ok"], "other dimensions don't overlap"


def test_needs_ready_pack_and_mod(ex):
    assert ex.run("owner", "examples", "install", PID, "--at", "0", "64", "0", "--yes")["code"] == "not_found"
    ready(ex)
    ex.gw.heartbeat_at = -1e9
    assert ex.run("owner", "examples", "install", PID, "--at", "0", "64", "0", "--yes")["code"] == "unavailable"


# ------------------------------------------------------------------ jobs
def test_repair_rollback_and_busy(ex):
    ready(ex)
    iid = placed(ex)
    events_of(ex, "example_place")
    assert "Repairing" in ex.run("owner", "examples", "repair", iid)["text"]
    assert events_of(ex, "example_place")[0]["install_id"] == iid
    r = ex.run("owner", "examples", "install", PID, "--at", "500", "64", "500", "--yes")
    assert r["code"] == "busy", "one job at a time"
    assert ex.run("owner", "examples", "rollback", iid, "--yes")["code"] == "busy"
    assert ex.report(iid, status="placed", done=120, total=120, changed=3)["ok"]
    r = ex.run("owner", "examples", "rollback", iid)
    assert r["code"] == "confirm" and "--yes" in r["text"]
    assert ex.run("owner", "examples", "rollback", iid, "--yes")["ok"]
    assert events_of(ex, "example_rollback")[0]["install_id"] == iid
    assert ex.report(iid, status="rolled_back", pieces=4)["ok"]
    row = ex.gw.store.example_install(iid)
    assert row["status"] == "rolled_back" and row["snapshots"] == "deleted"
    assert ex.run("owner", "examples", "rollback", iid, "--yes")["code"] == "state"
    assert ex.run("owner", "examples", "repair", iid)["code"] == "state"
    assert not ex.report(iid, status="placed")["ok"], "late reports don't change a finished install"
    assert ex.run("owner", "examples", "install", PID, "--at", "100", "64", "200", "--yes")["ok"], \
        "a rolled-back install no longer occupies its area"


def test_cancel_and_failure(ex):
    ready(ex)
    r = ex.run("owner", "examples", "install", PID, "--at", "0", "70", "0", "--yes")
    iid = r["data"]["install"]
    assert ex.run("owner", "examples", "cancel", iid)["ok"]
    assert events_of(ex, "example_cancel")[0]["install_id"] == iid
    assert ex.report(iid, status="cancelled", done=30, total=120)["ok"]
    assert ex.gw.store.example_install(iid)["status"] == "interrupted"
    assert ex.run("owner", "examples", "repair", iid)["ok"]
    assert ex.report(iid, status="failed", error="chunk could not be loaded")["ok"]
    row = ex.gw.store.example_install(iid)
    assert row["status"] == "failed" and "chunk" in row["error"]


def test_forget_snapshots(ex):
    ready(ex)
    iid = placed(ex)
    assert ex.run("owner", "examples", "forget", iid)["code"] == "confirm"
    assert ex.run("owner", "examples", "forget", iid, "--yes")["ok"]
    assert events_of(ex, "example_forget")[0]["install_id"] == iid
    assert ex.report(iid, status="forgotten")["ok"]
    assert ex.gw.store.example_install(iid)["snapshots"] == "deleted"
    r = ex.run("owner", "examples", "rollback", iid, "--yes")
    assert r["code"] == "state" and "backup" in r["text"]


def test_interrupted_when_the_server_forgets_the_job(ex):
    ready(ex)
    iid = ex.run("owner", "examples", "install", PID, "--at", "0", "70", "0", "--yes")["data"]["install"]
    ex.heartbeat(job=iid)
    ex.heartbeat(job=None)
    assert ex.gw.store.example_install(iid)["status"] == "placing", "too soon after sending"
    ex.gw.examples.sent_at[iid] = -1e9
    ex.heartbeat(job=iid)
    assert ex.gw.store.example_install(iid)["status"] == "placing"
    ex.heartbeat(job=None)
    ex.heartbeat(job=None)
    row = ex.gw.store.example_install(iid)
    assert row["status"] == "interrupted" and "restart" in row["error"]


def test_gateway_restart_interrupts_running_jobs(ex):
    ready(ex)
    iid = ex.run("owner", "examples", "install", PID, "--at", "0", "70", "0", "--yes")["data"]["install"]
    gw2 = Gateway(ex.settings, Store(ex.settings.data_dir, False))
    try:
        assert gw2.store.example_install(iid)["status"] == "interrupted"
    finally:
        gw2.stop()


def test_concurrent_mod_calls(ex):
    """The mod downloads, reports and sends heartbeats at the same time; the shared database must cope."""
    import concurrent.futures as cf
    ready(ex)
    r = ex.run("owner", "examples", "install", PID, "--at", "0", "70", "0", "--yes")
    iid, name = r["data"]["install"], list(ex.files)[0]

    def one(k):
        if k % 3 == 0:
            return get_file(ex, iid, name)[0]
        if k % 3 == 1:
            return ex.call("POST", f"/v1/mod/examples/{iid}/report", {"status": "progress", "done": k})[0]
        return ex.call("POST", "/v1/mod/heartbeat", {"players": [], "example_job": iid})[0]
    with cf.ThreadPoolExecutor(12) as pool:
        codes = list(pool.map(one, range(300)))
    assert set(codes) == {200}, sorted(set(codes))


# ------------------------------------------------------------------ roles
def test_roles(ex):
    ready(ex)
    iid = placed(ex)
    ex.account("ann", "auditor")
    ex.account("op", "operator")
    for argv in (["examples", "list"], ["examples", "info", PID], ["examples", "installs"],
                 ["examples", "status", iid], ["examples", "catalog"]):
        assert ex.run("ann", *argv)["ok"], argv
    for who in ("ann", "op"):
        for argv in (["examples", "install", PID, "--at", "0", "70", "0", "--yes"], ["examples", "rollback", iid, "--yes"],
                     ["examples", "repair", iid], ["examples", "delete", PID, VER, "--yes"],
                     ["examples", "validate", PID]):
            assert ex.run(who, *argv)["code"] == "forbidden", (who, argv)
        assert ex.upload(who, "manifest.json", ex.manifest)["code"] == "forbidden"


# ------------------------------------------------------------------ automations
AREA_DEF = """
id = "town-welcome"
name = "Town welcome"
[trigger]
event = "player.enter_area"
area = { name = "town", example = "tiny-town" }
[[actions]]
type = "message"
text = "Welcome to the {area}, {player}."
"""
TOUR_DEF = """
id = "town-tour"
name = "Town tour"
[trigger]
event = "player.trigger"
name = "town"
[[actions]]
type = "example_tour"
example = "tiny-town"
"""
RESTORE_DEF = """
id = "town-restore"
name = "Town restore"
[trigger]
every = "1d"
[[actions]]
type = "example_restore"
example = "tiny-town"
"""


def imp(ex, who, text):
    st, r = ex.call("POST", "/v1/mgmt/run", {"key": ex.keys[who], "argv": ["automations", "import", "--stdin"],
                                             "input": text}, token=MGMT_TOKEN)
    return r


def test_example_area_follows_the_install(ex):
    ready(ex)
    assert imp(ex, "owner", AREA_DEF)["ok"]
    assert ex.run("owner", "automations", "enable", "town-welcome")["ok"]
    assert ex.heartbeat()["areas"] == [], "not installed yet"
    placed(ex)
    a = ex.heartbeat()["areas"]
    assert a == [{"id": "town-welcome", "on": "enter", "dimension": "minecraft:overworld", "from": [96, 63, 198],
                  "to": [103, 65, 202]}]


def test_example_tour_and_restore(ex):
    ready(ex)
    iid = placed(ex)
    ex.gw.store.player_seen(STEVE, "Steve", online=True)
    ex.heartbeat()
    assert imp(ex, "owner", TOUR_DEF)["ok"] and ex.run("owner", "automations", "enable", "town-tour")["ok"]
    rid = ex.run("owner", "automations", "run", "town-tour", "--player", "Steve")["data"]["run"]
    ex.gw.automation.execute(rid)
    msgs = []
    for _ in range(20):
        msgs += [e["text"] for e in ex.events_until(1, timeout=0.5) if e.get("type") == "message"]
        if len(msgs) >= 2:
            break
    assert msgs[:2] == ["Square: 100 65 200", "Tower: 102 65 201"], msgs
    assert ex.gw.store.run(rid)["status"] == "succeeded"
    # restore is privileged: an admin's restore automation waits for the owner
    ex.account("adm", "admin")
    r = imp(ex, "adm", RESTORE_DEF)
    assert r["ok"] and ex.gw.store.automation("town-restore")["state"] == "pending_approval"
    assert ex.run("owner", "automations", "approve", "town-restore")["ok"]
    rid = ex.run("owner", "automations", "run", "town-restore")["data"]["run"]
    ex.gw.automation.execute(rid)
    assert ex.gw.store.run(rid)["status"] == "succeeded"
    ev = events_of(ex, "example_place")
    assert ev and ev[0]["install_id"] == iid
    assert ex.gw.store.example_install(iid)["status"] == "placing"


def test_example_actions_validate():
    from mcai.automation import DefinitionError, parse_definition, parse_text
    d = parse_definition(parse_text(RESTORE_DEF))
    assert d.privileged and "examples.install" in d.required_permissions
    with pytest.raises(DefinitionError, match="needs a trigger with a player"):
        parse_definition(parse_text(TOUR_DEF.replace('event = "player.trigger"\nname = "town"', 'every = "1h"')))
    with pytest.raises(DefinitionError, match="invalid format"):
        parse_definition(parse_text(RESTORE_DEF.replace('"tiny-town"', '"../x"')))
