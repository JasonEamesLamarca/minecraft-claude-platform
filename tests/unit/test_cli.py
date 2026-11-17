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
