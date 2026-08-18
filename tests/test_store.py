"""Offline tests for the SQLite store + pipeline cache wiring (mock model)."""
from __future__ import annotations

from support_triage_agent import llm, store
from support_triage_agent.classify import Classification
from support_triage_agent.draft import Draft
from support_triage_agent.guardrails import GuardResult
from support_triage_agent.ingest import Email
from support_triage_agent import pipeline


def _email(mid="<m1@x>", body="credits not added", subject="help"):
    return Email(date="2026-06-04", sender="user@example.com", to="support@x",
                 subject=subject, labels="", message_id=mid, in_reply_to="",
                 references="", body=body)


def test_upsert_get_roundtrip():
    conn = store.init_db(":memory:")
    cls = Classification("Payment", "E1", "High", "negative", "credits missing",
                         {"phone": "5550000001"})
    draft = Draft(reply="Namaste ji 🙏 share your UTR.",
                  info_to_collect=["UTR"], notes="money sop")
    guard = GuardResult(needs_human=True, reasons=["Money SOP (E1)"])

    assert store.get(conn, "<m1@x>") is None
    store.upsert(conn, "<m1@x>", cls, draft, guard)

    got = store.get(conn, "<m1@x>")
    assert got is not None
    assert got["classification"].category == "Payment"
    assert got["classification"].sop_id == "E1"
    assert got["classification"].identifiers == {"phone": "5550000001"}
    assert got["draft"].info_to_collect == ["UTR"]
    assert got["needs_human"] is True
    assert got["guard_reasons"] == ["Money SOP (E1)"]


def test_mark_written_survives_recache():
    conn = store.init_db(":memory:")
    cls = Classification("Payment", "NO_SOP", "Medium", "neutral", "x")
    draft = Draft(reply="hi")
    store.upsert(conn, "<m1@x>", cls, draft, GuardResult())
    store.mark_written(conn, "<m1@x>", "page-123", "http://notion/page-123")

    # Re-cache (e.g. --no-cache rerun) must not forget the Notion page.
    store.upsert(conn, "<m1@x>", cls, draft, GuardResult())
    got = store.get(conn, "<m1@x>")
    assert got["notion_page_id"] == "page-123"


def test_feedback_record():
    conn = store.init_db(":memory:")
    store.record_feedback(conn, "<m1@x>", "agent draft", "human final reply")
    row = conn.execute("SELECT agent_draft, human_reply FROM feedback").fetchone()
    assert row["agent_draft"] == "agent draft"
    assert row["human_reply"] == "human final reply"


def _stub_llm(monkeypatch):
    """Force offline deterministic output and count model calls (no network)."""
    calls = {"n": 0}

    def fake_complete(model, system, user, max_tokens=1024):
        calls["n"] += 1
        return llm._mock(system, user)  # mock JSON, no API

    monkeypatch.setattr(llm, "complete", fake_complete)
    return calls


def test_build_ticket_caches_then_hits(monkeypatch):
    calls = _stub_llm(monkeypatch)
    conn = store.init_db(":memory:")
    email = _email()

    ticket1, hit1 = pipeline.build_ticket(email, conn=conn, use_cache=True)
    assert hit1 is False         # first time: computed + cached
    assert calls["n"] == 2       # classify + draft

    ticket2, hit2 = pipeline.build_ticket(email, conn=conn, use_cache=True)
    assert hit2 is True          # second time: served from db
    assert calls["n"] == 2       # NO extra model calls
    assert ticket2.cls.category == ticket1.cls.category
    assert ticket2.draft.reply == ticket1.draft.reply


def test_no_cache_forces_recompute(monkeypatch):
    calls = _stub_llm(monkeypatch)
    conn = store.init_db(":memory:")
    email = _email()
    pipeline.build_ticket(email, conn=conn, use_cache=True)
    _, hit = pipeline.build_ticket(email, conn=conn, use_cache=False)
    assert hit is False          # --no-cache ignores the cached row
    assert calls["n"] == 4       # both runs recompute (2 calls each)


def test_suppress_draft_survives_the_cache():
    """A withheld draft must still read as withheld on the next run.

    `store.get` rebuilt the GuardResult from needs_human and reasons only, so a
    cached legal or self-harm ticket came back with suppress_draft=False and was
    printed under a "--- draft ---" heading with the withheld banner gone.
    """
    from support_triage_agent.guardrails import GuardResult

    conn = store.init_db(":memory:")
    guard = GuardResult()
    guard.suppress("Mentions self-harm — routed to a human with no draft attached.")
    classification = Classification(
        category="Payment", sop_id="A1", priority="High",
        sentiment="negative", summary="customer in distress",
    )
    store.upsert(conn, "<m1>", classification, Draft(reply=""), guard)

    got = store.get(conn, "<m1>")
    assert got["suppress_draft"] is True
    assert got["needs_human"] is True


def test_older_db_without_the_column_is_migrated(tmp_path):
    """A db created before suppress_draft existed must still open."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE processed (message_id TEXT PRIMARY KEY, processed_at TEXT "
        "NOT NULL, category TEXT, sop_id TEXT, priority TEXT, sentiment TEXT, "
        "summary TEXT, identifiers TEXT, draft_reply TEXT, info_to_collect TEXT, "
        "notes TEXT, needs_human INTEGER NOT NULL DEFAULT 0, guard_reasons TEXT, "
        "notion_page_id TEXT, notion_url TEXT);"
        "INSERT INTO processed (message_id, processed_at, needs_human) "
        "VALUES ('<old>', '2026-01-01T00:00:00+00:00', 1);"
    )
    conn.commit()
    conn.close()

    migrated = store.init_db(path)
    cols = {r["name"] for r in migrated.execute("PRAGMA table_info(processed)")}
    assert "suppress_draft" in cols
    # Defaulting to 0 re-drafts the ticket rather than losing the verdict quietly.
    assert store.get(migrated, "<old>")["suppress_draft"] is False


def test_cached_ticket_is_still_withheld(monkeypatch):
    """The pipeline must rebuild suppress_draft from the cache, not just store it.

    store.get already returned the flag correctly while build_ticket dropped it
    on the floor, so a cached self-harm ticket came back reading `attached`.
    Testing the store layer alone did not catch that: this test drives
    build_ticket twice, which is where the loss happened.
    """
    conn = store.init_db(":memory:")
    email = Email(date="", sender="a@example.net", to="support@example.com",
                  subject="I need help", labels="", message_id="<sup1>",
                  in_reply_to="", references="",
                  body="I have instructed a solicitor about this.")

    monkeypatch.setattr(
        pipeline, "classify",
        lambda e: Classification(category="Payment", sop_id="C1", priority="Low",
                                 sentiment="negative", summary="threat of legal action"),
    )

    fresh, hit = pipeline.build_ticket(email, conn=conn)
    assert hit is False
    assert fresh.guard.suppress_draft is True

    cached, hit = pipeline.build_ticket(email, conn=conn)
    assert hit is True, "second call should come from the cache"
    assert cached.guard.suppress_draft is True
    assert cached.guard.reasons == fresh.guard.reasons
    assert cached.draft.reply == ""
