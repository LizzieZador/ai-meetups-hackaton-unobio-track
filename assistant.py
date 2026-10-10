"""The site's chat Assistant — sees what the client has on screen, answers from
the loaded report, and queries our databases only when the answer needs them.

Grounding rule: the assistant never asserts a fact or suggests a drug from the
model's own trained knowledge. Every concrete answer comes from:

  SCREEN          what the client is looking at (page, open/visible cards, inputs)
  REPORT_CONTEXT  the report loaded in the browser (what the cards show)
  TOOL_RESULTS    lookups run for this message against our own sources

Flow per message:
  1. plan   — a small LLM call sees SCREEN + a report summary + the question and
              returns which tools (if any) to run; none when the answer is
              already on screen;
  2. run    — at most MAX_TOOLS lookups, each isolated (a failure costs only
              that tool's result);
  3. answer — one LLM call, grounded only in the blocks above, structured.
"""
import json
import re

from cards import CARDS
from llm import ask_assistant_reply
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_drug_profiles import fetch_drug_profile
from sources.fetch_indication_drugs import fetch_indication_drugs
from sources.fetch_lens import fetch_patent_signals
from sources.fetch_opentargets import fetch_opentargets
from sources.fetch_orange_book import fetch_orange_book_patents
from sources.fetch_pubmed import fetch_pubmed
from sources.fetch_regulatory import fetch_regulatory_signals
from sources.fetch_secedgar import fetch_press_releases

MAX_HISTORY_TURNS = 6  # last N (user, assistant) pairs sent back as context
MAX_TOOLS = 3
MAX_RECORDS = 6
MAX_REPORT_CONTEXT_CHARS = 200_000

SITE_STRUCTURE = "\n".join(
    f"{i}. {spec['title']}" + (f" — {spec['subtitle']}" if spec.get("subtitle") else "")
    for i, spec in enumerate(CARDS, start=1)
)


# ---------------------------------------------------------------------------
# Tools — thin wrappers over our own fetchers, each returning compact text.
# ---------------------------------------------------------------------------
def _records(records: list[dict] | None) -> str:
    rows = (records or [])[:MAX_RECORDS]
    return "\n".join(f"- {r.get('raw_snippet', '')} [{r.get('source', '')}]({r.get('url', '')})" for r in rows) or "no results"


def _names(args: dict, key: str) -> list[str]:
    value = args.get(key) or []
    return [value] if isinstance(value, str) else [str(v) for v in value][:5]


def _tool_drugs(args: dict) -> str:
    candidates = fetch_indication_drugs(args["indication"], args.get("mechanism"))
    lines = []
    for c in candidates:
        first_us = (
            f"drug first US-approved {c['us_first_approval']} as {c['us_brand']} (any indication)"
            if c.get("us_first_approval") else "no US approval on record for any indication"
        )
        trials = f"{len(c['active_trials'])} active trial(s): {', '.join(c['active_trials'][:4])}" if c.get("active_trials") else "no active trials"
        via = " | ACTS THROUGH THE QUERIED MECHANISM" if c.get("via_queried_mechanism") else ""
        lines.append(
            f"- {c['name']} | stage for {c.get('disease')}: {c.get('stage') or 'unknown'} | {c.get('disease_approval')} | "
            f"{c.get('drug_type') or 'type unknown'} | mechanism: {c.get('mechanism') or 'not curated'} | "
            f"targets: {', '.join(c.get('targets') or []) or 'not curated'} | {first_us} | {trials}{via} | {c['url']}"
        )
    return "\n".join(lines) or "no drugs found for that indication"


def _tool_trials(args: dict) -> str:
    status = "RECRUITING,ACTIVE_NOT_RECRUITING,NOT_YET_RECRUITING" if args.get("active_only") else None
    trials = fetch_clinicaltrials(args["indication"], args.get("intervention") or "", max_results=MAX_RECORDS, status_filter=status)
    return "\n".join(
        f"- {t['nctId']} | {t.get('briefTitle')} | {', '.join(t.get('phases') or []) or 'phase n/a'} | {t.get('overallStatus')} | "
        f"drugs: {', '.join(t.get('interventions') or []) or 'n/a'} | sponsor: {t.get('leadSponsor')} | {t.get('url')}"
        for t in trials or []
    ) or "no trials found"


def _iso(yyyymmdd: str | None) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}" if yyyymmdd and len(yyyymmdd) == 8 else "n/a"


