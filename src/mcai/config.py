"""Settings from environment variables; secrets from files (Docker secrets), never from env values."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "").strip()
    value = float(raw) if raw else default
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def read_secret(path: str | Path, min_len: int = 0) -> str:
    """Read a secret file. Raises if it is missing or shorter than min_len. The value is never logged."""
    value = Path(path).read_text(encoding="utf-8").strip()
    if len(value) < min_len:
        raise ValueError(f"secret file {path} is empty or too short")
    return value


@dataclass(frozen=True)
class GatewaySettings:
    host: str
    port: int
    data_dir: Path
    mod_token: str
    worker_token: str
    max_prompt_chars: int
    max_reply_chars: int
    player_requests_per_minute: int
    max_pending_per_player: int
    max_queue: int
    daily_budget_usd: float
    request_timeout_s: int
    audit_log_text: bool

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        return cls(
            host=os.environ.get("GATEWAY_HOST", "0.0.0.0"),
            port=env_int("GATEWAY_PORT", 8080, 1, 65535),
            data_dir=Path(os.environ.get("GATEWAY_DATA_DIR", "/data")),
            mod_token=read_secret(os.environ.get("GATEWAY_MOD_TOKEN_FILE", "/run/secrets/gateway_mod_token"), 32),
            worker_token=read_secret(os.environ.get("GATEWAY_WORKER_TOKEN_FILE", "/run/secrets/worker_token"), 32),
            max_prompt_chars=env_int("AI_MAX_PROMPT_CHARS", 500, 10, 4000),
            max_reply_chars=env_int("AI_MAX_REPLY_CHARS", 1200, 100, 4000),
            player_requests_per_minute=env_int("AI_PLAYER_REQUESTS_PER_MINUTE", 3, 1, 60),
            max_pending_per_player=env_int("AI_MAX_PENDING_PER_PLAYER", 1, 1, 10),
            max_queue=env_int("AI_MAX_QUEUE", 20, 1, 1000),
            daily_budget_usd=env_float("AI_DAILY_BUDGET_USD", 2.0, 0.0, 10000.0),
            request_timeout_s=env_int("AI_REQUEST_TIMEOUT_SECONDS", 180, 10, 3600),
            audit_log_text=os.environ.get("AUDIT_LOG_TEXT", "false").strip().lower() == "true",
        )


class JsonLogFormatter:
    """One JSON object per log line, UTC timestamps (structured logging for docker logs)."""

    def __init__(self, service: str):
        self.service = service

    def format(self, record) -> str:
        import datetime as _dt
        import json as _json
        rec = {"ts": _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc).isoformat(timespec="milliseconds")
               .replace("+00:00", "Z"), "level": record.levelname.lower(), "service": self.service,
               "msg": record.getMessage()}
        if record.exc_info:
            import traceback as _tb
            rec["exc"] = "".join(_tb.format_exception(*record.exc_info))[-2000:]
        return _json.dumps(rec, ensure_ascii=False)


def setup_logging(service: str) -> None:
    import logging as _logging
    handler = _logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter(service))  # type: ignore[arg-type]
    root = _logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
