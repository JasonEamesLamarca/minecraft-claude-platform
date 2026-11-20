"""Shared fixtures: a throw-away copy of the deployment files, so tests never touch the real .env/secrets."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY_FILES = ["compose.yaml", ".env.example"]


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    for f in DEPLOY_FILES:
        shutil.copy2(REPO / f, tmp_path / f)
    shutil.copytree(REPO / "scripts", tmp_path / "scripts")
    return tmp_path


def run_setup(workdir: Path, *args: str, stdin: str = "", extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MC_", "AI_", "ANTHROPIC", "CLAUDE"))}
    env["COMPOSE_PROJECT_NAME"] = "mcai-test"
    env["MCAI_SKIP_MOD_BUILD"] = "1"
    env["MCAI_SKIP_NAME_CHECK"] = "1"      # no Mojang lookups from unit tests
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(workdir / "scripts" / "setup.sh"), "--non-interactive", *args],
        input=stdin, capture_output=True, text=True, env=env, cwd=workdir, timeout=120,
    )


def read_env(workdir: Path) -> dict:
    out = {}
    for line in (workdir / ".env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def compose_config(workdir: Path) -> dict:
    res = subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=workdir,
                         capture_output=True, text=True, check=True, timeout=60)
    return json.loads(res.stdout)


VALID = ["--accept-eula", "--owner", "Test_Player1", "--port", "25599", "--memory", "2G", "--ai-auth", "none"]
