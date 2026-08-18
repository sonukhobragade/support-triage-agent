"""
Tests for notion_writer. No network: every case patches requests.post.

notion_writer had no tests, which is how a wrong property type survived. Each
case below is a defect found in review, not a restatement of the code.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from support_triage_agent import config, notion_writer


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "NOTION_TOKEN", "secret_token", raising=False)
    monkeypatch.setattr(config, "NOTION_DB_ID", "db-1234", raising=False)


class _Resp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"id": "page-1", "object": "page"}


class TestCredentialValidation:
    @pytest.mark.parametrize("token", ["", "   ", "\t", "\n"])
    def test_blank_token_fails_locally(self, monkeypatch, token):
        """Whitespace is truthy, so `Bearer    ` went to Notion instead of
        failing here with a message naming the missing setting."""
        monkeypatch.setattr(config, "NOTION_TOKEN", token, raising=False)
        monkeypatch.setattr(config, "NOTION_DB_ID", "db-1234", raising=False)
        with patch("support_triage_agent.notion_writer.requests.post") as post:
            with pytest.raises(RuntimeError, match="NOTION_TOKEN"):
                notion_writer.create_ticket(_ticket())
        post.assert_not_called()

    @pytest.mark.parametrize("db_id", ["", "   ", "\t"])
    def test_blank_db_id_fails_locally(self, monkeypatch, db_id):
        monkeypatch.setattr(config, "NOTION_TOKEN", "secret_token", raising=False)
        monkeypatch.setattr(config, "NOTION_DB_ID", db_id, raising=False)
        with patch("support_triage_agent.notion_writer.requests.post") as post:
            with pytest.raises(RuntimeError, match="NOTION_DB_ID"):
                notion_writer.create_ticket(_ticket())
        post.assert_not_called()

    def test_padded_token_is_not_sent_padded(self, monkeypatch):
        """Validating a stripped copy while sending the raw value still puts
        the padding on the wire."""
        monkeypatch.setattr(config, "NOTION_TOKEN", "  secret_token  ", raising=False)
        monkeypatch.setattr(config, "NOTION_DB_ID", "  db-1234  ", raising=False)
        with patch(
            "support_triage_agent.notion_writer.requests.post", return_value=_Resp()
        ) as post:
            notion_writer.create_ticket(_ticket())
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret_token"
        assert post.call_args.kwargs["json"]["parent"]["database_id"] == "db-1234"


class TestPropertyTypes:
    def test_status_uses_the_status_type(self, configured):
        """Status is a status property, not a select. Notion answers a select
        payload for it with HTTP 400, so no ticket was ever created."""
        props = _ticket().properties()
        assert "status" in props["Status"], "Status must be sent as a status property"
        assert "select" not in props["Status"]
        assert props["Status"]["status"]["name"] == config.DEFAULT_STATUS

    def test_select_properties_stay_select(self, configured):
        props = _ticket().properties()
        for name in ("Problem Category", "Priority", "Sentiment"):
            assert "select" in props[name], f"{name} is a select property"

    def test_title_and_checkbox_types(self, configured):
        props = _ticket().properties()
        assert "title" in props["Complaint Title"]
        assert isinstance(props["Needs Human"]["checkbox"], bool)


class TestRequest:
    def test_timeout_is_set(self, configured):
        """Without a timeout a stalled Notion blocks the whole run."""
        with patch(
            "support_triage_agent.notion_writer.requests.post", return_value=_Resp()
        ) as post:
            notion_writer.create_ticket(_ticket())
        assert post.call_args.kwargs["timeout"] > 0


# --- helpers -------------------------------------------------------------

def _ticket():
    """Build a Ticket from whatever the module's dataclasses require."""
    from support_triage_agent.classify import Classification
    from support_triage_agent.guardrails import GuardResult
    from support_triage_agent.ingest import Email

    email = Email(
        date="Thu, 4 Jun 2026 08:55:06 +0000",
        sender="customer@example.com",
        to="support@example.com",
        subject="Refund not received",
        labels="",
        message_id="<m1>",
        in_reply_to="",
        references="",
        body="I paid but have not received my refund.",
        from_support=False,
        bulk=False,
        identifiers={},
    )
    cls = Classification(
        category=config.PROBLEM_CATEGORIES[0],
        sop_id="SOP-1",
        priority="High",
        sentiment="negative",
        summary="Refund not received.",
        identifiers={},
    )
    from support_triage_agent.draft import Draft

    return notion_writer.Ticket(
        email=email,
        cls=cls,
        draft=Draft(reply="Namaste, we are looking into this."),
        guard=GuardResult(),
    )
