#!/usr/bin/env python3
"""Write a synthetic Gmail-Takeout-style .mbox, so the pipeline can be run
end to end without anyone's real inbox.

Everything here is invented. The addresses are RFC 2606 reserved domains, the
phone number is in the 555 range (no country issues a subscriber number in it),
and no message is a copy of anything a real customer sent.

    python scripts/mock_mailbox.py --out data/sample_inbox.mbox

The messages are chosen to exercise the parts of ingest that decide what
becomes a ticket, because those decisions are made in code and are worth
checking without a model in the loop:

  * two messages from one customer a day apart, which should become ONE ticket
  * a third message from her after we replied, which should become a SECOND one
  * two messages from another customer a month apart, split by the 7-day window
  * a newsletter carrying List-Unsubscribe (bulk, dropped)
  * a bounce from a no-reply address (noise sender, dropped)
  * mail to a non-support address (not ours, dropped)
  * an HTML-only message (body must survive tag stripping)
  * a message whose whole complaint is in the subject and whose body is empty
  * legal / chargeback / self-harm mail, where the guardrails withhold the draft
"""
from __future__ import annotations

import argparse
from pathlib import Path

SUPPORT = "support@example.com"

# (from, to, date, subject, message_id, extra_headers, content_type, body)
MESSAGES: list[tuple[str, str, str, str, str, dict, str, str]] = [
    # --- one customer, three messages, two tickets ---
    (
        "Asha Rao <asha.rao@example.net>", SUPPORT,
        "Mon, 5 Jan 2026 09:12:03 +0530",
        "Paid 499 but no credits showing",
        "<c1a@example.net>", {}, "text/plain",
        "Hello,\n\nI paid Rs. 499 this morning and the credits are not in my "
        "account. UTR TXN884201. My number is 5550000001.\n\nAsha",
    ),
    (
        "Asha Rao <asha.rao@example.net>", SUPPORT,
        "Tue, 6 Jan 2026 08:40:00 +0530",
        "Re: Paid 499 but no credits showing",
        "<c1b@example.net>", {"In-Reply-To": "<c1a@example.net>"}, "text/plain",
        "Still nothing. Please check.",
    ),
    (
        f"Example Co Support <{SUPPORT}>", "asha.rao@example.net",
        "Wed, 7 Jan 2026 11:00:00 +0530",
        "Re: Paid 499 but no credits showing",
        "<s1@example.com>", {}, "text/plain",
        "Namaste Asha, we have credited your account. Warm regards, Example Co Team",
    ),
    (
        "Asha Rao <asha.rao@example.net>", SUPPORT,
        "Thu, 8 Jan 2026 19:05:00 +0530",
        "App crashes when I open the chat",
        "<c1c@example.net>", {}, "text/plain",
        "Different problem now. The app closes itself when I open a chat.",
    ),

    # --- another customer, two messages a month apart: two tickets ---
    (
        "Ben Ortiz <ben.ortiz@example.org>", SUPPORT,
        "Fri, 9 Jan 2026 14:00:00 +0000",
        "Cannot change my registered email",
        "<c2a@example.org>", {}, "text/plain",
        "The settings page rejects my new email address.",
    ),
    (
        "Ben Ortiz <ben.ortiz@example.org>", SUPPORT,
        "Mon, 16 Feb 2026 14:00:00 +0000",
        "Refund for duplicate charge",
        "<c2b@example.org>", {}, "text/plain",
        "I was charged twice for the same order. ORDER 5512094.",
    ),

    # --- things that must NOT become tickets ---
    (
        "Example Weekly <news@example.org>", SUPPORT,
        "Sat, 10 Jan 2026 06:00:00 +0000",
        "Your weekly digest is here",
        "<bulk1@example.org>",
        {"List-Unsubscribe": "<mailto:unsubscribe@example.org>",
         "List-Id": "digest.example.org"},
        "text/plain",
        "Ten things you missed this week.",
    ),
    (
        "Mail Delivery Subsystem <no-reply@example.org>", SUPPORT,
        "Sat, 10 Jan 2026 07:30:00 +0000",
        "Delivery Status Notification (Failure)",
        "<bounce1@example.org>", {}, "text/plain",
        "Your message could not be delivered.",
    ),
    (
        "Chris Lang <chris.lang@example.net>", "someone-else@example.org",
        "Sat, 10 Jan 2026 08:00:00 +0000",
        "Lunch on Tuesday?",
        "<personal1@example.net>", {}, "text/plain",
        "Are you free Tuesday?",
    ),

    # --- awkward shapes that still must become tickets ---
    (
        "Dana Iqbal <dana.iqbal@example.net>", SUPPORT,
        "Sun, 11 Jan 2026 10:00:00 +0000",
        "Booking page will not load",
        "<html1@example.net>", {}, "text/html",
        "<html><head><style>p{color:red}</style></head><body>"
        "<p>The booking page shows a <b>blank screen</b> on Safari.</p>"
        "<script>track()</script></body></html>",
    ),
    (
        "Eli Novak <eli.novak@example.net>", SUPPORT,
        "Sun, 11 Jan 2026 12:00:00 +0000",
        "Wrong provider joined my session and I want it looked at",
        "<subjectonly1@example.net>", {}, "text/plain",
        "",
    ),

    # --- guardrail territory: a plausible draft is worse than none ---
    (
        "Farah Massi <farah.massi@example.net>", SUPPORT,
        "Mon, 12 Jan 2026 09:00:00 +0000",
        "Speaking to my solicitor about this",
        "<legal1@example.net>", {}, "text/plain",
        "I have instructed a solicitor and will take legal action if this is "
        "not resolved this week.",
    ),
    (
        "Gil Marek <gil.marek@example.net>", SUPPORT,
        "Mon, 12 Jan 2026 10:00:00 +0000",
        "Raising a chargeback with my bank",
        "<charge1@example.net>", {}, "text/plain",
        "I have started a chargeback for the transaction with my bank.",
    ),
    (
        "Hana Petrov <hana.petrov@example.net>", SUPPORT,
        "Mon, 12 Jan 2026 11:00:00 +0000",
        "I feel awful about all of this",
        "<harm1@example.net>", {}, "text/plain",
        "Everything has gone wrong and some days I think about suicide. I just "
        "want my account sorted out.",
    ),
]


def render(messages=MESSAGES) -> str:
    """Render the messages as mbox text (the `From ` separator format)."""
    out: list[str] = []
    for sender, to, date, subject, msg_id, extra, ctype, body in messages:
        out.append("From nobody@example.com Mon Jan  5 00:00:00 2026")
        out.append(f"From: {sender}")
        out.append(f"To: {to}")
        out.append(f"Date: {date}")
        out.append(f"Subject: {subject}")
        out.append(f"Message-ID: {msg_id}")
        for key, value in extra.items():
            out.append(f"{key}: {value}")
        out.append("MIME-Version: 1.0")
        out.append(f'Content-Type: {ctype}; charset="utf-8"')
        out.append("")
        # A line starting "From " inside a body would be read as the start of
        # the next message. mbox escapes it with a leading ">".
        for line in body.split("\n"):
            out.append(">" + line if line.startswith("From ") else line)
        out.append("")
    return "\n".join(out) + "\n"


def write(path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8")
    return len(MESSAGES)


def main() -> int:
    ap = argparse.ArgumentParser(description="write a synthetic sample .mbox")
    ap.add_argument("--out", default="data/sample_inbox.mbox")
    args = ap.parse_args()
    n = write(args.out)
    print(f"Wrote {n} synthetic messages -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
