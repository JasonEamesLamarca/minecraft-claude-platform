"""AI providers. Each turns one job into a ProviderResult. Providers never get tools in Phase 2."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

from .prompt import build_system_prompt, build_user_prompt

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

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class Provider:
    name = "base"

    def answer(self, job: dict) -> ProviderResult:
        raise NotImplementedError


class NoneProvider(Provider):
    """Used when no AI credential is configured: answers every request with a 'not configured' error."""
    name = "none"

    def answer(self, job: dict) -> ProviderResult:
        return ProviderResult(False, error="no AI credential configured", error_code="not_configured", provider=self.name)


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
                 binary: str | None = None, mc_version: str = "1.21.1"):
        if auth_method not in ("api-key", "subscription"):
            raise ValueError("auth_method must be api-key or subscription")
        self.auth_method = auth_method
        self.credential = credential
        self.model = model
        self.max_request_usd = max_request_usd
        self.binary = binary or shutil.which("claude") or "claude"
        self.system_prompt = build_system_prompt(mc_version)

    def command(self) -> list[str]:
        cmd = [self.binary, "-p", "--output-format", "json", "--tools", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--max-turns", "1", "--max-budget-usd", f"{self.max_request_usd:.2f}",
               "--no-session-persistence", "--system-prompt", self.system_prompt]
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

    def answer(self, job: dict) -> ProviderResult:
        prompt = build_user_prompt(job.get("player_name", "Player"), job.get("text", ""), job.get("context"))
        timeout = max(10, int(job.get("timeout_s", 180)) - 10)
        try:
            proc = subprocess.run(self.command(), input=prompt, capture_output=True, text=True, timeout=timeout,
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
