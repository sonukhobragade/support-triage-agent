"""Classify an inbound email: category, SOP id, priority, sentiment, identifiers."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from . import config
from . import llm
from .ingest import Email


@dataclass
class Classification:
    category: str
    sop_id: str
    priority: str
    sentiment: str
    summary: str
    identifiers: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def _sop_playbook() -> str:
    try:
        return config.SOP_PLAYBOOK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "(SOP playbook not found)"


_SYSTEM = """You are the triage classifier for Example Co customer support (an AI \
consultation marketplace). Classify each customer email using ONLY the SOP playbook \
provided. Do not invent categories or SOPs.

Return a SINGLE JSON object, no prose, with these keys:
- "category": one of {categories}
- "sop_id": the best-matching SOP id from the playbook (e.g. "A3", "E1", "J2"), \
or "NO_SOP" if none clearly fits
- "priority": one of {priorities}. Anything involving money/credits not received, \
double charges, failed refunds, or angry customers skews higher.
- "sentiment": "negative", "neutral", or "positive"
- "identifiers": object with any of {{phone, transaction_id, amount, provider_name, \
app_version}} found in the email (omit if absent)
- "summary": one short sentence describing the issue

SOP PLAYBOOK:
{playbook}
"""


def _system() -> str:
    return _SYSTEM.format(
        categories=config.PROBLEM_CATEGORIES,
        priorities=config.PRIORITIES,
        playbook=_sop_playbook(),
    )


def classify(email: Email) -> Classification:
    user = (
        f"FROM: {email.sender}\nSUBJECT: {email.subject}\n\n"
        f"BODY:\n{email.body[:4000]}"
    )
    data = llm.complete_json(config.CLASSIFY_MODEL, _system(), user, max_tokens=600)

    category = data.get("category", "")
    if category not in config.PROBLEM_CATEGORIES:
        category = "Generic Technical Problem"  # safe default
    priority = data.get("priority", "")
    if priority not in config.PRIORITIES:
        priority = "Medium"

    # Merge model-found identifiers with the regex-seeded ones from ingest.
    identifiers = dict(email.identifiers)
    identifiers.update(data.get("identifiers") or {})

    return Classification(
        category=category,
        sop_id=str(data.get("sop_id") or config.NO_SOP).upper().replace("NO_SOP", config.NO_SOP),
        priority=priority,
        sentiment=data.get("sentiment", "neutral"),
        summary=data.get("summary", email.subject[:120]),
        identifiers=identifiers,
    )
