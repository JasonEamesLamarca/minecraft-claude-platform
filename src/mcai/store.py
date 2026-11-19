"""Gateway state (SQLite, WAL) and the audit log (JSON lines, UTC). Thread-safe via one lock."""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
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
CREATE TABLE IF NOT EXISTS players (
    uuid        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'player',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    online      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS players_name ON players(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_uuid TEXT NOT NULL,
    name        TEXT NOT NULL,
    joined_at   TEXT NOT NULL,
    left_at     TEXT
);
CREATE INDEX IF NOT EXISTS sessions_player ON sessions(player_uuid, joined_at);
CREATE TABLE IF NOT EXISTS accounts (
    name        TEXT PRIMARY KEY,
    role        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    disabled    INTEGER NOT NULL DEFAULT 0,
    player_uuid TEXT
);
CREATE TABLE IF NOT EXISTS account_keys (
    fingerprint TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    public_key  TEXT NOT NULL,
    comment     TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL,
    added_by    TEXT NOT NULL,
    revoked_at  TEXT
);
CREATE TABLE IF NOT EXISTS link_codes (
    code        TEXT PRIMARY KEY,
    account     TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    definition  TEXT NOT NULL,            -- normalized JSON
    state       TEXT NOT NULL,            -- see automation.STATES
    owner       TEXT NOT NULL,            -- account:<name> or host; its permissions are checked at every run
    source      TEXT NOT NULL,            -- manual | ai | example
    privileged  INTEGER NOT NULL DEFAULT 0,
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    note        TEXT,
    next_run_at REAL,                     -- epoch seconds of the next schedule slot
    runtime     TEXT NOT NULL DEFAULT '{}',
    failures    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS automation_runs (
    id            TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    version       INTEGER NOT NULL,
    idem_key      TEXT NOT NULL UNIQUE,   -- schedule slot / event id: the same one never runs twice
    trigger       TEXT NOT NULL,
    subject       TEXT NOT NULL DEFAULT '',
    event         TEXT NOT NULL DEFAULT '{}',
    chain         TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL,
    attempt       INTEGER NOT NULL DEFAULT 0,
    step          INTEGER NOT NULL DEFAULT 0,
    created       REAL NOT NULL,
    started       REAL,
    finished      REAL,
    next_attempt  REAL,
    error         TEXT,
    output        TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS automation_runs_auto ON automation_runs(automation_id, created);
CREATE INDEX IF NOT EXISTS automation_runs_status ON automation_runs(status);
CREATE TABLE IF NOT EXISTS example_packs (
    id          TEXT NOT NULL,
    version     TEXT NOT NULL,
    status      TEXT NOT NULL,            -- uploading | ready | invalid
    official    INTEGER NOT NULL DEFAULT 0,  -- manifest hash matches the catalog shipped with the platform
    manifest    TEXT NOT NULL,            -- validated manifest JSON
    files       TEXT NOT NULL DEFAULT '{}',  -- section file -> "ok"
    error       TEXT,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS example_installs (
    id          TEXT PRIMARY KEY,
    pack        TEXT NOT NULL,
    version     TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    anchor      TEXT NOT NULL,            -- [x, y, z] world position of the pack anchor
    bbox        TEXT NOT NULL,            -- {"from": [x,y,z], "to": [x,y,z]}
    status      TEXT NOT NULL,            -- see examples.INSTALL_STATES
    job         TEXT,                     -- place | rollback | forget while the mod works on it
    progress    TEXT NOT NULL DEFAULT '{}',
    snapshots   TEXT NOT NULL DEFAULT 'none',  -- none | present | deleted
    error       TEXT,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    finished_at TEXT
);
"""


# ask:   queued -> running -> done | failed
# build: queued -> running -> proposed -> approved -> built -> rolling_back -> rolled_back
#        (proposed -> cancelled | expired | failed; approved -> interrupted; running -> cancelled)
ACTIVE = ("queued", "running", "proposed", "approved", "rolling_back")
MIGRATIONS = [("op_level", "INTEGER NOT NULL DEFAULT 0"), ("plan", "TEXT"), ("summary", "TEXT"),
              ("proposed_at", "TEXT"), ("decided_by", "TEXT"), ("decided_at", "TEXT"), ("built_at", "TEXT"),
              ("placed", "INTEGER NOT NULL DEFAULT 0"), ("snapshot", "TEXT"), ("automation_run", "TEXT"),
              ("max_usd", "REAL")]
AUDIT_FILE_RE = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})(-legacy)?\.jsonl$")


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_day_start() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")


class _Rows:
    """A fully read statement result, so no cursor is used after the lock is released."""

    def __init__(self, cur: sqlite3.Cursor):
        self.rows = cur.fetchall() if cur.description else []
        self.rowcount, self.lastrowid = cur.rowcount, cur.lastrowid

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return iter(self.rows)


class _SerializedDB:
    """The gateway's threads (HTTP requests, automations, sweeper) share one SQLite connection. Python's sqlite3
    objects are not safe for concurrent use, so every statement runs under one lock and its rows are read inside it."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params=()) -> _Rows:
        with self._lock:
            return _Rows(self._conn.execute(sql, params))

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Store:
    def __init__(self, data_dir: Path, audit_log_text: bool = False):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir = data_dir / "audit"
        self.audit_dir.mkdir(exist_ok=True)
        self.audit_log_text = audit_log_text
        self._lock = threading.Lock()
        conn = sqlite3.connect(str(data_dir / "gateway.db"), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        self.db = _SerializedDB(conn)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(requests)")}
        for col, decl in MIGRATIONS:                       # upgrade databases from older versions in place
            if col not in have:
                self.db.execute(f"ALTER TABLE requests ADD COLUMN {col} {decl}")
        if "snapshot" not in have:                         # builds made before snapshot tracking (0.3.x)
            self.db.execute("UPDATE requests SET snapshot='present' WHERE kind='build' AND status IN "
                            "('built','interrupted','rolled_back')")
        os.chmod(data_dir / "gateway.db", 0o600)
        self._migrate_legacy_audit()

    # ---------- audit (one JSONL file per UTC day, hash-chained) ----------
    def audit_files(self) -> list[Path]:
        return sorted(p for p in self.audit_dir.iterdir() if AUDIT_FILE_RE.match(p.name))

    @property
    def audit_path(self) -> Path:
        """Today's audit file."""
        return self.audit_dir / f"audit-{dt.datetime.now(dt.timezone.utc):%Y-%m-%d}.jsonl"

    def _migrate_legacy_audit(self) -> None:
        old = self.audit_dir / "audit.jsonl"
        if not old.exists():
            return
        try:
            first = json.loads(old.open(encoding="utf-8").readline())["ts"][:10]
        except (ValueError, KeyError, TypeError):
            first = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        old.rename(self.audit_dir / f"audit-{first}-legacy.jsonl")

    @staticmethod
    def _tail_hash(f) -> str | None:
        """Hash of the last line of an open binary file (None if empty)."""
        f.seek(0, os.SEEK_END)
        end = f.tell()
        if end == 0:
            return None
        f.seek(max(0, end - 65536))
        lines = f.read().rstrip(b"\n").split(b"\n")
        return hashlib.sha256(lines[-1]).hexdigest()[:32]

    def _last_hash(self) -> str:
        for p in reversed(self.audit_files()):
            with open(p, "rb") as f:
                h = self._tail_hash(f)
            if h:
                return h
        return ""

    def audit(self, event: str, **fields) -> None:
        """Append one audit record. Request text is only stored as length + hash unless AUDIT_LOG_TEXT=true.
        Each record carries "prev", a hash of the previous record, so edits or deletions can be detected
        (`mcai audit verify`). A file lock keeps the chain intact when another process (scripts/set-role.sh)
        writes too."""
        if "text" in fields and not self.audit_log_text:
            t = fields.pop("text") or ""
            fields["text_len"] = len(t)
            fields["text_sha256"] = hashlib.sha256(t.encode()).hexdigest()[:16]
        with self._lock:
            ts = utcnow()
            path = self.audit_dir / f"audit-{ts[:10]}.jsonl"
            new = not path.exists()
            with open(path, "a+b") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    prev = self._tail_hash(f)
                    if prev is None:                 # first record of the day: chain from the previous file
                        prev = self._last_hash()
                    rec = {"ts": ts, "event": event, **fields, "prev": prev}
                    f.seek(0, os.SEEK_END)
                    f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True).encode() + b"\n")
                    f.flush()
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            if new:
                os.chmod(path, 0o600)

    def audit_records(self, since: str = "", prefix: str = "", actor: str = "", limit: int = 1000) -> list[dict]:
        """Newest last. `since` is an ISO timestamp; `prefix` filters event names; `actor` matches any field value."""
        out: list[dict] = []
        day = since[:10]
        for p in reversed(self.audit_files()):
            m = AUDIT_FILE_RE.match(p.name)
            chunk = []
            for raw in p.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if since and rec.get("ts", "") < since:
                    continue
                if prefix and not str(rec.get("event", "")).startswith(prefix):
                    continue
                if actor and actor not in {str(v) for v in rec.values()}:
                    continue
                chunk.append(rec)
            out[:0] = chunk
            if len(out) >= limit or (day and m and m.group(1) < day and not m.group(2)):
                break
        return out[-limit:]

    def audit_verify(self) -> tuple[int, str | None]:
        """(records checked, description of the first break or None). Files deleted by retention are expected."""
        prev, n = None, 0
        for p in self.audit_files():
            for i, raw in enumerate(p.read_bytes().splitlines(), 1):
                try:
                    rec = json.loads(raw)
                except ValueError:
                    return n, f"{p.name} line {i}: not JSON"
                if "prev" in rec and prev is not None and rec["prev"] != prev:
                    return n, f"{p.name} line {i}: chain broken (record changed, inserted or removed before it)"
                prev = hashlib.sha256(raw).hexdigest()[:32]
                n += 1
        return n, None

    def audit_prune(self, keep_days: int) -> list[str]:
        """Delete daily audit files older than keep_days (0 keeps everything)."""
        if keep_days <= 0:
            return []
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)).strftime("%Y-%m-%d")
        removed = []
        for p in self.audit_files():
            m = AUDIT_FILE_RE.match(p.name)
            if m and m.group(1) < cutoff:
                p.unlink()
                removed.append(p.name)
        return removed

    # ---------- requests ----------
    def create_request(self, player_uuid: str, player_name: str, kind: str, text: str, context: dict,
                       op_level: int = 0, automation_run: str | None = None, max_usd: float | None = None) -> str:
        """Short ids (6 hex chars) so players can type them; collisions are retried."""
        with self._lock:
            for _ in range(20):
                rid = uuid.uuid4().hex[:6]
                try:
                    self.db.execute(
                        "INSERT INTO requests(id, player_uuid, player_name, kind, text, context, status, created_at,"
                        " op_level, automation_run, max_usd) VALUES (?,?,?,?,?,?, 'queued', ?, ?, ?, ?)",
                        (rid, player_uuid, player_name, kind, text, json.dumps(context), utcnow(), op_level,
                         automation_run, max_usd))
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

    def mark_queued_again(self, rid: str) -> bool:
        """A job handed to a worker that never received it (its connection had closed) goes back to the queue."""
        with self._lock:
            cur = self.db.execute("UPDATE requests SET status='queued', started_at=NULL WHERE id=? AND status='running'",
                                  (rid,))
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

    def recent_requests(self, n: int = 20, player_uuid: str | None = None, statuses: tuple = ()) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM requests WHERE 1=1", []
        if player_uuid:
            q += " AND player_uuid=?"
            args.append(player_uuid)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args += list(statuses)
        with self._lock:
            return self.db.execute(q + " ORDER BY created_at DESC LIMIT ?", (*args, n)).fetchall()

    def snapshots_to_prune(self, older_than_iso: str) -> list[sqlite3.Row]:
        """Builds whose undo snapshot still exists and is no longer needed: undone, or older than the undo window."""
        with self._lock:
            return self.db.execute(
                "SELECT id, player_uuid, status FROM requests WHERE snapshot='present' AND status NOT IN "
                "('approved','rolling_back') AND (status='rolled_back' OR COALESCE(built_at, decided_at, created_at) < ?)",
                (older_than_iso,)).fetchall()

    def set_snapshot(self, rid: str, state: str) -> None:
        with self._lock:
            self.db.execute("UPDATE requests SET snapshot=? WHERE id=?", (state, rid))

    # ---------- players ----------
    def player(self, uuid_: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM players WHERE uuid=?", (uuid_,)).fetchone()

    def player_by_name(self, name: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM players WHERE name=? COLLATE NOCASE ORDER BY last_seen DESC LIMIT 1",
                                   (name,)).fetchone()

    def players(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute("SELECT * FROM players ORDER BY online DESC, last_seen DESC").fetchall()

    def owner_player(self) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM players WHERE role='owner' LIMIT 1").fetchone()

    def player_seen(self, uuid_: str, name: str, online: bool | None = None) -> tuple[bool, str]:
        """Insert or update a player. Returns (is_new, previous name)."""
        now = utcnow()
        with self._lock:
            row = self.db.execute("SELECT name FROM players WHERE uuid=?", (uuid_,)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO players(uuid, name, first_seen, last_seen, online) VALUES (?,?,?,?,?)",
                                (uuid_, name, now, now, int(bool(online))))
                return True, name
            sets, args = "name=?, last_seen=?", [name, now]
            if online is not None:
                sets += ", online=?"
                args.append(int(online))
            self.db.execute(f"UPDATE players SET {sets} WHERE uuid=?", (*args, uuid_))
            return False, row["name"]

    def set_player_role(self, uuid_: str, role: str) -> None:
        with self._lock:
            self.db.execute("UPDATE players SET role=? WHERE uuid=?", (role, uuid_))

    def session_open(self, uuid_: str, name: str) -> int:
        with self._lock:
            self.db.execute("UPDATE sessions SET left_at=? WHERE player_uuid=? AND left_at IS NULL", (utcnow(), uuid_))
            return self.db.execute("INSERT INTO sessions(player_uuid, name, joined_at) VALUES (?,?,?)",
                                   (uuid_, name, utcnow())).lastrowid

    def session_close(self, uuid_: str) -> int | None:
        """Close the open session; returns its id (None if there was none)."""
        with self._lock:
            row = self.db.execute("SELECT id FROM sessions WHERE player_uuid=? AND left_at IS NULL ORDER BY id DESC"
                                  " LIMIT 1", (uuid_,)).fetchone()
            self.db.execute("UPDATE sessions SET left_at=? WHERE player_uuid=? AND left_at IS NULL", (utcnow(), uuid_))
            self.db.execute("UPDATE players SET online=0 WHERE uuid=?", (uuid_,))
            return row[0] if row else None

    def playtime_hours(self, uuid_: str) -> float:
        now = dt.datetime.now(dt.timezone.utc)
        total = 0.0
        with self._lock:
            rows = self.db.execute("SELECT joined_at, left_at FROM sessions WHERE player_uuid=?", (uuid_,)).fetchall()
        for r in rows:
            a = dt.datetime.fromisoformat(r[0].replace("Z", "+00:00"))
            b = dt.datetime.fromisoformat(r[1].replace("Z", "+00:00")) if r[1] else now
            total += max(0.0, (b - a).total_seconds())
        return total / 3600

    def online_uuids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self.db.execute("SELECT uuid FROM players WHERE online=1")}

    def sessions(self, uuid_: str | None = None, n: int = 50) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM sessions", []
        if uuid_:
            q += " WHERE player_uuid=?"
            args.append(uuid_)
        with self._lock:
            return self.db.execute(q + " ORDER BY joined_at DESC LIMIT ?", (*args, n)).fetchall()

    # ---------- management accounts ----------
    def account(self, name: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()

    def accounts(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT a.*, (SELECT COUNT(*) FROM account_keys k WHERE k.account=a.name AND k.revoked_at IS NULL)"
                " AS keys FROM accounts a ORDER BY name").fetchall()

    def create_account(self, name: str, role: str, by: str) -> bool:
        with self._lock:
            try:
                self.db.execute("INSERT INTO accounts(name, role, created_at, created_by) VALUES (?,?,?,?)",
                                (name, role, utcnow(), by))
                return True
            except sqlite3.IntegrityError:
                return False

    def update_account(self, name: str, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self.db.execute(f"UPDATE accounts SET {cols} WHERE name=?", (*fields.values(), name))

    def add_key(self, account: str, fingerprint: str, public_key: str, comment: str, by: str) -> bool:
        with self._lock:
            row = self.db.execute("SELECT revoked_at FROM account_keys WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row is not None:
                return False             # a key is never reused, even after revocation
            self.db.execute("INSERT INTO account_keys(fingerprint, account, public_key, comment, added_at, added_by)"
                            " VALUES (?,?,?,?,?,?)", (fingerprint, account, public_key, comment, utcnow(), by))
            return True

    def revoke_key(self, fingerprint: str) -> sqlite3.Row | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM account_keys WHERE fingerprint=? AND revoked_at IS NULL",
                                  (fingerprint,)).fetchone()
            if row:
                self.db.execute("UPDATE account_keys SET revoked_at=? WHERE fingerprint=?", (utcnow(), fingerprint))
            return row

    def revoke_all_keys(self, account: str) -> int:
        with self._lock:
            return self.db.execute("UPDATE account_keys SET revoked_at=? WHERE account=? AND revoked_at IS NULL",
                                   (utcnow(), account)).rowcount

    def keys(self, account: str | None = None, active_only: bool = True) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM account_keys WHERE 1=1", []
        if account:
            q += " AND account=?"
            args.append(account)
        if active_only:
            q += " AND revoked_at IS NULL"
        with self._lock:
            return self.db.execute(q + " ORDER BY added_at", args).fetchall()

    def key_owner(self, fingerprint: str) -> sqlite3.Row | None:
        """The enabled account an active key belongs to, or None."""
        with self._lock:
            return self.db.execute(
                "SELECT a.* FROM account_keys k JOIN accounts a ON a.name=k.account WHERE k.fingerprint=?"
                " AND k.revoked_at IS NULL AND a.disabled=0", (fingerprint,)).fetchone()

    def active_keys(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT k.fingerprint, k.public_key, k.account FROM account_keys k JOIN accounts a ON a.name=k.account"
                " WHERE k.revoked_at IS NULL AND a.disabled=0 ORDER BY k.added_at").fetchall()

    def new_link_code(self, account: str, ttl_s: int = 600) -> str:
        code = uuid.uuid4().hex[:8]
        with self._lock:
            self.db.execute("DELETE FROM link_codes WHERE account=? OR expires_at < ?", (account, time.time()))
            self.db.execute("INSERT INTO link_codes(code, account, expires_at) VALUES (?,?,?)",
                            (code, account, time.time() + ttl_s))
        return code

    def use_link_code(self, code: str) -> str | None:
        with self._lock:
            row = self.db.execute("SELECT account, expires_at FROM link_codes WHERE code=?", (code,)).fetchone()
            self.db.execute("DELETE FROM link_codes WHERE code=?", (code,))
            return row["account"] if row and row["expires_at"] >= time.time() else None

    # ---------- runtime settings (changed through the management API) ----------
    def setting(self, key: str) -> str | None:
        with self._lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def set_setting(self, key: str, value: str | None, by: str) -> None:
        with self._lock:
            if value is None:
                self.db.execute("DELETE FROM settings WHERE key=?", (key,))
            else:
                self.db.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at,"
                                " updated_by=excluded.updated_by", (key, value, utcnow(), by))

    # ---------- automations ----------
    def automations(self, states: tuple | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM automations", []
        if states:
            q += f" WHERE state IN ({','.join('?' * len(states))})"
            args = list(states)
        with self._lock:
            return self.db.execute(q + " ORDER BY id", args).fetchall()

    def automation(self, aid: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()

    def automation_upsert(self, aid: str, by: str, at: str, **fields) -> int:
        """Insert (version 1) or replace the definition (version + 1). Returns the version."""
        with self._lock:
            row = self.db.execute("SELECT version FROM automations WHERE id=?", (aid,)).fetchone()
            if row is None:
                cols = ["id", "created_at", "created_by", "version", *fields]
                self.db.execute(f"INSERT INTO automations({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                (aid, at, by, 1, *fields.values()))
                return 1
            version = row[0] + 1
            sets = ", ".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE automations SET {sets}, version=? WHERE id=?", (*fields.values(), version, aid))
            return version

    def automation_update(self, aid: str, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self.db.execute(f"UPDATE automations SET {sets} WHERE id=?", (*fields.values(), aid))

    def run_create(self, aid: str, version: int, key: str, trigger: str, subject: str, event: dict, chain: list,
                   created: float, status: str = "queued", error: str | None = None) -> str | None:
        """None if a run with this idempotency key already exists (duplicate event / slot)."""
        rid = uuid.uuid4().hex[:10]
        with self._lock:
            try:
                self.db.execute(
                    "INSERT INTO automation_runs(id, automation_id, version, idem_key, trigger, subject, event, chain,"
                    " status, created, finished, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, aid, version, key[:200], trigger, subject, json.dumps(event)[:4000], json.dumps(chain),
                     status, created, created if status != "queued" else None, error))
            except sqlite3.IntegrityError:
                return None
        return rid

    def run(self, rid: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM automation_runs WHERE id=?", (rid,)).fetchone()

    def run_update(self, rid: str, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self.db.execute(f"UPDATE automation_runs SET {sets} WHERE id=?", (*fields.values(), rid))

    def run_claim(self, rid: str, now: float) -> bool:
        with self._lock:
            return self.db.execute("UPDATE automation_runs SET status='running', started=COALESCE(started, ?) "
                                   "WHERE id=? AND status IN ('queued','retry_wait')", (now, rid)).rowcount == 1

    def runs_due(self, now: float) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute("SELECT id FROM automation_runs WHERE status='queued' OR (status='retry_wait' AND "
                                   "next_attempt <= ?) ORDER BY created LIMIT 50", (now,)).fetchall()

    def runs_with_status(self, statuses: tuple) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(f"SELECT * FROM automation_runs WHERE status IN ({','.join('?' * len(statuses))})",
                                   statuses).fetchall()

    def runs_since(self, aid: str, since: float) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM automation_runs WHERE automation_id=? AND created >= ? AND "
                                   "status != 'skipped'", (aid, since)).fetchone()[0]

    def last_run_at(self, aid: str, subject: str) -> float:
        with self._lock:
            row = self.db.execute("SELECT MAX(created) FROM automation_runs WHERE automation_id=? AND subject=? AND "
                                  "status != 'skipped'", (aid, subject)).fetchone()
        return row[0] or 0.0

    def runs_list(self, aid: str | None = None, n: int = 20) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM automation_runs", []
        if aid:
            q += " WHERE automation_id=?"
            args.append(aid)
        with self._lock:
            return self.db.execute(q + " ORDER BY created DESC, rowid DESC LIMIT ?", (*args, n)).fetchall()

    def automation_ai_usage(self, aid: str) -> tuple[float, int]:
        """AI cost (USD) and tokens used today (UTC) by one automation's runs."""
        with self._lock:
            row = self.db.execute(
                "SELECT COALESCE(SUM(r.cost_usd),0), COALESCE(SUM(r.input_tokens + r.output_tokens),0) FROM requests r "
                "JOIN automation_runs a ON a.id = r.automation_run WHERE a.automation_id=? AND r.created_at >= ?",
                (aid, utc_day_start())).fetchone()
        return float(row[0]), int(row[1])

    def automation_ai_calls(self, seconds: int) -> int:
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM requests WHERE automation_run IS NOT NULL AND created_at >= ?",
                                   (since,)).fetchone()[0]

    # ---------- example packs and installs (ADR-0012) ----------
    def example_pack(self, pid: str, version: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM example_packs WHERE id=? AND version=?", (pid, version)).fetchone()

    def example_packs(self, pid: str | None = None) -> list[sqlite3.Row]:
        if pid:
            return self.db.execute("SELECT * FROM example_packs WHERE id=? ORDER BY created_at DESC", (pid,)).fetchall()
        return self.db.execute("SELECT * FROM example_packs ORDER BY id, created_at DESC").fetchall()

    def example_pack_put(self, pid: str, version: str, by: str, **fields) -> None:
        now = utcnow()
        with self._lock:
            if self.example_pack(pid, version) is None:
                self.db.execute("INSERT INTO example_packs (id, version, status, manifest, created_at, created_by, "
                                "updated_at) VALUES (?,?,?,?,?,?,?)", (pid, version, "uploading", "{}", now, by, now))
            if fields:
                cols = ", ".join(f"{k}=?" for k in fields)
                self.db.execute(f"UPDATE example_packs SET {cols}, updated_at=? WHERE id=? AND version=?",
                                (*fields.values(), now, pid, version))

    def example_pack_delete(self, pid: str, version: str) -> None:
        self.db.execute("DELETE FROM example_packs WHERE id=? AND version=?", (pid, version))

    def example_install(self, iid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM example_installs WHERE id=?", (iid,)).fetchone()

    def example_installs(self, statuses: tuple = (), pack: str | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM example_installs WHERE 1=1", []
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args += list(statuses)
        if pack:
            q += " AND pack=?"
            args.append(pack)
        return self.db.execute(q + " ORDER BY created_at DESC", args).fetchall()

    def example_install_create(self, iid: str, pack: str, version: str, dimension: str, anchor: list, bbox: dict,
                               by: str) -> None:
        now = utcnow()
        self.db.execute("INSERT INTO example_installs (id, pack, version, dimension, anchor, bbox, status, created_at, "
                        "created_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (iid, pack, version, dimension, json.dumps(anchor), json.dumps(bbox), "queued", now, by, now))

    def example_install_update(self, iid: str, from_status: tuple | None = None, **fields) -> bool:
        cols = ", ".join(f"{k}=?" for k in fields)
        q, args = f"UPDATE example_installs SET {cols}, updated_at=? WHERE id=?", [*fields.values(), utcnow(), iid]
        if from_status:
            q += f" AND status IN ({','.join('?' * len(from_status))})"
            args += list(from_status)
        with self._lock:
            return self.db.execute(q, args).rowcount == 1
