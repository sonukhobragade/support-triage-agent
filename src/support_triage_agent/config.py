"""Configuration: env vars + the fixed enums that mirror the Notion schema."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # override=True so .env wins over empty/placeholder vars already in the
    # shell environment (e.g. an exported but blank ANTHROPIC_API_KEY).
    load_dotenv(override=True)
except Exception:  # dotenv optional at runtime
    pass

# --- Paths ---
# DATA_DIR defaults to <repo>/data when run from a checkout, but when the package
# is pip-installed (e.g. in a Docker image) the repo-relative guess is wrong — set
# SUPPORT_DATA_DIR to point at the mounted data volume (the Dockerfile does this).
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("SUPPORT_DATA_DIR", str(ROOT / "data")))
SOP_PLAYBOOK_PATH = DATA_DIR / "sop_playbook.md"
# Overridable so a generated library can be used without overwriting the
# committed example, e.g. the one scripts/fetch_public_replies.py builds:
#   REPLY_LIBRARY=data/reply_library.public.json
REPLY_LIBRARY_PATH = Path(
    os.getenv("REPLY_LIBRARY", str(DATA_DIR / "reply_library.json"))
)
TICKETS_DB_PATH = Path(os.getenv("TICKETS_DB", str(DATA_DIR / "tickets.db")))

# --- PII detection ---
# One pattern, used by both identifier extraction and log redaction. They were
# separate copies that had already drifted: extraction accepted a +91 country
# code and redaction did not, so a number pulled out of an email was written to
# a log unredacted.
#
# The default accepts an optional country code and a 10-digit national number
# starting 2-9. It is deliberately wider than any single country's rules: for
# redaction, over-matching costs a masked order number, while under-matching
# leaks a customer's phone number into a log file. Override PHONE_PATTERN to
# tighten it for your locale.
PHONE_PATTERN = os.getenv(
    "PHONE_PATTERN",
    r"(?<!\d)(?:\+?\d{1,3}[-\s]?)?[2-9]\d{9}(?!\d)",
)

# --- Credentials ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
# No default. A concrete database id here points at one real workspace and
# silently writes there for anyone who forgets to set it.
NOTION_DB_ID = os.getenv("NOTION_DB_ID", "")
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")

SUPPORT_ADDRESSES = [
    a.strip().lower()
    for a in os.getenv("SUPPORT_ADDRESSES", "support@example.com").split(",")
    if a.strip()
]

# --- Noise filtering: drop automated/marketing mail that isn't a real customer ---
# Substrings matched against the lowercased sender address. Override via env
# (comma-separated) with NOISE_SENDERS.
_DEFAULT_NOISE_SENDERS = [
    "mailer-daemon", "no-reply", "noreply", "donotreply", "do-not-reply",
    "postmaster", "notification", "notifications",
    "shop.tiktok.com", "business.facebook.com", "mail.instagram.com",
    "linkedin.com", "internshala.com", "accounts.google.com",
    "googleplay-noreply", "play-developer-console",
]
NOISE_SENDERS = [
    s.strip().lower()
    for s in os.getenv("NOISE_SENDERS", ",".join(_DEFAULT_NOISE_SENDERS)).split(",")
    if s.strip()
]

# --- Models (verified current ids; override via env) ---
CLASSIFY_MODEL = os.getenv("CLASSIFY_MODEL", "claude-haiku-4-5-20251001")
DRAFT_MODEL = os.getenv("DRAFT_MODEL", "claude-sonnet-4-6")

# --- Transport ---
# "anthropic" (default) or "openai". The OpenAI transport speaks
# /v1/chat/completions, which Ollama, vLLM, LM Studio, llama.cpp, OpenRouter and
# OpenAI itself all serve, so the pipeline can run against a local model with no
# account and no key. That matters beyond convenience: this tool reads customer
# mail, and sending it to a hosted API is a decision somebody should make
# deliberately rather than inherit from a default.
LLM_TRANSPORT = os.getenv("LLM_TRANSPORT", "anthropic").strip().lower()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
# Ollama ignores the value but the header must exist.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# One local model usually serves both roles, so this overrides both model ids
# above when set. Leave unset to keep CLASSIFY_MODEL/DRAFT_MODEL distinct.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
# Local models on a laptop are slow. The Anthropic client uses 120s; a first
# call that has to load 9GB of weights from disk can exceed that.
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "300"))

# --- Enums; these must match your Notion database's select options exactly ---
# Notion rejects a select value that is not already an option on the property,
# so changing a category here means adding the same option there.
PROBLEM_CATEGORIES = [
    "Payment",
    "Generic Technical Problem",
    "Provider chat problem",
    "Return/Refund",
]
PRIORITIES = ["Low", "Medium", "High", "Critical", "Urgent"]
# Status options: New, In Progress, Pending Customer Response, Resolved.
DEFAULT_STATUS = "New"

# SOP ids whose replies touch money or an escalation. Guardrails force these to
# a human no matter how confident the draft looks. The ids are whatever your own
# playbook uses, so set MONEY_SOPS to a comma-separated list matching it; the
# default reflects the sample playbook in data/sop_playbook.md.
MONEY_SOPS = {
    s.strip()
    for s in os.getenv("MONEY_SOPS", "A1,A2").split(",")
    if s.strip()
}

NO_SOP = "NO_SOP"

# Naming a specific colleague in a customer reply is a category of mistake, but
# who your colleagues are is local to you. Set EMPLOYEE_NAMES to a
# comma-separated list; mail mentioning one is routed to a human with no draft.
EMPLOYEE_NAMES = [
    n.strip() for n in os.getenv("EMPLOYEE_NAMES", "").split(",") if n.strip()
]
NEEDS_HUMAN_TAG = "Needs Human"
