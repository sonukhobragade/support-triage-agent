"""Conversation-categories report over support email.

Volume, first-response time, and reply coverage per topic.

Two things the chat-style dashboard asked for do not exist for email and are
deliberately absent rather than faked:
  * "median duration" — email has no session. We report median first-response
    time (customer mail -> our first human reply) instead.
  * escalation / sentiment — those live in `processed` (LLM output), which
    shares no message_id with the bulk-loaded `emails` backlog. Run the
    pipeline over the backlog to populate them.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from email.utils import parsedate_to_datetime
from statistics import median

from .categorize import subcategorize
from .ingest import load_emails
from .pairs import NOISE_REPLY, clean_reply, parent_id

# Roll the 18 sub_categories up into reporting buckets.
# "Others" from the mockup is split: vendor spam and provider applications
# are not support and are excluded from the table (counted in the footer);
# the genuine support topics inside it get their own rows.
BUCKETS: dict[str, str] = {
    "Report not received / where is it": "Report Issues",
    "Report blank / not generated": "Report Issues",
    "Cannot download report": "Report Issues",
    "Wrong birth details / inaccurate report": "Report Issues",
    "Autopay / unwanted subscription": "Subscription/Autopay",
    "Cancel subscription (no refund asked)": "Subscription/Autopay",
    "Refund request (generic)": "Return/Refund",
    "Account deletion / data removal": "Delete account",
    "Payment / amount issue (unspecified)": "Payment Issues",
    "Double / extra charge": "Payment Issues",
    "Charged but app shows not deducted": "Payment Issues",
    "Credits / chat problem": "Provider chat issues",
    "Service quality / not satisfied": "Service quality",
    "Product question / how it works": "Product question",
    "Login / OTP / app crash": "Login / OTP / app crash",
    "General query / other": "Uncategorized",
}

# Real inbound mail, but not customer support. Excluded from the table.
NOT_SUPPORT = {
    "Business / spam (not support)": "vendor/agency pitches",
    "Provider onboarding / join request": "job applications",
}


def _hours(earlier: str, later: str) -> float | None:
    try:
        delta = parsedate_to_datetime(later) - parsedate_to_datetime(earlier)
    except (TypeError, ValueError):
        return None
    secs = delta.total_seconds()
    return secs / 3600 if secs >= 0 else None


def first_response_hours(extract_path: str) -> dict[str, float]:
    """message_id -> hours until our first real human reply."""
    emails = list(load_emails(extract_path))
    by_id = {e.message_id: e for e in emails if e.message_id}
    best: dict[str, float] = {}

    for reply in emails:
        if not reply.from_support:
            continue
        pid = parent_id(reply)
        parent = by_id.get(pid) if pid else None
        if parent is None or parent.from_support:
            continue
        body = clean_reply(reply.body)
        if len(body) < 20 or NOISE_REPLY.search(body):
            continue  # automated ticket notification, not a real reply
        hrs = _hours(parent.date, reply.date)
        if hrs is None:
            continue
        if pid not in best or hrs < best[pid]:
            best[pid] = hrs
    return best


def _fmt_hours(hrs: float) -> str:
    if hrs < 1:
        return f"{round(hrs * 60)}m"
    if hrs < 48:
        return f"{hrs:.1f}h"
    return f"{hrs / 24:.1f}d"


def collect(db_path: str, extract_path: str | None) -> tuple[list[dict], dict]:
    con = sqlite3.connect(db_path)
    # Categorize from each row's own text rather than trusting the stored
    # sub_category column: labels written by older runs were matched back to
    # rows by a message_id that stitching had already changed, so a row could
    # carry another email's label. Recomputing here keeps the table reproducible.
    rows = [
        (message_id, subcategorize(f"{subject}\n{body}"))
        for message_id, subject, body in con.execute(
            "select message_id, subject, body from emails"
        )
    ]
    con.close()

    frt = first_response_hours(extract_path) if extract_path else {}

    volume: dict[str, int] = defaultdict(int)
    latencies: dict[str, list[float]] = defaultdict(list)
    excluded: dict[str, int] = defaultdict(int)
    unmapped: dict[str, int] = defaultdict(int)

    for message_id, sub in rows:
        if sub in NOT_SUPPORT:
            excluded[sub] += 1
            continue
        bucket = BUCKETS.get(sub)
        if bucket is None:
            unmapped[sub] += 1
            continue
        volume[bucket] += 1
        if message_id in frt:
            latencies[bucket].append(frt[message_id])

    total = sum(volume.values())
    table = []
    for bucket, n in sorted(volume.items(), key=lambda kv: -kv[1]):
        lat = latencies[bucket]
        table.append(
            {
                "category": bucket,
                "emails": n,
                "share": 100.0 * n / total if total else 0.0,
                "median_first_response": median(lat) if lat else None,
                "replied": len(lat),
                "reply_rate": 100.0 * len(lat) / n if n else 0.0,
            }
        )

    meta = {
        "total": total,
        "excluded": dict(excluded),
        "unmapped": dict(unmapped),
        "with_extract": bool(frt),
    }
    return table, meta


def render(table: list[dict], meta: dict) -> str:
    head = f"{'CATEGORY':<26}{'EMAILS':>7}{'SHARE':>8}{'MED 1ST REPLY':>16}{'REPLY RATE':>13}"
    lines = ["Conversation categories", "Volume, first-response time, and reply coverage by topic", "", head, "-" * len(head)]

    for row in table:
        lat = _fmt_hours(row["median_first_response"]) if row["median_first_response"] is not None else "--"
        lines.append(
            f"{row['category']:<26}{row['emails']:>7}{row['share']:>7.1f}%"
            f"{lat:>16}{row['reply_rate']:>12.0f}%"
        )

    lines += ["-" * len(head), f"{'TOTAL (support)':<26}{meta['total']:>7}"]

    if meta["excluded"]:
        lines.append("")
        lines.append("Excluded — inbound mail, but not customer support:")
        for sub, n in sorted(meta["excluded"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n:>5}  {sub} ({NOT_SUPPORT[sub]})")

    if meta["unmapped"]:
        lines.append("")
        lines.append("Unmapped sub_categories (add to BUCKETS):")
        for sub, n in sorted(meta["unmapped"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n:>5}  {sub}")

    if not meta["with_extract"]:
        lines += ["", "No extract given — first-response columns are blank."]

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/tickets.db")
    ap.add_argument("--extract", default="data/mail_extract.jsonl.gz")
    args = ap.parse_args()

    table, meta = collect(args.db, args.extract)
    print(render(table, meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
