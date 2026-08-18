"""Create tickets in the Customer Complaints database via the Notion REST API.

Property names map EXACTLY to the existing schema:
  Complaint Title (title), Description (rich_text), Problem Category (select),
  Priority (select), Status (status), Date Received (date),
  Customer Contact No (rich_text), Transaction ID (rich_text), Provider Name (rich_text)
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

from . import config
from .classify import Classification
from .draft import Draft
from .guardrails import GuardResult
from .ingest import Email

_API = "https://api.notion.com/v1/pages"


def _iso_date(raw: str) -> str:
    """RFC2822 email Date header -> ISO-8601 for Notion. Falls back to raw."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return raw


def _headers() -> dict:
    # Stripped here as well as at validation: validating a stripped copy while
    # sending the raw value still puts the padding on the wire.
    return {
        "Authorization": f"Bearer {(config.NOTION_TOKEN or '').strip()}",
        "Notion-Version": config.NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rich(text: str) -> list:
    return [{"type": "text", "text": {"content": text[:2000]}}] if text else []


def _para(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich(text)},
    }


def _heading(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": _rich(text)},
    }


@dataclass
class Ticket:
    """A fully-assembled ticket, ready to write or to print in a dry run."""
    email: Email
    cls: Classification
    draft: Draft
    guard: GuardResult

    def title(self) -> str:
        base = self.cls.summary or self.email.subject or "Support request"
        return ("[NEEDS HUMAN] " if self.guard.needs_human else "") + base[:180]

    def properties(self) -> dict:
        ident = self.cls.identifiers
        props: dict = {
            "Complaint Title": {"title": _rich(self.title())},
            "Problem Category": {"select": {"name": self.cls.category}},
            "SOP": {"rich_text": _rich(self.cls.sop_id)},
            "Priority": {"select": {"name": self.cls.priority}},
            "Sentiment": {"select": {"name": self.cls.sentiment}},
            # A status property is not a select. Notion rejects
            # {"select": ...} for it with HTTP 400, so every ticket creation
            # failed against the documented schema.
            "Status": {"status": {"name": config.DEFAULT_STATUS}},
            "Needs Human": {"checkbox": bool(self.guard.needs_human)},
            "From": {"rich_text": _rich(self.email.sender)},
            "Draft Reply": {"rich_text": _rich(self.draft.reply[:1900])},
            # Promised by the ticket contract but never serialised, so the
            # reviewer opened a ticket with no summary of what was asked.
            "Description": {"rich_text": _rich(self.cls.summary[:1900])},
        }
        if self.email.date:
            props["Date Received"] = {"date": {"start": _iso_date(self.email.date)}}
        if ident.get("phone"):
            props["Customer Contact No"] = {"rich_text": _rich(str(ident["phone"]))}
        if ident.get("transaction_id"):
            props["Transaction ID"] = {"rich_text": _rich(str(ident["transaction_id"]))}
        return props

    def children(self) -> list:
        blocks = [_heading("Suggested reply (review before sending)"),
                  _para(self.draft.reply or "(no draft)")]
        if self.draft.info_to_collect:
            blocks.append(_heading("Info to collect"))
            blocks += [_para(f"• {x}") for x in self.draft.info_to_collect]
        blocks.append(_heading("Triage"))
        blocks.append(_para(
            f"SOP: {self.cls.sop_id} | sentiment: {self.cls.sentiment} | "
            f"reviewer note: {self.draft.notes}"
        ))
        if self.guard.reasons:
            blocks.append(_heading("⚠ Needs human"))
            blocks += [_para(f"• {r}") for r in self.guard.reasons]
        blocks.append(_heading("Original email"))
        blocks.append(_para(f"From: {self.email.sender}\nSubject: {self.email.subject}"))
        blocks.append(_para(self.email.body[:1800]))
        return blocks


def create_ticket(ticket: Ticket) -> dict:
    """POST a new page to the Customer Complaints database. Returns the API JSON."""
    # Stripped before checking: a value of "   " is truthy, so a token of
    # spaces passed validation and went out as `Authorization: Bearer    `,
    # failing at Notion instead of locally with a clear message.
    token = (config.NOTION_TOKEN or "").strip()
    db_id = (config.NOTION_DB_ID or "").strip()
    if not token:
        raise RuntimeError("NOTION_TOKEN is not set.")
    if not db_id:
        raise RuntimeError(
            "NOTION_DB_ID is not set. There is no default; set it to the "
            "database you want tickets written to."
        )
    payload = {
        "parent": {"database_id": db_id},
        "properties": ticket.properties(),
        "children": ticket.children(),
    }
    resp = requests.post(_API, headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()
