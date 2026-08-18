"""Offline tests for rule-based sub-categorization."""
from __future__ import annotations

from support_triage_agent.categorize import subcategorize, OTHER


def test_routes_known_issues():
    cases = {
        "Please cancel my autopay subscription, money deducted without knowing":
            "Autopay / unwanted subscription",
        "delete my account from example": "Account deletion / data removal",
        "My premium report shows blank after payment": "Report blank / not generated",
        "wrong date of birth in my report, cannot fix": "Wrong birth details / inaccurate report",
        "how to download my report pdf": "Cannot download report",
        "I am an provider with 10 years experience, want to join your platform":
            "Provider onboarding / join request",
        "Collaboration proposal for Example Co partnership": "Business / spam (not support)",
    }
    for text, expected in cases.items():
        assert subcategorize(text) == expected, f"{text!r} -> {subcategorize(text)!r}"


def test_specific_beats_generic_priority():
    # Autopay + refund both present → autopay (more specific) wins by order.
    t = "I want a refund, you charged my autopay subscription without asking"
    assert subcategorize(t) == "Autopay / unwanted subscription"


def test_unmatched_falls_to_other():
    assert subcategorize("Plz halp") == OTHER
