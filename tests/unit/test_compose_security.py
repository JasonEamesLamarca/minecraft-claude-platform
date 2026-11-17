"""Security invariants of compose.yaml (ADR-0006, threat model T4/T11/T12/T13). Checked on the rendered config."""
import re

import pytest

from conftest import REPO, VALID, compose_config, run_setup


@pytest.fixture
def config(workdir):
    assert run_setup(workdir, *VALID).returncode == 0
    return compose_config(workdir)


def services(config):
    return config["services"].items()


def test_images_pinned_by_digest(config):
    for name, svc in services(config):
        if "build" in svc:          # built locally: its base image must be pinned instead
            dockerfile = REPO / svc["build"]["dockerfile"]
            froms = [l for l in dockerfile.read_text().splitlines() if l.startswith("FROM ")]
            assert froms and all(re.search(r"@sha256:[0-9a-f]{64}", l) for l in froms), f"{name}: unpinned base image"
        else:
            assert re.search(r":[\w.\-]+@sha256:[0-9a-f]{64}$", svc["image"]), f"{name} image not pinned: {svc['image']}"


def test_hardening_on_every_service(config):
    for name, svc in services(config):
        assert svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
        assert not svc.get("privileged", False), name
        assert svc.get("user") and not svc["user"].startswith("0"), f"{name} must not run as root"
        assert svc.get("network_mode") in (None, ""), f"{name} must not use host networking"
        assert svc.get("pids_limit"), name
        assert svc.get("mem_limit"), name


def test_no_docker_socket_mounted(config):
    for name, svc in services(config):
        for v in svc.get("volumes", []):
            assert "docker.sock" not in str(v.get("source", "")), name


def test_only_game_and_ssh_ports_published(config):
    published = {name: svc.get("ports", []) for name, svc in services(config) if svc.get("ports")}
    assert sorted(published) == ["minecraft", "ssh-gateway"]
    targets = [int(p["target"]) for p in published["minecraft"]]
    assert targets == [25565], "RCON (25575) or other ports must never be published"
    ssh = published["ssh-gateway"]
    assert [int(p["target"]) for p in ssh] == [2222]
    assert ssh[0]["host_ip"] == "127.0.0.1", "SSH administration must default to this computer only"


def test_ssh_gateway_is_locked_down(config):
    svc = config["services"]["ssh-gateway"]
    assert {x["source"] for x in svc.get("secrets", [])} == {"management_token"}
    assert svc.get("read_only") is True
    assert [v["target"] for v in svc.get("volumes", []) if v.get("type") == "bind"] == ["/data"]
    sshd = (REPO / "containers/management/sshd_config").read_text()
    for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitTTY no",
                 "AllowTcpForwarding no", "AllowStreamLocalForwarding no", "AllowAgentForwarding no",
                 "X11Forwarding no", "PermitTunnel no", "DisableForwarding yes", "PermitUserEnvironment no",
                 "ForceCommand /usr/local/bin/mcai-server", "MaxAuthTries 3", "PermitRootLogin no"):
        assert f"\n{line}\n" in sshd, line
    assert "Subsystem" not in sshd, "no sftp/scp subsystem"


def test_management_token_only_in_gateway_and_ssh_gateway(config):
    holders = {n for n, svc in services(config) if "management_token" in {x["source"] for x in svc.get("secrets", [])}}
    assert holders == {"gateway", "ssh-gateway"}


def test_gateway_reads_logs_and_backups_read_only(config):
    gw = config["services"]["gateway"]
    mounts = {v["target"]: v for v in gw["volumes"]}
    assert mounts["/mc-logs"].get("read_only") is True and mounts["/backups"].get("read_only") is True


def test_internal_network_is_internal(config):
    assert config["networks"]["internal"].get("internal") is True


def test_rcon_password_from_secret_not_env(config):
    mc = config["services"]["minecraft"]
    assert mc["environment"].get("RCON_PASSWORD_FILE") == "/run/secrets/rcon_password"
    assert "RCON_PASSWORD" not in mc["environment"]


def test_secure_minecraft_defaults(config):
    env = config["services"]["minecraft"]["environment"]
    assert env["ONLINE_MODE"] == "true"
    assert env["ENFORCE_WHITELIST"] == "true"
    assert env["ENFORCE_SECURE_PROFILE"] == "true"


def test_minecraft_root_fs_read_only(config):
    assert config["services"]["minecraft"].get("read_only") is True


def test_env_example_documents_every_variable():
    compose = (REPO / "compose.yaml").read_text()
    used = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose)) - {"COMPOSE_PROJECT_NAME"}
    example = {l.split("=", 1)[0] for l in (REPO / ".env.example").read_text().splitlines()
               if l and not l.startswith("#") and "=" in l}
    assert not used - example, f"undocumented variables: {sorted(used - example)}"


def test_env_example_has_no_values_for_secrets():
    text = (REPO / ".env.example").read_text()
    assert not re.search(r"^(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|RCON_PASSWORD)=", text, re.M)


def test_ops_reset_to_owner_whitelist_merged(config):
    env = config["services"]["minecraft"]["environment"]
    assert env["EXISTING_OPS_FILE"] == "SYNCHRONIZE"     # in-game op grants do not persist
    assert env["EXISTING_WHITELIST_FILE"] == "MERGE"     # whitelisted friends survive restarts


def test_ai_credential_only_in_worker(config):
    for name, svc in services(config):
        secrets = {x["source"] for x in svc.get("secrets", [])}
        if name == "ai-worker":
            assert secrets == {"ai_credential", "worker_token"}
        else:
            assert "ai_credential" not in secrets, f"{name} must not get the AI credential"


def test_worker_has_no_volumes_and_gateway_no_internet(config):
    worker = config["services"]["ai-worker"]
    assert not [v for v in worker.get("volumes", []) if v.get("type") != "tmpfs"], "AI worker must not mount host data"
    assert worker.get("read_only") is True
    assert set(config["services"]["gateway"]["networks"]) == {"internal"}
    assert set(worker["networks"]) == {"internal", "ai-egress"}


def test_worker_token_and_mod_token_separated(config):
    def sec(name):
        return {x["source"] for x in config["services"][name].get("secrets", [])}
    assert "worker_token" not in sec("minecraft")
    assert "gateway_mod_token" not in sec("ai-worker")


def test_claude_code_version_pinned():
    text = (REPO / "containers/claude/Dockerfile").read_text()
    assert re.search(r"ARG CLAUDE_CODE_VERSION=\d+\.\d+\.\d+", text)
    assert "DISABLE_AUTOUPDATER=1" in text


def test_services_run_as_installing_user(config, workdir):
    """Secret files are mode 600 and owned by the installing user, so every service must run as that UID/GID
    (a fixed UID worked only when the installer happened to have that UID)."""
    env = dict(l.split("=", 1) for l in (workdir / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
    for name, svc in services(config):
        assert svc["user"] == f"{env['PUID']}:{env['PGID']}", f"{name} runs as {svc['user']}"
