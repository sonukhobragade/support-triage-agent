#!/usr/bin/env python3
"""Run the pipeline end to end on a generated mailbox, and check the result.

    python scripts/demo.py

No inbox, no Notion, no Slack. With ANTHROPIC_API_KEY set the real models are
called; without it the mock model runs and the plumbing is still exercised.

What this checks is deliberately the part that does NOT depend on the model:
which messages become tickets, how consecutive messages are stitched into one
conversation, what the regexes pull out, when the guardrails withhold a draft,
and whether the cache stops a second run from paying for the first one again.
Those decisions are made in code. A model that answers well cannot rescue them
and a model that answers badly cannot break them, so they are worth asserting.

Draft *quality* is not checked here, and this script does not pretend to. It
would need a judge, a key, and a rubric, and it would not be reproducible from
a clean clone.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support_triage_agent import config, guardrails, ingest, llm, mbox_extract  # noqa: E402
from support_triage_agent import store  # noqa: E402
from support_triage_agent.pipeline import build_ticket  # noqa: E402

import mock_mailbox  # noqa: E402

# The operator's own .env is loaded by config with override=True, which is right
# for a real run and wrong for this one: a local SUPPORT_ADDRESSES or NOISE_SENDERS
# would quietly change what the demo counts. Pin the documented defaults.
config.SUPPORT_ADDRESSES = ["support@example.com"]
config.NOISE_SENDERS = list(config._DEFAULT_NOISE_SENDERS)
config.EMPLOYEE_NAMES = []
config.MONEY_SOPS = {"A1", "A2"}
config.PHONE_PATTERN = r"(?<!\d)(?:\+?\d{1,3}[-\s]?)?[2-9]\d{9}(?!\d)"
# ingest compiles the phone pattern at import time, so it needs recompiling too.
ingest._PHONE_RE = re.compile(config.PHONE_PATTERN)

checks: list[tuple[str, bool]] = []


def check(label: str, passed: bool) -> None:
    checks.append((label, bool(passed)))
    print(f"  {'PASS' if passed else 'FAIL'}  {label}")


def rule(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 62 - len(title)))


def one(emails, needle: str):
    """The single email whose subject contains `needle`, or None."""
    hits = [e for e in emails if needle.lower() in e.subject.lower()]
    return hits[0] if len(hits) == 1 else None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="support-triage-demo-"))
    config.TICKETS_DB_PATH = tmp / "tickets.db"

    calls = {"n": 0}
    real_complete = llm.complete

    def counting_complete(*a, **kw):
        calls["n"] += 1
        return real_complete(*a, **kw)

    llm.complete = counting_complete

    mode = "REAL models (ANTHROPIC_API_KEY set)" if llm.have_key() else "MOCK model"
    print(f"Model mode: {mode}")

    rule("mbox -> extract")
    mbox_path = tmp / "sample_inbox.mbox"
    written = mock_mailbox.write(mbox_path)
    extract_path = tmp / "mail_extract.jsonl"
    n = mbox_extract.extract(str(mbox_path), str(extract_path))
    print(f"  {written} messages generated, {n} extracted")
    check("every generated message survived the mbox round trip", n == written)

    raw = list(ingest.load_emails(str(extract_path)))
    html = one(raw, "Booking page")
    check("HTML-only body was stripped to text",
          html is not None and "blank screen" in html.body
          and "<b>" not in html.body and "track()" not in html.body
          and "color:red" not in html.body)
    subj_only = one(raw, "Wrong provider joined")
    check("a complaint sent with an empty body kept the subject as its body",
          subj_only is not None and subj_only.body.strip() == subj_only.subject)
    check("only the newsletter was marked bulk",
          [e.subject for e in raw if e.bulk] == ["Your weekly digest is here"])

    rule("which messages become tickets")
    emails = ingest.inbound_customer_emails(str(extract_path), limit=None)
    for e in emails:
        print(f"  {guardrails.redact_pii(e.sender):<38} {e.subject[:44]}")

    print()
    subjects = [e.subject for e in emails]
    check("the newsletter was dropped (List-Unsubscribe)",
          "Your weekly digest is here" not in subjects)
    check("the bounce was dropped (no-reply sender)",
          not any("Delivery Status" in s for s in subjects))
    check("mail not addressed to support was dropped",
          not any("Lunch" in s for s in subjects))
    check("nine conversations from fourteen messages", len(emails) == 9)

    rule("stitching messages into conversations")
    asha_first = one(emails, "Paid 499")
    check("two messages a day apart became ONE conversation",
          asha_first is not None
          and "TXN884201" in asha_first.body
          and "Still nothing" in asha_first.body)
    check("her later message became a SEPARATE ticket, because we had replied "
          "in between",
          one(emails, "App crashes") is not None)
    # Ben's two messages are 38 days apart with no reply between them. Only the
    # 7-day window separates them, so this is the check that the window works.
    ben = [e for e in emails if "ben.ortiz" in e.sender]
    check("two messages 38 days apart stayed separate (7-day window)",
          len(ben) == 2)

    rule("what the regexes pulled out")
    ids = asha_first.identifiers if asha_first else {}
    print(f"  {ids}")
    check("phone found", ids.get("phone") == "5550000001")
    check("transaction reference found", ids.get("transaction_id") == "TXN884201")
    check("amount found", ids.get("amount") == "499")

    rule("redaction (what reaches a log)")
    line = f"{asha_first.sender}: {asha_first.body}" if asha_first else ""
    redacted = guardrails.redact_pii(line)
    print(f"  {redacted.splitlines()[0]}")
    check("the phone number is masked", "5550000001" not in redacted)
    check("the address is masked", "asha.rao@example.net" not in redacted)

    rule("triage + guardrails")
    conn = store.init_db()
    tickets = []
    for e in emails:
        ticket, hit = build_ticket(e, conn=conn)
        tickets.append(ticket)
        flags = "; ".join(ticket.guard.reasons) or "-"
        print(f"  {e.subject[:40]:<42} human={ticket.guard.needs_human} "
              f"draft={'withheld' if ticket.guard.suppress_draft else 'attached'}")
        if ticket.guard.suppress_draft:
            print(f"      {flags}")

    print()
    suppressed = {t.email.subject for t in tickets if t.guard.suppress_draft}
    check("the solicitor email got no draft",
          "Speaking to my solicitor about this" in suppressed)
    check("the chargeback email got no draft",
          "Raising a chargeback with my bank" in suppressed)
    check("the self-harm mention got no draft",
          "I feel awful about all of this" in suppressed)
    check("a withheld draft is actually empty, not merely flagged",
          all(t.draft.reply == "" for t in tickets if t.guard.suppress_draft))
    check("nothing else was suppressed", len(suppressed) == 3)
    check("every unmatched SOP was routed to a human",
          all(t.guard.needs_human for t in tickets
              if t.cls.sop_id == config.NO_SOP))
    # Suppression must read the CUSTOMER's text. A draft that happens to say
    # "lawyer" is not a customer threatening legal action, and withholding it
    # would train reviewers to ignore the banner.
    from support_triage_agent.classify import Classification as _C
    from support_triage_agent.draft import Draft as _D
    only_in_draft = guardrails.check(
        _C("Payment", "C1", "Low", "neutral", "wants a receipt"),
        _D(reply="Our lawyer can send the invoice if you need one."),
        source_text="Please send me a copy of my invoice.")
    check("a draft that merely mentions a lawyer is not suppressed",
          only_in_draft.suppress_draft is False)
    # 9 classify calls + 6 draft calls. The 3 suppressed messages are never
    # drafted: that decision reads the customer's text only, so paying for a
    # reply and then discarding it buys nothing.
    print(f"  model calls for {len(emails)} conversations: {calls['n']}")
    check("no draft was generated for a message that cannot carry one",
          calls["n"] == len(emails) + (len(emails) - len(suppressed)))

    rule("the cache")
    before = calls["n"]
    second = [build_ticket(e, conn=conn) for e in emails]
    hits = sum(1 for _, hit in second if hit)
    print(f"  first pass: {before} model calls. "
          f"second pass: {calls['n'] - before} model calls, {hits} cache hits.")
    check("a second run re-used every ticket", hits == len(emails))
    check("and called the model zero times", calls["n"] == before)
    check("the cached ticket carries its full guardrail verdict",
          all((t.guard.needs_human, t.guard.suppress_draft, t.guard.reasons)
              == (c.guard.needs_human, c.guard.suppress_draft, c.guard.reasons)
              for t, (c, _) in zip(tickets, second)))
    check("a suppressed ticket is still suppressed when it comes from cache",
          {c.email.subject for c, _ in second if c.guard.suppress_draft}
          == suppressed)

    rule("guardrails that this mailbox cannot reach")
    # A money SOP and a refund promise both need a model to produce them. Drive
    # the same code directly rather than leaving the rules unexercised.
    from support_triage_agent.classify import Classification
    from support_triage_agent.draft import Draft
    money = guardrails.check(
        Classification("Payment", "A1", "High", "negative", "double charge"),
        Draft(reply="We are checking this for you."))
    check("a money SOP always goes to a human", money.needs_human)
    promise = guardrails.check(
        Classification("Payment", "B3", "High", "negative", "double charge"),
        Draft(reply="Your refund will be processed today."))
    check("a draft promising a refund is flagged", promise.needs_human)
    human = guardrails.check(
        Classification("Payment", "B3", "Low", "neutral", "question"),
        Draft(reply="You are speaking to a real person here, not a bot."))
    check("a draft claiming to be human is flagged", human.needs_human)

    conn.close()

    failed = [label for label, ok in checks if not ok]
    print("\n" + "-" * 70)
    print(f"{len(checks) - len(failed)} of {len(checks)} checks passed")
    for label in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
