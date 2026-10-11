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
from concurrent.futures import ThreadPoolExecutor

import requests

from cards import CARDS
from llm import ask_assistant_reply
from raw_data import _base_name
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_cms_spending import fetch_medicare_spending
from sources.fetch_drug_profiles import fetch_drug_profile
from sources.fetch_ema import fetch_eu_approvals
from sources.fetch_faers import fetch_adverse_events
from sources.fetch_fda_labels import fetch_drug_labels
from sources.fetch_purple_book import fetch_biologic_exclusivity
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


_generic_cache: dict[str, str] = {}


def _to_generic(name: str) -> str:
    """Brand -> generic (INN): every drug source is keyed by generic name, so
    "Ocrevus" found nothing in CMS while "ocrelizumab" has $669M. A name that
    isn't a known US brand is returned unchanged (it's likely generic already)."""
    key = name.strip().lower()
    if key not in _generic_cache:
        fda = _fda_brand_generic(key)
        # Biologics licensed since 2017 carry a 4-letter FDA suffix
        # ("BIMEKIZUMAB-BKZX"); EMA and Open Targets know only the bare INN.
        generic = (re.sub(r"-[a-z]{4}$", "", fda.lower()) if fda else None) or _ema_brand_generic(key) or name.strip()
        # "FINGOLIMOD HYDROCHLORIDE" -> "fingolimod": sources search the moiety.
        _generic_cache[key] = _base_name(generic) or generic
    return _generic_cache[key]


def _fda_brand_generic(brand: str) -> str | None:
    """The single active ingredient of the product sold under exactly this US
    brand. Exact brand only ("Keytruda", not "Keytruda Qlex", a combination),
    read from the products themselves — some applications lack the harmonized
    openfda block entirely (Brukinsa)."""
    try:
        resp = requests.get(
            "https://api.fda.gov/drug/drugsfda.json",
            params={"search": f'products.brand_name:"{brand}"', "limit": 10},
            timeout=10,
        )
        results = resp.json().get("results") or [] if resp.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return None
    for app in results:
        for p in app.get("products") or []:
            ingredients = p.get("active_ingredients") or []
            if (p.get("brand_name") or "").lower() == brand and len(ingredients) == 1:
                return ingredients[0].get("name")
    return None


def _ema_brand_generic(brand: str) -> str | None:
    """EU-only brands (e.g. Cenrifki = tolebrutinib) from the EMA table."""
    from sources.fetch_ema import _load

    hit = next((m for m in _load() if (m.get("name_of_medicine") or "").lower() == brand), None)
    return (hit or {}).get("international_non_proprietary_name_common_name") or None


def _is_name(value) -> bool:
    """A usable drug/term: text with at least one letter — a stray number from
    the planner ("5") used to be searched and matched "Lidoderm 5%"."""
    return isinstance(value, str) and any(ch.isalpha() for ch in value)


def _names(args: dict, key: str) -> list[str]:
    value = args.get(key) or []
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    names = [v.strip() for v in values if _is_name(v)][:5]
    return [_to_generic(n) for n in names] if key == "drugs" else names


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


def _flag(value) -> bool:
    """The planner writes booleans as JSON true or as text ("false") — the
    string "false" is truthy in Python and used to switch the filter ON."""
    return value is True or str(value).strip().lower() in ("true", "yes", "1")


def _tool_trials(args: dict) -> str:
    status = "RECRUITING,ACTIVE_NOT_RECRUITING,NOT_YET_RECRUITING" if _flag(args.get("active_only")) else None
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
    p = fetch_drug_profile(_to_generic(args["drug"]))
    if not p:
        return "no exact Open Targets drug match"
    return (f"- {p['name']} ({p['chembl_id']}) | type: {p.get('drug_type')} | max stage: {p.get('stage')} | "
            f"mechanism: {p.get('mechanism') or 'not curated'} | targets: {', '.join(p.get('targets') or []) or 'not curated'} | "
            f"https://platform.opentargets.org/drug/{p['chembl_id']}")


