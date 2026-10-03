"""Thin wrapper around the Claude API.

Deliberately NOT using LangChain/CrewAI/LangGraph (see plan) — every agent calls
this one function directly with its own system + user prompt.

Also tracks token usage so the pipeline can report "approximate cost per
underwriting run" — a named Required Deliverable in the task brief.
"""
import json
import os

import anthropic

MODEL = "claude-sonnet-5"

# Anthropic first-party list pricing, USD per million tokens. Update if the
# model or its pricing changes — this is an estimate, not a billing record.
PRICE_PER_MTOK_INPUT = 2.00
PRICE_PER_MTOK_OUTPUT = 10.00

_client: anthropic.Anthropic | None = None
_usage_log: list[dict] = []


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def reset_usage_log() -> None:
    """Call at the start of a pipeline run so cost is scoped to that one run."""
    _usage_log.clear()


def get_usage_summary() -> dict:
    """Aggregate token usage + approximate USD cost for every call since the last reset."""
    input_tokens = sum(u["input_tokens"] for u in _usage_log)
    output_tokens = sum(u["output_tokens"] for u in _usage_log)
    cost = (
        (input_tokens / 1_000_000) * PRICE_PER_MTOK_INPUT
        + (output_tokens / 1_000_000) * PRICE_PER_MTOK_OUTPUT
    )
    return {
        "model": MODEL,
        "calls": len(_usage_log),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "approx_cost_usd": round(cost, 4),
    }


def ask_claude_json(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> list[dict]:
    """Call Claude and parse the reply as a JSON array of claim dicts.

    Returns [] if the model didn't return valid JSON — a bad LLM reply must never
    crash the pipeline, it just means that agent contributes no claims this round.
    """
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    _usage_log.append({
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    })

    text = response.content[0].text
    try:
        start, end = text.index("["), text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
