"""Setup wizard: fails safely, generates secrets correctly, never writes credentials to .env."""
import stat

import pytest

from conftest import VALID, read_env, run_setup


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_refuses_without_eula(workdir):
    r = run_setup(workdir, "--owner", "Test_Player1")
    assert r.returncode != 0
    assert "EULA" in r.stderr
    assert read_env(workdir).get("MC_EULA") != "TRUE"
    assert not (workdir / "secrets" / "rcon_password").exists()


@pytest.mark.parametrize("args, message", [
    (["--owner", "x"], "not a valid Minecraft username"),
    (["--owner", "bad name!"], "not a valid Minecraft username"),
    (["--owner", "Steve", "--port", "80"], "Port must be"),
    (["--owner", "Steve", "--port", "abc"], "Port must be"),
    (["--owner", "Steve", "--memory", "512M"], "Memory must"),
    (["--owner", "Steve", "--ai-auth", "magic"], "--ai-auth must be"),
])
def test_rejects_invalid_input(workdir, args, message):
    r = run_setup(workdir, "--accept-eula", *args)
    assert r.returncode != 0
    assert message in r.stderr


def test_owner_required_noninteractive(workdir):
    r = run_setup(workdir, "--accept-eula")
    assert r.returncode != 0


def test_success_creates_private_files(workdir):
    r = run_setup(workdir, *VALID)
    assert r.returncode == 0, r.stderr
    env = read_env(workdir)
    assert env["MC_EULA"] == "TRUE"
    assert env["MC_OWNER"] == "Test_Player1"
    assert env["MC_PORT"] == "25599"
    assert env["MC_MEMORY"] == "2G" and env["MC_MEM_LIMIT"] == "4g"
    assert env["PUID"].isdigit() and env["PGID"].isdigit()
    assert mode(workdir / ".env") == 0o600
    assert mode(workdir / "secrets") == 0o700
    for s in ("rcon_password", "gateway_mod_token", "worker_token"):
        f = workdir / "secrets" / s
        assert mode(f) == 0o600
        value = f.read_text().strip()
        assert len(value) >= 32
        assert value not in (workdir / ".env").read_text()


def test_rerun_keeps_secrets(workdir):
    assert run_setup(workdir, *VALID).returncode == 0
    before = (workdir / "secrets" / "rcon_password").read_text()
    assert run_setup(workdir, *VALID).returncode == 0
    assert (workdir / "secrets" / "rcon_password").read_text() == before


def test_secrets_are_unique_per_install(workdir, tmp_path_factory):
    assert run_setup(workdir, *VALID).returncode == 0
    a = (workdir / "secrets" / "rcon_password").read_text()
    b = (workdir / "secrets" / "gateway_mod_token").read_text()
    assert a != b


def test_no_ai_leaves_empty_credential_file(workdir):
    assert run_setup(workdir, *VALID).returncode == 0
    f = workdir / "secrets" / "ai_credential"
    assert f.exists() and f.read_text() == "" and mode(f) == 0o600


def test_api_key_goes_to_secret_file_not_env(workdir):
    key = "sk-ant-api03-" + "A" * 40
    r = run_setup(workdir, *VALID[:-1], "api-key", stdin=key + "\n")
    assert r.returncode == 0, r.stderr
    f = workdir / "secrets" / "ai_credential"
    assert f.read_text() == key and mode(f) == 0o600
    assert key not in (workdir / ".env").read_text()
    assert key not in r.stdout + r.stderr
    assert read_env(workdir)["AI_AUTH_METHOD"] == "api-key"


def test_switching_auth_method_replaces_credential_and_warns(workdir):
    run_setup(workdir, *VALID[:-1], "api-key", stdin="sk-ant-api03-" + "A" * 40 + "\n")
    token = "sk-ant-oat01-" + "B" * 40
    r = run_setup(workdir, *VALID[:-1], "subscription", stdin=token + "\n")
    assert r.returncode == 0, r.stderr
    assert (workdir / "secrets" / "ai_credential").read_text() == token
    assert read_env(workdir)["AI_AUTH_METHOD"] == "subscription"
    assert "legal-and-compliance" in r.stderr          # terms notice shown for subscription tokens


def test_switching_to_none_clears_credential(workdir):
    run_setup(workdir, *VALID[:-1], "api-key", stdin="sk-ant-api03-" + "A" * 40 + "\n")
    assert run_setup(workdir, *VALID).returncode == 0
    assert (workdir / "secrets" / "ai_credential").read_text() == ""


