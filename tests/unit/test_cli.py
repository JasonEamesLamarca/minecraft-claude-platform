"""The mcai client: config + pinned host keys, and the exact ssh command it runs (no forwarding, strict host
key checking, safe quoting)."""
import json
import shlex
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "management-cli"))
import mcai_cli as mcai  # noqa: E402

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM0ZlbRxvJeha1Tyk7VWsHnGdq3m8m2Jw0zv1d8gB7cK"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(mcai, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(mcai, "ssh_binary", lambda: "ssh")
    return tmp_path / "cfg"


def test_remote_add_pins_host_key(cfg):
    assert mcai.main(["remote", "add", "home", "nas.local", "--port", "2200", "--host-key", KEY + " comment"]) == 0
    data = json.loads((cfg / "config.json").read_text())
    assert data["default"] == "home" and data["remotes"]["home"]["host_key"] == KEY
    assert (cfg / "known_hosts").read_text() == f"mcai-home {KEY}\n"


@pytest.mark.parametrize("args", [["bad name", "h", "--host-key", KEY], ["ok", "h", "--host-key", "not a key"],
                                  ["ok", "h", "--port", "0", "--host-key", KEY]])
def test_remote_add_validates(cfg, args):
    with pytest.raises(SystemExit):
        mcai.main(["remote", "add", *args])


def test_ssh_command_is_locked_down(cfg):
    mcai.main(["remote", "add", "home", "nas.local", "--host-key", KEY])
    c = mcai.Config()
    cmd = mcai.ssh_command(c, "home", c.data["remotes"]["home"], ["say", "hello; rm -rf /", "$(id)"])
    opts = " ".join(cmd)
    for o in ("StrictHostKeyChecking=yes", "BatchMode=yes", "ForwardAgent=no", "ForwardX11=no",
              "ClearAllForwardings=yes", "HostKeyAlias=mcai-home", f"UserKnownHostsFile={cfg / 'known_hosts'}"):
        assert o in opts, o
    assert cmd[cmd.index("--") - 1] == "mcai@nas.local" and "-T" in cmd
    assert shlex.split(cmd[-1]) == ["say", "hello; rm -rf /", "$(id)"]       # arrives as three plain arguments


def test_no_remote_configured(cfg):
    with pytest.raises(SystemExit, match="remote add"):
        mcai.main(["status"])


def test_definition_files_are_sent_on_stdin(cfg, tmp_path, monkeypatch):
    f = tmp_path / "welcome.toml"
    f.write_text('id = "welcome"\n')
    assert mcai.file_argument(["automations", "import", str(f), "--replace"]) == \
        (["automations", "import", "--stdin", "--replace"], 'id = "welcome"\n')
    assert mcai.file_argument(["auto", "validate", str(f)])[0] == ["auto", "validate", "--stdin"]
    assert mcai.file_argument(["automations", "list"]) == (["automations", "list"], None)
    with pytest.raises(SystemExit, match="Can't read"):
        mcai.file_argument(["automations", "import", str(tmp_path / "missing.toml")])
    big = tmp_path / "big.toml"
    big.write_text("x" * (mcai.MAX_FILE + 1))
    with pytest.raises(SystemExit, match="larger"):
        mcai.file_argument(["automations", "import", str(big)])

    mcai.main(["remote", "add", "home", "nas.local", "--host-key", KEY])
    seen = {}

    class Done:
        returncode = 0

    def fake_run(cmd, input=None):
        seen["cmd"], seen["input"] = cmd, input
        return Done()
    monkeypatch.setattr(mcai.subprocess, "run", fake_run)
    assert mcai.main(["automations", "import", str(f)]) == 0
    assert shlex.split(seen["cmd"][-1]) == ["automations", "import", "--stdin"] and seen["input"] == b'id = "welcome"\n'


# ------------------------------------------------------------------ example downloads
@pytest.mark.parametrize("url", [
    "https://github.com/o/r/releases/download/examples-1.0.0/atlantis-lite-manifest.json",
    "http://github.com/o/r/releases/download/t/f", "https://evil.example/o/r/releases/download/t/f",
    "https://github.com/o/r/releases/download/../f", "https://user@github.com/o/r/releases/download/t/f",
    "https://github.com/o/r/archive/main.zip", "https://github.com/o/r/releases/download/t/f?x=1",
])
def test_client_and_server_allow_the_same_urls(url):
    from mcai.examples import allowed_url as server_allowed
    assert mcai.allowed_url(url) == server_allowed(url)
    assert mcai.allowed_url(url, redirect=True) == server_allowed(url, redirect=True)


