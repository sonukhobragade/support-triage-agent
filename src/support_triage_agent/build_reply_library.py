"""Mine data/reply_library.json from our own past support replies.

The extract holds thousands of replies WE sent (from_support). Most are canned
templates. This script cleans each reply (drops quoted history + signatures),
buckets it into a Problem Category by keyword, deduplicates near-identical
templates, and keeps the most frequent ones per category — frequency is a proxy
for "this is a real standard template", which is exactly the voice draft.py
should ground on.

No API calls — deterministic keyword bucketing. Output shape matches
draft._reply_library(): {"categories": {category: [reply_text, ...]}}.

Usage:
  python -m support_triage_agent.build_reply_library            # writes data/reply_library.json
  python -m support_triage_agent.build_reply_library --per-category 8 --min-len 40
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict

from . import config
from .ingest import load_emails
from .pairs import NOISE_REPLY as _NOISE_REPLY  # noqa: F401 (re-export for tests)
from .pairs import clean_reply  # noqa: F401 (re-export for callers/tests)

# Keyword buckets, checked in order. First match wins; else Generic Technical.
_BUCKETS = [
    ("Return/Refund", re.compile(r"\b(refund(ed)?|return|reversed|wallet credit)\b", re.I)),
    ("Payment", re.compile(r"\b(payment|debited|transaction|UTR|deducted|charged|credits?)\b", re.I)),
    # "provider" appeared three times in this alternation, left by a rename.
    # A duplicated branch is dead weight the regex engine still walks.
    ("Provider chat problem", re.compile(r"\b(provider|chat|consultation)\b", re.I)),
]
_GENERIC = "Generic Technical Problem"


def _bucket(text: str) -> str:
    for category, pat in _BUCKETS:
        if pat.search(text):
            return category
    return _GENERIC


def _norm(text: str) -> str:
    """Normalization key for dedup: lowercase, strip digits/punctuation, and key
    on the first ~120 chars.

    Dropping digits collapses templates differing only by amount/phone/order id.
    Keying on the prefix merges template variants that diverge only in a trailing
    clause (e.g. an extra parenthetical), so we count the template, not its
    near-identical instances.
    """
    t = re.sub(r"\d+", "#", text.lower())
    t = re.sub(r"[^a-z#\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine reply_library.json from past support replies")
    ap.add_argument("--input", default="data/mail_extract.jsonl.gz",
                    help="path to the jsonl(.gz) mail extract")
    ap.add_argument("--out", default=str(config.REPLY_LIBRARY_PATH),
                    help="output path for reply_library.json")
    ap.add_argument("--per-category", type=int, default=8,
                    help="max replies kept per category")
    ap.add_argument("--min-len", type=int, default=40,
                    help="skip cleaned replies shorter than this (chars)")
    args = ap.parse_args()

    library = _build_from(args.input, args.per_category, args.min_len)
    # Nested under "categories" so metadata cannot be mistaken for a category.
    # A flat mapping made draft._reply_library() read "version": 1 as a
    # category whose examples were the integer 1.
    document = {
        "version": 2,
        "note": "Mined from resolved tickets by build_reply_library.",
        "categories": library,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(document, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in library.values())
    print(f"Wrote {total} replies across {len(library)} categories -> {args.out}")
    for cat, examples in library.items():
        print(f"  {cat}: {len(examples)}")
    return 0


def _build_from(input_path: str, per_category: int, min_len: int) -> dict[str, list[str]]:
    groups: dict[str, dict[str, list]] = defaultdict(dict)
    for e in load_emails(input_path):
        if not e.from_support:
            continue
        text = clean_reply(e.body)
        if len(text) < min_len:
            continue
        if _NOISE_REPLY.search(text):
            continue  # automated ticket notification / marketing, not a real reply
        cat = _bucket(text)
        key = _norm(text)
        slot = groups[cat].get(key)
        if slot is None:
            groups[cat][key] = [1, text]
        else:
            slot[0] += 1
            if len(text) < len(slot[1]):
                slot[1] = text

    library: dict[str, list[str]] = {}
    for cat in config.PROBLEM_CATEGORIES:
        ranked = sorted(groups.get(cat, {}).values(), key=lambda s: s[0], reverse=True)
        library[cat] = [text for _count, text in ranked[:per_category]]
    return library


if __name__ == "__main__":
    raise SystemExit(main())
