"""Live Gmail ingest via IMAP (read-only by design — never sends).

Fetches recent mail from the support inbox, turns each message into an
`ingest.Email`, applies the same support/noise/bulk filters as the mbox path,
then runs the existing pipeline (classify + draft + optional Notion write).

Nothing in the mailbox is modified, including flags: messages are read with
BODY.PEEK so they are not marked \\Seen. Whether a message has been handled is
tracked in the local store, not in the mailbox, because thread stitching means
one conversation can span several messages and a reply to an old thread must
re-post with full context. Read/unread cannot express that.

This is the Phase-1 "go live (read-only inbox)" connector. It reuses the whole
v0 pipeline unchanged — only the input source swaps from mbox extract to IMAP.

Auth (easiest method): a Gmail App Password on the support account (requires
2-Step Verification). Put in .env:

    GMAIL_USER=support@example.com
    GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx   # 16 chars, no spaces

Run:
    # dry run: draft tickets, write NOTHING to Notion
    python -m support_triage_agent.gmail_fetch --limit 10

    # write tickets to Notion (the local store prevents re-processing)
    python -m support_triage_agent.gmail_fetch --write

Automate (cron, every 5 min):
    */5 * * * * cd /path/support-triage-agent && .venv/bin/python -m support_triage_agent.gmail_fetch --write
"""
from __future__ import annotations

import argparse
import email
import imaplib
import os
import sys
from email.message import Message
from email.utils import format_datetime, parsedate_to_datetime

from . import config, llm, slack_notify, store
from .ingest import (Email, _is_noise, _is_support, _to_support,
                     extract_identifiers, stitch_threads)
from .notion_writer import create_ticket
from .pipeline import _print_ticket, build_ticket

IMAP_HOST = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")


def _body_text(msg: Message) -> str:
    """Extract the plain-text body, falling back to stripped HTML."""
    if msg.is_multipart():
        plain, html = "", ""
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            if ctype == "text/plain" and not plain:
                plain = _decode(part)
            elif ctype == "text/html" and not html:
                html = _decode(part)
        return plain or _strip_html(html)
    payload = _decode(msg)
    return payload if msg.get_content_type() == "text/plain" else _strip_html(payload)


def _decode(part: Message) -> str:
    raw = part.get_payload(decode=True)
    if raw is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, TypeError):
        return raw.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"[ \t]*\n[ \t]*", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _to_email(msg: Message) -> Email:
    """Map a raw IMAP message to an ingest.Email (mirrors ingest.load_emails)."""
    subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
    sender = msg.get("From", "")
    body = _body_text(msg)
    if not body.strip() and subject.strip():
        body = subject  # subject-only complaint fallback
    raw_date = msg.get("Date", "")
    try:
        date = format_datetime(parsedate_to_datetime(raw_date))
    except (TypeError, ValueError):
        date = raw_date
    e = Email(
        date=date,
        sender=sender,
        to=msg.get("To", "") + ("," + msg.get("Cc", "") if msg.get("Cc") else ""),
        subject=subject,
        labels="",
        message_id=msg.get("Message-ID", "").strip(),
        in_reply_to=msg.get("In-Reply-To", "").strip(),
        references=msg.get("References", "").strip(),
        body=body,
        from_support=_is_support(sender),
        bulk=_is_bulk(msg),
    )
    e.identifiers = extract_identifiers(f"{e.subject}\n{e.body}")
    return e


def _is_bulk(msg: Message) -> bool:
    """Mailing-list / automated bulk mail (List-* headers or bulk Precedence)."""
    if msg.get("List-Unsubscribe") or msg.get("List-Id"):
        return True
    return (msg.get("Precedence", "").lower() in {"bulk", "list", "junk"})


def _keep(e: Email) -> bool:
    """Same filter as ingest.inbound_customer_emails (minus stitching)."""
    if e.from_support:
        return False  # our own reply
    if not _to_support(e.to):
        return False  # not addressed to support
    if _is_noise(e.sender):
        return False
    if not e.body.strip():
        return False
    return True


_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_since(days: int) -> str:
    """IMAP SINCE date string (DD-Mon-YYYY) for `days` ago, UTC."""
    from datetime import datetime, timedelta, timezone
    d = datetime.now(timezone.utc) - timedelta(days=days)
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month - 1]}-{d.year}"


def _fetch_folder(imap, folder: str, since: str) -> list[Email]:
    """All messages in `folder` since `since` as Email objects (BODY.PEEK)."""
    typ, _ = imap.select(folder, readonly=True)
    if typ != "OK":
        return []
    typ, data = imap.search(None, "SINCE", since)
    if typ != "OK":
        return []
    out: list[Email] = []
    for uid in data[0].split():
        typ, raw = imap.fetch(uid, "(BODY.PEEK[])")
        if typ != "OK" or not raw or not raw[0]:
            continue
        out.append(_to_email(email.message_from_bytes(raw[0][1])))
    return out