def _pack(tmp_path):
    import hashlib
    d = tmp_path / "dist" / "tiny"
    d.mkdir(parents=True)
    secs = []
    for i in range(2):
        data = bytes([i]) * 100
        (d / f"tiny-s{i}.schem").write_bytes(data)
        secs.append({"file": f"tiny-s{i}.schem", "sha256": hashlib.sha256(data).hexdigest(), "bytes": 100})
    manifest = json.dumps({"id": "tiny", "version": "1.0.0", "sections": secs}).encode()
    (d / "manifest.json").write_bytes(manifest)
    return tmp_path / "dist", manifest


def _fake_server(monkeypatch, catalog):
    import base64
    calls = []
    monkeypatch.setattr(mcai.Config, "remote", lambda self, name: ("home", {"host": "h", "port": 2222}))

    def server(cfg, name, r, argv, data=None):
        calls.append((argv, data))
        if argv[:2] == ["examples", "catalog"]:
            return {"ok": True, "data": {"catalog": catalog}}
        assert base64.b64encode(data).decode()
        return {"ok": True, "text": f"{argv[4]} ok"}
    monkeypatch.setattr(mcai, "_server_json", server)
    return calls


def test_download_from_local_folder_uploads_manifest_first(cfg, tmp_path, monkeypatch, capsys):
    import hashlib
    src, manifest = _pack(tmp_path)
    calls = _fake_server(monkeypatch, [{"id": "tiny", "version": "1.0.0", "manifest_sha256":
                                        hashlib.sha256(manifest).hexdigest(),
                                        "manifest_url": "https://github.com/o/r/releases/download/t/tiny-manifest.json"}])
    assert mcai.main(["examples", "download", "tiny", "--from", str(src)]) == 0
    sent = [c[0][4] for c in calls[1:]]
    assert sent == ["manifest.json", "tiny-s0.schem", "tiny-s1.schem"]
    assert calls[1][1] == manifest and calls[2][1] == bytes([0]) * 100
    assert "unofficial" not in capsys.readouterr().out


def test_download_stops_on_a_bad_section(cfg, tmp_path, monkeypatch):
    src, manifest = _pack(tmp_path)
    (src / "tiny" / "tiny-s1.schem").write_bytes(b"x" * 100)
    calls = _fake_server(monkeypatch, [])
    with pytest.raises(SystemExit, match="checksum"):
        mcai.main(["examples", "download", "tiny", "--from", str(src)])
    assert [c[0][4] for c in calls[1:]] == ["manifest.json", "tiny-s0.schem"]


def test_online_download_checks_the_pinned_hash(cfg, tmp_path, monkeypatch):
    src, manifest = _pack(tmp_path)
    url = "https://github.com/o/r/releases/download/t/tiny-manifest.json"
    calls = _fake_server(monkeypatch, [{"id": "tiny", "version": "1.0.0", "manifest_sha256": "0" * 64,
                                        "manifest_url": url}])
    monkeypatch.setattr(mcai, "fetch", lambda u, limit=0: manifest)
    with pytest.raises(SystemExit, match="doesn't match the checksum"):
        mcai.main(["examples", "download", "tiny"])
    assert len(calls) == 1, "nothing was uploaded"


def test_online_download_fetches_sections_next_to_the_manifest(cfg, tmp_path, monkeypatch):
    import hashlib
    src, manifest = _pack(tmp_path)
    base = "https://github.com/o/r/releases/download/t"
    files = {f"{base}/tiny-manifest.json": manifest,
             f"{base}/tiny-s0.schem": bytes([0]) * 100, f"{base}/tiny-s1.schem": bytes([1]) * 100}
    _fake_server(monkeypatch, [{"id": "tiny", "version": "1.0.0", "manifest_sha256":
                                hashlib.sha256(manifest).hexdigest(), "manifest_url": f"{base}/tiny-manifest.json"}])
    fetched = []
    monkeypatch.setattr(mcai, "fetch", lambda u, limit=0: fetched.append(u) or files[u])
    assert mcai.main(["examples", "download", "tiny"]) == 0
    assert fetched == list(files)


def test_fetch_refuses_disallowed_urls():
    with pytest.raises(SystemExit, match="only this project's GitHub release"):
        mcai.fetch("https://evil.example/x.schem")
