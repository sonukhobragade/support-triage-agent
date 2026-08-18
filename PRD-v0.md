# Support Triage Agent — PRD v0

**Status:** v0, human-in-the-loop, draft-only — not wired to a live inbox
**Last updated:** 2026-06-04
**Repo:** `support-triage-agent`
**Examples:** `docs/BEFORE_AFTER.md`

---

## 0. Build status (as of 2026-06-04)

v0 works end-to-end against an exported mailbox: human-in-the-loop, draft-only. No corpus is published with this repository. Several Phase-2 items were pulled forward during the build.

| Capability | Status | Notes |
|---|---|---|
| Ingest + filtering | ✅ Done | mbox extract; subject-folded-into-body for empty-body mail |
| **Thread-stitching** | ✅ Done (pulled fwd) | same-sender messages merged into one conversation (7-day window, reply closes thread) |
| Classify (category/SOP/priority/sentiment/identifiers) | ✅ Done | Claude |
| Draft in the team's voice | ✅ Done | learned from the reply library, not prompt-specified |
| Reply library (mined from history) | ✅ Done | top replies per category, PII-redacted |
| **Keyword-RAG grounding** | ✅ Done (pulled fwd from Phase 3) | BM25 over the corpus of real (email→reply) pairs; drafts match real practice |
| Guardrails in code | ✅ Done | + future-tense refund-promise detection |
| Notion ticket write | ✅ Done | Customer Complaints DB |
| SQLite store (dedupe + cache + feedback + bulk emails) | ✅ Done | `data/tickets.db` |
| **Full categorization + product analytics** | ✅ Done (pulled fwd from Phase 2) | whole-corpus categorisation into sub-categories; refund root-causes; pushed to the tracker test cases |
| Automated tests | ✅ 69 passing | offline; the classification accuracy eval is opt-in (`eval_*`, not collected by pytest) and needs your own labelled mail |
| **Live Gmail ingest** | ⏳ Phase 1 | only remaining piece to operate on real incoming mail |
| **Fraud & abuse handling** | 🔜 Phase 2 | fake claims, serial refunders, abusive senders |

Cost so far: full analysis < $1, full triage+draft of the whole corpus in the tens of dollars (one-time, cached); ~1.5¢ per new email.

---

## 1. Background

The problem: support requests arrive by email, and a support team that has
already written down how it handles them still answers each one by hand.

The agent assumes you have, in Notion or equivalent:

- a **ticket database** — categories, priority, status, customer identifiers;
- an **SOP map** — category → sub-category → handling steps and reply templates;
- optionally **FAQs** and an on-call roster.

Property and database names are configuration; see `.env.example`.

The input is a historical support mailbox exported via Google Takeout. A corpus of a few thousand messages is enough to validate the taxonomy and build a reply library from how the team actually responds, rather than guessing.

The goal of v0 is to remove the repetitive triage-and-draft work from the team while keeping a human firmly in control of every customer-facing reply.

## 2. Goal (v0)

An **on-demand agent** that turns incoming support emails into **review-ready Notion tickets** — classified, prioritized, and with a **drafted reply grounded in your own SOPs and past replies** — where a **human always sends** the final response.

## 3. Non-goals (explicitly out of scope for v0)

- Auto-sending replies to customers.
- An always-on / scheduled production service (v0 runs on demand).
- Slack ingestion (Slack remains internal context only).
- The daily digest / analytics (Phase 2).
- Qdrant / vector RAG retrieval (v0 injects SOPs directly into the prompt).
- Automatic ticket assignment/routing to CS agents.

## 4. Users

- **Support reviewer** (primary) — opens the generated ticket, edits the drafted reply, sends it, updates status.
- **Support lead** (secondary) — visibility into volume, categories, and gaps.

## 5. Pipeline overview

```
Emails (mbox extract now / Gmail later)
        │
        ▼
   [1] Ingest        normalize sender, subject, body, thread, identifiers
        │
        ▼
   [2] Classify      Problem Category + SOP id + Priority + sentiment
        │
        ▼
   [3] Draft         reply grounded in SOP + reply library + FAQs
        │            + the identifiers the SOP requires us to collect
        ▼
   [4] Write ticket  create page in Customer Complaints DB (Status = New)
        │
        ▼
   [5] Human review  edit draft → send from Gmail → update status
```

## 6. Functional requirements

### 6.1 Ingest
- v0 input is the cleaned mbox extract (`mail_extract.jsonl.gz`) filtered to support mail.
- Each record: date, from, to, subject, gmail_labels, message_id, in_reply_to, references, body.
- Group messages into threads (via subject + In-Reply-To/References) so a customer query is paired with any existing reply.
- Skip threads already resolved/answered (avoid duplicate drafts).

### 6.2 Classify
For each new customer email, produce:
- **Problem Category** — one of: `Payment`, `Generic Technical Problem`, `Provider chat problem`, `Return/Refund`.
- **SOP id** — the matching sub-category SOP from your playbook; or `NO_SOP` if none fits.
- **Priority** — one of: `Low`, `Medium`, `High`, `Critical`, `Urgent`.
- **Sentiment** — negative / neutral / positive (drives priority calibration).
- **Extracted identifiers** — registered phone, transaction/UTR id, provider name, amount, app version — whatever is present.

### 6.3 Draft
- Generate a suggested reply **grounded only in** the matched SOP steps + template, FAQs, and the real reply library mined from history.
- Match the team's voice, including its language mix, by learning it from past replies.
- Explicitly list the **identifiers/proof to collect** per the SOP.
- If `NO_SOP`: do not invent policy — produce a short holding acknowledgement and flag for a human.

