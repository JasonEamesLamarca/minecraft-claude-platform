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


def _optional_secret(path: str, min_len: int = 32) -> str:
    """A secret that may be absent (that feature is then off). Present but too short is an error."""
    return read_secret(path, min_len) if path and Path(path).exists() else ""


def _timezone(name: str) -> str:
    from .cron import ScheduleError, timezone
    try:
        timezone(name)
    except ScheduleError as e:
        raise ValueError(f"AUTOMATION_TIMEZONE/TZ: {e}") from None
    return name


def _optional_dir(path: str) -> Path | None:
    return Path(path) if path and Path(path).is_dir() else None


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
    build_timeout_s: int = 420
    approve_ttl_s: int = 900
    exec_timeout_s: int = 600
    build_max_blocks: int = 40_000
    build_max_size: tuple = (48, 64, 48)
    build_max_distance: int = 64
    build_max_actions: int = 2000
    undo_keep_days: int = 7
    # management (Phase 4)
    management_token: str = ""             # empty: management API disabled
    owner_name: str = ""                   # Minecraft name that becomes the owner at its first join
    online_mode: bool = True
    mc_logs_dir: Path | None = None
    backups_dir: Path | None = None
    rcon_host: str = "minecraft"
    rcon_port: int = 25575
    rcon_password: str = ""
    audit_retention_days: int = 365
    # automations (Phase 5)
    automation_timezone: str = "UTC"       # default for definitions without a timezone
    automation_max_runs_per_minute: int = 30
    automation_ai_calls_per_hour: int = 10
    automation_workers: int = 2
    automation_examples_dir: Path | None = None

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
            build_timeout_s=env_int("AI_BUILD_TIMEOUT_SECONDS", 420, 30, 3600),
            approve_ttl_s=env_int("AI_APPROVE_TTL_SECONDS", 900, 30, 86400),
            exec_timeout_s=env_int("AI_EXEC_TIMEOUT_SECONDS", 600, 30, 7200),
            build_max_blocks=env_int("AI_BUILD_MAX_BLOCKS", 40_000, 1, 1_000_000),
            build_max_size=(env_int("AI_BUILD_MAX_X", 48, 1, 256), env_int("AI_BUILD_MAX_Y", 64, 1, 384),
                            env_int("AI_BUILD_MAX_Z", 48, 1, 256)),
            build_max_distance=env_int("AI_BUILD_MAX_DISTANCE", 64, 1, 512),
            build_max_actions=env_int("AI_BUILD_MAX_ACTIONS", 2000, 1, 20_000),
            undo_keep_days=env_int("AI_UNDO_KEEP_DAYS", 7, 0, 3650),
            management_token=_optional_secret(os.environ.get("GATEWAY_MANAGEMENT_TOKEN_FILE",
                                                             "/run/secrets/management_token")),
            owner_name=os.environ.get("MC_OWNER", "").strip(),
            online_mode=os.environ.get("MC_ONLINE_MODE", "true").strip().lower() == "true",
            mc_logs_dir=_optional_dir(os.environ.get("MC_LOGS_DIR", "/mc-logs")),
            backups_dir=_optional_dir(os.environ.get("BACKUPS_DIR", "/backups")),
            rcon_host=os.environ.get("RCON_HOST", "minecraft"),
            rcon_port=env_int("RCON_PORT", 25575, 1, 65535),
            rcon_password=_optional_secret(os.environ.get("RCON_PASSWORD_FILE", "/run/secrets/rcon_password"), 8),
            audit_retention_days=env_int("AUDIT_RETENTION_DAYS", 365, 0, 36500),
            automation_timezone=_timezone(os.environ.get("AUTOMATION_TIMEZONE", "") or os.environ.get("TZ", "")
                                          or "UTC"),
            automation_max_runs_per_minute=env_int("AUTOMATION_MAX_RUNS_PER_MINUTE", 30, 1, 600),
            automation_ai_calls_per_hour=env_int("AUTOMATION_AI_CALLS_PER_HOUR", 10, 0, 1000),
            automation_workers=env_int("AUTOMATION_WORKERS", 2, 1, 8),
            automation_examples_dir=_optional_dir(os.environ.get("AUTOMATION_EXAMPLES_DIR",
                                                                 "/app/automation-examples")),
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
