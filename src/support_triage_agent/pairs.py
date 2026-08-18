"""Shared email-reply pairing + cleaning, used by reply-library mining, feedback
backfill, and retrieval grounding.

A "pair" is (customer Email, our cleaned reply text), linked by In-Reply-To /
References threading. No LLM.
"""
from __future__ import annotations

import re

from .guardrails import redact_pii
from .ingest import Email, load_emails

# Lines at/after these markers are quoted history or signatures — drop them.
_QUOTE_MARKERS = [
    re.compile(r"^\s*On .+ wrote:\s*$", re.I),
    re.compile(r"^\s*>"),
    re.compile(r"^\s*-{2,}\s*Forwarded message", re.I),
    re.compile(r"^\s*From:\s.+", re.I),
    re.compile(r"^\s*Sent from my ", re.I),
    re.compile(r"^\s*_{5,}\s*$"),
]
# Common Example Co sign-offs — cut the reply here.
_SIGNOFF = re.compile(
    r"\n\s*(Regards|Thanks|Thank you|Warm regards|Best regards|Team Example Co|"
    r"Example Co Support|Support Team)\b.*", re.I | re.S
)
_MID_RE = re.compile(r"<[^>]+>")

# Automated ticketing-system notifications and recruitment/marketing noise that
# slip through the from_support filter — not human-written replies. Shared by
# reply-library mining and retrieval grounding.
NOISE_REPLY = re.compile(
    r"your ticket|ticket has been|support rep has indicated|reopen|"
    r"has been closed|ticket\s*[-“\"#]|new comment in the ticket|"
    r"freshdesk\.com|bharat ek khoj|we have received your|"
    r"Hi Employer|recruitment consultation|unsubscribe|do not reply",
    re.I,
)


def clean_reply(body: str) -> str:
    """Return just the reply text — no quoted history, no signature, PII redacted."""
    out_lines: list[str] = []
    for line in body.splitlines():
        if any(m.match(line) for m in _QUOTE_MARKERS):
            break  # everything below is quoted/forwarded
        out_lines.append(line)
    text = "\n".join(out_lines)
    text = _SIGNOFF.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # These examples are committed/sent to the model — strip customer phone/email.
    return redact_pii(text)


def parent_id(reply: Email) -> str | None:
    """Message-id of the email this reply answers: In-Reply-To, else last
    Message-ID in References."""
    if reply.in_reply_to:
        m = _MID_RE.search(reply.in_reply_to)
        if m:
            return m.group(0)
    ids = _MID_RE.findall(reply.references or "")
    return ids[-1] if ids else None


def customer_reply_pairs(path: str, min_len: int = 20) -> list[tuple[Email, str]]:
    """All (customer email, our cleaned reply) pairs in the extract, thread-linked."""
    emails = list(load_emails(path))
    by_id = {e.message_id: e for e in emails if e.message_id}
    out: list[tuple[Email, str]] = []
    for reply in emails:
        if not reply.from_support:
            continue
        pid = parent_id(reply)
        parent = by_id.get(pid) if pid else None
        if parent is None or parent.from_support:
            continue
        human = clean_reply(reply.body)
        if len(human) < min_len:
            continue
        if NOISE_REPLY.search(human):
            continue  # automated ticket notification / marketing, not a real reply
        out.append((parent, human))
    return out
