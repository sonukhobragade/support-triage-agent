"""Keyword-RAG: retrieve our most-similar past replies for a new email.

Builds a BM25 index over the *customer email text* of every (customer email ->
our reply) pair in the extract, and returns the paired *replies* as grounding
for drafting. Stdlib only — no embeddings, no vector store, no deps.

draft.py calls top_k(query) to ground a draft in how the team actually answered
similar emails, falling back to the static reply library on a weak match.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

from . import config
from .pairs import customer_reply_pairs

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Very common English/Hinglish + boilerplate words that add no retrieval signal.
_STOP = frozenset(
    "the a an and or to of in is it for on at be was i you we my your our this "
    "that have has not no please kindly namaste sir hi hello regards thanks ji "
    "am are will can could would do does me us as so but if".split()
)

# BM25 parameters (standard defaults).
_K1 = 1.5
_B = 0.75


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower())
            if t not in _STOP and len(t) > 1]


@dataclass
class _Index:
    docs: list[list[str]]          # tokenized customer emails
    replies: list[str]             # paired reply text (the grounding payload)
    idf: dict[str, float]
    avg_len: float


@lru_cache(maxsize=1)
def _index() -> _Index:
    """Build (once) the BM25 index over all customer->reply pairs in the extract.

    If the extract is absent (e.g. a deployed container with no mail history),
    return an empty index — top_k then yields [] and draft falls back to the
    static reply library / SOP. RAG grounding degrades gracefully, never crashes.
    """
    extract = config.DATA_DIR / "mail_extract.jsonl.gz"
    if not extract.exists():
        return _Index(docs=[], replies=[], idf={}, avg_len=0.0)
    pairs = customer_reply_pairs(str(extract))
    docs, replies = [], []
    for parent, reply in pairs:
        docs.append(_tokens(f"{parent.subject}\n{parent.body}"))
        replies.append(reply)

    n = len(docs)
    df: dict[str, int] = {}
    for doc in docs:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1
    # BM25 idf with +1 smoothing (always positive).
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    avg_len = (sum(len(d) for d in docs) / n) if n else 0.0
    return _Index(docs=docs, replies=replies, idf=idf, avg_len=avg_len)


def _score(query_terms: list[str], doc: list[str], idx: _Index) -> float:
    if not doc:
        return 0.0
    tf: dict[str, int] = {}
    for t in doc:
        tf[t] = tf.get(t, 0) + 1
    dl = len(doc)
    s = 0.0
    for q in query_terms:
        f = tf.get(q)
        if not f:
            continue
        idf = idx.idf.get(q, 0.0)
        s += idf * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / idx.avg_len))
    return s


def top_k(query: str, k: int = 5, min_score: float = 1.0) -> list[str]:
    """Return up to k of our past replies whose customer email best matches the
    query. Empty list if the index is empty or the best match scores below
    min_score (caller should then fall back to the static reply library)."""
    idx = _index()
    if not idx.docs:
        return []
    qt = _tokens(query)
    if not qt:
        return []
    scored = sorted(
        ((_score(qt, doc, idx), i) for i, doc in enumerate(idx.docs)),
        reverse=True,
    )
    out = []
    for score, i in scored[:k]:
        if score < min_score:
            break
        out.append(idx.replies[i])
    return out
