"""Offline tests for feedback backfill thread-linking (no extract, no API)."""
from __future__ import annotations

from support_triage_agent.backfill_feedback import _parent_id
from support_triage_agent.ingest import Email


def _reply(in_reply_to="", references=""):
    return Email(date="", sender="support@example.com", to="", subject="Re: x",
                 labels="", message_id="<r1@x>", in_reply_to=in_reply_to,
                 references=references, body="reply", from_support=True)


def test_parent_from_in_reply_to():
    assert _parent_id(_reply(in_reply_to="<cust1@example.net>")) == "<cust1@example.net>"


def test_parent_from_references_uses_last():
    r = _reply(references="<a@x> <b@x> <cust2@example.net>")
    assert _parent_id(r) == "<cust2@example.net>"


def test_parent_none_when_unthreaded():
    assert _parent_id(_reply()) is None