def _tool_fda(args: dict) -> str:
    regs = fetch_regulatory_signals(_names(args, "drugs"))
    return "\n".join(
        f"- {r['drug_name']} | first US approval {_iso(r.get('first_approval_date'))} | brands: {', '.join(r.get('brand_names') or [])} | "
        f"sponsor: {r.get('sponsor')} | designations: {', '.join(r.get('designations') or []) or 'none'} | {r.get('url')}"
        for r in regs
    ) or "no US FDA records found"


def _tool_patents(args: dict) -> str:
    books = fetch_orange_book_patents(fetch_regulatory_signals(_names(args, "drugs")))
    return "\n".join(
        f"- {b['raw_snippet']} Patents: {', '.join(f'{p['patent_no']} until {p['expires']}' for p in b['patents'][:6])} | {b['url']}"
        for b in books
    ) or "no Orange Book listings (only FDA-approved small molecules are listed)"


def _tool_profile(args: dict) -> str:
    p = fetch_drug_profile(args["drug"])
    if not p:
        return "no exact Open Targets drug match"
    return (f"- {p['name']} ({p['chembl_id']}) | type: {p.get('drug_type')} | max stage: {p.get('stage')} | "
            f"mechanism: {p.get('mechanism') or 'not curated'} | targets: {', '.join(p.get('targets') or []) or 'not curated'} | "
            f"https://platform.opentargets.org/drug/{p['chembl_id']}")


TOOLS = {
    "drugs_for_indication": (
        "indication (English disease name), optional mechanism — drugs with clinical evidence for that "
        "disease: stage for it, approval status, mechanism/targets, active trials (Open Targets + FDA + CT.gov)",
        _tool_drugs,
    ),
    "clinical_trials": (
        "indication, optional intervention, optional active_only (true/false) — ClinicalTrials.gov trials",
        _tool_trials,
    ),
    "literature": ("query — top PubMed articles", lambda a: _records(fetch_pubmed(a["query"], max_results=MAX_RECORDS))),
    "target_disease_evidence": (
        "mechanism, indication — Open Targets target–disease association and drugs on that target",
        lambda a: _records(fetch_opentargets(a["mechanism"], a["indication"])),
    ),
    "drug_profile": ("drug — Open Targets profile: targets, modality, max stage", _tool_profile),
    "fda_approvals": ("drugs (list) — US approval date, brand, sponsor, designations (openFDA + Orange Book)", _tool_fda),
    "us_patents_exclusivity": ("drugs (list) — FDA Orange Book patents and exclusivity expiry dates", _tool_patents),
    "company_filings": ("terms (list) — SEC EDGAR 8-K filings mentioning them", lambda a: _records(fetch_press_releases(_names(a, "terms"), max_per_term=3))),
    "worldwide_patents": ("terms (list) — Lens.org patent filings worldwide", lambda a: _records(fetch_patent_signals(_names(a, "terms"), max_per_term=3))),
}


# ---------------------------------------------------------------------------
# Step 1 — plan.
# ---------------------------------------------------------------------------
_PLAN_PROMPT = """You plan database lookups for a biotech investment assistant. You see the client's \
SCREEN, a REPORT_SUMMARY of the loaded report (if any), ON_SCREEN_CARDS (full content of the open or \
visible cards) and the message. Decide which lookups are needed to answer well. Rules:
- Run NOTHING when the answer is already in ON_SCREEN_CARDS or the loaded report, or the question \
is about how the site works, or it is small talk. Check ON_SCREEN_CARDS first: if the asked-for \
data is there (e.g. patent expiry dates on an open Patent card), no lookup. Lookups cost time — \
only run what the answer needs.
- Run lookups when the question needs data the report doesn't hold: other diseases or drugs, drug \
recommendations, approvals, patents, trials, literature, filings.
- "This card", "here", "these drugs" refer to SCREEN.open_card or SCREEN.visible_cards.
- Disease names in English (translate: "розсіяний склероз" -> "multiple sclerosis"). If the user \
names no disease but means the loaded one, use REPORT_SUMMARY's indication.
- At most 3 lookups, only from TOOLS, with the exact argument names shown.
Return ONLY JSON: {"lookups": [{"tool": "<name>", "args": {...}}]} — an empty list when none are needed.

TOOLS:
""" + "\n".join(f"- {name}: {desc}" for name, (desc, _) in TOOLS.items())


