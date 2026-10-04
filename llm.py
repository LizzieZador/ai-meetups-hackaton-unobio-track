"""LLM wrapper supporting both Gemini API and Claude API.

Every agent calls ask_claude_json() directly with its own system + user prompt.
If GEMINI_API_KEY is present in the environment, it uses Google Gemini (free tier).
Otherwise, it uses the Anthropic Claude API.
"""
import json
import os
import requests

MODEL_ANTHROPIC = "claude-sonnet-5"
MODEL_GEMINI = "gemini-3.5-flash"

# Approximate pricing per 1M tokens (USD)
PRICE_ANTHROPIC_IN = 2.00
PRICE_ANTHROPIC_OUT = 10.00
PRICE_GEMINI_IN = 0.075
PRICE_GEMINI_OUT = 0.30

_anthropic_client = None
_usage_log: list[dict] = []


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic_client


def reset_usage_log() -> None:
    """Call at the start of a pipeline run so cost is scoped to that one run."""
    _usage_log.clear()


def get_usage_summary() -> dict:
    """Aggregate token usage + approximate USD cost for every call since the last reset."""
    input_tokens = sum(u.get("input_tokens", 0) for u in _usage_log)
    output_tokens = sum(u.get("output_tokens", 0) for u in _usage_log)
    model = _usage_log[0]["model"] if _usage_log else "unknown"

    if "gemini" in model.lower():
        cost = (input_tokens / 1_000_000) * PRICE_GEMINI_IN + (output_tokens / 1_000_000) * PRICE_GEMINI_OUT
    else:
        cost = (input_tokens / 1_000_000) * PRICE_ANTHROPIC_IN + (output_tokens / 1_000_000) * PRICE_ANTHROPIC_OUT

    return {
        "model": model,
        "calls": len(_usage_log),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "approx_cost_usd": round(cost, 4),
    }


def _ask_gemini(api_key: str, system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> list[dict]:
    candidate_models = [MODEL_GEMINI, "gemini-3.8-flash"]
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "maxOutputTokens": max_tokens,
        },
    }

    for model in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usageMetadata", {})
                _usage_log.append({
                    "model": model,
                    "input_tokens": usage.get("promptTokenCount", 0),
                    "output_tokens": usage.get("candidatesTokenCount", 0),
                })
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return parsed
                    if isinstance(parsed, dict) and "claims" in parsed and isinstance(parsed["claims"], list):
                        return parsed["claims"]
                    return [parsed]
                except (json.JSONDecodeError, KeyError):
                    start, end = text.index("["), text.rindex("]") + 1
                    return json.loads(text[start:end])
            else:
                print(f"[LLM Warning] {model} HTTP {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            print(f"[LLM Warning] {model} failed: {e}")
            continue
    return []


def _ask_claude(system_prompt: str, user_prompt: str, max_tokens: int = 3000) -> list[dict]:
    client = _get_anthropic_client()
    response = client.messages.create(
        model=MODEL_ANTHROPIC,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    _usage_log.append({
        "model": MODEL_ANTHROPIC,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    })

    text = response.content[0].text
    try:
        start, end = text.index("["), text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []


def ask_claude_json(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> list[dict]:
    """Unified entrypoint: calls Gemini if GEMINI_API_KEY is available, otherwise Claude."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        return _ask_gemini(gemini_key, system_prompt, user_prompt, max_tokens)
    return _ask_claude(system_prompt, user_prompt, max_tokens)

