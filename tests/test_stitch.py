"""Offline tests for thread-stitching (no extract, no API)."""
from __future__ import annotations

from support_triage_agent.ingest import Email, stitch_threads

CUST = "cust@example.com"
SUP = "support@example.com"


def _msg(body, date, sender=CUST, to=SUP, support=False, subject=""):
    return Email(date=date, sender=sender, to=to, subject=subject, labels="",
                 message_id=f"<{date}@x>", in_reply_to="", references="",
                 body=body, from_support=support)


def test_merges_two_close_customer_messages():
    msgs = [
        _msg("Amount issues", "Thu, 4 Jun 2026 08:52:49 +0000"),
        _msg("Alice 5550000001", "Thu, 4 Jun 2026 08:55:06 +0000"),
    ]
    out = stitch_threads(msgs)
    assert len(out) == 1
    assert "Amount issues" in out[0].body and "5550000001" in out[0].body
    assert out[0].identifiers.get("phone") == "5550000001"  # re-extracted from merge


def test_reply_in_between_splits_thread():
    msgs = [
        _msg("issue one", "Mon, 1 Jun 2026 10:00:00 +0000"),
        _msg("Resolved?", "Mon, 1 Jun 2026 12:00:00 +0000", sender=SUP, to=CUST, support=True),
        _msg("new issue", "Mon, 1 Jun 2026 14:00:00 +0000"),
    ]
    out = stitch_threads(msgs)
    assert len(out) == 2  # reply between → two separate threads


def test_gap_beyond_window_splits():
    msgs = [
        _msg("first", "Mon, 1 Jun 2026 10:00:00 +0000"),
        _msg("much later", "Mon, 15 Jun 2026 10:00:00 +0000"),  # 14 days > 7
    ]
    out = stitch_threads(msgs, window_days=7)
    assert len(out) == 2


def test_within_window_no_reply_merges():
    msgs = [
        _msg("part one", "Mon, 1 Jun 2026 10:00:00 +0000"),
        _msg("part two", "Wed, 3 Jun 2026 10:00:00 +0000"),  # 2 days < 7
    ]
    out = stitch_threads(msgs, window_days=7)
    assert len(out) == 1


def test_different_senders_not_merged():
    msgs = [
        _msg("a", "Mon, 1 Jun 2026 10:00:00 +0000", sender="a@example.com"),
        _msg("b", "Mon, 1 Jun 2026 10:01:00 +0000", sender="b@example.com"),
    ]
    out = stitch_threads(msgs)
    assert len(out) == 2