def _report_summary(report: dict | None) -> str:
    if not report:
        return "none loaded"
    return json.dumps({
        "mechanism": report.get("mechanism"),
        "indication": report.get("indication"),
        "verdict": report.get("verdict"),
        "cards": [f"{c['number']}. {c['title']}" for c in report.get("cards", [])],
    }, ensure_ascii=False)


def _on_screen_cards(report: dict | None, screen: dict, limit: int = 12_000) -> str:
    """Content of the open card (or the visible ones) — so the planner can see
    the answer is already on screen instead of guessing from card titles."""
    if not report:
        return "none"
    ids = [(screen.get("open_card") or {}).get("id")] if screen.get("open_card") else [
        c.get("id") for c in screen.get("visible_cards") or []
    ]
    cards = [
        {k: v for k, v in c.items() if k in ("number", "title", "columns", "rows", "sections", "items")}
        for c in report.get("cards", []) if c.get("id") in ids
    ]
    text = json.dumps(cards, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + " …[cut]"


def _plan(message: str, history_text: str, report: dict | None, screen: dict) -> list[dict]:
    prompt = (
        f"SCREEN: {json.dumps(screen, ensure_ascii=False)}\n"
        f"REPORT_SUMMARY: {_report_summary(report)}\n"
        f"ON_SCREEN_CARDS: {_on_screen_cards(report, screen)}\n"
        + (f"Recent conversation:\n{history_text}\n" if history_text else "")
        + f"Message: {message}"
    )
    try:
        raw = ask_assistant_reply(_PLAN_PROMPT, prompt, max_tokens=800)
        plan = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    except Exception as e:
        print(f"[Assistant Warning] planning failed: {e}")
        return []
    lookups = [l for l in plan.get("lookups") or [] if isinstance(l, dict) and l.get("tool") in TOOLS]
    return lookups[:MAX_TOOLS]


def _run_tools(lookups: list[dict]) -> str:
    blocks = []
    for lookup in lookups:
        name, args = lookup["tool"], lookup.get("args") or {}
        try:
            result = TOOLS[name][1](args)
        except Exception as e:  # a missing arg or an API outage costs only this tool
            result = f"lookup failed ({e.__class__.__name__}: {e})"
        blocks.append(f"### {name} {json.dumps(args, ensure_ascii=False)}\n{result}")
    return "\n\n".join(blocks) or "none (no lookup was needed)"


# ---------------------------------------------------------------------------
# Step 3 — answer.
# ---------------------------------------------------------------------------
def _format_report_context(report: dict | None, screen: dict) -> str:
    """The loaded report without duplicated data (raw card `items` already shown
    in rows/sections, chart render config). The open card, if any, goes first."""
    if not report:
        return "null (no report is loaded)"
    open_id = (screen.get("open_card") or {}).get("id")
    cards = []
    for card in report.get("cards", []):
        c = {k: v for k, v in card.items() if k not in ("chart", "items")}
        if card.get("view") == "list":
            c["items"] = card.get("items", [])
        cards.append(c)
    cards.sort(key=lambda c: c.get("id") != open_id)
    text = json.dumps({**report, "cards": cards}, ensure_ascii=False)
    if len(text) <= MAX_REPORT_CONTEXT_CHARS:
        return text
    return text[:MAX_REPORT_CONTEXT_CHARS] + " [TRUNCATED — later cards are missing]"


SYSTEM_PROMPT = f"""You are the Assistant on a biotech "Investment Committee" web tool. The site \
takes a Mechanism/Target + Indication, queries our databases (PubMed, Open Targets, \
ClinicalTrials.gov, AACT, openFDA, FDA Orange Book, SEC EDGAR, Lens.org, a market-data CSV) and \
shows a verdict (Invest / Conditional / Do Not Invest) plus these cards:

{SITE_STRUCTURE}

Blocks you receive: SCREEN (what the client sees now — page, open card, visible cards, inputs), \
REPORT_CONTEXT (the loaded report), TOOL_RESULTS (lookups run for this message).

GROUNDING: every drug, target, trial, date, number or judgement you state must come from those \
blocks. Nothing from your own general knowledge — if it isn't in the blocks, say our sources don't \
have it. Explaining how the site works is always allowed.

USE THE SCREEN: "this", "here", "this card" = SCREEN.open_card, else SCREEN.visible_cards. If the \
page is "loading", the evaluation is still running; if "welcome", no report is in view.

DRUG RECOMMENDATIONS (from drugs_for_indication results): rank by evidence for THIS disease — \
"approved for this disease" > Phase 3 / pre-approval > earlier > "unverified" (never present an \
unverified one as approved); ties by active trials. A "first US-approved ... (any indication)" date \
is not approval for this disease. Rows marked ACTS THROUGH THE QUERIED MECHANISM: lead with them \
when asked about that mechanism, drop them when asked for alternatives to it. For investment \
questions favour late-stage investigational assets with active trials, and use approved drugs as \
the competitive benchmark. For a question about treating a person: one clause noting this is \
investment research, not medical advice.

FORMAT — structured, zero filler:
**Answer:** one sentence that answers the question.
Then only the sections that apply, each a few "- " lines of facts:
**Evidence** · **Candidates** (ranked: name — stage, mechanism, approval, trials) · **Why** · **Next**
End every fact line with a short source tag: [Card 5] / [Report] only for facts taken from \
REPORT_CONTEXT; [Open Targets], [FDA], [Orange Book], [CT.gov NCTxxxx], [PubMed], [SEC], [Lens] for \
facts from TOOL_RESULTS.
Reply in the language the user wrote in (Ukrainian question -> Ukrainian answer, section labels too). \
No greetings, no restating the question, no generic caveats. At most ~10 lines.

SEARCH line: to propose evaluating something on the site, end with exactly one line
SEARCH: <short target gene or 2-4 word drug class> | <indication in English>
e.g. "SEARCH: BTK inhibitor | Multiple Sclerosis". Omit it otherwise.

LIMITATIONS (state them accurately if asked): you see structured screen state, not pixels; one \
round of at most {MAX_TOOLS} lookups per message; you don't run the full committee evaluation \
yourself (it takes ~2 min — you propose it via SEARCH); Open Targets curation has gaps; FDA data is \
US-only and the Orange Book lists only approved small molecules (no biologics); trial and \
literature lookups return top results, not exhaustive lists; SEC data is filing metadata, not \
sentiment; no paid sources (sales, analyst consensus); market CSV figures are unverified; chat \
memory is the last {MAX_HISTORY_TURNS} exchanges of the open chat."""


def _format_history(history: list[dict]) -> str:
    trimmed = history[-(MAX_HISTORY_TURNS * 2):]
    return "\n".join(f"{'User' if h.get('role') == 'user' else 'Assistant'}: {h.get('text', '')}" for h in trimmed)


_SEARCH_LINE = re.compile(r"^\s*SEARCH:\s*(.+?)\s*\|\s*(.+?)\s*$", re.I | re.M)


def ask_assistant(message: str, history: list[dict], report: dict | None, screen: dict | None = None) -> dict:
    """Returns {"reply": str, "search": {"mechanism", "indication"} | None, "lookups": [tool names]}."""
    screen = screen or {}
    history_text = _format_history(history)
    lookups = _plan(message, history_text, report, screen)
    tool_results = _run_tools(lookups)

    user_prompt = (
        f"SCREEN: {json.dumps(screen, ensure_ascii=False)}\n\n"
        f"REPORT_CONTEXT: {_format_report_context(report, screen)}\n\n"
        f"TOOL_RESULTS:\n{tool_results}\n\n"
        + (f"Recent conversation:\n{history_text}\n\n" if history_text else "")
        + f"User: {message}"
    )
    # Headroom for hidden reasoning; the visible length is capped by the prompt.
    raw = ask_assistant_reply(SYSTEM_PROMPT, user_prompt, max_tokens=2000)

    search = None
    m = _SEARCH_LINE.search(raw)
    if m:
        s_mechanism, s_indication = m.group(1).strip(), m.group(2).strip()
        if s_mechanism and s_indication and "<" not in s_mechanism + s_indication:
            search = {"mechanism": s_mechanism, "indication": s_indication}
        raw = raw[: m.start()].rstrip()
    raw = re.sub(r"(?im)^\s*SEARCH:.*$", "", raw).rstrip()  # e.g. a leftover "SEARCH: None"

    return {
        "reply": raw or "I'm not sure how to answer that — try rephrasing?",
        "search": search,
        "lookups": [l["tool"] for l in lookups],
    }