def _tool_safety(args: dict) -> str:
    names = _names(args, "drugs")
    labels = {l["drug_name"].lower(): l for l in fetch_drug_labels(names)}
    lines = []
    for ae in fetch_adverse_events(names, args.get("indication") or ""):
        boxed = (labels.get(ae["drug_name"].lower()) or {}).get("boxed_warning")
        lines.append(f"- {ae['raw_snippet']} Boxed warning: {boxed or 'none on US label'} | {ae['url']}")
    return "\n".join(lines) or "no FAERS reports or US labels found"


def _tool_labels(args: dict) -> str:
    return "\n".join(
        f"- {l['drug_name']} ({l.get('brand')}) | US indication: {l.get('indication')} | boxed warning: {l.get('boxed_warning') or 'none'} | {l['url']}"
        for l in fetch_drug_labels(_names(args, "drugs"))
    ) or "no US labels found"


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
    "drug_safety": (
        "drugs (list), optional indication — FAERS adverse-event reports (total, serious, top reactions) "
        "and the US label's boxed warning",
        _tool_safety,
    ),
    "drug_labels": ("drugs (list) — official US label: approved indications and boxed warning", _tool_labels),
    "eu_approvals": ("drugs (list) — EMA EU authorisation: status, date, indication, conditional/orphan/PRIME", lambda a: _records(fetch_eu_approvals(_names(a, "drugs")))),
    "medicare_spending": (
        "drugs (list) — CMS Medicare Part D/B spending, claims and beneficiaries per year (real US spending; "
        "Medicare only, not total sales)",
        lambda a: _records(fetch_medicare_spending(_names(a, "drugs"))),
    ),
    "biologic_exclusivity": ("drugs (list) — FDA Purple Book: biologic licensure and exclusivity dates", lambda a: _records(fetch_biologic_exclusivity(_names(a, "drugs")))),
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
- At most 3 lookups, only from TOOLS, with the exact argument names shown. Pass drugs by generic \
(INN) name when you know it from the blocks (e.g. "ocrelizumab", not "Ocrevus").
Return ONLY JSON: {"lookups": [{"tool": "<name>", "args": {...}}]} — an empty list when none are needed.

TOOLS:
""" + "\n".join(f"- {name}: {desc}" for name, (desc, _) in TOOLS.items())


def _report_summary(report: dict | None) -> str:
    if not report:
        return "none loaded"
    return json.dumps({
        "mechanism": report.get("mechanism"),
        "indication": report.get("indication"),
        "program_details": report.get("details") or {},
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
    except Exception as e:
        print(f"[Assistant Warning] planning failed: {e}")
        return []
    plan = _first_json_object(raw, "lookups")
    lookups = plan.get("lookups") if isinstance(plan.get("lookups"), list) else []
    return [l for l in lookups if isinstance(l, dict) and l.get("tool") in TOOLS][:MAX_TOOLS]


def _first_json_object(text: str, key: str) -> dict:
    """The first JSON object in `text` that has `key` — scanning every "{", so
    prose with its own braces or a markdown fence around the JSON can't hide it
    (slicing first "{" to last "}" broke on "Plan {a}: {...}")."""
    decoder = json.JSONDecoder()
    i = (text or "").find("{")
    while i != -1:
        try:
            obj, _ = decoder.raw_decode(text, i)
            if isinstance(obj, dict) and key in obj:
                return obj
        except json.JSONDecodeError:
            pass
        i = text.find("{", i + 1)
    return {}


_LIST_ARGS = ("drugs", "terms")
_REQUIRED_ARGS = {
    "drugs_for_indication": ["indication"], "clinical_trials": ["indication"], "literature": ["query"],
    "target_disease_evidence": ["mechanism", "indication"], "drug_profile": ["drug"],
    "fda_approvals": ["drugs"], "us_patents_exclusivity": ["drugs"], "drug_safety": ["drugs"],
    "drug_labels": ["drugs"], "eu_approvals": ["drugs"], "medicare_spending": ["drugs"],
    "biologic_exclusivity": ["drugs"], "company_filings": ["terms"], "worldwide_patents": ["terms"],
}


def _usable_arg(key: str, value) -> bool:
    if key in _LIST_ARGS:
        return any(_is_name(v) for v in ([value] if isinstance(value, str) else value if isinstance(value, list) else []))
    return isinstance(value, bool) or isinstance(value, str) and bool(value.strip())


def _run_tool(lookup: dict) -> str:
    name = lookup["tool"]
    args = lookup.get("args") if isinstance(lookup.get("args"), dict) else {}
    # Scalar args must be text (lists only for drugs/terms) — anything else
    # from the planner counts as not given rather than crashing the tool.
    args = {k: v for k, v in args.items() if _usable_arg(k, v)}
    header = f"### {name} {json.dumps(args, ensure_ascii=False)}"
    missing = [a for a in _REQUIRED_ARGS.get(name, []) if not args.get(a)]
    if missing:
        return f"{header}\nnot run — missing argument(s): {', '.join(missing)}"
    try:
        result = TOOLS[name][1](args)
    except Exception as e:  # an API outage costs only this tool
        result = f"lookup failed ({e.__class__.__name__}: {e})"
    return f"{header}\n{result}"


def _run_tools(lookups: list[dict]) -> str:
    """The planned lookups, run in parallel; blocks keep the planner's order."""
    if not lookups:
        return "none (no lookup was needed)"
    with ThreadPoolExecutor(max_workers=MAX_TOOLS) as pool:
        return "\n\n".join(pool.map(_run_tool, lookups))


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
ClinicalTrials.gov, AACT, openFDA approvals/labels/FAERS, FDA Orange Book & Purple Book, EMA, CMS \
Medicare spending, SEC EDGAR, Lens.org, a market-data CSV) and \
shows a verdict (Invest / Conditional / Do Not Invest) plus these cards:

