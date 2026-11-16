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
    status        TEXT NOT NULL,          -- see ACTIVE / FINAL below
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    reply         TEXT,
    error         TEXT,
    cost_usd      REAL NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    provider      TEXT,
    op_level      INTEGER NOT NULL DEFAULT 0,
    plan          TEXT,
    summary       TEXT,
    proposed_at   TEXT,
    decided_by    TEXT,
    decided_at    TEXT,
    built_at      TEXT,
    placed        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS requests_player ON requests(player_uuid, created_at);
CREATE INDEX IF NOT EXISTS requests_status ON requests(status);
"""


# ask:   queued -> running -> done | failed
# build: queued -> running -> proposed -> approved -> built -> rolling_back -> rolled_back
#        (proposed -> cancelled | expired | failed; approved -> interrupted; running -> cancelled)
ACTIVE = ("queued", "running", "proposed", "approved", "rolling_back")
MIGRATIONS = [("op_level", "INTEGER NOT NULL DEFAULT 0"), ("plan", "TEXT"), ("summary", "TEXT"),
              ("proposed_at", "TEXT"), ("decided_by", "TEXT"), ("decided_at", "TEXT"), ("built_at", "TEXT"),
              ("placed", "INTEGER NOT NULL DEFAULT 0")]


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
        have = {r[1] for r in self.db.execute("PRAGMA table_info(requests)")}
        for col, decl in MIGRATIONS:                       # upgrade databases from older versions in place
            if col not in have:
                self.db.execute(f"ALTER TABLE requests ADD COLUMN {col} {decl}")
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
    def create_request(self, player_uuid: str, player_name: str, kind: str, text: str, context: dict,
                       op_level: int = 0) -> str:
        """Short ids (6 hex chars) so players can type them; collisions are retried."""
        with self._lock:
            for _ in range(20):
                rid = uuid.uuid4().hex[:6]
                try:
                    self.db.execute(
                        "INSERT INTO requests(id, player_uuid, player_name, kind, text, context, status, created_at,"
                        " op_level) VALUES (?,?,?,?,?,?, 'queued', ?, ?)",
                        (rid, player_uuid, player_name, kind, text, json.dumps(context), utcnow(), op_level))
                    return rid
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("could not allocate a request id")

    def transition(self, rid: str, from_status: tuple, to_status: str, **fields) -> bool:
        """Atomic compare-and-set of the status (plus extra columns). False if the status was not in from_status."""
        cols = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
        marks = ",".join("?" * len(from_status))
        with self._lock:
            cur = self.db.execute(f"UPDATE requests SET {cols} WHERE id=? AND status IN ({marks})",
                                  (to_status, *fields.values(), rid, *from_status))
            return cur.rowcount == 1

    def latest(self, player_uuid: str, statuses: tuple, kind: str | None = None) -> sqlite3.Row | None:
        marks = ",".join("?" * len(statuses))
        q = f"SELECT * FROM requests WHERE player_uuid=? AND status IN ({marks})"
        args = [player_uuid, *statuses]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self._lock:
            return self.db.execute(q + " ORDER BY created_at DESC LIMIT 1", args).fetchone()

    def history(self, player_uuid: str, n: int = 5) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute("SELECT id, kind, status, text, summary, created_at FROM requests WHERE player_uuid=?"
                                   " ORDER BY created_at DESC LIMIT ?", (player_uuid, n)).fetchall()

    def with_status(self, statuses: tuple) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(statuses))
        with self._lock:
            return self.db.execute(f"SELECT * FROM requests WHERE status IN ({marks})", statuses).fetchall()

    def get(self, rid: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()

    def mark_running(self, rid: str) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE requests SET status='running', started_at=? WHERE id=? AND status='queued'",
                                  (utcnow(), rid))
            return cur.rowcount == 1

    def finish(self, rid: str, ok: bool, reply: str | None, error: str | None, cost_usd: float,
               input_tokens: int, output_tokens: int, provider: str, status: str | None = None) -> bool:
        """Record the AI result once. Returns False if the request was no longer running (duplicate/late/cancelled).
        The cost is recorded even then, so the daily budget stays correct."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE requests SET status=?, finished_at=?, reply=?, error=?, cost_usd=?, input_tokens=?,"
                " output_tokens=?, provider=? WHERE id=? AND status IN ('queued','running')",
                (status or ("done" if ok else "failed"), utcnow(), reply, error, max(0.0, cost_usd),
                 max(0, input_tokens), max(0, output_tokens), provider, rid))
            if cur.rowcount == 0 and cost_usd > 0:
                self.db.execute("UPDATE requests SET cost_usd=MAX(cost_usd, ?) WHERE id=?", (cost_usd, rid))
            return cur.rowcount == 1

    def pending_for(self, player_uuid: str) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(ACTIVE))
        with self._lock:
            return self.db.execute(
                f"SELECT id, kind, status, created_at FROM requests WHERE player_uuid=? AND status IN ({marks})"
                " ORDER BY created_at", (player_uuid, *ACTIVE)).fetchall()

    def spend_today(self) -> float:
        with self._lock:
            row = self.db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM requests WHERE created_at >= ?",
                                  (utc_day_start(),)).fetchone()
            return float(row[0])

    def fail_unfinished(self, reason: str) -> list[sqlite3.Row]:
        """At startup: nothing left over from a previous run is ever re-run (no duplicates).
        queued/running -> failed, proposed -> expired, approved/rolling_back -> interrupted (undo stays possible)."""
        with self._lock:
            rows = self.db.execute("SELECT id, player_uuid, status FROM requests WHERE status IN "
                                   "('queued','running','proposed','approved','rolling_back')").fetchall()
            now = utcnow()
            self.db.execute("UPDATE requests SET status='failed', error=?, finished_at=? WHERE status IN ('queued','running')",
                            (reason, now))
            self.db.execute("UPDATE requests SET status='expired', decided_at=? WHERE status='proposed'", (now,))
            self.db.execute("UPDATE requests SET status='interrupted', error=? WHERE status IN ('approved','rolling_back')",
                            (reason,))
            return rows
