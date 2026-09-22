#!/usr/bin/env python3
"""Measure H1 recall, split by whether the customer names the misconduct.

    python scripts/eval_h1.py
    python scripts/eval_h1.py --set data/h1_set.jsonl --out reports/h1.json

H1 is the route that is supposed to draft nothing and hand the ticket to a
person: legal threats, regulators, chargebacks, self-harm, the conduct of a
named employee. `scripts/eval_classify.py` scores the whole classifier and
reported 85% on the SOP field, but only three of its 60 emails were H1, which
is not enough to say anything about the one route where a miss is expensive.

This set is 40 emails in 20 matched pairs. Both members of a pair describe the
same situation with the same labels; the only difference is wording. The
`named` member uses the word for the thing ("chargeback", "harassment", "I am
taking legal action"). The `described` member reports the same facts without
ever naming them ("I have asked my bank to reverse the payment", "he has
messaged me eleven times since Friday").

The pairing is the point. Each pair carries identical ground truth by
construction, so a gap between the two halves cannot be the labels being
arguable in one half: it is the classifier reacting to vocabulary. Recall is
reported per half, and every pair where one member was caught and the other was
not is printed, because those are the cases worth reading.

A real model is required; mock mode returns a fixed answer and grading it would
measure the mock.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support_triage_agent import classify, config, llm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Reuse the loader and the Email adapter from the main scorer rather than
# keeping a second copy that can drift out of step with it.
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "eval_classify", ROOT / "scripts" / "eval_classify.py"
)
_ec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ec)
load_set = _ec.load_set
as_email = _ec.as_email
cost_usd = _ec.cost_usd
pct = _ec.pct


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "data" / "h1_set.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--min-described-recall",
        type=float,
        default=0.0,
        help="exit 1 if recall on the described half falls below this (0-1). "
             "The floor belongs on the described half: the named half was "
             "already at 90% when the route was a keyword list, so it is the "
             "half that cannot detect the regression.",
    )
    args = ap.parse_args()

    if not llm.have_key():
        print(
            "No model configured, so there is nothing to score: mock mode\n"
            "returns one fixed classification. Set ANTHROPIC_API_KEY.",
            file=sys.stderr,
        )
        return 2

    rows = load_set(Path(args.set))
    if args.limit:
        rows = rows[: args.limit]

    results = []
    started = time.time()
    for i, row in enumerate(rows, 1):
        got = classify.classify(as_email(row))
        caught = got.sop_id == "H1"
        results.append({
            "id": row["id"],
            "pair": row["pair"],
            "phrasing": row["phrasing"],
            "topic": row["topic"],
            "subject": row["subject"],
            "got_sop": got.sop_id,
            "got_category": got.category,
            "got_priority": got.priority,
            "caught": caught,
        })
        print("." if caught else "x", end="", flush=True)
        if i % 20 == 0:
            print(f" {i}/{len(rows)}", flush=True)
    print()
    wall = time.time() - started

    named = [r for r in results if r["phrasing"] == "named"]
    desc = [r for r in results if r["phrasing"] == "described"]
    n_ok = sum(r["caught"] for r in named)
    d_ok = sum(r["caught"] for r in desc)

    print(f"\n{len(results)} emails, all labelled H1, {config.CLASSIFY_MODEL}\n")
    print(f"  named the thing       {pct(n_ok, len(named))}  ({n_ok}/{len(named)})")
    print(f"  described it only     {pct(d_ok, len(desc))}  ({d_ok}/{len(desc)})")
    gap = (n_ok / len(named) - d_ok / len(desc)) * 100 if named and desc else 0.0
    print(f"  gap                   {gap:5.1f} points")

    # Where the two halves of a pair disagree. Same facts, same labels, one
    # caught and one not: the clearest evidence the wording did the work.
    by_pair: dict[str, dict] = {}
    for r in results:
        by_pair.setdefault(r["pair"], {})[r["phrasing"]] = r
    split = [
        (p, v) for p, v in sorted(by_pair.items())
        if "named" in v and "described" in v
        and v["named"]["caught"] != v["described"]["caught"]
    ]
    if split:
        print(f"\n  pairs that split ({len(split)} of {len(by_pair)}):")
        for p, v in split:
            kept = "named" if v["named"]["caught"] else "described"
            lost = v["described"] if kept == "named" else v["named"]
            print(f"  {p}  {lost['topic']:<28} {kept} caught, "
                  f"{lost['phrasing']} -> {lost['got_sop']}")

    missed = [r for r in results if not r["caught"]]
    if missed:
        print("\n  every miss:")
        for r in missed:
            print(f"  {r['id']}  {r['phrasing']:<10} {r['got_sop']:<8} "
                  f"{r['subject'][:52]}")

    stats = dict(llm.cache_stats)
    total = cost_usd(stats)
    print(f"\n  cost     ${total:.4f} total, ${total / len(results):.5f} per email")
    print(f"  wall     {wall:.1f}s")

    report = {
        "model": config.CLASSIFY_MODEL,
        "n": len(results),
        "recall": {
            "named": n_ok / len(named),
            "described": d_ok / len(desc),
            "gap_points": gap,
        },
        "pairs_split": [p for p, _ in split],
        "cost_usd": total,
        "results": results,
    }
    out = Path(args.out) if args.out else ROOT / "reports" / f"h1_{int(started)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"\n  written  {shown}")

    if args.min_described_recall:
        got = d_ok / len(desc)
        if got < args.min_described_recall:
            print(
                f"\nFAIL: described-half recall {got:.1%} is below the floor "
                f"{args.min_described_recall:.1%}. A prompt change has made the "
                f"safety route depend on the customer's vocabulary again.",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
