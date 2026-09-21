"""The eval script's own arithmetic, checked without spending anything.

Scoring code that is wrong is worse than no scoring code, because the number it
prints still looks like a measurement. These cover the parts that decide what
the headline number says: which labels count as correct, how a priority one band
out is treated, and what the token arithmetic charges.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from support_triage_agent import config  # noqa: E402


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "eval_classify", ROOT / "scripts" / "eval_classify.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_script()


def test_band_distance_counts_steps_along_the_priority_order():
    assert ev.band_distance("High", "High") == 0
    assert ev.band_distance("High", "Critical") == 1
    assert ev.band_distance("Low", "Urgent") == len(config.PRIORITIES) - 1


def test_band_distance_rejects_a_label_that_is_not_a_priority():
    # A model returning "P1" must not be silently scored as near-correct.
    assert ev.band_distance("High", "P1") == -1


def test_cost_applies_the_cache_multipliers_not_the_raw_input_rate():
    # 1M cache-read tokens bill at 0.1x, 1M cache writes at 1.25x.
    read_only = ev.cost_usd({"read": 1_000_000, "write": 0, "uncached": 0, "output": 0})
    write_only = ev.cost_usd({"read": 0, "write": 1_000_000, "uncached": 0, "output": 0})
    assert read_only == pytest.approx(ev.USD_PER_MTOK_IN * 0.1)
    assert write_only == pytest.approx(ev.USD_PER_MTOK_IN * 1.25)


def test_cost_charges_output_tokens_at_the_output_rate():
    assert ev.cost_usd(
        {"read": 0, "write": 0, "uncached": 0, "output": 1_000_000}
    ) == pytest.approx(ev.USD_PER_MTOK_OUT)


def test_every_label_in_the_shipped_set_is_one_the_classifier_can_return():
    """A label outside the enum would be scored as a permanent failure."""
    rows = ev.load_set(ROOT / "data" / "eval_set.jsonl")
    assert len(rows) >= 50, "the set is the measurement; a short one is not one"
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate ids double-count an email"
    playbook = (ROOT / "data" / "sop_playbook.md").read_text(encoding="utf-8")
    for row in rows:
        assert row["category"] in config.PROBLEM_CATEGORIES, row["id"]
        assert row["priority"] in config.PRIORITIES, row["id"]
        assert row["sop_id"] == config.NO_SOP or f"## {row['sop_id']} " in playbook, row["id"]


def test_rows_become_emails_the_classifier_accepts():
    rows = ev.load_set(ROOT / "data" / "eval_set.jsonl")
    email = ev.as_email(rows[0])
    assert email.subject == rows[0]["subject"]
    assert email.body == rows[0]["body"]
    assert email.from_support is False


def test_report_is_written_and_scores_a_stubbed_model(tmp_path, monkeypatch):
    """End to end with a stand-in model, so the report shape is exercised."""
    from support_triage_agent import llm

    monkeypatch.setattr(llm, "have_key", lambda: True)
    monkeypatch.setattr(
        llm,
        "complete_json",
        lambda *a, **k: {
            "category": "Payment",
            "sop_id": "A2",
            "priority": "High",
            "sentiment": "neutral",
            "summary": "stub",
        },
    )
    out = tmp_path / "report.json"
    monkeypatch.setattr(
        sys, "argv", ["eval_classify.py", "--limit", "4", "--out", str(out)]
    )
    assert ev.main() == 0

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["n"] == 4
    assert 0.0 <= report["accuracy"]["category"] <= 1.0
    assert len(report["results"]) == 4
    # Every answer was "Payment", so the other categories must score zero.
    assert report["accuracy"]["all_three"] < 1.0
