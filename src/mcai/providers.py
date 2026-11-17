"""AI providers. Each turns one job into a ProviderResult. Providers never get tools in Phase 2."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from .prompt import (build_automation_prompt, build_automation_user_prompt, build_builder_prompt,
                     build_system_prompt, build_user_prompt)

log = logging.getLogger("mcai.provider")


@dataclass
class ProviderResult:
    ok: bool
    text: str = ""
    error: str = ""
    error_code: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str = ""
    plan: dict | None = field(default=None)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class Provider:
    name = "base"

    def answer(self, job: dict) -> ProviderResult:
        raise NotImplementedError

    def build(self, job: dict) -> ProviderResult:
        raise NotImplementedError

    def automation(self, job: dict) -> ProviderResult:
        raise NotImplementedError

    def run(self, job: dict) -> ProviderResult:
        kind = job.get("kind")
        if kind == "build":
            return self.build(job)
        if kind == "automation":
            return self.automation(job)
        return self.answer(job)


class NoneProvider(Provider):
    """Used when no AI credential is configured: answers every request with a 'not configured' error."""
    name = "none"

    def answer(self, job: dict) -> ProviderResult:
        return ProviderResult(False, error="no AI credential configured", error_code="not_configured", provider=self.name)

    build = answer
    automation = answer


class MockProvider(Provider):
    """Deterministic provider for tests and CI: no network, no cost."""
    name = "mock"

    def answer(self, job: dict) -> ProviderResult:
        text = job.get("text", "")
        if "MOCK_FAIL" in text:
            return ProviderResult(False, error="mock failure requested", error_code="provider_error", provider=self.name)
        prompt = build_user_prompt(job.get("player_name", "Player"), text, job.get("context"))
        return ProviderResult(True, text=f"[mock] You asked: {text}", cost_usd=0.0,
                              input_tokens=len(prompt) // 4, output_tokens=10, provider=self.name)

    def build(self, job: dict) -> ProviderResult:
        """A fixed small pillar 2 blocks east/south of the origin, or a forbidden block for MOCK_BAD_BUILD."""
        ox, oy, oz = job["build"]["origin"]
        if "MOCK_BAD_BUILD" in job.get("text", ""):
            plan = {"title": "Bad", "actions": [{"op": "setblock", "pos": [ox + 2, oy, oz + 2], "block": "minecraft:tnt"}]}
        else:
            plan = {"title": "Mock pillar", "actions": [
                {"op": "fill", "from": [ox + 2, oy, oz + 2], "to": [ox + 4, oy + 3, oz + 4],
                 "block": "minecraft:stone_bricks", "mode": "hollow"},
                {"op": "setblock", "pos": [ox + 3, oy + 4, oz + 3], "block": "minecraft:torch"}]}
        return ProviderResult(True, text="[mock] A small stone pillar with a torch.", plan=plan, provider=self.name)

    def automation(self, job: dict) -> ProviderResult:
        """A fixed definition: a daily broadcast; MOCK_BAD_AUTOMATION asks for a forbidden action type,
        MOCK_PRIVILEGED for a role change (needs the owner's approval)."""
        text = job.get("text", "")
        if "MOCK_BAD_AUTOMATION" in text:
            d = {"id": "mock-bad", "name": "Bad", "trigger": {"every": "1h"},
                 "actions": [{"type": "command", "command": "op everyone"}]}
        elif "MOCK_PRIVILEGED" in text:
            d = {"id": "mock-promote", "name": "Promote regulars", "trigger": {"event": "player.join"},
                 "conditions": {"playtime_hours_min": 5, "player_roles": ["player"]},
                 "actions": [{"type": "set_role", "role": "builder"}]}
        else:
            d = {"id": "mock-daily", "name": "Daily greeting", "description": text[:100],
                 "trigger": {"schedule": "0 18 * * *"}, "conditions": {"players_online_min": 1},
                 "actions": [{"type": "broadcast", "text": "Good evening, {players_online} players online!"}]}
        return ProviderResult(True, text="Here is the definition:\n" + json.dumps(d), provider=self.name,
                              input_tokens=len(text) // 4, output_tokens=50)


class ClaudeCodeProvider(Provider):
    """Runs Claude Code headless (`claude -p`) with every tool removed: it can only produce text.

    Flags (verified against the Claude Code CLI reference, Nov 2026):
      --tools ""                      no built-in tools (no Bash, file, web access)
      --strict-mcp-config + empty     no MCP servers from any config file
      --max-turns 1 / --max-budget-usd  hard caps per request
      --no-session-persistence        nothing written to disk
      --system-prompt                 replaces the default coding-agent prompt
    The credential is passed only in the subprocess environment.
    """
    name = "claude-code"

    def __init__(self, auth_method: str, credential: str, model: str = "", max_request_usd: float = 0.25,
                 binary: str | None = None, mc_version: str = "1.21.1", max_build_usd: float = 1.0,
                 build_max_turns: int = 10):
        if auth_method not in ("api-key", "subscription"):
            raise ValueError("auth_method must be api-key or subscription")
        self.auth_method = auth_method
        self.credential = credential
        self.model = model
        self.max_request_usd = max_request_usd
        self.binary = binary or shutil.which("claude") or "claude"
        self.system_prompt = build_system_prompt(mc_version)
        self.builder_prompt = build_builder_prompt(mc_version)
        self.max_build_usd = max_build_usd
        self.build_max_turns = build_max_turns

    def command(self, max_usd: float | None = None, system_prompt: str | None = None) -> list[str]:
        budget = min(self.max_request_usd, max_usd) if max_usd else self.max_request_usd
        cmd = [self.binary, "-p", "--output-format", "json", "--tools", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--max-turns", "1", "--max-budget-usd", f"{budget:.2f}",
               "--no-session-persistence", "--system-prompt", system_prompt or self.system_prompt]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def environment(self) -> dict:
        keep = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "CLAUDE_CONFIG_DIR", "TMPDIR")
        env = {k: v for k, v in os.environ.items() if k in keep}
        env.update(DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        if self.auth_method == "api-key":
            env["ANTHROPIC_API_KEY"] = self.credential
        else:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.credential
        return env

    def build_command(self, mcp_config: str) -> list[str]:
        """Builds: still no built-in tools; the only tools are the two plan tools of the mcai MCP server."""
        cmd = [self.binary, "-p", "--output-format", "json", "--tools", "",
               "--strict-mcp-config", "--mcp-config", mcp_config,
               "--allowedTools", "mcp__mcai__validate_plan,mcp__mcai__propose_build",
               "--max-turns", str(self.build_max_turns), "--max-budget-usd", f"{self.max_build_usd:.2f}",
               "--no-session-persistence", "--system-prompt", self.builder_prompt]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    @staticmethod
    def mcp_config(context: dict, proposal_file: str) -> str:
        return json.dumps({"mcpServers": {"mcai": {
            "type": "stdio", "command": sys.executable, "args": ["-m", "mcai.mcp_build"],
            "env": {"PYTHONPATH": os.environ.get("PYTHONPATH", "/app"), "MCAI_BUILD_CONTEXT": json.dumps(context),
                    "MCAI_PROPOSAL_FILE": proposal_file}}}})

    def build(self, job: dict) -> ProviderResult:
        tmpdir = os.environ.get("TMPDIR", "/tmp")
        fd, proposal = tempfile.mkstemp(prefix="proposal-", suffix=".json", dir=tmpdir)
        os.close(fd)
        try:
            prompt = build_user_prompt(job.get("player_name", "Player"), job.get("text", ""), job.get("context"))
            result = self._run(self.build_command(self.mcp_config(job["build"], proposal)), prompt, job)
            if not result.ok:
                return result
            try:
                with open(proposal, encoding="utf-8") as f:
                    result.plan = json.load(f)
            except (OSError, ValueError):
                result.plan = None
            if not result.plan:
                return ProviderResult(False, error="no plan proposed", error_code="no_plan", cost_usd=result.cost_usd,
                                      input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                                      provider=self.name, text=result.text)
            return result
        finally:
            os.unlink(proposal)

    def answer(self, job: dict) -> ProviderResult:
        prompt = build_user_prompt(job.get("player_name", "Player"), job.get("text", ""), job.get("context"))
        return self._run(self.command(job.get("max_usd")), prompt, job)

    def automation(self, job: dict) -> ProviderResult:
        """An automation proposal: text only (no tools); the gateway validates the JSON it contains."""
        system = build_automation_prompt(os.environ.get("AUTOMATION_TIMEZONE", "") or os.environ.get("TZ", "") or "UTC")
        return self._run(self.command(system_prompt=system), build_automation_user_prompt(job.get("text", "")), job)

    def _run(self, cmd: list[str], prompt: str, job: dict) -> ProviderResult:
        timeout = max(10, int(job.get("timeout_s", 180)) - 10)
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                                  env=self.environment(), cwd=os.environ.get("TMPDIR", "/tmp"))
        except subprocess.TimeoutExpired:
            return ProviderResult(False, error="claude timed out", error_code="timeout", provider=self.name)
        except OSError as e:
            return ProviderResult(False, error=f"cannot run claude: {e.strerror}", error_code="provider_error",
                                  provider=self.name)
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            log.warning("claude exited %s without JSON output; stderr: %s", proc.returncode, proc.stderr[-500:])
            return ProviderResult(False, error=f"claude exit {proc.returncode}", error_code="provider_error",
                                  provider=self.name)
        usage = data.get("usage") or {}
        cost = float(data.get("total_cost_usd") or 0)
        tokens_in = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) \
            + int(usage.get("cache_creation_input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        if data.get("is_error") or proc.returncode != 0 or data.get("subtype", "success") != "success":
            detail = str(data.get("result") or data.get("subtype") or "error")[:300]
            log.warning("claude reported an error: %s", detail)
            return ProviderResult(False, error=detail, error_code="provider_error", cost_usd=cost,
                                  input_tokens=tokens_in, output_tokens=tokens_out, provider=self.name)
        return ProviderResult(True, text=str(data.get("result", "")), cost_usd=cost, input_tokens=tokens_in,
                              output_tokens=tokens_out, provider=self.name)
