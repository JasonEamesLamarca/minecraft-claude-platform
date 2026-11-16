"""Gateway state (SQLite, WAL) and the audit log (JSON lines, UTC). Thread-safe via one lock."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id            TEXT PRIMARY KEY,
    player_uuid   TEXT NOT NULL,
    player_name   TEXT NOT NULL,
    kind          TEXT NOT NULL,
    text          TEXT NOT NULL,
    context       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL,          -- queued | running | done | failed | rejected
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    reply         TEXT,
    error         TEXT,
    cost_usd      REAL NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    provider      TEXT
);
CREATE INDEX IF NOT EXISTS requests_player ON requests(player_uuid, created_at);
CREATE INDEX IF NOT EXISTS requests_status ON requests(status);
"""


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_day_start() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")


class Store:
    def __init__(self, data_dir: Path, audit_log_text: bool = False):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "audit").mkdir(exist_ok=True)
        self.audit_path = data_dir / "audit" / "audit.jsonl"
        self.audit_log_text = audit_log_text
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(data_dir / "gateway.db"), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        for p in (data_dir / "gateway.db", self.audit_path):
            if p.exists():
                os.chmod(p, 0o600)

    # ---------- audit ----------
    def audit(self, event: str, **fields) -> None:
        """Append one audit record. Request text is only stored as length + hash unless AUDIT_LOG_TEXT=true."""
        if "text" in fields and not self.audit_log_text:
            t = fields.pop("text") or ""
            fields["text_len"] = len(t)
            fields["text_sha256"] = hashlib.sha256(t.encode()).hexdigest()[:16]
        rec = {"ts": utcnow(), "event": event, **fields}
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        with self._lock:
            new = not self.audit_path.exists()
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if new:
                os.chmod(self.audit_path, 0o600)

    # ---------- requests ----------
    def create_request(self, player_uuid: str, player_name: str, kind: str, text: str, context: dict) -> str:
        rid = uuid.uuid4().hex[:12]
        with self._lock:
            self.db.execute(
                "INSERT INTO requests(id, player_uuid, player_name, kind, text, context, status, created_at)"
                " VALUES (?,?,?,?,?,?, 'queued', ?)",
                (rid, player_uuid, player_name, kind, text, json.dumps(context), utcnow()))
        return rid

    def get(self, rid: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()

    def mark_running(self, rid: str) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE requests SET status='running', started_at=? WHERE id=? AND status='queued'",
                                  (utcnow(), rid))
            return cur.rowcount == 1

    def finish(self, rid: str, ok: bool, reply: str | None, error: str | None, cost_usd: float,
               input_tokens: int, output_tokens: int, provider: str) -> bool:
        """Record the result once. Returns False if the request was already finished (duplicate/late result)."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE requests SET status=?, finished_at=?, reply=?, error=?, cost_usd=?, input_tokens=?,"
                " output_tokens=?, provider=? WHERE id=? AND status IN ('queued','running')",
                ("done" if ok else "failed", utcnow(), reply, error, max(0.0, cost_usd), max(0, input_tokens),
                 max(0, output_tokens), provider, rid))
            return cur.rowcount == 1

    def pending_for(self, player_uuid: str) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT id, kind, status, created_at FROM requests WHERE player_uuid=? AND status IN ('queued','running')"
                " ORDER BY created_at", (player_uuid,)).fetchall()

    def spend_today(self) -> float:
        with self._lock:
            row = self.db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM requests WHERE created_at >= ?",
                                  (utc_day_start(),)).fetchone()
            return float(row[0])

    def fail_unfinished(self, reason: str) -> list[sqlite3.Row]:
        """At startup: requests left queued/running by a previous run are failed (never re-run: no duplicates)."""
        with self._lock:
            rows = self.db.execute("SELECT id, player_uuid FROM requests WHERE status IN ('queued','running')").fetchall()
            self.db.execute("UPDATE requests SET status='failed', error=?, finished_at=? WHERE status IN ('queued','running')",
                            (reason, utcnow()))
            return rows
