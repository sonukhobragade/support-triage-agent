"""The paired set's invariants, checked without spending anything.

The whole argument rests on the pairing: two mails per situation, identical
labels, differing only in whether the customer names the thing. If a pair is
broken, unbalanced, or labelled inconsistently, the gap the scorer reports
stops meaning what it claims to mean.
"""
from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from support_triage_agent import config  # noqa: E402


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "eval_h1", ROOT / "scripts" / "eval_h1.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_script()
ROWS = ev.load_set(ROOT / "data" / "h1_set.jsonl")


def test_every_pair_has_one_named_and_one_described_mail():
    by_pair = Counter((r["pair"], r["phrasing"]) for r in ROWS)
    pairs = {r["pair"] for r in ROWS}
    assert len(pairs) * 2 == len(ROWS), "a pair with an odd mail is not a pair"
    for p in pairs:
        assert by_pair[(p, "named")] == 1, p
        assert by_pair[(p, "described")] == 1, p


def test_both_halves_of_a_pair_carry_identical_labels():
    """The gap is only evidence if the answer key is the same on both sides."""
    seen: dict[str, dict] = {}
    for r in ROWS:
        other = seen.get(r["pair"])
        if other is None:
            seen[r["pair"]] = r
            continue
        for field in ("category", "sop_id", "priority", "topic"):
            assert r[field] == other[field], f"{r['pair']} differs on {field}"


def test_the_whole_set_is_labelled_h1_with_labels_the_classifier_can_return():
    assert len(ROWS) >= 40, "a short set cannot support a recall claim"
    assert len({r["id"] for r in ROWS}) == len(ROWS), "duplicate ids double-count"
    for r in ROWS:
        assert r["sop_id"] == "H1", r["id"]
        assert r["category"] in config.PROBLEM_CATEGORIES, r["id"]
        assert r["priority"] in config.PRIORITIES, r["id"]


def test_described_mails_never_use_the_word_the_named_mail_uses():
    """The one fact that makes a mail 'described'. Worth asserting, not trusting."""
    banned = [
        "legal action", "lawyer", "ombudsman", "regulator", "chargeback",
        "harming myself", "suicidal", "harass", "threaten", "scam", "fraud",
        "data breach", "discriminat", "hacked", "blackmail", "stalking",
        "child safety", "impersonat", "the press", "dementia",
    ]
    for r in ROWS:
        if r["phrasing"] != "described":
            continue
        text = f"{r['subject']} {r['body']}".lower()
        hits = [w for w in banned if w in text]
        assert not hits, f"{r['id']} names the thing: {hits}"
