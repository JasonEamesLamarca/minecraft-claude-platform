"""Roles, SSH key parsing and the SSH gateway's log parser / authorized_keys writer."""
import base64
import shutil
import struct
import subprocess

import pytest

from mcai import rbac, sshgw
from mcai.sshkeys import KeyError_, fingerprint, parse_public_key

ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM0ZlbRxvJeha1Tyk7VWsHnGdq3m8m2Jw0zv1d8gB7cK alice@laptop"


# ------------------------------------------------------------------ rbac
def test_roles_ordered_and_complete():
    assert list(rbac.ROLES) == sorted(rbac.ROLES, key=lambda r: -rbac.RANK[r])
    for perm, roles in rbac.PERMISSIONS.items():
        assert roles <= set(rbac.ROLES), perm


def test_permission_highlights():
    assert rbac.can("builder", "ai.build") and not rbac.can("player", "ai.build")
    assert not rbac.can("auditor", "ai.ask"), "auditors are read-only"
    assert rbac.can("auditor", "audit.read") and not rbac.can("operator", "audit.read")
    assert rbac.can("owner", "server.console") and not rbac.can("admin", "server.console")
    assert not any(rbac.can("auditor", p) for p in ("players.moderate", "roles.assign", "server.control",
                                                    "accounts.manage", "ai.configure", "ai.decide_any"))
    with pytest.raises(KeyError):
        rbac.can("owner", "no.such.permission")


@pytest.mark.parametrize("actor,current,new,ok", [
    ("owner", "player", "admin", True),
    ("owner", "admin", "player", True),
    ("owner", "owner", "admin", False),        # nobody demotes an owner through the API
    ("owner", "player", "owner", False),       # owners are made on the host
    ("admin", "player", "operator", True),
    ("admin", "player", "admin", False),       # not to your own level
    ("admin", "admin", "player", False),       # not someone at your level
    ("admin", "operator", "auditor", True),
    ("operator", "player", "builder", False),  # operators can't assign roles at all
    ("auditor", "player", "builder", False),
    ("admin", "player", "superuser", False),
])
def test_may_assign(actor, current, new, ok):
    assert rbac.may_assign(actor, current, new) is ok


# ------------------------------------------------------------------ keys
@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh-keygen")
def test_fingerprint_matches_ssh_keygen(tmp_path):
    key = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "me@host", "-f", str(key)], check=True)
    pub = (tmp_path / "k.pub").read_text()
    expected = subprocess.run(["ssh-keygen", "-lf", str(tmp_path / "k.pub")], capture_output=True, text=True,
                              check=True).stdout.split()[1]
    k = parse_public_key(pub)
    assert k.fingerprint == expected and k.comment == "me@host" and k.line == " ".join(pub.split()[:2])


def test_parse_valid_key():
    k = parse_public_key(ED)
    assert k.type == "ssh-ed25519" and k.fingerprint.startswith("SHA256:") and "alice" not in k.line


@pytest.mark.parametrize("bad", [
    "", "hello", "ssh-dss AAAAB3NzaC1kc3MAAACBAP",                        # DSA refused
    'command="sh" ' + ED,                                                 # options refused
    "ssh-ed25519 !!!notbase64",
    "ssh-rsa " + ED.split()[1],                                           # type doesn't match the blob
    ED + "\n" + ED,                                                       # two keys
])
def test_parse_rejects(bad):
    with pytest.raises(KeyError_):
        parse_public_key(bad)


def test_short_rsa_refused():
    def mpint(n):
        b = n.to_bytes((n.bit_length() + 8) // 8, "big")
        return struct.pack(">I", len(b)) + b
    blob = struct.pack(">I", 7) + b"ssh-rsa" + mpint(65537) + mpint((1 << 2047) + 1)
    with pytest.raises(KeyError_, match="3072"):
        parse_public_key("ssh-rsa " + base64.b64encode(blob).decode())


def test_fingerprint_format():
    assert fingerprint(b"x").startswith("SHA256:") and not fingerprint(b"x").endswith("=")


# ------------------------------------------------------------------ ssh gateway
@pytest.mark.parametrize("line,result", [
    ("Accepted publickey for mcai from 172.17.0.1 port 49824 ssh2: ED25519 SHA256:cO+8S/epbq", "accepted"),
    ("Failed publickey for mcai from 10.0.0.5 port 1 ssh2: ED25519 SHA256:dh49WV05yU", "key_rejected"),
    ("Invalid user root from 203.0.113.9 port 4242", "invalid_user"),
    ("Connection closed by authenticating user mcai 10.0.0.5 port 22 [preauth]", "closed_preauth"),
    ("Disconnected from user mcai 172.17.0.1 port 49824", "disconnected"),
    ("sshd[12]: Invalid user admin from 198.51.100.1 port 1", "invalid_user"),
])
def test_parse_sshd_line(line, result):
    ev = sshgw.parse_sshd_line(line)
    assert ev["result"] == result and ev["source_ip"]
    if result in ("accepted", "key_rejected"):
        assert ev["fingerprint"].startswith("SHA256:")


def test_parse_sshd_ignores_other_lines():
    assert sshgw.parse_sshd_line("Server listening on 0.0.0.0 port 2222.") is None


def test_authorized_keys_only_restricted_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(sshgw, "AUTH_KEYS", tmp_path / "authorized_keys")
    assert sshgw.write_authorized_keys(["restrict " + ED, "command=\"/bin/sh\" " + ED, "restrict x\nssh-rsa y"])
    text = (tmp_path / "authorized_keys").read_text()
    assert text == "restrict " + ED + "\n"
    assert (tmp_path / "authorized_keys").stat().st_mode & 0o777 == 0o600
    assert not sshgw.write_authorized_keys(["restrict " + ED])          # unchanged: not rewritten
