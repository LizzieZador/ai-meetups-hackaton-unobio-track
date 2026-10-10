"""LLM wrapper supporting ChatGPT (via the team's OpenAI-compatible gateway),
Gemini, and Claude — tried in that priority order.

Every agent calls ask_claude_json() directly with its own system + user prompt.
Priority: if TEAM_API_KEY is set, ChatGPT (the team gateway) is tried first.
If it's unset, or the call fails for any reason, falls back to Gemini
(GEMINI_API_KEY / GOOGLE_API_KEY), and if that also fails, to Claude
(ANTHROPIC_API_KEY). Each stage is independent — a provider just needs its key
set to be tried; it doesn't need the earlier ones configured too.

Responses are cached to disk by a hash of (system_prompt, user_prompt): the
Gemini free tier caps at 20 requests/day *per model*, and one full pipeline run
already uses 4 calls — re-running the same indication/mechanism pair during
testing or a demo must not burn more of that budget.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

MODEL_ANTHROPIC = "claude-sonnet-5"
MODEL_GEMINI = "gemini-3.5-flash"
GEMINI_CANDIDATES = [MODEL_GEMINI, "gemini-3.5-flash-lite", "gemini-3.8-flash"]  # tried in order
GEMINI_THINKING_BUDGET = 2000  # see _ask_gemini: a hint to the model, not a hard cap
GEMINI_MAX_ATTEMPTS = 4  # per model, for transient 429/500/503 only

MODEL_OPENAI = "gpt-6-luna"
OPENAI_BASE_URL = "https://secrethon-gateway.nicewave-ab4e0867.swedencentral.azurecontainerapps.io/v1"

# Approximate pricing per 1M tokens (USD)
PRICE_ANTHROPIC_IN = 2.00
PRICE_ANTHROPIC_OUT = 10.00
PRICE_GEMINI_IN = 0.075
PRICE_GEMINI_OUT = 0.30
# The team gateway's real per-token price isn't published anywhere we can
# confirm — 0.0 is a known placeholder, not a verified free tier. Update these
# the moment the team gateway's actual pricing is known.
PRICE_OPENAI_IN = 0.0
PRICE_OPENAI_OUT = 0.0

_anthropic_client = None
_openai_client = None
_usage_log: list[dict] = []


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic_client


def _get_openai_client(api_key: str):
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(base_url=OPENAI_BASE_URL, api_key=api_key)
    return _openai_client


def reset_usage_log() -> None:
    """Call at the start of a pipeline run so cost is scoped to that one run."""
    _usage_log.clear()


def get_usage_summary() -> dict:
    """Aggregate token usage and price each provider's calls at its own rate."""
    input_tokens = sum(u.get("input_tokens", 0) for u in _usage_log)
    output_tokens = sum(u.get("output_tokens", 0) for u in _usage_log)
    models = list(dict.fromkeys(u["model"] for u in _usage_log))
    model = models[0] if len(models) == 1 else ("mixed: " + ", ".join(models) if models else "unknown")
    cost = 0.0
    for usage in _usage_log:
        used_model = usage["model"]
        if "gemini" in used_model.lower():
            price_in, price_out = PRICE_GEMINI_IN, PRICE_GEMINI_OUT
        elif used_model == MODEL_OPENAI:
            price_in, price_out = PRICE_OPENAI_IN, PRICE_OPENAI_OUT
        else:
            price_in, price_out = PRICE_ANTHROPIC_IN, PRICE_ANTHROPIC_OUT
        cost += (usage.get("input_tokens", 0) / 1_000_000) * price_in
        cost += (usage.get("output_tokens", 0) / 1_000_000) * price_out

    return {
        "model": model,
        "calls": len(_usage_log),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "approx_cost_usd": round(cost, 4),
    }


def _salvage_objects(text: str) -> list[dict]:
    """Recover complete {...} objects from a truncated JSON array (e.g. output cut by max tokens)."""
    decoder, objs, i = json.JSONDecoder(), [], text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            objs.append(obj)
        i = text.find("{", end)
    return objs


