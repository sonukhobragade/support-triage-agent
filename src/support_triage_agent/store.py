"""SQLite persistence: dedupe, classify/draft cache, and human-reply feedback.

Stdlib sqlite3 only. One file (data/tickets.db by default, override via TICKETS_DB).

Tables
------
processed : one row per processed email, keyed on message_id. Holds the
            classify + draft + guardrail output (the cache) and, once a Notion
            page is created, its id/url. Row exists == email already processed.
feedback  : agent draft vs the human's final sent reply, captured later for
            grounding/training data. Separate table — arrives via a different
            path than the pipeline.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .classify import Classification
from .draft import Draft
from .guardrails import GuardResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
    message_id      TEXT PRIMARY KEY,
    processed_at    TEXT NOT NULL,
    category        TEXT,
    sop_id          TEXT,
    priority        TEXT,
    sentiment       TEXT,
    summary         TEXT,
    identifiers     TEXT,
    draft_reply     TEXT,
    info_to_collect TEXT,
    notes           TEXT,
    needs_human     INTEGER NOT NULL DEFAULT 0,
    suppress_draft  INTEGER NOT NULL DEFAULT 0,
    guard_reasons   TEXT,
    notion_page_id  TEXT,
    notion_url      TEXT,
    slack_posted_at TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL,
    agent_draft  TEXT NOT NULL,
    human_reply  TEXT NOT NULL,
    recorded_at  TEXT NOT NULL
);

-- Raw inbound customer emails, loaded WITHOUT any LLM call. Regex identifiers
-- and a keyword-guessed category only. Cheap, queryable backlog; separate from
-- `processed` (which holds Claude triage output).
CREATE TABLE IF NOT EXISTS emails (
    message_id   TEXT PRIMARY KEY,
    date         TEXT,
    sender       TEXT,
    subject      TEXT,
    body         TEXT,
    category     TEXT,        -- keyword guess, not Claude
    identifiers  TEXT,        -- JSON
    loaded_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the tickets db and ensure the schema exists."""
    p = Path(path) if path is not None else config.TICKETS_DB_PATH
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Migrate older dbs created before slack_posted_at existed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(processed)")}
    if "slack_posted_at" not in cols:
        conn.execute("ALTER TABLE processed ADD COLUMN slack_posted_at TEXT")
    if "suppress_draft" not in cols:
        # Older rows predate the column. They default to 0, which is the
        # safe direction: a ticket is re-drafted rather than silently
        # losing the "no draft may be shown" verdict.
        conn.execute(
            "ALTER TABLE processed ADD COLUMN suppress_draft "
            "INTEGER NOT NULL DEFAULT 0"
        )
    conn.commit()
    return conn


def get(conn: sqlite3.Connection, message_id: str) -> dict | None:
    """Return the cached row for message_id as a dict, or None if not processed.

    The dict carries reconstructed `classification` and `draft` objects plus the
    raw needs_human / notion fields, so callers can both reuse the cache and
    decide whether a Notion write is still owed.
    """
    if not message_id:
        return None
    row = conn.execute(
        "SELECT * FROM processed WHERE message_id = ?", (message_id,)
    ).fetchone()
    if row is None:
        return None
    cls = Classification(
        category=row["category"] or "",
        sop_id=row["sop_id"] or config.NO_SOP,
        priority=row["priority"] or "",
        sentiment=row["sentiment"] or "neutral",
        summary=row["summary"] or "",
        identifiers=json.loads(row["identifiers"] or "{}"),
    )
    draft = Draft(
        reply=row["draft_reply"] or "",
        info_to_collect=json.loads(row["info_to_collect"] or "[]"),
        notes=row["notes"] or "",
    )
    return {
        "message_id": row["message_id"],
        "classification": cls,
        "draft": draft,
        "needs_human": bool(row["needs_human"]),
        "suppress_draft": bool(row["suppress_draft"]),
        "guard_reasons": json.loads(row["guard_reasons"] or "[]"),
        "notion_page_id": row["notion_page_id"],
        "notion_url": row["notion_url"],
    }


