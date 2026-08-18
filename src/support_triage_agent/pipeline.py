"""Orchestrate the v0 pipeline. Dry-run by default; --write touches Notion."""
from __future__ import annotations

import argparse
import sys

from . import llm, slack_notify, store
from .classify import classify
from .draft import Draft, draft_reply
from .guardrails import GuardResult, check, redact_pii, suppression_reasons
from .ingest import inbound_customer_emails
from .notion_writer import Ticket, create_ticket


def build_ticket(email, conn=None, use_cache: bool = True) -> tuple[Ticket, bool]:
    """Build a Ticket. Returns (ticket, cache_hit).

    When `conn` is given and `use_cache` is True, a previously processed email
    (by message_id) is rebuilt from the db — no Claude calls. Otherwise classify
    + draft run and the result is cached.
    """
    if conn is not None and use_cache:
        cached = store.get(conn, email.message_id)
        if cached is not None:
            # suppress_draft must be restored too. Without it a cached
            # legal or self-harm ticket came back looking ordinary and was
            # printed under a "--- draft ---" heading, with the withheld
            # banner gone.
            guard = GuardResult(
                needs_human=cached["needs_human"],
                reasons=cached["guard_reasons"],
                suppress_draft=cached["suppress_draft"],
            )
            ticket = Ticket(
                email=email, cls=cached["classification"],
                draft=cached["draft"], guard=guard,
            )
            return ticket, True

    cls = classify(email)
    source_text = f"{email.subject}\n{email.body}"

    # Withhold the draft rather than merely flagging it. A plausible draft on a
    # legal, regulatory or self-harm message anchors the reviewer on wording
    # they should be choosing themselves, which is worse than handing them the
    # email with nothing attached.
    #
    # Decide that BEFORE drafting. Suppression depends only on what the customer
    # wrote, so generating a reply and then discarding it buys nothing: it costs
    # a model call and it puts text on the wire that nobody should ever read.
    if suppression_reasons(source_text):
        draft = Draft(reply="", info_to_collect=[], notes="")
    else:
        draft = draft_reply(email, cls)

    guard = check(cls, draft, source_text=source_text)
    if guard.suppress_draft:
        draft = Draft(
            reply="",
            info_to_collect=[],
            notes="Draft suppressed by guardrails: " + "; ".join(guard.reasons),
        )
    if conn is not None:
        store.upsert(conn, email.message_id, cls, draft, guard)
    return Ticket(email=email, cls=cls, draft=draft, guard=guard), False


def _print_ticket(t: Ticket, idx: int, cache_hit: bool = False) -> None:
    tag = "  [cached]" if cache_hit else ""
    print(f"\n{'='*70}\n#{idx}  {t.title()}{tag}")
    print(f"  category={t.cls.category} | sop={t.cls.sop_id} | "
          f"priority={t.cls.priority} | sentiment={t.cls.sentiment}")
    if t.guard.needs_human:
        print(f"  NEEDS HUMAN: {'; '.join(t.guard.reasons)}")
    print(f"  from: {redact_pii(t.email.sender)}")
    if t.guard.suppress_draft:
        print("  --- draft withheld ---")
    else:
        print(f"  --- draft ---\n{t.draft.reply}")
    if t.draft.info_to_collect:
        print(f"  info to collect: {t.draft.info_to_collect}")


def run(input_path: str, write: bool, limit: int | None, use_cache: bool = True) -> int:
    emails = inbound_customer_emails(input_path, limit=limit)
    if not emails:
        print(f"No inbound customer emails found in {input_path}.")
        return 0

    if not llm.have_key():
        print("WARNING: ANTHROPIC_API_KEY not set — using MOCK model "
              "(plumbing test only, not real triage).\n")

    conn = store.init_db()
    created = 0
    failed = 0
    cache_hits = 0
    for i, email in enumerate(emails, 1):
        ticket, hit = build_ticket(email, conn=conn, use_cache=use_cache)
        cache_hits += hit
        _print_ticket(ticket, i, cache_hit=hit)
        if write:
            cached = store.get(conn, email.message_id)
            if cached and cached.get("notion_page_id"):
                print(f"  -> already in Notion: {cached.get('notion_url') or '(no url)'}")
                continue
            try:
                page = create_ticket(ticket)
                created += 1
                store.mark_written(conn, email.message_id,
                                   page.get("id", ""), page.get("url", ""))
                print(f"  -> created Notion ticket: {page.get('url', '(no url)')}")
                if not slack_notify.post_ticket(ticket, notion_url=page.get("url")):
                    # The Notion page now exists, so the next run skips this
                    # message entirely and the notification is never retried.
                    # Report it rather than letting the run look clean.
                    failed += 1
                    print("  -> FAILED to post to Slack (will not retry: "
                          "the Notion ticket already exists)", file=sys.stderr)
            except Exception as exc:  # keep going on individual failures
                failed += 1
                print(f"  -> FAILED to create ticket: {exc}", file=sys.stderr)

    conn.close()
    print(f"\n{'='*70}\nProcessed {len(emails)} emails "
          f"({cache_hits} from cache). "
          f"{'Created ' + str(created) + ' Notion tickets.' if write else 'Dry run — nothing written.'}")
    cs = llm.cache_stats
    if cs["read"] or cs["write"]:
        print(f"Prompt cache: {cs['read']} read, {cs['write']} written, "
              f"{cs['uncached']} uncached input tokens.")

    if failed:
        # Exiting 0 here reported a clean run to whatever scheduled it, so a
        # broken Notion token looked identical to an empty inbox.
        print(f"{failed} ticket(s) could not be written to Notion.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Customer support triage agent (v0)")
    ap.add_argument("--input", default="data/mail_extract.jsonl.gz",
                    help="path to the jsonl(.gz) mail extract")
    ap.add_argument("--write", action="store_true",
                    help="create real Notion tickets (default is dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit dry-run (default)")
    ap.add_argument("--limit", type=int, default=10, help="max emails to process")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the db cache; re-classify/re-draft every email")
    args = ap.parse_args()

    write = args.write and not args.dry_run
    if args.write and args.dry_run:
        print("Both --write and --dry-run given; staying in dry-run.")
    return run(args.input, write=write, limit=args.limit, use_cache=not args.no_cache)


if __name__ == "__main__":
    raise SystemExit(main())
