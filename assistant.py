"""The site's chat Assistant (top-right widget) — explains the UI, answers
questions about the currently loaded report, and helps find what to search
for next.

Grounding rule (the reason this is a two-step module instead of one free-form
chatbot call): the assistant is never allowed to assert a specific fact, or
suggest a specific drug/mechanism, from its own trained knowledge. Every
concrete answer or suggestion must be backed by either (a) REPORT_CONTEXT —
the report already loaded in the browser, exactly what the cards show, or
(b) a LIVE_LOOKUP this module just ran against our own connected sources
(Open Targets / PubMed / ClinicalTrials.gov — the same fetchers pipeline.py
uses) for whatever the user is asking about right now. Step 1 below extracts
a candidate mechanism/indication from the message; step 2 fetches real
records for it if so; step 3 is the only call that actually answers the user,
and its prompt makes both context blocks the sole source of fact, exactly
the evidence/inference discipline schema.py/agents/base.py enforce in code
for the main pipeline — here enforced by instruction, since a chat reply
isn't a structured Claim object code can validate line by line.

Uses llm.ask_assistant_reply() for both LLM calls — the same ChatGPT ->
Gemini -> Claude priority chain as every other agent in this project.
"""
import json
import re

from cards import CARDS
from llm import ask_assistant_reply
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_opentargets import fetch_opentargets
from sources.fetch_pubmed import fetch_pubmed

MAX_HISTORY_TURNS = 6  # last N (user, assistant) pairs sent back as context
MAX_LOOKUP_RECORDS_PER_SOURCE = 5


def _site_structure_text() -> str:
    """Generated straight from cards.CARDS so this description can never
    drift out of sync with the actual cards the site renders."""
    lines = [
        f"{i}. {spec['title']}" + (f" — {spec['subtitle']}" if spec.get("subtitle") else "")
        for i, spec in enumerate(CARDS, start=1)
    ]
    return "\n".join(lines)


SITE_STRUCTURE = _site_structure_text()

# ---------------------------------------------------------------------------
# Step 1 — extract a candidate mechanism/indication from the message, if any.
# Deliberately NOT asked to answer anything yet — this call's only job is to
# decide what (if anything) to go look up in our own connected sources next.
# ---------------------------------------------------------------------------
_EXTRACT_SYSTEM_PROMPT = """You help route messages for a biotech research assistant. Given a \
user's message (and optionally recent conversation), decide whether it refers to a specific \
disease/indication and/or a specific gene/protein/mechanism/target/drug — something worth \
looking up in real biomedical databases. Respond with EXACTLY one line, nothing else:
MECHANISM: <term or NONE> | INDICATION: <term or NONE>
Use NONE for a side that isn't specific enough to look up (e.g. a plain "how does this site \
work" question is NONE | NONE). Never invent a term that isn't implied by the message."""

_EXTRACT_LINE = re.compile(r"MECHANISM:\s*(.+?)\s*\|\s*INDICATION:\s*(.+?)\s*$", re.I | re.M)


def _extract_lookup_terms(message: str, history_text: str) -> tuple[str | None, str | None]:
    prompt = (f"Recent conversation:\n{history_text}\n\n" if history_text else "") + f"Message: {message}"
    try:
        raw = ask_assistant_reply(_EXTRACT_SYSTEM_PROMPT, prompt, max_tokens=60)
    except Exception:
        return None, None
    m = _EXTRACT_LINE.search(raw)
    if not m:
        return None, None
    mechanism, indication = m.group(1).strip(), m.group(2).strip()
    mechanism = None if mechanism.upper() == "NONE" or not mechanism else mechanism
    indication = None if indication.upper() == "NONE" or not indication else indication
    return mechanism, indication


# ---------------------------------------------------------------------------
# Step 2 — real fetches against our own connected sources for those terms.
# Same fetcher functions pipeline.py uses for a full evaluation, just a
# smaller slice (no LLM claim generation) so a chat reply stays fast.
# ---------------------------------------------------------------------------
def _run_live_lookup(mechanism: str | None, indication: str | None) -> list[dict]:
    records: list[dict] = []

    query = " ".join(t for t in (mechanism, indication) if t).strip()
    if query:
        try:
            pubmed = fetch_pubmed(query, max_results=MAX_LOOKUP_RECORDS_PER_SOURCE) or []
            records.extend(pubmed)
        except Exception:
            pass

    if mechanism and indication:
        try:
            ot = fetch_opentargets(mechanism, indication) or []
            records.extend(ot[:MAX_LOOKUP_RECORDS_PER_SOURCE])
        except Exception:
            pass
        try:
            trials = fetch_clinicaltrials(indication, mechanism, max_results=MAX_LOOKUP_RECORDS_PER_SOURCE) or []
            records.extend(trials)
        except Exception:
            pass

    return records


def _already_covered(mechanism: str | None, indication: str | None, report: dict | None) -> bool:
    """True when the extracted terms are basically what's already loaded —
    skip a redundant live lookup, REPORT_CONTEXT already has it grounded."""
    if not report:
        return False
    loaded_mech = (report.get("mechanism") or "").strip().lower()
    loaded_ind = (report.get("indication") or "").strip().lower()
    mech_match = not mechanism or mechanism.strip().lower() in loaded_mech or loaded_mech in mechanism.strip().lower()
    ind_match = not indication or indication.strip().lower() in loaded_ind or loaded_ind in indication.strip().lower()
    return mech_match and ind_match