{SITE_STRUCTURE}

Blocks you receive: SCREEN (what the client sees now — page, open card, visible cards, inputs), \
REPORT_CONTEXT (the loaded report), TOOL_RESULTS (lookups run for this message).

GROUNDING: every drug, target, trial, date, number or judgement you state must come from those \
blocks. Nothing from your own general knowledge — if it isn't in the blocks, say our sources don't \
have it. Explaining how the site works is always allowed.

USE THE SCREEN: "this", "here", "this card" = SCREEN.open_card, else SCREEN.visible_cards. If the \
page is "loading", the evaluation is still running; if "welcome", no report is in view. \
REPORT_CONTEXT.details holds the program details the user entered (modality, development stage, \
route, biomarkers, preclinical results) — "our program" / "our asset" means that profile; compare \
competitors against it when relevant.

DRUG RECOMMENDATIONS (from drugs_for_indication results): rank by evidence for THIS disease — \
"approved for this disease" > Phase 3 / pre-approval > earlier > "unverified" (never present an \
unverified one as approved); ties by active trials. A "first US-approved ... (any indication)" date \
is not approval for this disease. Rows marked ACTS THROUGH THE QUERIED MECHANISM: lead with them \
when asked about that mechanism, drop them when asked for alternatives to it. For investment \
questions favour late-stage investigational assets with active trials, and use approved drugs as \
the competitive benchmark. For a question about treating a person: one clause noting this is \
investment research, not medical advice.

LANGUAGE: answer in the language of the user's LATEST message, whatever it is — Ukrainian question -> Ukrainian answer, Russian -> Russian, English -> English, and so on. Translate the section labels too (**Answer:** -> **Відповідь:** / **Ответ:**, **Evidence** -> **Докази** / **Доказательства**, **Candidates** -> **Кандидати**, **Why** -> **Чому**, **Next** -> **Далі**, LIMITATIONS -> **Обмеження**). Never answer in a different language than the question, and never add a translation of your own answer. If the user switches language mid-chat, follow the newest message, not the history. A message with no words to judge by (only a number, a link or an emoji) takes the language of the user's previous message, else English. Keep in their original form, untranslated: drug, gene and company names (ocrelizumab, KRAS G12C, UCB), trial IDs, the source tags in square brackets, and the SEARCH line below.

