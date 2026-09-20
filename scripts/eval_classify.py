#!/usr/bin/env python3
"""Score the classifier against labelled emails, and report what a run costs.

    python scripts/eval_classify.py                  # the whole set
    python scripts/eval_classify.py --limit 10       # a cheaper sample
    python scripts/eval_classify.py --set data/my_set.jsonl

`scripts/demo.py` checks the decisions that are made in code. Those are worth
asserting, but they are not the classifier: the category, SOP and priority come
out of a model, and nothing in this repository said how often they were right.
This script answers that, on `data/eval_set.jsonl` — 60 invented emails, each
labelled with the category, SOP id and priority the playbook implies.

Three fields, scored separately, because they fail differently. Category is a
choice between four; SOP id is a lookup in a document the model is given; and
priority is a judgement call where the labels themselves are arguable. A single
"accuracy" number over the three would hide which one is actually weak.

Priority is also scored as "within one band" (High labelled as Critical, say),
because for a queue the neighbouring band is usually a survivable answer and an
exact-match number alone makes the field look worse than it behaves.

A real model is required. Mock mode returns a fixed answer and scoring it would
measure the mock, so the script refuses to run without a key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support_triage_agent import classify, config, llm  # noqa: E402
from support_triage_agent.ingest import Email  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Haiku 4.5 list price, US dollars per million tokens. Cache reads are 0.1x the
# input rate and cache writes 1.25x. Override for another model rather than
# editing: a stale constant reporting someone else's price is worse than none.
USD_PER_MTOK_IN = float(os.getenv("EVAL_USD_PER_MTOK_IN", "1.00"))
USD_PER_MTOK_OUT = float(os.getenv("EVAL_USD_PER_MTOK_OUT", "5.00"))
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def load_set(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def as_email(row: dict) -> Email:
    return Email(
        date="Mon, 5 Jan 2026 09:00:00 +0000",
        sender=row["sender"],
        to="support@example.com",
        subject=row["subject"],
        labels="",
        message_id=f"<{row['id']}@example.net>",
        in_reply_to="",
        references="",
        body=row["body"],
    )


def band_distance(a: str, b: str) -> int:
    """How many priority bands apart two labels are, or -1 if unrecognised."""
    order = config.PRIORITIES
    if a not in order or b not in order:
        return -1
    return abs(order.index(a) - order.index(b))


def cost_usd(stats: dict) -> float:
    """What the run's tokens cost at list price."""
    read = stats["read"] * CACHE_READ_MULTIPLIER
    write = stats["write"] * CACHE_WRITE_MULTIPLIER
    inp = (stats["uncached"] + read + write) / 1_000_000 * USD_PER_MTOK_IN
    out = stats["output"] / 1_000_000 * USD_PER_MTOK_OUT
    return inp + out


