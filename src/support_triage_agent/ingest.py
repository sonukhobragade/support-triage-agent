"""Load the cleaned mail extract and surface inbound customer emails to triage.

Input is the `mail_extract.jsonl.gz` produced by the Takeout extract script:
one JSON object per line with keys:
  date, from, to, subject, labels, message_id, in_reply_to, references, body
"""
from __future__ import annotations

import gzip
import json
import sys
import re
from collections import defaultdict
from dataclasses import dataclass, field
from email.utils import getaddresses, parsedate_to_datetime
from typing import Iterator

from . import config


@dataclass
class Email:
    date: str
    sender: str
    to: str
    subject: str
    labels: str
    message_id: str
    in_reply_to: str
    references: str
    body: str
    from_support: bool = False
    bulk: bool = False
    identifiers: dict = field(default_factory=dict)


def _addrs(raw: str) -> list[str]:
    return [a.lower() for _, a in getaddresses([raw or ""]) if a]


def _is_support(sender: str) -> bool:
    addrs = _addrs(sender)
    return any(a in config.SUPPORT_ADDRESSES for a in addrs)


def _to_support(recipients: str) -> bool:
    """True if a support address is among the recipients (To/Cc)."""
    addrs = _addrs(recipients)
    return any(a in config.SUPPORT_ADDRESSES for a in addrs)


def _is_noise(sender: str) -> bool:
    """True if sender matches a known noise pattern (bounces, no-reply, marketing)."""
    s = (sender or "").lower()
    return any(pat in s for pat in config.NOISE_SENDERS)


# Lightweight identifier extraction (the agent re-checks, but this seeds it).
_PHONE_RE = re.compile(config.PHONE_PATTERN)
_TXN_RE = re.compile(r"\b(?:UTR|TXN|TID|ORDER|RRN)[:\s#]*([A-Za-z0-9]{6,})\b", re.I)
_AMOUNT_RE = re.compile(r"(?:₹|Rs\.?|INR)\s?(\d{1,6}(?:\.\d{1,2})?)", re.I)


def extract_identifiers(text: str) -> dict:
    out: dict = {}
    if m := _PHONE_RE.search(text or ""):
        out["phone"] = m.group(0)
    if m := _TXN_RE.search(text or ""):
        out["transaction_id"] = m.group(1)
    if m := _AMOUNT_RE.search(text or ""):
        out["amount"] = m.group(1)
    return out


def load_emails(path: str) -> Iterator[Email]:
    """Stream Email objects from a .jsonl or .jsonl.gz extract.

    Unparseable lines are skipped, but counted and reported on stderr at the
    end. Dropping them in silence meant a half-written extract produced a
    smaller, entirely plausible run with no indication that mail was missing.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    malformed = 0
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            sender = rec.get("from", "")
            subject = rec.get("subject", "")
            body = rec.get("body", "")
            # Subject-only complaints (empty body): fold the subject in so triage
            # isn't blind to it. Mirrors the same fallback in mbox_extract, but
            # also recovers extracts produced before that fix.
            if not body.strip() and subject.strip():
                body = subject
            email = Email(
                date=rec.get("date", ""),
                sender=sender,
                to=rec.get("to", ""),
                subject=subject,
                labels=rec.get("labels", ""),
                message_id=rec.get("message_id", ""),
                in_reply_to=rec.get("in_reply_to", ""),
                references=rec.get("references", ""),
                body=body,
                from_support=_is_support(sender),
                bulk=bool(rec.get("bulk", False)),
            )
            email.identifiers = extract_identifiers(f"{email.subject}\n{email.body}")
            yield email

    if malformed:
        print(
            f"WARNING: skipped {malformed} unparseable line(s) in {path}.",
            file=sys.stderr,
        )


def _ts(e: Email) -> float | None:
    """Unix timestamp of an email's Date header, or None if unparseable."""
    try:
        return parsedate_to_datetime(e.date).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _merge_group(group: list[Email]) -> Email:
    """Collapse consecutive customer messages (chronological) into one Email.

    Bodies concatenated, identifiers re-extracted from the combined text, latest
    date + message_id kept (the ticket points at the most recent message)."""
    if len(group) == 1:
        return group[0]
    last = group[-1]
    body = "\n\n".join(m.body.strip() for m in group if m.body.strip())
    subject = next((m.subject for m in group if m.subject.strip()), last.subject)
    merged = Email(
        date=last.date, sender=group[0].sender, to=group[0].to, subject=subject,
        labels=last.labels, message_id=last.message_id,
        in_reply_to=last.in_reply_to, references=last.references, body=body,
        from_support=False, bulk=any(m.bulk for m in group),
    )
    merged.identifiers = extract_identifiers(f"{subject}\n{body}")
    return merged


def stitch_threads(emails: list[Email], window_days: int = 7) -> list[Email]:
    """Merge consecutive same-sender customer messages into one conversation.

    A new thread starts when the gap between two messages exceeds `window_days`,
    OR we sent the customer a reply in between (a reply = the prior issue was
    handled). Support emails are boundary signals only; they are not returned.
    """
    window = window_days * 86400
    cust: dict[str, list[Email]] = defaultdict(list)
    replies: dict[str, list[float]] = defaultdict(list)
    for e in emails:
        if e.from_support:
            t = _ts(e)
            if t is not None:
                for a in _addrs(e.to):
                    replies[a].append(t)
        else:
            addrs = _addrs(e.sender)
            if addrs:
                cust[addrs[0]].append(e)

    stitched: list[Email] = []
    for addr, msgs in cust.items():
        msgs.sort(key=lambda m: (_ts(m) is None, _ts(m) or 0.0))
        reps = sorted(replies.get(addr, []))
        group: list[Email] = []
        prev_t: float | None = None
        for m in msgs:
            mt = _ts(m)
            if group and prev_t is not None and mt is not None:
                gap_break = (mt - prev_t) > window
                replied_between = any(prev_t < r <= mt for r in reps)
                if gap_break or replied_between:
                    stitched.append(_merge_group(group))
                    group = []
            group.append(m)
            if mt is not None:
                prev_t = mt
        if group:
            stitched.append(_merge_group(group))

    stitched.sort(key=lambda m: (_ts(m) is None, _ts(m) or 0.0))
    return stitched


def inbound_customer_emails(path: str, limit: int | None = None,
                            stitch: bool = True) -> list[Email]:
    """Return inbound (customer -> us) emails that plausibly need a reply.

    With `stitch=True` (default), consecutive messages from the same customer are
    merged into one conversation first (see `stitch_threads`)."""
    allmsgs = list(load_emails(path))
    customers = stitch_threads(allmsgs) if stitch else [e for e in allmsgs if not e.from_support]

    out: list[Email] = []
    for e in customers:
        if not _to_support(e.to):
            continue  # not addressed to support — personal/misc noise
        if e.bulk:
            continue  # promo/newsletter/automated bulk mail, not a real customer
        if _is_noise(e.sender):
            continue  # bounces, no-reply, marketing senders
        if not e.body.strip():
            continue
        out.append(e)
        if limit and len(out) >= limit:
            break
    return out
