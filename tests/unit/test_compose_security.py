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


def test_only_game_port_published(config):
    published = {name: svc.get("ports", []) for name, svc in services(config) if svc.get("ports")}
    assert list(published) == ["minecraft"]
    targets = [int(p["target"]) for p in published["minecraft"]]
    assert targets == [25565], "RCON (25575) or other ports must never be published"


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
