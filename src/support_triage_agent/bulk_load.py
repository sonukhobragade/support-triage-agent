"""Bulk-load inbound customer emails into the `emails` table — NO Claude, no cost.

Stores every inbound customer email with its regex-extracted identifiers and a
keyword-guessed category. This is the cheap backlog: queryable storage, no
drafted replies and no LLM triage. Run the pipeline later (cache-backed) to add
real classification + drafts for any subset you choose.

Usage:
  python -m support_triage_agent.bulk_load                       # load all inbound customers
  python -m support_triage_agent.bulk_load --limit 200
"""
from __future__ import annotations

import argparse

from . import store
from .build_reply_library import _bucket  # reuse the keyword bucketer
from .ingest import inbound_customer_emails


def run(input_path: str, limit: int | None, reset: bool = False) -> int:
    emails = inbound_customer_emails(input_path, limit=limit)
    if not emails:
        print(f"No inbound customer emails found in {input_path}.")
        return 0

    conn = store.init_db()
    if reset:
        # Stitched threads key on the latest message_id, so old per-message rows
        # would linger on a plain reload — clear first for a clean refresh.
        conn.execute("DELETE FROM emails")
        conn.commit()
    for e in emails:
        category = _bucket(f"{e.subject}\n{e.body}")
        store.upsert_email(conn, e, category)
    total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    conn.close()
    print(f"Loaded {len(emails)} emails (no Claude). `emails` table now holds {total} rows.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Bulk-load emails into sqlite (no LLM)")
    ap.add_argument("--input", default="data/mail_extract.jsonl.gz",
                    help="path to the jsonl(.gz) mail extract")
    ap.add_argument("--limit", type=int, default=None, help="max emails to load")
    ap.add_argument("--reset", action="store_true",
                    help="clear the emails table before loading (clean refresh)")
    args = ap.parse_args()
    return run(args.input, limit=args.limit, reset=args.reset)


if __name__ == "__main__":
    raise SystemExit(main())
