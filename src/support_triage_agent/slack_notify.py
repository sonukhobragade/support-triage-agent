"""Post a drafted ticket to Slack via Incoming Webhook (stdlib only, no deps).

Notify-only: a human still reads the thread in Notion/Gmail and sends the reply.
This just surfaces new tickets to the team channel as they're drafted.

Env (.env):
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...   # points at the target channel
    SLACK_ENABLED=true                                       # off switch

Disabled / unset webhook -> no-op (returns False), never raises into the pipeline.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _enabled() -> bool:
    return (os.getenv("SLACK_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
            and bool(os.getenv("SLACK_WEBHOOK_URL", "").strip()))


def _truncate(text: str, n: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _quote(text: str, n: int = 900) -> str:
    return _truncate(text, n).replace(chr(10), chr(10) + ">")


def _blocks(ticket) -> list[dict]:
    g = ticket.guard
    e = ticket.email
    c = ticket.cls
    pri = {"Urgent": ":red_circle:", "Critical": ":red_circle:", "High": ":large_orange_circle:",
           "Medium": ":large_yellow_circle:", "Low": ":white_circle:"}.get(c.priority, ":white_circle:")
    sent = {"negative": ":slightly_frowning_face:", "neutral": ":neutral_face:",
            "positive": ":slightly_smiling_face:"}.get((c.sentiment or "").lower(), "")
    status = ":rotating_light: NEEDS HUMAN" if g.needs_human else ":white_check_mark: Ready to send"
    dot = ":red_circle:" if g.needs_human else ":large_green_circle:"

    blocks = [
        # big header — colored dot (red=review / green=ready) + issue title
        {"type": "header", "text": {"type": "plain_text",
            "text": _truncate(f"{dot} {ticket.title()}", 145), "emoji": True}},
        # status pill + compact one-row fields (Category | SOP | Priority | Sentiment)
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"{status}   |   *{c.category}*   |   SOP `{c.sop_id}`   |   "
                    f"{pri} {c.priority}   |   {sent} {c.sentiment}"}]},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": f":bust_in_silhouette: {e.sender}   ·   {e.date}"}]},
        {"type": "divider"},
        # customer's original email — code block so long/link-heavy bodies (bank
        # notices, quoted threads) render verbatim instead of mangling in a quote.
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f":envelope_with_arrow: *Customer email* — _{e.subject or '(no subject)'}_\n"
                    f"```{_truncate(e.body, 2800)}```"}},
    ]
    if g.needs_human and g.reasons:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                       "text": f":warning: {'; '.join(g.reasons)}"}]})
    blocks.append({"type": "divider"})
    # Draft reply in a code block — Slack shows a one-click "Copy" button on hover
    # (top-right of the block). Code blocks copy as plain text (no blockquote bars).
    blocks.append({"type": "section", "text": {"type": "mrkdwn",
                   "text": ":pencil2: *Draft reply* — _hover & click the Copy button_\n"
                           f"```{_truncate(ticket.draft.reply, 2800)}```"}})
    if ticket.draft.info_to_collect:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                       "text": ":clipboard: *Collect:* " + ", ".join(ticket.draft.info_to_collect)}]})
    return blocks


def post_ticket(ticket, notion_url: str | None = None) -> bool:
    """Post a ticket summary to Slack. Returns True on 2xx, False if disabled/failed.

    Never raises — a Slack failure must not break the triage pipeline."""
    if not _enabled():
        return False
    blocks = _blocks(ticket)
    if notion_url:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open Notion ticket"},
             "url": notion_url}]})
    # Top-level blocks (NOT an attachment) — attachments auto-collapse with a
    # "Show more" when tall, which would hide the draft. Top-level never collapses.
    # The red/green status dot in the header carries the color signal instead.
    payload = {
        "text": f"New ticket: {ticket.title()}",  # fallback / notification text
        "blocks": blocks,
    }
    req = urllib.request.Request(
        os.environ["SLACK_WEBHOOK_URL"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        print(f"  -> Slack notify failed: {exc}")
        return False
