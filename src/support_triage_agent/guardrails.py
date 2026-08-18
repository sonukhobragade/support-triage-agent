"""Hard rules, enforced in code after classify + draft. These are a safety net
on top of the prompt instructions — never the only line of defense."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config
from .classify import Classification

if TYPE_CHECKING:  # avoid circular import — only needed for type hints
    from .draft import Draft

# Phrases that would constitute promising money back. If a draft contains one
# of these for a money/policy SOP, flag for mandatory human review.
_REFUND_PROMISE_RE = re.compile(
    # 1. refund/credit/credits near a past-tense confirmation OR a future-tense
    #    Hinglish promise (ho jayega, kar denge, mil jayega, de denge...).
    r"\b(refund(ed)?|credit(ed)?|credits?)\b.{0,40}\b("
    r"approved|processed|done|issued|complete|initiated?|"
    r"kar diya|ho gaya|de diya|ho jayega|ho jayegi|ho jayenge|"
    r"kar denge|de denge|mil jayega|mil jayegi|karenge)\b"
    # 2. an English future promise: "(we) will/shall ... refund/credit",
    #    including "will be refunded".
    r"|\b(will|shall|going to)\b.{0,30}\b(refund(ed)?|credit(ed)?)\b",
    re.I,
)
# Claims that the correspondent is a person rather than an AI.
#
# The previous pattern was r"(real|human|actual|live).{0,20}(provider|provider|
# provider)" — the same word three times, left behind by a rename. It matched
# only the literal word "provider", so "you are speaking to a real person" and
# every other phrasing of the claim went straight through the guard it was
# supposed to trip.
_HUMAN_CLAIM_RE = re.compile(
    r"\b(?:i|we)\s+(?:am|are)\s+(?:a\s+)?(?:real|human|actual|live)\b"
    r"|\b(?:real|human|actual|live)\s+(?:person|agent|human|representative|"
    r"advisor|operator|team\s+member)\b"
    r"|\b(?:not|isn't|is\s+not)\s+(?:a\s+)?(?:bot|robot|ai|machine)\b"
    r"|\bspeaking\s+(?:to|with)\s+(?:a\s+)?(?:real|human|actual)\b",
    re.I,
)

# Subjects where a plausible draft is worse than no draft, because it anchors
# the reviewer on wording that may carry legal or safety weight. These are
# suppressed rather than merely flagged.
#
# EMPLOYEE_NAMES is configuration: naming a specific colleague in a customer
# reply is a category of mistake, but who your colleagues are is local to you.
_SUPPRESS_PATTERNS = {
    "legal action": r"\b(lawyer|solicitor|attorney|legal\s+action|sue|suing|"
                    r"litigation|court|subpoena|small\s+claims)\b",
    "regulator": r"\b(regulator|ombudsman|financial\s+conduct|data\s+protection\s+"
                 r"authority|gdpr\s+complaint|consumer\s+(?:forum|court)|"
                 r"trading\s+standards)\b",
    "chargeback": r"\b(chargeback|charge\s+back|dispute[sd]?\s+(?:the\s+)?"
                  r"(?:payment|transaction|charge)|section\s+75)\b",
    "self-harm": r"\b(suicide|suicidal|kill\s+myself|end\s+my\s+life|self[-\s]harm|"
                 r"harm\s+myself)\b",
}
_SUPPRESS_RES = {name: re.compile(pat, re.I) for name, pat in _SUPPRESS_PATTERNS.items()}


@dataclass
class GuardResult:
    needs_human: bool = False
    reasons: list[str] = field(default_factory=list)
    #: True when no draft may be shown to the reviewer at all.
    suppress_draft: bool = False

    def flag(self, reason: str) -> None:
        self.needs_human = True
        self.reasons.append(reason)

    def suppress(self, reason: str) -> None:
        """Flag for a human AND withhold the draft entirely."""
        self.flag(reason)
        self.suppress_draft = True


def suppression_reasons(source_text: str) -> list[str]:
    """Reasons this message may not carry a draft at all.

    Split out of `check` because it depends only on what the customer wrote.
    That lets a caller decide to withhold the draft *before* generating one:
    drafting a reply to a self-harm message and then discarding it costs a
    model call and produces text nobody should ever have seen.
    """
    reasons: list[str] = []
    for label, pattern in _SUPPRESS_RES.items():
        if pattern.search(source_text):
            reasons.append(
                f"Mentions {label} — routed to a human with no draft attached."
            )
    for name in config.EMPLOYEE_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", source_text, re.I):
            reasons.append(
                "Names a specific employee — routed to a human with no draft attached."
            )
    return reasons


def check(cls: Classification, draft: Draft, source_text: str = "") -> GuardResult:
    """Apply the hard rules.

    ``source_text`` is the customer's own subject and body. The suppression
    rules run against it rather than against the draft, because the question
    is what the customer raised, not how the model happened to word a reply.
    Callers that cannot supply it still get every other rule.
    """
    res = GuardResult()

    for reason in suppression_reasons(source_text or cls.summary or ""):
        res.suppress(reason)

    # Rule 4: no SOP match -> human.
    if cls.sop_id == config.NO_SOP:
        res.flag("No matching SOP — needs a human decision.")

    # Rule 2: never promise refunds/credits beyond SOP.
    if _REFUND_PROMISE_RE.search(draft.reply):
        res.flag("Draft appears to promise a refund/credit — must be verified.")

    # Money SOPs always get a human before sending.
    if cls.sop_id in config.MONEY_SOPS:
        res.flag(f"Money/escalation SOP ({cls.sop_id}) — verify proof before sending.")

    # Rule 3: never claim AI assistants are human.
    if _HUMAN_CLAIM_RE.search(draft.reply):
        res.flag("Draft may imply a human provider — AI assistants are AI.")

    return res


def redact_pii(text: str) -> str:
    """For logs only — never log raw customer identifiers."""
    text = re.sub(config.PHONE_PATTERN, "<phone>", text)
    text = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "<email>", text)
    return text