def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):5.1f}%" if d else "    — "


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "data" / "eval_set.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="score only the first N")
    ap.add_argument("--out", default="", help="write the JSON report here")
    args = ap.parse_args()

    if not llm.have_key():
        print(
            "No model configured, so there is nothing to score: mock mode returns\n"
            "one fixed classification and grading it would measure the mock.\n"
            "Set ANTHROPIC_API_KEY, or LLM_TRANSPORT=openai with OPENAI_BASE_URL\n"
            "pointing at a local model.",
            file=sys.stderr,
        )
        return 2

    rows = load_set(Path(args.set))
    if args.limit:
        rows = rows[: args.limit]

    results = []
    started = time.time()
    for i, row in enumerate(rows, 1):
        t0 = time.time()
        got = classify.classify(as_email(row))
        elapsed = time.time() - t0
        results.append({
            "id": row["id"],
            "subject": row["subject"],
            "latency_s": round(elapsed, 3),
            "category": {"want": row["category"], "got": got.category},
            "sop_id": {"want": row["sop_id"], "got": got.sop_id},
            "priority": {"want": row["priority"], "got": got.priority},
        })
        mark = "." if got.category == row["category"] else "x"
        print(mark, end="", flush=True)
        if i % 20 == 0:
            print(f" {i}/{len(rows)}", flush=True)
    print()
    wall = time.time() - started

    n = len(results)
    cat_ok = sum(r["category"]["want"] == r["category"]["got"] for r in results)
    sop_ok = sum(r["sop_id"]["want"] == r["sop_id"]["got"] for r in results)
    pri_ok = sum(r["priority"]["want"] == r["priority"]["got"] for r in results)
    pri_near = sum(
        0 <= band_distance(r["priority"]["want"], r["priority"]["got"]) <= 1
        for r in results
    )
    all_three = sum(
        r["category"]["want"] == r["category"]["got"]
        and r["sop_id"]["want"] == r["sop_id"]["got"]
        and r["priority"]["want"] == r["priority"]["got"]
        for r in results
    )

    print(f"\n{n} emails, {config.CLASSIFY_MODEL}\n")
    print(f"  category          {pct(cat_ok, n)}  ({cat_ok}/{n})")
    print(f"  sop_id            {pct(sop_ok, n)}  ({sop_ok}/{n})")
    print(f"  priority exact    {pct(pri_ok, n)}  ({pri_ok}/{n})")
    print(f"  priority ±1 band  {pct(pri_near, n)}  ({pri_near}/{n})")
    print(f"  all three         {pct(all_three, n)}  ({all_three}/{n})")

    # Per-category recall, because one weak category is invisible in the total.
    print("\n  category                      labelled  correct")
    by_cat = Counter(r["category"]["want"] for r in results)
    for cat in sorted(by_cat):
        ok = sum(
            r["category"]["got"] == cat
            for r in results
            if r["category"]["want"] == cat
        )
        print(f"  {cat:<28}  {by_cat[cat]:>8}  {pct(ok, by_cat[cat])}")

    confusions = Counter(
        (r["category"]["want"], r["category"]["got"])
        for r in results
        if r["category"]["want"] != r["category"]["got"]
    )
    if confusions:
        print("\n  confused                                   n")
        for (want, got), c in confusions.most_common():
            print(f"  {want} -> {got:<28} {c:>3}")

    sop_conf = Counter(
        (r["sop_id"]["want"], r["sop_id"]["got"])
        for r in results
        if r["sop_id"]["want"] != r["sop_id"]["got"]
    )
    if sop_conf:
        print("\n  sop confused                               n")
        for (want, got), c in sop_conf.most_common():
            print(f"  {want} -> {got:<32} {c:>3}")

    stats = dict(llm.cache_stats)
    total = cost_usd(stats)
    lat = sorted(r["latency_s"] for r in results)
    print(f"\n  tokens   in {stats['uncached']:,}  cache-read {stats['read']:,}  "
          f"cache-write {stats['write']:,}  out {stats['output']:,}")
    print(f"  cost     ${total:.4f} total, ${total / n:.5f} per email")
    print(f"  latency  p50 {lat[len(lat) // 2]:.2f}s  "
          f"p95 {lat[max(0, int(len(lat) * 0.95) - 1)]:.2f}s  "
          f"wall {wall:.1f}s")
    if stats["read"] == 0 and n > 1:
        print("\n  note: nothing was read from cache. The SOP-playbook prefix is")
        print("  below the model's minimum cacheable length, so every email pays")
        print("  full price for the same system prompt.")

    report = {
        "model": config.CLASSIFY_MODEL,
        "n": n,
        "accuracy": {
            "category": cat_ok / n,
            "sop_id": sop_ok / n,
            "priority_exact": pri_ok / n,
            "priority_within_one": pri_near / n,
            "all_three": all_three / n,
        },
        "tokens": stats,
        "cost_usd": {"total": total, "per_email": total / n},
        "latency_s": {
            "p50": lat[len(lat) // 2],
            "p95": lat[max(0, int(len(lat) * 0.95) - 1)],
            "wall": wall,
        },
        "results": results,
    }
    out = Path(args.out) if args.out else ROOT / "reports" / f"eval_{int(started)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    # --out can point anywhere, so relative_to would raise on a path outside
    # the checkout. Shorten where it is easy and print what was given otherwise.
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"\n  written  {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
