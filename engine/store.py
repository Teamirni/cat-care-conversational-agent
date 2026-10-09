# -*- coding: utf-8 -*-
"""SQLite 持久化（§3）。线程安全、WAL、进程重启后按 sessionId 完整恢复。

候选生命周期：candidate → confirmed | deleted（SKILL.md §后台状态与记忆）
"""
from __future__ import annotations

import itertools
import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config

_LOCAL = threading.local()
_WRITE_LOCK = threading.Lock()
_SEQ = itertools.count()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    welcomed    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS session_profile (
    session_id  TEXT NOT NULL,
    field       TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, field)
);
CREATE TABLE IF NOT EXISTS profile_candidates (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL,
    field                 TEXT NOT NULL,
    value                 TEXT NOT NULL,
    reason                TEXT NOT NULL DEFAULT '',
    confidence            TEXT NOT NULL DEFAULT 'medium',
    requires_confirmation INTEGER NOT NULL DEFAULT 1,
    status                TEXT NOT NULL DEFAULT 'candidate',
    created_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    category    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'candidate',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS device_events (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    type         TEXT NOT NULL,
    value        TEXT NOT NULL,
    unit         TEXT,
    observed_at  TEXT NOT NULL,
    quality      TEXT NOT NULL DEFAULT 'measured',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    instruction TEXT NOT NULL DEFAULT '',
    schedule    TEXT NOT NULL,
    timezone    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    next_run_at TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    message_id      INTEGER,
    trace_id        TEXT NOT NULL,
    attempt         INTEGER NOT NULL,
    generator       TEXT NOT NULL DEFAULT 'rule',
    retrieve_level  TEXT NOT NULL DEFAULT 'R0',
    evidence_level  TEXT NOT NULL DEFAULT 'E0',
    assert_level    TEXT NOT NULL DEFAULT 'A0',
    kb_hits         TEXT NOT NULL DEFAULT '[]',
    evidence_ids    TEXT NOT NULL DEFAULT '[]',
    issues          TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'rejected',
    elapsed_ms      REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_session ON profile_candidates(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_session    ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_session    ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_review_trace        ON review_log(session_id, trace_id, attempt);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _next_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{next(_SEQ)}"


def connect() -> sqlite3.Connection:
    """每线程一个连接（threading.local），避免跨线程使用 sqlite3 对象。"""
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(config.DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        _LOCAL.conn = conn
    return conn


def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def _query(sql: str, params=()) -> list[dict]:
    return _rows(connect().execute(sql, params))


def _write(sql: str, params=()):
    with _WRITE_LOCK:
        conn = connect()
        conn.execute(sql, params)
        conn.commit()


def _write_many(statements: list[tuple[str, tuple]]):
    with _WRITE_LOCK:
        conn = connect()
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()


# ---------------------------------------------------------------- session
def ensure_session(session_id: str) -> dict:
    with _WRITE_LOCK:
        conn = connect()
        conn.execute("INSERT OR IGNORE INTO sessions(session_id, created_at, welcomed) VALUES (?, ?, 0)",
                     (session_id, _now()))
        conn.commit()
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return dict(row)


def is_welcomed(session_id: str) -> bool:
    rows = _query("SELECT welcomed FROM sessions WHERE session_id = ?", (session_id,))
    return bool(rows and rows[0]["welcomed"])


def mark_welcomed(session_id: str):
    ensure_session(session_id)
    _write("UPDATE sessions SET welcomed = 1 WHERE session_id = ?", (session_id,))


def reset_session(session_id: str):
    _write_many([
        ("DELETE FROM session_profile WHERE session_id = ?", (session_id,)),
        ("DELETE FROM profile_candidates WHERE session_id = ?", (session_id,)),
        ("DELETE FROM memories WHERE session_id = ?", (session_id,)),
        ("DELETE FROM device_events WHERE session_id = ?", (session_id,)),
        ("DELETE FROM reminders WHERE session_id = ?", (session_id,)),
        ("DELETE FROM messages WHERE session_id = ?", (session_id,)),
        ("DELETE FROM review_log WHERE session_id = ?", (session_id,)),
        ("DELETE FROM sessions WHERE session_id = ?", (session_id,)),
    ])


# ---------------------------------------------------------------- profile
def get_profile(session_id: str) -> dict:
    rows = _query("SELECT field, value FROM session_profile WHERE session_id = ?", (session_id,))
    return {row["field"]: row["value"] for row in rows}


def save_profile(session_id: str, profile: dict):
    now = _now()
    _write_many([
        ("INSERT INTO session_profile(session_id, field, value, updated_at) VALUES (?, ?, ?, ?) "
         "ON CONFLICT(session_id, field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
         (session_id, field, str(value), now))
        for field, value in profile.items() if value
    ])


# ---------------------------------------------------------------- profile candidates
def upsert_profile_candidate(session_id: str, candidate: dict) -> str:
    """同一 session + field + value 只保留一条候选，避免重复堆积（routers.ts L266-273）。"""
    rows = _query(
        "SELECT id, status FROM profile_candidates "
        "WHERE session_id = ? AND field = ? AND value = ? AND status != 'deleted'",
        (session_id, candidate["field"], candidate["value"]),
    )
    if rows:
        return rows[0]["id"]
    candidate_id = _next_id("profile-candidate")
    _write(
        "INSERT INTO profile_candidates(id, session_id, field, value, reason, confidence, "
        "requires_confirmation, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?)",
        (candidate_id, session_id, candidate["field"], candidate["value"],
         candidate.get("reason", ""), candidate.get("confidence", "medium"),
         1 if candidate.get("requires_confirmation", True) else 0, _now()),
    )
    return candidate_id


def list_profile_candidates(session_id: str) -> list[dict]:
    rows = _query(
        "SELECT * FROM profile_candidates WHERE session_id = ? AND status != 'deleted' ORDER BY created_at, id",
        (session_id,),
    )
    for row in rows:
        row["requires_confirmation"] = bool(row["requires_confirmation"])
    return rows


def _candidate_by_id(session_id: str, candidate_id: str) -> dict | None:
    rows = _query("SELECT * FROM profile_candidates WHERE session_id = ? AND id = ?", (session_id, candidate_id))
    return rows[0] if rows else None


def confirm_profile_candidate(session_id: str, candidate_id: str):
    """确认候选 → 写入确认档案（SKILL.md 原则 6：确认前不得写入长期上下文）。"""
    candidate = _candidate_by_id(session_id, candidate_id)
    if not candidate:
        raise KeyError(candidate_id)
    _write_many([
        ("UPDATE profile_candidates SET status = 'confirmed' WHERE id = ?", (candidate_id,)),
        ("INSERT INTO session_profile(session_id, field, value, updated_at) VALUES (?, ?, ?, ?) "
         "ON CONFLICT(session_id, field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
         (session_id, candidate["field"], candidate["value"], _now())),
    ])
    return _candidate_by_id(session_id, candidate_id)


def correct_profile_candidate(session_id: str, candidate_id: str, value: str):
    """纠正 → 保存用户修正值（SKILL.md §后台状态与记忆）。"""
    if not _candidate_by_id(session_id, candidate_id):
        raise KeyError(candidate_id)
    value = value.strip()
    _write_many([
        ("UPDATE profile_candidates SET value = ?, status = 'confirmed' WHERE id = ?", (value, candidate_id)),
        ("INSERT INTO session_profile(session_id, field, value, updated_at) VALUES (?, ?, ?, ?) "
         "ON CONFLICT(session_id, field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
         (session_id, _candidate_by_id(session_id, candidate_id)["field"], value, _now())),
    ])
    return _candidate_by_id(session_id, candidate_id)


def remove_profile_candidate(session_id: str, candidate_id: str):
    if not _candidate_by_id(session_id, candidate_id):
        raise KeyError(candidate_id)
    _write("UPDATE profile_candidates SET status = 'deleted' WHERE id = ?", (candidate_id,))
    return {"success": True}


# ---------------------------------------------------------------- memories
def propose_memory(session_id: str, content: str, category: str) -> dict:
    rows = _query("SELECT * FROM memories WHERE session_id = ? AND content = ? AND status != 'deleted'",
                  (session_id, content))
    if rows:
        return rows[0]
    memory_id = _next_id("memory")
    _write("INSERT INTO memories(id, session_id, content, category, status, created_at) VALUES (?, ?, ?, ?, 'candidate', ?)",
           (memory_id, session_id, content, category, _now()))
    return {"id": memory_id, "session_id": session_id, "content": content, "category": category, "status": "candidate"}


def list_memories(session_id: str) -> list[dict]:
    return _query("SELECT id, content, category, status FROM memories "
                  "WHERE session_id = ? AND status != 'deleted' ORDER BY created_at, id", (session_id,))


def confirm_memory(session_id: str, memory_id: str):
    rows = _query("SELECT * FROM memories WHERE session_id = ? AND id = ?", (session_id, memory_id))
    if not rows:
        raise KeyError(memory_id)
    _write("UPDATE memories SET status = 'confirmed' WHERE id = ?", (memory_id,))
    return {"id": memory_id, "status": "confirmed"}


def remove_memory(session_id: str, memory_id: str):
    rows = _query("SELECT * FROM memories WHERE session_id = ? AND id = ?", (session_id, memory_id))
    if not rows:
        raise KeyError(memory_id)
    _write("UPDATE memories SET status = 'deleted' WHERE id = ?", (memory_id,))
    return {"success": True}


# ---------------------------------------------------------------- devices
def ingest_device_event(session_id: str, event: dict) -> dict:
    event_id = _next_id("device")
    _write("INSERT INTO device_events(id, session_id, type, value, unit, observed_at, quality, created_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
           (event_id, session_id, event["type"], event["value"], event.get("unit"),
            event.get("observedAt", ""), event.get("quality", "measured"), _now()))
    return {**event, "id": event_id}


def list_device_events(session_id: str, limit: int = 20) -> list[dict]:
    rows = _query("SELECT id, type, value, unit, observed_at, quality FROM device_events "
                  "WHERE session_id = ? ORDER BY created_at DESC, id DESC LIMIT ?", (session_id, limit))
    rows.reverse()
    for row in rows:
        row["observedAt"] = row.pop("observed_at")
    return rows


# ---------------------------------------------------------------- reminders
def create_reminder(session_id: str, reminder: dict) -> dict:
    reminder_id = _next_id("reminder")
    _write("INSERT INTO reminders(id, session_id, type, title, instruction, schedule, timezone, status, next_run_at, created_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
           (reminder_id, session_id, reminder.get("type", "observation"), reminder["title"],
            reminder.get("instruction", ""), reminder["schedule"], reminder.get("timezone", config.DEFAULT_TIMEZONE),
            "active", reminder.get("nextRunAt"), _now()))
    return {**reminder, "id": reminder_id, "status": "active"}


def list_reminders(session_id: str) -> list[dict]:
    return _query("SELECT id, type, title, instruction, schedule, timezone, status, next_run_at FROM reminders "
                  "WHERE session_id = ? AND status != 'deleted' ORDER BY created_at, id", (session_id,))


def set_reminder_status(session_id: str, reminder_id: str, status: str):
    rows = _query("SELECT * FROM reminders WHERE session_id = ? AND id = ?", (session_id, reminder_id))
    if not rows:
        raise KeyError(reminder_id)
    _write("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
    return {"id": reminder_id, "status": status}


# ---------------------------------------------------------------- messages
def append_message(session_id: str, role: str, content: str) -> int:
    with _WRITE_LOCK:
        conn = connect()
        cursor = conn.execute("INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                              (session_id, role, content, _now()))
        conn.commit()
        return int(cursor.lastrowid)


def list_messages(session_id: str, limit: int = 200) -> list[dict]:
    rows = _query("SELECT id, role, content, created_at FROM messages WHERE session_id = ? "
                  "ORDER BY id DESC LIMIT ?", (session_id, limit))
    rows.reverse()
    return rows


# ---------------------------------------------------------------- review_log
def log_review(session_id: str, trace_id: str, record: dict, message_id: int | None = None):
    _write("INSERT INTO review_log(session_id, message_id, trace_id, attempt, generator, retrieve_level, "
           "evidence_level, assert_level, kb_hits, evidence_ids, issues, status, elapsed_ms, created_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
           (session_id, message_id, trace_id, record["attempt"], record.get("generator", "rule"),
            record["retrieve"], record["evidence"], record["assert"],
            json.dumps(record.get("kb", []), ensure_ascii=False),
            json.dumps(record.get("evidence_ids", []), ensure_ascii=False),
            json.dumps(record.get("issues", []), ensure_ascii=False),
            record.get("status", "rejected"), record.get("elapsed_ms", 0), _now()))


def list_review_log(session_id: str, trace_id: str) -> list[dict]:
    rows = _query("SELECT attempt, generator, retrieve_level, evidence_level, assert_level, "
                  "kb_hits, evidence_ids, issues, status, elapsed_ms FROM review_log "
                  "WHERE session_id = ? AND trace_id = ? ORDER BY attempt", (session_id, trace_id))
    for row in rows:
        row["kb"] = json.loads(row.pop("kb_hits"))
        row["evidence_ids"] = json.loads(row.pop("evidence_ids"))
        row["issues"] = json.loads(row.pop("issues"))
    return rows


def list_review_by_trace(trace_id: str) -> list[dict]:
    """按 trace_id 反查（debug 面板用）。"""
    rows = _query("SELECT session_id, attempt, generator, retrieve_level, evidence_level, assert_level, "
                  "kb_hits, evidence_ids, issues, status, elapsed_ms, created_at FROM review_log "
                  "WHERE trace_id = ? ORDER BY attempt", (trace_id,))
    for row in rows:
        row["kb"] = json.loads(row.pop("kb_hits"))
        row["evidence_ids"] = json.loads(row.pop("evidence_ids"))
        row["issues"] = json.loads(row.pop("issues"))
    return rows
