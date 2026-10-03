"""Thin wrapper around the Claude API.

Deliberately NOT using LangChain/CrewAI/LangGraph (see plan) — every agent calls
this one function directly with its own system + user prompt.
"""
import json
import os

import anthropic

MODEL = "claude-sonnet-5"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


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
    text = response.content[0].text
    try:
        start, end = text.index("["), text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
