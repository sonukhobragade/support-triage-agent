"""Offline tests for reply-library mining (no API, no extract file needed)."""
from __future__ import annotations

from support_triage_agent.build_reply_library import clean_reply, _bucket, _norm, _NOISE_REPLY


def test_clean_strips_quoted_history_and_signoff():
    body = (
        "Namaste,\n\nKindly share your registered number.\n\n"
        "Regards,\nTeam Example Co\n\n"
        "On Mon, 1 Jan 2026 at 10:00, Customer <c@example.com> wrote:\n"
        "> my credits are missing\n"
    )
    out = clean_reply(body)
    assert out.startswith("Namaste,")
    assert "registered number" in out
    assert "Regards" not in out
    assert "wrote:" not in out
    assert "credits are missing" not in out  # quoted customer text dropped


def test_clean_redacts_pii():
    out = clean_reply("Namaste, your number 5550000002 and a@example.org are noted.")
    assert "5550000002" not in out
    assert "a@example.org" not in out
    assert "<phone>" in out and "<email>" in out


def test_bucketing():
    assert _bucket("We have refunded INR 199 to your wallet") == "Return/Refund"
    assert _bucket("Your payment was debited but credits not added") == "Payment"
    assert _bucket("The provider chat was not responding") == "Provider chat problem"
    assert _bucket("The app keeps crashing on launch") == "Generic Technical Problem"


def test_noise_filter_catches_ticketing_system():
    assert _NOISE_REPLY.search("Your ticket - Support - has been closed")
    assert _NOISE_REPLY.search('Ticket "#1273 - " has been reopened, visit freshdesk.com')
    assert _NOISE_REPLY.search("There is a new comment in the ticket")
    assert not _NOISE_REPLY.search("Namaste, kindly share your registered number")


def test_norm_collapses_digits():
    # Templates differing only by amount/order id share a dedup key.
    assert _norm("We refunded INR 199 to order 5521") == _norm("We refunded INR 50 to order 9")


def test_norm_merges_on_shared_prefix():
    # Divergence past the 120-char prefix window → same key (template variants).
    base = "Namaste, " + "kindly share your registered number on the app. " * 3
    assert _norm(base + "Variant A trailing clause one") == _norm(base + "Variant B different end")
