"""Tests. The guardrail/ingest tests run offline. The classification eval is a
skeleton you fill with hand-labeled historical emails once the extract is in.

Run:  pytest -q
"""
from __future__ import annotations

from support_triage_agent import config
from support_triage_agent.ingest import Email, extract_identifiers, _is_support
from support_triage_agent.classify import Classification
from support_triage_agent.draft import Draft
from support_triage_agent.guardrails import check


def _email(body="", sender="user@example.com", subject="help"):
    return Email(date="", sender=sender, to="", subject=subject, labels="",
                 message_id="", in_reply_to="", references="", body=body)


# --- offline unit tests ---

def test_identifier_extraction():
    ids = extract_identifiers("paid Rs 500 via UTR: AX12345678 from 5550000001")
    assert ids.get("phone") == "5550000001"
    assert ids.get("transaction_id") == "AX12345678"
    assert ids.get("amount") == "500"


def test_support_detection():
    config.SUPPORT_ADDRESSES[:] = ["support@example.com"]
    assert _is_support("Example Support <support@example.com>") is True
    assert _is_support("angry.customer@example.com") is False


def test_guardrail_flags_no_sop():
    cls = Classification("Generic Technical Problem", config.NO_SOP, "Medium",
                         "neutral", "x")
    res = check(cls, Draft(reply="Namaste ji 🙏 we'll look into it."))
    assert res.needs_human is True


def test_guardrail_flags_refund_promise():
    cls = Classification("Return/Refund", "I1", "High", "negative", "x")
    res = check(cls, Draft(reply="Namaste ji 🙏 aapka refund ho gaya hai."))
    assert res.needs_human is True


def test_guardrail_flags_future_tense_refund_promise():
    # Soft/future-tense promises that slipped past the original past-tense-only
    # regex. Use a non-money SOP so ONLY the refund-promise regex can flag them.
    cls = Classification("Generic Technical Problem", "B1", "Medium", "neutral", "x")
    for reply in [
        "Namaste, we will verify and refund to your original payment method.",
        "Namaste ji 🙏 aapka refund ho jayega ya credits credit ho jayenge.",
        "Don't worry, hum aapko refund kar denge.",
        "Your amount will be refunded shortly.",
    ]:
        res = check(cls, Draft(reply=reply))
        assert res.needs_human is True, f"missed promise: {reply!r}"


def test_guardrail_ignores_proof_request_not_a_promise():
    # Asking for proof / saying it'll be CHECKED is not a promise — regex alone
    # (non-money SOP) must not flag it.
    cls = Classification("Generic Technical Problem", "B1", "Medium", "neutral", "x")
    res = check(cls, Draft(reply="Please share your UTR so we can check refund eligibility."))
    assert res.needs_human is False


def test_guardrail_flags_money_sop():
    # A1 is Refunds in data/sop_playbook.md and is listed in MONEY_SOPS.
    cls = Classification("Payment", "A1", "High", "negative", "x")
    res = check(cls, Draft(reply="Namaste ji 🙏 please share your UTR."))
    assert res.needs_human is True  # money SOP always verified by human


# --- classification eval (fill in from labeled history) ---

# Opt-in accuracy evaluation, not a unit test.
#
# It ships empty on purpose: a meaningful accuracy figure needs labelled mail
# from your own inbox, and that is exactly the data that must not live in a
# repository. Fill this in locally, against your own extract, and it runs.
#
# It is named eval_* rather than test_* so it does not appear as a passing
# test in CI. A test that unconditionally skips reads as coverage that does
# not exist, which is worse than no test at all.
LABELED_SAMPLES: list[tuple[str, str]] = [
    # (email_body, expected_category)
]


def eval_classification_accuracy():
    """Run manually: pytest tests/test_classify.py::eval_classification_accuracy"""
    if not LABELED_SAMPLES or not config.ANTHROPIC_API_KEY:
        import pytest
        pytest.skip("Add LABELED_SAMPLES and ANTHROPIC_API_KEY to run the eval.")
    from support_triage_agent.classify import classify
    correct = sum(
        classify(_email(body=b)).category == expected
        for b, expected in LABELED_SAMPLES
    )
    accuracy = correct / len(LABELED_SAMPLES)
    assert accuracy >= 0.8, f"classification accuracy {accuracy:.0%} below target"
