#!/usr/bin/env python3
"""
Build a reply library from a public support dataset.

    python scripts/fetch_public_replies.py --out data/reply_library.public.json

Why this exists: `data/reply_library.json` is four hand-written replies. It
shows the schema and it lets the tests run, and it is far too thin to show what
retrieval actually does. BM25 over four documents retrieves the same document
every time.

This builds a real library, a few hundred replies across the categories the
classifier knows about, so a new email retrieves something that was not written
for it. That is the point of the whole retrieval design, and it is invisible at
four documents.

## Source and licence

Bitext's customer-support training dataset: 27k synthetic instruction/response
pairs across ordinary e-commerce support intents.

    https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset

It is **synthetic**, which is the reason it was chosen. Real support corpora are
scraped customer conversations, and pointing this repository at other people's
complaints would contradict everything its README says about handling support
mail.

Licence: **CDLA-Sharing-1.0**, which is not MIT. Publishing a derivative of the
data means publishing it under CDLA-Sharing-1.0 too. So the generated file is
gitignored and is not committed here. Fetch it yourself; do not vendor the
output into an MIT repository without dealing with that.

## What you get

Responses only, grouped into this repository's categories. Bitext uses templated
placeholders like `{{Order Number}}`; those are rewritten into readable
bracketed hints so a drafted reply does not carry template syntax through to a
reviewer.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict

DATASET_URL = (
    "https://huggingface.co/datasets/bitext/"
    "Bitext-customer-support-llm-chatbot-training-dataset/resolve/main/"
    "Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
)

DATASET_NAME = "Bitext customer-support dataset (synthetic)"
DATASET_LICENCE = "CDLA-Sharing-1.0"

# Bitext intent -> the category names used in data/reply_library.json.
#
# Mapped by intent rather than by Bitext's coarser `category` column: "ORDER"
# covers both "where is my order" and "cancel my order", which want different
# replies. Intents with no counterpart here are dropped rather than forced into
# an approximate bucket.
INTENT_CATEGORIES = {
    "get_refund": "Refunds",
    "check_refund_policy": "Refunds",
    "track_refund": "Refunds",
    "delete_account": "Account deletion / data removal",
    "switch_account": "Account deletion / data removal",
    "payment_issue": "Billing",
    "check_payment_methods": "Billing",
    "check_invoice": "Billing",
    "get_invoice": "Billing",
    "check_cancellation_fee": "Billing",
    "track_order": "Delivery",
    "delivery_period": "Delivery",
    "delivery_options": "Delivery",
    "change_shipping_address": "Delivery",
    "set_up_shipping_address": "Delivery",
    "cancel_order": "Cancellations",
    "change_order": "Cancellations",
    "place_order": "Orders",
    "contact_human_agent": "Needs human",
    "complaint": "Needs human",
    "review": "Feedback",
    "create_account": "Account access",
    "recover_password": "Account access",
    "edit_account": "Account access",
    "registration_problems": "Account access",
    "newsletter_subscription": "Subscriptions",
}

# Bitext templates look like {{Order Number}}. Left alone, a drafted reply hands
# a reviewer literal template syntax, which reads as a bug in this tool.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_WS_RE = re.compile(r"[ \t]+")


def humanise_placeholders(text: str) -> str:
    """Rewrite `{{Order Number}}` as `[order number]`."""
    return _PLACEHOLDER_RE.sub(lambda m: f"[{m.group(1).strip().lower()}]", text or "")


def clean_response(text: str) -> str:
    """Normalise one dataset response, or return "" if it is not usable."""
    text = humanise_placeholders(text)
    text = _WS_RE.sub(" ", text.replace("\r\n", "\n"))
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # Very short responses are acknowledgements with no content, and BM25 will
    # happily rank them top for a short query.
    if len(text) < 60:
        return ""
    return text


def _dedupe_key(text: str) -> str:
    """Near-duplicate key: the dataset repeats one reply across many phrasings
    of the same question, and a library of forty copies of one reply retrieves
    exactly as badly as a library of one."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower())[:120]


def build_library(rows, per_category: int = 40) -> dict:
    """
    Group cleaned responses into the reply-library schema.

    `rows` is any iterable of dicts with `intent` and `response` keys, so the
    tests can pass literals instead of a download.
    """
    categories: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()

    for row in rows:
        category = INTENT_CATEGORIES.get((row.get("intent") or "").strip())
        if not category:
            continue
        if len(categories[category]) >= per_category:
            continue

        text = clean_response(row.get("response", ""))
        if not text:
            continue

        key = _dedupe_key(text)
        if key in seen:
            continue
        seen.add(key)
        categories[category].append(text)

    return {
        "version": 2,
        # No timestamp: a generated_at that moves on every run makes two
        # otherwise identical libraries look like different files.
        "generated_at": "",
        "note": (
            f"Built by scripts/fetch_public_replies.py from the {DATASET_NAME}, "
            f"licensed {DATASET_LICENCE}. Synthetic data, not real customer mail. "
            f"Source: {DATASET_URL}"
        ),
        "source": {"name": DATASET_NAME, "licence": DATASET_LICENCE, "url": DATASET_URL},
        "categories": dict(sorted(categories.items())),
    }


def fetch_rows(url: str = DATASET_URL, timeout: int = 120):
    """Stream the dataset CSV and yield rows. About 19 MB."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        if resp.status != 200:
            raise RuntimeError(f"Download failed: HTTP {resp.status} for {url}")
        stream = io.TextIOWrapper(resp, encoding="utf-8", newline="")
        yield from csv.DictReader(stream)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", default="data/reply_library.public.json")
    parser.add_argument("--per-category", type=int, default=40)
    parser.add_argument("--url", default=DATASET_URL)
    args = parser.parse_args(argv)

    print(f"Fetching {args.url}")
    try:
        library = build_library(fetch_rows(args.url), per_category=args.per_category)
    except Exception as exc:  # noqa: BLE001 - the message is the whole point
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    total = sum(len(v) for v in library["categories"].values())
    if not total:
        # An empty library silently disables retrieval, and every draft falls
        # back to a bare acknowledgement with nothing saying why.
        print("Failed: no usable replies found — has the dataset schema changed?",
              file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(library, fh, indent=1, ensure_ascii=False)
        fh.write("\n")

    print(f"Wrote {args.out}: {total} replies across "
          f"{len(library['categories'])} categories")
    for name, replies in library["categories"].items():
        print(f"  {len(replies):3d}  {name}")
    print()
    print(f"Data: {DATASET_NAME}, licensed {DATASET_LICENCE}.")
    print("Synthetic. Not real customer mail. The licence is not MIT: publishing")
    print("a derivative of this data means publishing it under CDLA-Sharing-1.0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
