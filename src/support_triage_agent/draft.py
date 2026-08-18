"""Draft a suggested reply, grounded in the SOP playbook and (if available) the
real reply library mined from history."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

from . import config
from . import llm
from . import retrieve
from .classify import Classification
from .ingest import Email


@dataclass
class Draft:
    reply: str
    info_to_collect: list[str] = field(default_factory=list)
    notes: str = ""


@lru_cache(maxsize=1)
def _sop_playbook() -> str:
    try:
        return config.SOP_PLAYBOOK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "(SOP playbook not found)"


def _library_categories(data: object) -> dict[str, list[str]]:
    """Normalise a reply library into {category: [reply, ...]}.

    Three shapes exist in the wild:

    * ``{"categories": {cat: [reply, ...]}}`` — what build_reply_library
      writes, and the one to prefer.
    * ``{"entries": [{"category": ..., "reply": ...}]}`` — the shipped
      synthetic example file.
    * ``{cat: [reply, ...]}`` — the original flat mapping, still readable so
      an existing library does not have to be rebuilt.

    The flat mapping is why this function exists. Metadata keys sat alongside
    the categories, so ``version: 1`` was read as a category whose examples
    were the integer 1, and slicing it raised TypeError on every draft.
    """
    if not isinstance(data, dict):
        return {}

    if isinstance(data.get("categories"), dict):
        source = data["categories"]
    elif isinstance(data.get("entries"), list):
        grouped: dict[str, list[str]] = {}
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                continue
            reply = entry.get("reply")
            if isinstance(reply, str) and reply:
                grouped.setdefault(entry.get("category", "General"), []).append(reply)
        return grouped
    else:
        source = data

    # Skip anything that is not a list of strings: metadata, or a malformed
    # entry that would otherwise blow up at format time.
    return {
        str(cat): [x for x in examples if isinstance(x, str)]
        for cat, examples in source.items()
        if isinstance(examples, list)
    }


@lru_cache(maxsize=1)
def _reply_library() -> str:
    """Few-shot examples grouped by category, if the mined library exists yet."""
    try:
        data = json.loads(config.REPLY_LIBRARY_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    lines = ["REAL REPLY EXAMPLES (match this voice):"]
    for category, examples in _library_categories(data).items():
        for ex in examples[:3]:
            lines.append(f"[{category}] {ex}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


_SYSTEM = """You draft customer support replies for Example Co. Write the reply \
the support team would actually send, in their voice: warm and short, \
acknowledging the customer's specific problem before asking for anything.

VOICE & STYLE:
- Warm and human, not robotic. 3-6 short lines. No corporate filler.
- Open with one line of genuine acknowledgement that names THEIR issue, not a \
generic "thank you for contacting us".
- Sign off every reply with exactly:
  Warm regards,
  Example Co Team
- Be specific to their email — never a template that ignores what they wrote.
- Ask for the FEWEST things needed to act. Don't dump a long form on them.

HARD RULES (safety — never violate):
- Ground the reply ONLY in the matched SOP and the real-reply examples below. \
Do not invent policy, features, compensation or timelines. If the SOP does not \
cover something, do not fill the gap from general knowledge.
- NEVER state a refund, credit or other remedy as already done. For any money \
issue, say the team will CHECK and resolve: conditional, never confirmed.
- NEVER claim to be human.
- If SOP id is NO_SOP, write only a brief honest acknowledgement that the team \
will look into it — do NOT improvise a solution.
- Do NOT ask for any identifier already in KNOWN IDENTIFIERS — acknowledge what \
they gave, ask only what's genuinely missing.

Every situation-specific rule — which problems exist, what each resolution is, \
and what proof to request — comes from the SOP PLAYBOOK below, not from this \
prompt. The playbook is the operator's to write and is loaded at runtime.

Keep it concise and specific to their issue.

Return a SINGLE JSON object, no prose:
- "reply": the drafted message text
- "info_to_collect": array of specific identifiers/proof to request (may be empty)
- "notes": short internal note for the human reviewer (not sent to customer)

SOP PLAYBOOK:
{playbook}

{reply_library}
"""


def _system() -> str:
    return _SYSTEM.format(playbook=_sop_playbook(), reply_library=_reply_library())


def draft_reply(email: Email, cls: Classification) -> Draft:
    # Keyword-RAG: ground this specific draft in the replies we actually sent to
    # the most-similar past emails. Goes in the user turn (per-email, varies) so
    # the system prompt stays byte-identical and cacheable. On a weak/empty match
    # this block is absent and the static reply library in the system prompt is
    # the fallback voice.
    examples = retrieve.top_k(f"{email.subject}\n{email.body}", k=5)
    grounding = ""
    if examples:
        joined = "\n---\n".join(examples)
        grounding = (
            "REAL REPLIES WE SENT TO VERY SIMILAR EMAILS — prefer this exact "
            "handling and voice over the generic examples:\n"
            f"{joined}\n\n"
        )

    user = (
        f"{grounding}"
        f"MATCHED SOP: {cls.sop_id}\nCATEGORY: {cls.category}\n"
        f"SENTIMENT: {cls.sentiment}\nKNOWN IDENTIFIERS: {cls.identifiers}\n\n"
        f"CUSTOMER EMAIL\nSUBJECT: {email.subject}\nBODY:\n{email.body[:4000]}"
    )
    data = llm.complete_json(config.DRAFT_MODEL, _system(), user, max_tokens=900)
    return Draft(
        reply=data.get("reply", "").strip(),
        info_to_collect=list(data.get("info_to_collect") or []),
        notes=data.get("notes", ""),
    )
