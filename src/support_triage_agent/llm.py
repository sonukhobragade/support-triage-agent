"""Thin wrapper over whichever model backend is configured.

Three modes, resolved by `mode()`:

- "anthropic" — LLM_TRANSPORT unset (the default) and ANTHROPIC_API_KEY present.
- "openai"    — LLM_TRANSPORT=openai. Speaks /v1/chat/completions, which Ollama,
                vLLM, LM Studio, llama.cpp, OpenRouter and OpenAI all serve, so
                the pipeline runs against a local model with no account.
- "mock"      — neither is configured. Deterministic placeholder output so the
                plumbing runs end to end. Wiring tests only, never real triage,
                and never something to score: a faithfulness metric run against
                mock drafts measures the mock.
"""
from __future__ import annotations

import json
import re

from . import config

_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic
        # bounded timeout + retries so a stalled API call can't freeze the
        # poll loop (the daemon catches errors, not hangs).
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY,
                            timeout=120.0, max_retries=2)
    return _client


def mode() -> str:
    """Which backend a call would use: "anthropic", "openai" or "mock"."""
    if config.LLM_TRANSPORT == "openai":
        return "openai"
    return "anthropic" if config.ANTHROPIC_API_KEY else "mock"


def have_key() -> bool:
    """True when a real model would be called (either transport)."""
    return mode() != "mock"


def _openai_complete(model: str, system: str, user: str, max_tokens: int) -> str:
    """POST /v1/chat/completions. No prompt caching: the endpoint has no
    equivalent of Anthropic's cache_control, so cache_stats stays at zero here
    rather than reporting savings that are not happening."""
    import requests

    resp = requests.post(
        f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        headers={
            "Content-Type": "application/json",
            # Ollama ignores the value but rejects a missing header.
            "Authorization": f"Bearer {config.OPENAI_API_KEY or 'not-needed'}",
        },
        json={
            "model": config.OPENAI_MODEL or model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # A check that passes on one sampling roll and fails on the next is
            # measuring the sampler, not the pipeline.
            "temperature": 0,
            "max_tokens": max_tokens,
        },
        timeout=config.LLM_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage") or {}
    cache_stats["uncached"] += usage.get("prompt_tokens", 0) or 0
    return data["choices"][0]["message"]["content"] or ""


# Aggregate cache-token usage across a run, so the pipeline can report whether
# the SOP-playbook prefix is actually being cached. Reset per process.
cache_stats = {"read": 0, "write": 0, "uncached": 0}


def complete(model: str, system: str, user: str, max_tokens: int = 1024) -> str:
    """Single-turn completion. Returns the text content.

    The `system` prompt (SOP playbook + instructions) is identical across every
    email, so it's sent as a cached block: a cache_control breakpoint on the
    system text. The first call writes it to the cache (~1.25x), every later call
    within the 5-min TTL reads it at ~0.1x. The per-email `user` turn varies and
    is never cached. NOTE: the cacheable-prefix minimum is 4096 tokens on Haiku
    4.5 (2048 on Sonnet 4.6); a system prompt shorter than that silently won't
    cache (cache_creation stays 0) — no error, just no savings.
    """
    backend = mode()
    if backend == "mock":
        return _mock(system, user)
    if backend == "openai":
        return _openai_complete(model, system, user, max_tokens)
    resp = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user}],
    )
    u = resp.usage
    cache_stats["read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    cache_stats["write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    cache_stats["uncached"] += getattr(u, "input_tokens", 0) or 0
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def complete_json(model: str, system: str, user: str, max_tokens: int = 1024) -> dict:
    """Completion expected to return a single JSON object. Robustly parsed."""
    raw = complete(model, system, user, max_tokens)
    return parse_json(raw)


def parse_json(text: str) -> dict:
    """Extract the first JSON object from a model response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _mock(system: str, user: str) -> str:
    """Deterministic placeholder so the pipeline can run without a key."""
    if "classify" in system.lower():
        return json.dumps({
            "category": "Generic Technical Problem",
            "sop_id": "NO_SOP",
            "priority": "Medium",
            "sentiment": "neutral",
            "identifiers": {},
            "summary": "[mock] classification — set ANTHROPIC_API_KEY for real output",
        })
    return json.dumps({
        "reply": "Namaste ji 🙏 [mock draft — set ANTHROPIC_API_KEY for a real, "
                 "SOP-grounded reply.]",
        "info_to_collect": [],
        "notes": "mock mode",
    })