def fetch_conversations(since_days: int = 7, limit: int | None = None) -> list[Email]:
    """Return stitched customer conversations from the last `since_days` days.

    Fetches BOTH the inbox (inbound) AND Sent (our replies), so thread-stitching
    can use our replies as conversation boundaries. Consecutive messages from the
    same customer are merged into ONE Email (full context — a phone number sent in
    a follow-up is known, won't be re-requested). Read/unread is ignored; dedup is
    DB-driven in the caller. Returns conversations newest-first.
    """
    user = os.getenv("GMAIL_USER", "")
    pw = (os.getenv("GMAIL_APP_PASSWORD", "") or "").replace(" ", "")
    if not user or not pw:
        sys.exit("Set GMAIL_USER and GMAIL_APP_PASSWORD in .env first.")

    since = _imap_since(since_days)
    # timeout guards every IMAP op (connect/login/fetch). Without it a stalled
    # TCP socket blocks the poll loop forever — the daemon's try/except can't
    # catch a hang, only an exception. A timeout turns a stall into a raised
    # error that the loop logs and retries next cycle.
    imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=60)
    imap.login(user, pw)
    try:
        allmsgs = _fetch_folder(imap, "INBOX", since)
        # Our sent replies — boundary signals for stitching (a reply closes a thread).
        for sent in ('"[Gmail]/Sent Mail"', "Sent"):
            replies = _fetch_folder(imap, sent, since)
            if replies:
                allmsgs += replies
                break
    finally:
        imap.logout()

    # Stitch consecutive same-customer messages, then apply the support/noise
    # filters (same as ingest.inbound_customer_emails).
    convos = stitch_threads(allmsgs)
    out: list[Email] = []
    for e in convos:
        if e.from_support or not _to_support(e.to) or e.bulk \
                or _is_noise(e.sender) or not e.body.strip():
            continue
        out.append(e)
    out.reverse()  # newest-first
    if limit:
        out = out[:limit]
    return out


def run(write: bool, limit: int | None, since_days: int = 7,
        use_cache: bool = True) -> int:
    conn = store.init_db()

    # Stitch threads, then drop anything already posted to Slack. A reply to an
    # older message merges with its earlier message(s) into ONE conversation, so
    # the draft sees full context. Dedup is DB-driven (the merged conversation's
    # message_id is the LATEST message — a new reply = new id = re-posted with the
    # full thread). Read/unread is irrelevant.
    convos = fetch_conversations(since_days=since_days)
    items = [e for e in convos if not store.slack_posted(conn, e.message_id)]
    if limit:
        items = items[:limit]

    if not items:
        print(f"No new support mail in the last {since_days}d "
              f"({len(convos)} conversations, all already posted).")
        conn.close()
        return 0

    if not llm.have_key():
        print("WARNING: ANTHROPIC_API_KEY not set — using MOCK model "
              "(plumbing test only, not real triage).\n")

    created = 0
    posted = 0
    failed = 0
    for i, e in enumerate(items, 1):
        ticket, hit = build_ticket(e, conn=conn, use_cache=use_cache)
        _print_ticket(ticket, i, cache_hit=hit)

        notion_url = None
        if write:
            cached = store.get(conn, e.message_id)
            if cached and cached.get("notion_page_id"):
                notion_url = cached.get("notion_url")
                print(f"  -> already in Notion: {notion_url or '(no url)'}")
            else:
                try:
                    page = create_ticket(ticket)
                    created += 1
                    notion_url = page.get("url")
                    store.mark_written(conn, e.message_id, page.get("id", ""), notion_url or "")
                    print(f"  -> created Notion ticket: {notion_url or '(no url)'}")
                except Exception as exc:
                    failed += 1
                    print(f"  -> FAILED to create ticket: {exc}", file=sys.stderr)
                    continue  # try again next run

        # Slack is the core deliverable — post the customer email + draft reply.
        if slack_notify.post_ticket(ticket, notion_url=notion_url):
            posted += 1
            store.mark_slack_posted(conn, e.message_id)
            print("  -> posted to Slack")
        else:
            failed += 1
            print("  -> FAILED to post to Slack", file=sys.stderr)

    conn.close()
    print(f"\n{'='*70}\nProcessed {len(items)} conversations. Posted {posted} to Slack. "
          f"{'Created ' + str(created) + ' Notion tickets.' if write else 'No Notion write.'}")

    if failed:
        # A scheduler that gets exit 0 after nothing was delivered will keep
        # reporting the job healthy while the channel stays silent.
        print(f"{failed} delivery failure(s).", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Live Gmail ingest (read-only IMAP)")
    ap.add_argument("--write", action="store_true",
                    help="create Notion tickets (mail is never modified; dedupe is local)")
    ap.add_argument("--limit", type=int, default=20, help="max emails per run")
    ap.add_argument("--since-days", type=int, default=7,
                    help="look back this many days (dedup is DB-driven, not read-state)")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the db cache; re-classify/re-draft every email")
    ap.add_argument("--loop", action="store_true",
                    help="run forever, polling every --interval seconds (for Docker/daemon)")
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between polls in --loop mode (default 300 = 5 min)")
    args = ap.parse_args()

    if not args.loop:
        return run(write=args.write, limit=args.limit, since_days=args.since_days,
                   use_cache=not args.no_cache)

    # Daemon mode — the container's own scheduler. No cron, survives nothing-to-do
    # runs, logs each cycle. A single fetch error is caught so the loop never dies.
    import time
    print(f"[loop] starting — polling every {args.interval}s", flush=True)
    heartbeat = os.path.join(
        os.path.dirname(str(config.TICKETS_DB_PATH)) or "/app/data", "heartbeat")
    while True:
        try:
            run(write=args.write, limit=args.limit, since_days=args.since_days,
                use_cache=not args.no_cache)
        except Exception as exc:  # never let one bad cycle kill the daemon
            print(f"[loop] cycle error: {exc}", file=sys.stderr, flush=True)
        # Heartbeat for the container healthcheck. A hung cycle (e.g. a blocked
        # socket with no timeout) stops refreshing this; the healthcheck then
        # marks the container unhealthy so Docker/Dokploy can restart it.
        try:
            with open(heartbeat, "w") as hb:
                hb.write(str(int(time.time())))
        except OSError:
            pass
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