def _parse_claims_json(text: str) -> list[dict]:
    """Tolerant parse of a model reply into a list of claim dicts ([] if unusable)."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(text[text.index("["): text.rindex("]") + 1])
        except (ValueError, json.JSONDecodeError):
            return _salvage_objects(text)
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("claims"), list):
            return [p for p in parsed["claims"] if isinstance(p, dict)]
        return [parsed]
    return []


def _ask_gemini(api_key: str, system_prompt: str, user_prompt: str, max_tokens: int = 8000) -> list[dict]:
    """Call Gemini, trying each candidate model; retry transient errors.

    Raises RuntimeError if no model answered at all (bad key, quota, network) so the
    UI can show the real cause instead of an empty report. A reply that arrives but
    isn't usable JSON just yields [] (that agent contributes no claims).

    maxOutputTokens is spent on the model's own thinking before any answer is
    written, and on these prompts thinking alone runs to ~4k tokens — at a 4000
    budget every agent reply came back truncated mid-claim. Hence the high
    ceiling plus a thinking budget (a hint, not a hard cap: the model may and
    does overshoot it, so the ceiling is what actually protects the answer).
    """
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": GEMINI_THINKING_BUDGET},
        },
    }
    headers = {"x-goog-api-key": api_key}  # header, not ?key=, so the key never lands in logs/errors
    last_error = "no response"

    for model in dict.fromkeys(GEMINI_CANDIDATES):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(GEMINI_MAX_ATTEMPTS):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
            except requests.RequestException as e:
                last_error = f"{model}: {e}"
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usageMetadata", {})
                _usage_log.append({
                    "model": model,
                    "input_tokens": usage.get("promptTokenCount", 0),
                    # thoughts are billed as output tokens but counted separately
                    "output_tokens": usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
                })
                try:
                    cand = data["candidates"][0]
                    # thinking models emit "thought" parts first; keep only the answer text
                    text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
                except (KeyError, IndexError):
                    return []  # blocked / empty candidate
                if cand.get("finishReason") not in (None, "STOP"):
                    print(f"[LLM Warning] {model} finishReason={cand.get('finishReason')} usage={usage}")
                return _parse_claims_json(text)
            try:
                detail = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = resp.text[:200]
            last_error = f"{model} HTTP {resp.status_code}: {detail}"
            print(f"[LLM Warning] {last_error}")
            if resp.status_code == 429 and "quota" in detail.lower():
                break  # daily/free-tier quota: waiting is pointless, try the next model
            if resp.status_code in (429, 500, 503):
                # 503 ("high demand") is routinely the free tier's answer and clears
                # on its own; back off increasingly rather than giving the agent up.
                time.sleep(3 * (attempt + 1))
                continue
            break  # 400/401/403/404: retrying the same model won't help
    raise RuntimeError(f"Gemini request failed ({last_error})")


def _ask_openai(api_key: str, system_prompt: str, user_prompt: str, max_tokens: int) -> list[dict]:
    """Call the team's OpenAI-compatible gateway (ChatGPT / gpt-6-luna) — given
    top priority over Gemini/Claude. Raises on any failure so the caller can
    fall back to the next provider; a reply that arrives but isn't usable JSON
    just yields [] (that agent contributes no claims)."""
    client = _get_openai_client(api_key)
    response = client.chat.completions.create(
        model=MODEL_OPENAI,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=max_tokens,
    )
    usage = response.usage
    _usage_log.append({
        "model": MODEL_OPENAI,
        "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
    })
    text = response.choices[0].message.content or ""
    return _parse_claims_json(text)


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


def _ask_openai_text(api_key: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    client = _get_openai_client(api_key)
    response = client.chat.completions.create(
        model=MODEL_OPENAI,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=max_tokens,
    )
    usage = response.usage
    _usage_log.append({
        "model": MODEL_OPENAI,
        "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
    })
    return response.choices[0].message.content or ""


def _ask_gemini_text(api_key: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    """Same Gemini-candidate/retry logic as _ask_gemini(), but plain text out
    (no responseMimeType=json) — for the conversational Assistant, not claims."""
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    headers = {"x-goog-api-key": api_key}
    last_error = "no response"

    for model in dict.fromkeys(GEMINI_CANDIDATES):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(GEMINI_MAX_ATTEMPTS):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
            except requests.RequestException as e:
                last_error = f"{model}: {e}"
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usageMetadata", {})
                _usage_log.append({
                    "model": model,
                    "input_tokens": usage.get("promptTokenCount", 0),
                    "output_tokens": usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
                })
                try:
                    cand = data["candidates"][0]
                    return "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
                except (KeyError, IndexError):
                    return ""
            try:
                detail = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = resp.text[:200]
            last_error = f"{model} HTTP {resp.status_code}: {detail}"
            print(f"[LLM Warning] {last_error}")
            if resp.status_code == 429 and "quota" in detail.lower():
                break
            if resp.status_code in (429, 500, 503):
                time.sleep(3 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"Gemini request failed ({last_error})")


def _ask_claude_text(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
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
    return response.content[0].text


def ask_assistant_reply(system_prompt: str, user_prompt: str, max_tokens: int = 700) -> str:
    """Same ChatGPT -> Gemini -> Claude priority chain as ask_claude_json(), but
    for a free-text conversational reply (the site's Assistant chat widget) —
    not a claims array, and not disk-cached (every chat turn is its own
    conversation, caching by prompt text would return a stale reply to a
    different follow-up that happens to hash the same)."""
    openai_key = _real_key("TEAM_API_KEY")
    gemini_key = _real_key("GEMINI_API_KEY") or _real_key("GOOGLE_API_KEY")
    claude_key = _real_key("ANTHROPIC_API_KEY")

    if not (openai_key or gemini_key or claude_key):
        raise RuntimeError(
            "No LLM key configured: set TEAM_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY in .env"
        )

    if openai_key:
        try:
            return _ask_openai_text(openai_key, system_prompt, user_prompt, max_tokens)
        except Exception as e:
            print(f"[LLM Warning] ChatGPT gateway failed ({e}); falling back to Gemini/Claude")

    if gemini_key:
        try:
            return _ask_gemini_text(gemini_key, system_prompt, user_prompt, max_tokens)
        except RuntimeError as e:
            if not claude_key:
                raise
            print(f"[LLM Warning] Gemini failed ({e}); falling back to Claude")

    if claude_key:
        return _ask_claude_text(system_prompt, user_prompt, max_tokens)

    raise RuntimeError("All configured LLM providers failed for this request")


def _real_key(name: str) -> str:
    """Env key, ignoring empty values and the '...' placeholders from .env.example."""
    value = os.environ.get(name, "").strip()
    return "" if not value or value.endswith("...") else value


def active_model() -> str:
    """Name of the model ask_claude_json() will try FIRST right now (for UI/diagnostics).
    Actual calls may still fall through to a later provider if this one fails."""
    if _real_key("TEAM_API_KEY"):
        return MODEL_OPENAI
    if _real_key("GEMINI_API_KEY") or _real_key("GOOGLE_API_KEY"):
        return MODEL_GEMINI
    return MODEL_ANTHROPIC


def _cache_path(system_prompt: str, user_prompt: str) -> Path:
    digest = hashlib.sha256(f"{system_prompt}\x00{user_prompt}".encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"llm_{digest}.json"


def ask_claude_json(system_prompt: str, user_prompt: str, max_tokens: int = 8000) -> list[dict]:
    """Unified entrypoint: Gemini if GEMINI_API_KEY (or GOOGLE_API_KEY) is set, otherwise Claude.

    Cached by a hash of the exact prompt pair — an identical call (same agent,
    same indication/mechanism/sources) never spends quota or money twice.
    """
    cache_path = _cache_path(system_prompt, user_prompt)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    result = _ask(system_prompt, user_prompt, max_tokens)

    if result:  # don't cache [] / failures — let the next call retry for real
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _ask(system_prompt: str, user_prompt: str, max_tokens: int) -> list[dict]:
    """Priority chain: ChatGPT (team gateway) -> Gemini -> Claude.

    Each stage only runs if its key is set, and a failure there just moves on
    to the next configured provider rather than failing the whole call.
    """
    openai_key = _real_key("TEAM_API_KEY")
    gemini_key = _real_key("GEMINI_API_KEY") or _real_key("GOOGLE_API_KEY")
    claude_key = _real_key("ANTHROPIC_API_KEY")

    if not (openai_key or gemini_key or claude_key):
        raise RuntimeError(
            "No LLM key configured: set TEAM_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY in .env"
        )

    if openai_key:
        try:
            return _ask_openai(openai_key, system_prompt, user_prompt, max_tokens)
        except Exception as e:
            print(f"[LLM Warning] ChatGPT gateway failed ({e}); falling back to Gemini/Claude")

    if gemini_key:
        try:
            return _ask_gemini(gemini_key, system_prompt, user_prompt, max_tokens)
        except RuntimeError as e:
            if not claude_key:
                raise
            # Gemini unreachable/quota-exhausted but a real Claude key exists too —
            # fall back instead of failing the whole report.
            print(f"[LLM Warning] Gemini failed ({e}); falling back to Claude")

    if claude_key:
        return _ask_claude(system_prompt, user_prompt, max_tokens)

    raise RuntimeError("All configured LLM providers failed for this request")