### 6.4 Write ticket (mapping to the Notion database schema)

| Ticket field | Source |
|---|---|
| Complaint Title | one-line summary of the issue |
| Description | short summary of the customer's message |
| Problem Category | from classify |
| Priority | from classify |
| Status | `New` |
| Date Received | email date |
| Customer Contact No | extracted (if present) |
| Transaction ID | extracted (if present) |
| Assigned To | left blank (human assigns) |
| Resolution Date | left blank |

Page **body** holds: the **drafted reply**, the **matched SOP id + reasoning**, the **info-to-collect** checklist, and the original email quote.

### 6.5 Human review
- Reviewer opens the ticket, edits the draft, sends from Gmail, sets Status (`In Progress` / `Pending Customer Response` / `Resolved`).
- v0 success depends on drafts being good enough to send with light edits.

## 7. Guardrails (hard rules — domain-critical)

1. **Never auto-send.** Every reply is a draft for human review.
2. **Never promise refunds/credits beyond the SOP.** Money/credits issues → escalate + collect proof, never resolved autonomously.
3. **Never misrepresent AI assistants as human** (SOP B1).
4. **No SOP match → tag `Needs Human`.** Do not fabricate policy for TBD cases (Incorrect Recharge, Offers, etc.).
5. **PII discipline.** Customer phone/transaction data stays inside Notion + the local pipeline; never placed in logs or third-party endpoints beyond the model call needed to draft.

## 8. Grounding data (dependency, in progress)

Built from the corpus analysis:
- **Taxonomy validation** — real distribution across the 4 categories + sub-types; surfaces categories that exist in volume but have no SOP.
- **Reply library** — actual sent replies grouped by category → become few-shot examples and templates so drafts sound like us.
- **Gap list** — frequency-ranked queries with no SOP, for a human to decide on.

## 9. Tech stack

- **Language:** Python 3 (matches existing tooling, pytest discipline).
- **Reasoning:** Claude (Anthropic API) for classify + draft.
- **Email:** mbox extract for v0 dev; Gmail API for live ingest in Phase 2.
- **Tickets:** Notion API → existing Customer Complaints DB.

## 10. Proposed repo structure

```
support-triage-agent/
├── PRD-v0.md
├── README.md
├── pyproject.toml
├── .env.example                 # ANTHROPIC_API_KEY, NOTION_TOKEN, NOTION_DB_ID
├── data/
│   ├── mail_extract.jsonl.gz    # cleaned support mail (input)
│   ├── sop_playbook.md          # SOP playbook (grounding)
│   └── reply_library.json       # mined from history (grounding)
├── src/support_triage_agent/
│   ├── ingest.py                # load + thread the mbox extract
│   ├── classify.py              # category / SOP / priority / sentiment
│   ├── draft.py                 # grounded reply generation
│   ├── notion_writer.py         # create tickets in Customer Complaints
│   ├── guardrails.py            # the hard rules in code
│   └── pipeline.py              # orchestrates 1→4, dry-run by default
└── tests/
    └── test_classify.py         # eval set from labeled historical emails
```

## 11. Success metrics (v0)

- **Classification accuracy** ≥ target on a hand-labeled sample (category + SOP).
- **Draft acceptance** — % of drafts a reviewer sends with only light edits.
- **Coverage** — % of tickets matched to an SOP vs. `Needs Human`.
- **Time saved** — median reviewer time per ticket vs. baseline.

## 12. Risks / open questions

- Classification quality on mixed-language emails — needs the eval set.
- TBD SOPs (Incorrect Recharge, Offers, "Is my info safe", session recording) — block full coverage until policy is set.
- PII handling review before any data leaves the local environment.
- Gmail access path for Phase 2 (connector vs. Gmail API service account).

## 13. Phases after v0

> Updated 2026-06-04 — RAG and analytics were pulled forward into v0 (see §0). Revised plan:

- **Phase 1 — Go live (read-only inbox).** Connect Gmail (read-only), run the existing pipeline on real incoming mail → tickets in Notion. Still draft-only; human sends. The only new piece is the inbox connector. (PII review + Gmail auth path before live.)
- **Phase 2 — Fraud & abuse handling.** Fake refund claims (cross-check txn/UTR vs records), serial refunders (per-phone history, already in the local DB), abusive/threatening senders (sentiment + abuse detection → route to senior), spam/extortion (separate queue). Foundation already exists via the per-customer DB.
- **Phase 3 — Scale & selective automation.** Weekly trend digest, vector RAG over a growing KB (upgrade from keyword BM25), ticket routing, and — only after supervised draft-acceptance evaluation — auto-send for a single proven-safe category. Money & no-SOP cases always stay human.

## 14. What v0 surfaced (product insight)

The categorisation was a by-product of building the reply library, and it turned
out to be the more valuable half. Reading every ticket at once answers a question
no individual ticket does: which product gaps are generating the mail.

What that looks like in practice, stated as patterns rather than figures,
since the numbers depend entirely on whose inbox you run it over:

- One refund driver usually dominates, and it is often a billing flow the user
  could not undo themselves.
- A large share of support is people trying to reverse something. Cancel, delete,
  refund. Each one is a missing self-serve button.
- A meaningful slice of a support inbox is not support at all. Sales pitches,
  recruiters, spam. Worth measuring before you size a team against ticket count.
- The recurring functional bug usually shows up as a cluster of near-identical
  complaints, which is exactly what a category count surfaces and a reading of
  individual tickets does not.

**The takeaway that generalises:** the biggest reducers of support volume are
product fixes, not better agent tuning. Triage makes the queue cheaper to serve.
Fixing what generates the queue makes it smaller.
