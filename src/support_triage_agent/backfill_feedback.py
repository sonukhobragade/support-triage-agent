"""Backfill the feedback table with (customer email -> our real reply) pairs.

No Claude. Walks the extract, links each support reply to the customer email it
answers (via In-Reply-To / References threading), and stores the pair as a
feedback row: agent_draft is empty (there was no agent — this is historical
ground truth), human_reply is our actual cleaned + PII-redacted reply.

This gives the real dataset for grounding/training: how the team actually
answered each kind of email. Rerunnable — clears prior backfill rows first.

Usage:
  python -m support_triage_agent.backfill_feedback
"""
from __future__ import annotations

import argparse

from . import store
from .pairs import customer_reply_pairs, parent_id as _parent_id  # noqa: F401 (re-export for tests)


def run(input_path: str, min_len: int) -> int:
    conn = store.init_db()
    # Clear prior backfill rows (agent_draft = '') so reruns don't duplicate.
    conn.execute("DELETE FROM feedback WHERE agent_draft = ''")
    conn.commit()

    pairs = customer_reply_pairs(input_path, min_len=min_len)
    for parent, human in pairs:
        store.record_feedback(conn, parent.message_id, agent_draft="", human_reply=human)
    paired = len(pairs)

    total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    conn.close()
    print(f"Backfilled {paired} (customer email -> real reply) pairs. "
          f"feedback table now holds {total} rows.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill feedback table from email threads (no LLM)")
    ap.add_argument("--input", default="data/mail_extract.jsonl.gz",
                    help="path to the jsonl(.gz) mail extract")
    ap.add_argument("--min-len", type=int, default=20,
                    help="skip cleaned replies shorter than this (chars)")
    args = ap.parse_args()
    return run(args.input, min_len=args.min_len)


if __name__ == "__main__":
    raise SystemExit(main())