def upsert(
    conn: sqlite3.Connection,
    message_id: str,
    cls: Classification,
    draft: Draft,
    guard: GuardResult,
) -> None:
    """Insert or replace the cached classify+draft+guard output for an email.

    Preserves any existing notion_page_id/url (a re-cache shouldn't forget that
    a ticket was already written).
    """
    if not message_id:
        return
    # Carry slack_posted_at forward as well as the Notion fields. INSERT OR
    # REPLACE writes a whole new row, so any column omitted here reverts to
    # its default — and dropping slack_posted_at made an already-notified
    # conversation look unposted, so re-running notified the channel twice.
    prev = conn.execute(
        "SELECT notion_page_id, notion_url, slack_posted_at "
        "FROM processed WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    page_id = prev["notion_page_id"] if prev else None
    url = prev["notion_url"] if prev else None
    slack_at = prev["slack_posted_at"] if prev else None
    conn.execute(
        """
        INSERT OR REPLACE INTO processed (
            message_id, processed_at, category, sop_id, priority, sentiment,
            summary, identifiers, draft_reply, info_to_collect, notes,
            needs_human, suppress_draft, guard_reasons, notion_page_id,
            notion_url, slack_posted_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id, _now(), cls.category, cls.sop_id, cls.priority,
            cls.sentiment, cls.summary, json.dumps(cls.identifiers),
            draft.reply, json.dumps(draft.info_to_collect), draft.notes,
            int(guard.needs_human), int(guard.suppress_draft),
            json.dumps(guard.reasons), page_id, url, slack_at,
        ),
    )
    conn.commit()


def mark_written(
    conn: sqlite3.Connection, message_id: str, page_id: str, url: str
) -> None:
    """Record that a Notion ticket was created for this email (idempotency)."""
    conn.execute(
        "UPDATE processed SET notion_page_id = ?, notion_url = ? WHERE message_id = ?",
        (page_id, url, message_id),
    )
    conn.commit()


def slack_posted(conn: sqlite3.Connection, message_id: str) -> bool:
    """True if this email was already posted to Slack (dedupe across polls)."""
    if not message_id:
        return False
    row = conn.execute(
        "SELECT slack_posted_at FROM processed WHERE message_id = ?", (message_id,)
    ).fetchone()
    return bool(row and row["slack_posted_at"])


def mark_slack_posted(conn: sqlite3.Connection, message_id: str) -> None:
    """Record that this email's ticket was posted to Slack."""
    if not message_id:
        return
    conn.execute(
        "UPDATE processed SET slack_posted_at = ? WHERE message_id = ?",
        (_now(), message_id),
    )
    conn.commit()


def upsert_email(conn: sqlite3.Connection, email, category: str) -> None:
    """Store a raw customer email + regex identifiers + keyword category (no LLM)."""
    if not email.message_id:
        return
    # UPSERT, not INSERT OR REPLACE: REPLACE deletes the old row and inserts a
    # fresh one, so any column not named here (sub_category) is silently reset
    # to NULL on every re-load. Update the named columns in place instead.
    conn.execute(
        "INSERT INTO emails "
        "(message_id, date, sender, subject, body, category, identifiers, loaded_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(message_id) DO UPDATE SET "
        "date=excluded.date, sender=excluded.sender, subject=excluded.subject, "
        "body=excluded.body, category=excluded.category, "
        "identifiers=excluded.identifiers, loaded_at=excluded.loaded_at",
        (email.message_id, email.date, email.sender, email.subject, email.body,
         category, json.dumps(email.identifiers), _now()),
    )
    conn.commit()


def record_feedback(
    conn: sqlite3.Connection, message_id: str, agent_draft: str, human_reply: str
) -> None:
    """Store the agent draft alongside the human's final reply (training data)."""
    conn.execute(
        "INSERT INTO feedback (message_id, agent_draft, human_reply, recorded_at) "
        "VALUES (?,?,?,?)",
        (message_id, agent_draft, human_reply, _now()),
    )
    conn.commit()
