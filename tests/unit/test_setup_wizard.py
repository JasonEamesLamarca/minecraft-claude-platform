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