def test_old_credential_files_are_removed(workdir):
    (workdir / "secrets").mkdir(mode=0o700)
    (workdir / "secrets" / "anthropic_api_key").write_text("old")
    assert run_setup(workdir, *VALID).returncode == 0
    assert not (workdir / "secrets" / "anthropic_api_key").exists()


def test_empty_credential_rejected(workdir):
    r = run_setup(workdir, *VALID[:-1], "api-key", stdin="\n")
    assert r.returncode != 0
    assert "No credential" in r.stderr


# ------------------------------------------------------------------ SSH administration (Phase 4)
def test_ssh_defaults_to_this_computer_only(workdir):
    assert run_setup(workdir, *VALID).returncode == 0
    env = read_env(workdir)
    assert env["MCAI_SSH_BIND"] == "127.0.0.1" and env["MCAI_SSH_PORT"] == "2222"
    assert mode(workdir / "secrets" / "management_token") == 0o600
    assert len((workdir / "secrets" / "management_token").read_text().strip()) >= 32


def test_ssh_owner_key_queued_for_the_gateway(workdir, tmp_path):
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIM0ZlbRxvJeha1Tyk7VWsHnGdq3m8m2Jw0zv1d8gB7cK me@laptop\n")
    r = run_setup(workdir, *VALID, "--ssh-key", str(pub), "--ssh-bind", "0.0.0.0")
    assert r.returncode == 0, r.stderr
    queued = workdir / "data" / "gateway" / "bootstrap" / "owner.pub"
    assert queued.read_text().startswith("ssh-ed25519 AAAA") and mode(queued) == 0o600
    assert read_env(workdir)["MCAI_SSH_BIND"] == "0.0.0.0"


@pytest.mark.parametrize("content,name,message", [
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n", "id_ed25519", "not a public key file"),
    ("hello world\n", "id.pub", "doesn't look like an OpenSSH public key"),
])
def test_ssh_key_rejects_private_or_garbage(workdir, tmp_path, content, name, message):
    f = tmp_path / name
    f.write_text(content)
    r = run_setup(workdir, *VALID, "--ssh-key", str(f))
    assert r.returncode != 0 and message in r.stderr
    assert not (workdir / "data" / "gateway" / "bootstrap" / "owner.pub").exists()


def test_ssh_bind_validated(workdir):
    r = run_setup(workdir, *VALID, "--ssh-bind", "everywhere; rm -rf /")
    assert r.returncode != 0 and "--ssh-bind" in r.stderr


def test_rerun_keeps_stored_credential(workdir):
    assert run_setup(workdir, *VALID[:-2], "--ai-auth", "api-key", stdin="sk-ant-api03-test\n").returncode == 0
    r = run_setup(workdir)                       # non-interactive re-run, no --ai-auth: nothing to type
    assert r.returncode == 0, r.stderr
    assert (workdir / "secrets" / "ai_credential").read_text() == "sk-ant-api03-test"


def _fake_curl(tmp_path, code, body="{}"):
    """A curl on PATH that answers every lookup (Playerdb, Mojang) with one status and body; nothing leaves the machine."""
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    (bin_ / "body").write_text(body)
    (bin_ / "curl").write_text(
        "#!/bin/sh\nout=/dev/null\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && out=$2; shift; done\n"
        f"cat '{bin_ / 'body'}' > \"$out\"\nprintf {code}\n")
    (bin_ / "curl").chmod(0o755)
    import os
    return {"MCAI_SKIP_NAME_CHECK": "", "PATH": f"{bin_}:{os.environ['PATH']}"}


@pytest.mark.parametrize("code,body", [(400, '{"code":"minecraft.invalid_username"}'), (404, "{}")])
def test_owner_without_a_minecraft_account_is_refused(workdir, tmp_path, code, body):
    r = run_setup(workdir, *VALID, extra_env=_fake_curl(tmp_path, code, body))
    assert r.returncode != 0
    assert "no Minecraft Java account" in r.stderr


@pytest.mark.parametrize("code,warned", [(200, False), (429, True), (500, True)])
def test_owner_lookup_found_or_unknown_continues(workdir, tmp_path, code, warned):
    r = run_setup(workdir, *VALID, extra_env=_fake_curl(tmp_path, code))
    assert r.returncode == 0, r.stderr
    assert ("Couldn't check" in r.stderr) == warned