def _format_live_lookup(records: list[dict]) -> str:
    if not records:
        return "none (nothing was looked up for this message, or the lookup returned no results)"
    lines = []
    for r in records:
        source = r.get("source", "Unknown source")
        url = r.get("url", "")
        snippet = r.get("raw_snippet", "")
        lines.append(f"- [{source}]({url}): {snippet}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 3 — the only call that actually replies to the user.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = f"""You are the embedded help Assistant on a biotech "Investment Committee" \
web tool. The site takes an Indication + a Mechanism/Target, runs real queries against \
8 connected databases (PubMed, Open Targets, ClinicalTrials.gov, AACT, a manual \
epidemiology/pricing CSV, openFDA Drugs@FDA, SEC EDGAR, and Lens.org patents), and \
produces one verdict (Invest / Conditional / Do Not Invest) plus these cards:

{SITE_STRUCTURE}

You are given two context blocks below: REPORT_CONTEXT (the report currently loaded in the \
user's browser, exactly what the cards show) and LIVE_LOOKUP (real records just fetched from \
our own PubMed / Open Targets / ClinicalTrials.gov connections for this message, if relevant).

STRICT RULE: every concrete fact or suggestion you give — a drug name, a target, a trial, a \
number, a date, "this looks promising" — MUST come from REPORT_CONTEXT or LIVE_LOOKUP. Never \
state or suggest a drug, mechanism, or fact from your own general knowledge. You may talk \
normally and explain how the site works (the card list above is fixed and always safe to state), \
but any substantive answer or recommendation must be traceable to one of those two blocks.

Your job, in order of how a message should be handled:
1. If the user asks what something on the page means, or how the site works in general, just \
explain it plainly using the card list above. Do NOT attach a SEARCH suggestion to a plain \
"how does this work" question.
2. If the user asks about a specific drug/trial/approval/number: answer from REPORT_CONTEXT if \
it's there; otherwise check LIVE_LOOKUP; if neither has it, say plainly it isn't available from \
our connected sources right now — never guess — and you may still suggest a SEARCH if \
LIVE_LOOKUP or the message itself points to a specific mechanism/indication worth evaluating.
3. If the user describes a medical need and LIVE_LOOKUP found real records for it, summarize \
what was actually found (real drug/target names from LIVE_LOOKUP, not invented ones) and offer \
a SEARCH for the specific mechanism/indication those records are about. If LIVE_LOOKUP found \
nothing, say so plainly instead of guessing a drug name.

SEARCH directive: when proposing a new search, end your reply with one line in exactly this \
style, filled in with REAL values grounded in LIVE_LOOKUP or REPORT_CONTEXT — for example:
SEARCH: KRAS G12C | Non-Small Cell Lung Cancer
Never use placeholder words like "mechanism" or "indication" in that line. Omit the line \
entirely when you have nothing concrete to propose.

Keep replies short (2-5 sentences) and plain. Never fabricate citations, trial IDs, or numbers."""


def _format_report_context(report: dict | None) -> str:
    if not report:
        return "null (no report is currently loaded in the browser)"
    # The full report is exactly what the cards already render — passed through
    # as-is rather than re-summarized, so nothing the UI shows is invisible to
    # the assistant and nothing it says can be more detailed than what's on screen.
    return json.dumps(report, ensure_ascii=False)[:40000]  # hard cap: a pathological report never blows the prompt


def _format_history(history: list[dict]) -> str:
    trimmed = history[-(MAX_HISTORY_TURNS * 2):]
    return "\n".join(f"{'User' if h.get('role') == 'user' else 'Assistant'}: {h.get('text', '')}" for h in trimmed)


_SEARCH_LINE = re.compile(r"^\s*SEARCH:\s*(.+?)\s*\|\s*(.+?)\s*$", re.I | re.M)


def ask_assistant(message: str, history: list[dict], report: dict | None) -> dict:
    """Returns {"reply": str, "search": {"mechanism", "indication"} | None}."""
    history_text = _format_history(history)

    mechanism, indication = _extract_lookup_terms(message, history_text)
    live_records: list[dict] = []
    if (mechanism or indication) and not _already_covered(mechanism, indication, report):
        live_records = _run_live_lookup(mechanism, indication)

    user_prompt = (
        f"REPORT_CONTEXT: {_format_report_context(report)}\n\n"
        f"LIVE_LOOKUP: {_format_live_lookup(live_records)}\n\n"
        + (f"Recent conversation:\n{history_text}\n\n" if history_text else "")
        + f"User: {message}"
    )
    raw = ask_assistant_reply(SYSTEM_PROMPT, user_prompt)

    search = None
    m = _SEARCH_LINE.search(raw)
    if m:
        s_mechanism, s_indication = m.group(1).strip(), m.group(2).strip()
        # Defend against the model echoing the instruction's own placeholder
        # syntax (e.g. "<mechanism>") instead of a real proposed value.
        if "<" not in s_mechanism and "<" not in s_indication and s_mechanism and s_indication:
            search = {"mechanism": s_mechanism, "indication": s_indication}
        raw = raw[: m.start()].rstrip()

    return {"reply": raw or "I'm not sure how to answer that — try rephrasing?", "search": search}
