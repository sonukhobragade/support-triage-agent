"""Offline tests for BM25 retrieval (constructed index — no extract, no API)."""
from __future__ import annotations

import pytest

from support_triage_agent import retrieve


def test_tokenize_drops_stopwords_and_short():
    toks = retrieve._tokens("I have paid Rs 199 for the report download")
    assert "paid" in toks and "199" in toks and "report" in toks and "download" in toks
    assert "i" not in toks and "the" not in toks and "for" not in toks  # stopwords


@pytest.fixture
def index(monkeypatch):
    docs = [
        retrieve._tokens("payment debited but credits not added wallet"),
        retrieve._tokens("provider chat provider was rude and unhelpful"),
        retrieve._tokens("report download not working please help"),
    ]
    replies = ["REPLY-PAYMENT", "REPLY-PROVIDER", "REPLY-DOWNLOAD"]
    import math
    n = len(docs)
    df = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    avg = sum(len(d) for d in docs) / n
    idx = retrieve._Index(docs=docs, replies=replies, idf=idf, avg_len=avg)
    monkeypatch.setattr(retrieve, "_index", lambda: idx)
    return idx


def test_top_k_ranks_relevant_reply_first(index):
    hits = retrieve.top_k("my credits not added after payment", k=2, min_score=0.1)
    assert hits[0] == "REPLY-PAYMENT"


def test_top_k_threshold_returns_empty_on_no_match(index):
    # Query shares no terms with any doc → below threshold → empty (caller falls back).
    assert retrieve.top_k("profile insight profile", k=5, min_score=1.0) == []
