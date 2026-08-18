"""
Tests for the guardrails.

These are the rules that decide what must never reach a customer, so each one
is tested for the thing it is supposed to stop rather than for a happy path.

Two of these are regression tests. The human-claim pattern was
`(real|human|actual|live).{0,20}(provider|provider|provider)` — the same word
three times, left over from a rename — so it matched one literal word and let
every actual phrasing through. And the README promised that legal, regulatory,
chargeback and self-harm mail is "routed straight to a person with no draft
attached", which no code performed.
"""

from __future__ import annotations

import pytest

from support_triage_agent import config
from support_triage_agent.classify import Classification
from support_triage_agent.draft import Draft
from support_triage_agent.guardrails import check, suppression_reasons


def cls(sop_id: str = "C1", **kw) -> Classification:
    """C1 (Delivery) by default: a real SOP that does not touch money, so the
    money rule does not fire in tests that are not about the money rule."""
    fields = {
        "category": "General query / other",
        "sop_id": sop_id,
        "priority": "P3",
        "sentiment": "neutral",
        "summary": "",
    }
    fields.update(kw)
    return Classification(**fields)


def draft(reply: str = "Thanks, we are looking into this.") -> Draft:
    return Draft(reply=reply)


class TestSuppression:
    """Suppressed mail gets a human and no draft at all."""

    @pytest.mark.parametrize("text", [
        "I will be contacting my lawyer about this",
        "This is going to small claims court",
        "I am starting legal action",
    ])
    def test_legal_action_suppresses(self, text):
        r = check(cls(), draft(), source_text=text)
        assert r.suppress_draft is True
        assert r.needs_human is True

    @pytest.mark.parametrize("text", [
        "I am reporting you to the ombudsman",
        "I will file a GDPR complaint",
        "Taking this to the consumer forum",
    ])
    def test_regulator_suppresses(self, text):
        assert check(cls(), draft(), source_text=text).suppress_draft is True

    @pytest.mark.parametrize("text", [
        "I have requested a chargeback from my bank",
        "I disputed the transaction with my card issuer",
    ])
    def test_chargeback_suppresses(self, text):
        assert check(cls(), draft(), source_text=text).suppress_draft is True

    def test_self_harm_suppresses(self):
        r = check(cls(), draft(), source_text="I feel suicidal about this debt")
        assert r.suppress_draft is True
        assert any("self-harm" in reason for reason in r.reasons)

    def test_named_employee_suppresses(self, monkeypatch):
        monkeypatch.setattr(config, "EMPLOYEE_NAMES", ["Alice"])
        r = check(cls(), draft(), source_text="Alice promised me a refund")
        assert r.suppress_draft is True

    def test_employee_names_are_configuration_not_a_fixed_list(self, monkeypatch):
        monkeypatch.setattr(config, "EMPLOYEE_NAMES", [])
        assert check(cls(), draft(), source_text="Alice promised me a refund").suppress_draft is False

    def test_ordinary_mail_is_not_suppressed(self):
        r = check(cls(), draft(), source_text="How do I change my address?")
        assert r.suppress_draft is False

    def test_suppression_reads_the_customer_text_not_the_draft(self):
        """A draft that happens to say "court" must not suppress; what the
        customer raised is what matters."""
        r = check(cls(), draft("The tennis court booking is confirmed."),
                  source_text="How do I change my address?")
        assert r.suppress_draft is False


class TestHumanClaim:
    @pytest.mark.parametrize("reply", [
        "I am a real person, not a bot",
        "You are speaking to a human agent",
        "I'm not a bot, I promise",
        "This is a live representative replying",
    ])
    def test_claiming_to_be_human_is_flagged(self, reply):
        assert check(cls(), draft(reply)).needs_human is True

    def test_ordinary_reply_is_not_flagged(self):
        assert check(cls(), draft("We will check this and come back to you.")).needs_human is False


class TestMoneyPromises:
    @pytest.mark.parametrize("reply", [
        "Your refund has been processed",
        "We will refund you today",
        "aapka refund ho jayega",
    ])
    def test_promising_money_is_flagged(self, reply):
        assert check(cls(), draft(reply)).needs_human is True

    def test_conditional_wording_passes(self):
        r = check(cls(), draft("The team will check the payment and confirm."))
        assert r.needs_human is False


class TestSopRules:
    def test_no_sop_needs_a_human(self):
        assert check(cls(sop_id=config.NO_SOP), draft()).needs_human is True

    def test_money_sop_needs_a_human(self, monkeypatch):
        monkeypatch.setattr(config, "MONEY_SOPS", {"A1"})
        assert check(cls(sop_id="A1"), draft()).needs_human is True


class TestBackwardCompatibility:
    def test_source_text_is_optional(self):
        """Callers that cannot supply the customer text still get every other
        rule rather than an error."""
        assert check(cls(sop_id=config.NO_SOP), draft()).needs_human is True


class TestSuppressionDecidedBeforeDrafting:
    """`suppression_reasons` reads only the customer's text, which is what lets
    the pipeline skip the draft call entirely instead of generating a reply and
    throwing it away. Generating it cost a model call and produced wording for a
    self-harm or legal message that nobody should ever have read.
    """

    def test_agrees_with_check(self):
        text = "I have instructed a solicitor about this"
        assert suppression_reasons(text)
        assert check(cls(), draft(), source_text=text).suppress_draft is True

    def test_silent_on_ordinary_mail(self):
        text = "My order has not arrived yet, can you check?"
        assert suppression_reasons(text) == []
        assert check(cls(), draft(), source_text=text).suppress_draft is False

    def test_pipeline_does_not_draft_a_suppressed_message(self, monkeypatch):
        from support_triage_agent import pipeline
        from support_triage_agent.ingest import Email

        drafted = []
        monkeypatch.setattr(pipeline, "classify", lambda e: cls())
        monkeypatch.setattr(
            pipeline, "draft_reply",
            lambda e, c: drafted.append(e.message_id) or draft("hello"),
        )

        def email(mid: str, body: str) -> Email:
            return Email(date="", sender="a@example.net", to="support@example.com",
                         subject="help", labels="", message_id=mid,
                         in_reply_to="", references="", body=body)

        ok, _ = pipeline.build_ticket(email("<m1>", "where is my order?"))
        bad, _ = pipeline.build_ticket(email("<m2>", "I am contacting my lawyer"))

        assert drafted == ["<m1>"], "the suppressed message must never be drafted"
        assert ok.draft.reply == "hello"
        assert bad.draft.reply == ""
        assert bad.guard.suppress_draft is True