FORMAT — structured, zero filler:
**Answer:** one sentence that answers the question.
Then only the sections that apply, each a few "- " lines of facts:
**Evidence** · **Candidates** (ranked: name — stage, mechanism, approval, trials) · **Why** · **Next**
End every fact line with a short source tag: [Card 5] / [Report] only for facts taken from \
REPORT_CONTEXT; [Open Targets], [FDA], [FDA label], [FAERS], [Orange Book], [Purple Book], [EMA], \
[CMS], [CT.gov NCTxxxx], [PubMed], [SEC], [Lens] for \
facts from TOOL_RESULTS.
No greetings, no restating the question, no generic caveats. At most ~10 lines.

SEARCH line: to propose evaluating something on the site, end with exactly one line
SEARCH: <short target gene or 2-4 word drug class> | <indication in English>
e.g. "SEARCH: BTK inhibitor | Multiple Sclerosis". Omit it otherwise. This line is read by the site, not by the user: keep the keyword exactly "SEARCH:" and both terms in English even when the rest of your answer is in another language (the databases are English-only).

LIMITATIONS (state them accurately if asked): you see structured screen state, not pixels; one \
round of at most {MAX_TOOLS} lookups per message; you don't run the full committee evaluation \
yourself (it takes ~2 min — you propose it via SEARCH); Open Targets curation has gaps; regulatory \
data covers the US (FDA) and EU (EMA) only; FAERS counts are spontaneous reports, not incidence or \
causation; CMS figures are Medicare spending only, not total sales; trial and literature lookups \
return top results, not exhaustive lists; SEC data is filing metadata, not sentiment; no paid \
sources (global sales, analyst consensus); market CSV figures are unverified; chat \
memory is the last {MAX_HISTORY_TURNS} exchanges of the open chat."""


def _format_history(history: list[dict]) -> str:
    # Entries come from the browser's localStorage — skip anything malformed.
    valid = [h for h in history if isinstance(h, dict) and h.get("text")] if isinstance(history, list) else []
    trimmed = valid[-(MAX_HISTORY_TURNS * 2):]
    return "\n".join(f"{'User' if h.get('role') == 'user' else 'Assistant'}: {h['text']}" for h in trimmed)


# A SEARCH line anywhere in the reply, optionally wrapped in markdown bold.
# The keyword is asked for in English, but a model answering in Ukrainian or
# Russian sometimes translates it anyway — accepted here so the "Run this
# search" button doesn't silently disappear on a non-English answer.
_SEARCH_WORD = r"(?:SEARCH|ПОШУК|ПОИСК)"
_SEARCH_LINE = re.compile(
    rf"^[ \t]*\**[ \t]*{_SEARCH_WORD}:[ \t]*(.+?)[ \t]*\|[ \t]*(.+?)[ \t]*\**[ \t]*$", re.I | re.M
)
_ANY_SEARCH_LINE = re.compile(rf"^[ \t]*\**[ \t]*{_SEARCH_WORD}:.*$\n?", re.I | re.M)


def ask_assistant(message: str, history: list[dict], report: dict | None, screen: dict | None = None) -> dict:
    """Returns {"reply": str, "search": {"mechanism", "indication"} | None, "lookups": [tool names]}."""
    screen = dict(screen or {})
    if not isinstance(screen.get("open_card"), dict):
        screen["open_card"] = None
    screen["visible_cards"] = [c for c in screen.get("visible_cards") or [] if isinstance(c, dict)] \
        if isinstance(screen.get("visible_cards"), list) else []
    if report is not None and not isinstance(report.get("cards"), list):
        report = {**report, "cards": []}
    elif report is not None:
        report = {**report, "cards": [c for c in report["cards"] if isinstance(c, dict)]}
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
    m = _SEARCH_LINE.search(raw or "")
    if m:
        s_mechanism, s_indication = m.group(1).strip(" *"), m.group(2).strip(" *")
        if s_mechanism and s_indication and "<" not in s_mechanism + s_indication:
            search = {"mechanism": s_mechanism, "indication": s_indication}
    # Remove only the SEARCH line(s) — truncating at the first one dropped the
    # whole answer when the model put SEARCH first. Also catches "SEARCH: None".
    raw = _ANY_SEARCH_LINE.sub("", raw or "").strip()

    return {
        "reply": raw or "I'm not sure how to answer that — try rephrasing?",
        "search": search,
        "lookups": [l["tool"] for l in lookups],
    }
