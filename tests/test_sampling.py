"""Sampling is pinned where the model allows it, and omitted where it 400s.

Two identical eval runs disagreed on 11 of 180 fields because this path never
set temperature. The fix cannot simply always send it: Anthropic removed
sampling controls on Claude 4.6 and later, so the same line that makes Haiku
deterministic makes Sonnet 5 raise.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support_triage_agent import llm  # noqa: E402


def test_sampling_is_allowed_on_the_models_that_accept_it():
    assert llm.supports_temperature("claude-haiku-4-5-20251001")
    assert llm.supports_temperature("claude-haiku-4-5")


def test_sampling_is_omitted_on_models_that_reject_it():
    for model in (
        "claude-sonnet-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-fable-5-1",
    ):
        assert not llm.supports_temperature(model), model


class _FakeUsage:
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    input_tokens = 10
    output_tokens = 5


class _FakeResponse:
    usage = _FakeUsage()
    content = []


class _FakeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _call_with(monkeypatch, model):
    client = _FakeClient()
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm, "mode", lambda: "anthropic")
    llm.complete(model, "system", "user")
    return client.messages.kwargs


def test_temperature_zero_is_sent_to_haiku(monkeypatch):
    # Through extra_body: the SDK dropped the named parameter, the wire kept it.
    kwargs = _call_with(monkeypatch, "claude-haiku-4-5")
    assert kwargs["extra_body"] == {"temperature": 0}


def test_no_temperature_is_sent_to_a_model_that_would_reject_it(monkeypatch):
    assert "extra_body" not in _call_with(monkeypatch, "claude-sonnet-5")
