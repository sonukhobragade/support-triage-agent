"""Backfill the `emails` table from live IMAP — NO Claude, no cost, never sends.

The mbox extract is a point-in-time Gmail Takeout snapshot, so `emails` goes
stale the moment it is built. This pulls everything since a given date straight
from the support inbox (read-only, BODY.PEEK — read/unread state untouched) and
upserts it with the same keyword category + regex identifiers `bulk_load` uses.

Usage:
  python -m support_triage_agent.backfill_imap --since-days 40 --dry-run
  python -m support_triage_agent.backfill_imap --since-days 40
"""
from __future__ import annotations

import argparse

from . import store
from .build_reply_library import _bucket
from .categorize import subcategorize
from .gmail_fetch import fetch_conversations


def run(since_days: int, dry_run: bool = False) -> int:
    convos = fetch_conversations(since_days=since_days)
    if not convos:
        print(f"No customer conversations in the last {since_days} days.")
        return 0

    conn = store.init_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(emails)").fetchall()]
    if "sub_category" not in cols:
        conn.execute("ALTER TABLE emails ADD COLUMN sub_category TEXT")

    known = {r[0] for r in conn.execute("SELECT message_id FROM emails")}
    new = [e for e in convos if e.message_id and e.message_id not in known]

    print(f"Fetched {len(convos)} conversations, {len(new)} new.")
    if dry_run:
        for e in new[:10]:
            print(f"  {e.date[:16]:<18} {subcategorize(e.subject + chr(10) + e.body):<40} {e.subject[:40]}")
        print("\nDry run — nothing written.")
        conn.close()
        return 0

    for e in new:
        text = f"{e.subject}\n{e.body}"
        store.upsert_email(conn, e, _bucket(text))
        # upsert_email's INSERT OR REPLACE does not carry sub_category — set it here.
        conn.execute("UPDATE emails SET sub_category=? WHERE message_id=?",
                     (subcategorize(text), e.message_id))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    conn.close()
    print(f"Inserted {len(new)} emails. `emails` table now holds {total} rows.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill emails table from IMAP (no LLM)")
    ap.add_argument("--since-days", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true", help="show what would be inserted")
    args = ap.parse_args()
    return run(args.since_days, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
