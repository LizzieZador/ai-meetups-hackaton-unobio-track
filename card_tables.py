"""Deterministic per-card table/graph builders — no LLM call, no new network
call, pure Python over data cards.py already assembled (claims by section,
raw source records, Open Targets analogs).

Reshapes today's flat claim/record list into the table rows or chart series
each card needs, per the user's spec. A cell with no real backing data is the
literal string "Not available" — never a fabricated estimate (the project's
existing evidence/inference separation already has a home for genuine
analytical judgment calls; a table cell with nothing behind it is not one).

Every builder has the same signature: (claims_by_section, sources, analogs) -> dict.
The returned dict is merged into the card's existing output by cards.py:
  {"view": "table", "columns": [...], "rows": [...], "show_claims_below": bool}
  {"view": "graph", "chart": {...Chart.js config...} | None}
A card with no entry in VIEW_BUILDERS keeps today's "view": "list" behavior.

`show_claims_below` controls whether the card's own claim_sections claims are
also rendered as a list under the table — only when the table draws from a
*different* raw data bucket than those claims (so nothing is ever shown
twice). It defaults to False (the table already IS those claims, reshaped).
"""
import re
from collections import Counter

from schema import Claim

NA = "Not available"


def _claims(claims_by_section: dict[str, list[Claim]], *sections: str) -> list[Claim]:
    out: list[Claim] = []
    for s in sections:
        out.extend(claims_by_section.get(s, []))
    return out


def _cell(text, url: str | None = None):
    """A plain string cell, or {"text", "url"} when the value should link out."""
    text = text if text not in (None, "") else NA
    return {"text": text, "url": url} if url else text


def _short(text: str, limit: int = 130) -> str:
    """Trim to the point, never mid-word/mid-sentence — same safety-net
    pattern as quick_summary.py's _short(), just a tighter default limit:
    a table cell needs the fact, not the full sentence it came wrapped in."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    if cut != -1:
        return text[: cut + 1]
    cut = text.rfind(" ", 0, limit)
    return (text[:cut] if cut != -1 else text[:limit]) + "…"


# ---------------------------------------------------------------------------
# Card 1 — Unmet need: bucket the card's OWN existing claims into 3 fixed
# rows by keyword match. This never writes new text — it only re-sorts real,
# already-generated claims. A bucket with nothing matching shows "Not
# available" rather than an invented sentence.
# ---------------------------------------------------------------------------
_SOC_KEYWORDS = ("standard of care", "current treatment", "current therapy", "first-line", "existing therapy", "approved therapy")
_GAP_KEYWORDS = ("unmet need", "gap", "limitation", "fail", "resistance", "inadequate", "poor outcome")
_TPP_KEYWORDS = ("target product profile", "differentiat", "profile", "positioning", "advantage over")


def build_unmet_need_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    pool = _claims(claims_by_section, "human_translation_thesis", "commercial_opportunity")
    # The 3 keyword sets aren't mutually exclusive (e.g. a claim can mention
    # both "standard of care" and "unmet need") — track which claims have
    # already been placed in an earlier row so the same sentence is never
    # shown twice under two different Points.
    used: set[int] = set()

    def bucket(keywords: tuple[str, ...]) -> str:
        matches = [c for c in pool if id(c) not in used and any(k in c.claim.lower() for k in keywords)]
        if not matches:
            return NA
        selected = matches[:2]
        used.update(id(c) for c in selected)
        return " ".join(c.claim.strip().rstrip(".") + "." for c in selected)

    rows = [
        {"Point": "Current Standard of Care", "Content": bucket(_SOC_KEYWORDS)},
        {"Point": "Key Gaps", "Content": bucket(_GAP_KEYWORDS)},
        {"Point": "Target Product Profile", "Content": bucket(_TPP_KEYWORDS)},
    ]
    return {"view": "table", "columns": ["Point", "Content"], "rows": rows, "show_claims_below": False}


# ---------------------------------------------------------------------------
# Card 2 — Market sentiment: SEC EDGAR has no sentiment score anywhere, only
# filing metadata. Graph the one honest quantity that exists: filing count
# per company. Labeled "filing activity," never "sentiment."
# ---------------------------------------------------------------------------
def _clean_filer_name(name: str) -> str:
    """EDGAR's display name is e.g. "Verastem, Inc.  (VSTM)  (CIK 0001526119)" —
    strip the CIK suffix (an internal id, not something a reader needs) but
    keep the ticker, so axis labels stay short and readable."""
    cleaned = re.sub(r"\s*\(CIK\s*\d+\)\s*$", "", name or "").strip()
    return cleaned or "Unknown filer"


_PIE_PALETTE = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]


def build_market_sentiment_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    records = sources.get("market_news", [])
    if not records:
        return {"view": "graph", "chart": None}

    by_name: dict[str, list[dict]] = {}
    for r in records:
        by_name.setdefault(_clean_filer_name(r.get("company")), []).append(r)

    counts = Counter({name: len(recs) for name, recs in by_name.items()})
    top = counts.most_common(6)
    total = sum(n for _, n in top) or 1

    # The percentage is baked into the legend label itself (not a tooltip
    # callback) because this chart config is plain JSON handed to Chart.js —
    # there's no live JS function to compute it client-side.
    labels = [
        f"{name} — {n} filing{'s' if n != 1 else ''} ({round(n / total * 100)}%)"
        for name, n in top
    ]

    # One entry per slice, same order as the chart's data — the front-end
    # looks this up by clicked slice index to show a real summary + the
    # actual filing records backing it (clicking a chart element only gives
    # Chart.js an index, so this has to be a plain parallel list, not keyed).
    chart_details = [
        {
            "label": name,
            "summary": (
                f"{n} SEC 8-K filing{'s' if n != 1 else ''} from {name} mentioned this program "
                f"({round(n / total * 100)}% of the filing activity shown in this chart)."
            ),
            "sources": [
                {"source": r.get("source"), "url": r.get("url"), "date_accessed": r.get("date_accessed")}
                for r in by_name[name]
            ],
        }
        for name, n in top
    ]

    chart = {
        "type": "pie",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": [n for _, n in top],
                "backgroundColor": _PIE_PALETTE[: len(top)],
                "borderColor": "#ffffff",
                "borderWidth": 2,
                "hoverOffset": 6,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {
                "legend": {
                    "position": "bottom",
                    "labels": {"boxWidth": 11, "font": {"size": 11}, "padding": 8},
                },
                "title": {
                    "display": True,
                    "text": "Share of SEC 8-K filing activity by company (not a sentiment score — no source provides one)",
                    "font": {"size": 12, "weight": "normal"},
                    "padding": {"bottom": 10},
                },
            },
        },
    }

    # Expanded (modal) view only: every single fetched record, not just the
    # top 6 the pie slices show, each tagged with the same color as its pie
    # slice (so a reader can connect a row back to the chart at a glance) —
    # a company past the chart's top 6 gets no color, it simply isn't a
    # slice. Grouped by company (same color together) then newest-first.
    color_by_name = {name: _PIE_PALETTE[i % len(_PIE_PALETTE)] for i, (name, _) in enumerate(top)}
    all_rows = []
    for name, _n in counts.most_common():  # every company, most filings first — matches the pie's own ranking
        for r in sorted(by_name[name], key=lambda r: r.get("file_date") or "", reverse=True):
            all_rows.append({
                "Company": name,
                "Form": r.get("form") or NA,
                "Filed": r.get("file_date") or NA,
                "Matched Term": r.get("matched_term") or NA,
                "Source": _cell(r.get("source"), r.get("url")),
                "_color": color_by_name.get(name),
            })

    return {
        "view": "graph",
        "chart": chart,
        "chart_details": chart_details,
        "table_columns": ["Company", "Form", "Filed", "Matched Term", "Source"],
        "table_rows": all_rows,
    }


# ---------------------------------------------------------------------------
# Card 3 — Scientific evidence: the claims themselves, tabulated. The
# scientific agent's prompt already tags each claim's text with a leading
# bracketed research-angle category (e.g. "[human genetics] ...", "[animal
# model] ..." — see agents/scientific_agent.py's SYSTEM_PROMPT) as a free-text
# convention, not a schema field. Pull it out into its own column instead of
# leaving it duplicated inside the Finding text.
# ---------------------------------------------------------------------------
_CATEGORY_PREFIX = re.compile(r"^\s*\[([^\[\]]{2,40})\]\s*")


def _split_category(claim_text: str) -> tuple[str, str]:
    m = _CATEGORY_PREFIX.match(claim_text or "")
    if not m:
        return NA, claim_text
    return m.group(1).strip().title(), _CATEGORY_PREFIX.sub("", claim_text, count=1)


# agents/scientific_agent.py already runs an 8-angle PubMed search (its own
# `query_groups` dict) and tags every record it fetches with a "group" key
# from that same taxonomy — genetics/tissue/perturbation/animal/pathway/
# clinical/contradictory/translation. Rather than dump every claim + every
# leftover raw record into one flat, unordered table (which read as a
# meaningless pile of text), this groups everything — claims and raw
# records alike — back into those same 8 real research angles, so the card
# reads as a structured literature review, not a dump.
_RESEARCH_ANGLES = [
    ("genetics", "Human Genetics"),
    ("tissue", "Tissue Expression"),
    ("perturbation", "Genetic / Pharmacologic Perturbation"),
    ("animal", "Animal Model"),
    ("pathway", "Pathway Biology"),
    ("clinical", "Clinical Evidence"),
    ("contradictory", "Contradictory Evidence"),
    ("translation", "Human Translation"),
]
_ANGLE_LABEL_BY_GROUP = dict(_RESEARCH_ANGLES)


def _match_angle(category_text: str) -> str | None:
    """Map a claim's free-text bracket category (the LLM's own wording,
    e.g. "[human genetics]") onto one of the 8 canonical research angles
    above, by keyword overlap rather than an exact string match."""
    text = (category_text or "").lower()
    for key, label in _RESEARCH_ANGLES:
        if key in text:
            return label
    if "translat" in text or "pharmacokinetic" in text or "biomarker" in text:
        return _ANGLE_LABEL_BY_GROUP["translation"]
    if "resistan" in text or "adverse" in text or "fail" in text or "toxicit" in text:
        return _ANGLE_LABEL_BY_GROUP["contradictory"]
    return None


def build_scientific_evidence_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    rows_by_angle: dict[str, list[dict]] = {label: [] for _, label in _RESEARCH_ANGLES}
    other_rows: list[dict] = []
    cited_sources: set[str] = set()

    for c in claims_by_section.get("scientific_thesis", []):
        category_text, finding = _split_category(c.claim)
        angle = _match_angle(category_text)
        if c.type == "evidence":
            source_cell = _cell(c.source, c.url)
            date_cell = c.date_accessed or NA
            if c.source:
                cited_sources.add(c.source)
        else:
            source_cell = f"Inference (confidence: {c.confidence})" if c.confidence else "Inference"
            date_cell = NA
        row = {"Finding": _short(finding), "Source": source_cell, "Date": date_cell}
        (rows_by_angle[angle] if angle else other_rows).append(row)

    # The LLM only turns a handful of PubMed records into claims — the "N
    # sources" footer counts every record this card actually fetched (via
    # raw_sources: ["pubmed"] in cards.py), which is far more than that
    # handful. Add the rest directly, under the same research angle they
    # were originally fetched for — real snippet, real link, no LLM step.
    for r in sources.get("pubmed", []):
        if r.get("source") in cited_sources:
            continue
        angle = _ANGLE_LABEL_BY_GROUP.get(r.get("group"))
        # raw_snippet is itself prefixed with the same "[group]" tag at fetch
        # time (agents/scientific_agent.py) — redundant now that the row
        # already sits under that angle's own section heading.
        _, raw_finding = _split_category(r.get("raw_snippet") or "")
        row = {
            "Finding": _short(raw_finding or NA),
            "Source": _cell(r.get("source"), r.get("url")),
            "Date": r.get("pub_date") or r.get("date_accessed") or NA,
        }
        (rows_by_angle[angle] if angle else other_rows).append(row)

    sections = [
        {"kind": "table", "title": label, "columns": ["Finding", "Source", "Date"], "rows": rows_by_angle[label]}
        for _, label in _RESEARCH_ANGLES
    ]
    if other_rows:
        sections.append({"kind": "table", "title": "Other Findings", "columns": ["Finding", "Source", "Date"], "rows": other_rows})

    return {"view": "composite", "sections": sections}


# ---------------------------------------------------------------------------
# Card 4 — Patient population, treatment dynamics & TAM: no table/graph (the
# underlying epidemiology CSV is still unverified — see data_gap_note in
# cards.py), but per mentor feedback this list should favor real,
# database-backed evidence over inference, and show much less of the latter:
#   1. Every evidence-type item first — raw "commercial" CSV records, plus
#      any evidence-type commercial_opportunity claims, PLUS real
#      population/epidemiology evidence claims pulled in from
#      scientific_thesis (PubMed-backed facts about mutation rate, incidence,
#      prevalence, etc.) — a different section's claims, but genuinely about
#      this card's own topic, and real evidence our databases produced,
#      which is exactly what was asked for: more database-backed evidence
#      on this specific card, not invented content.
#   2. Boilerplate "Deterministic market model parameter assumption for X."
#      claims (financials.py's internal bookkeeping of which inputs it used —
#      not an analytical statement) are dropped outright, never counted as
#      displayed inference.
#   3. Of what's left, only the single most substantive inference claim is
#      kept (the longest one — the generic one-line assumption claims are a
#      handful of words; the real funnel/peak-sales estimate is a full
#      sentence, so length is a simple, reliable way to prefer it).
# ---------------------------------------------------------------------------
_MAX_INFERENCE_ITEMS = 1
_BOILERPLATE_ASSUMPTION = re.compile(r"^deterministic market model parameter assumption for", re.I)
_POPULATION_KEYWORDS = (
    "prevalence", "incidence", "patient", "population", "mutation rate", "affect",
    "frequency", "epidemiolog", "diagnosed", "cases per",
)


def build_patient_population_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    commercial_claims = claims_by_section.get("commercial_opportunity", [])
    population_evidence = [
        c for c in claims_by_section.get("scientific_thesis", [])
        if c.type == "evidence" and any(k in c.claim.lower() for k in _POPULATION_KEYWORDS)
    ]

    claim_items = [{"kind": "claim", **c.to_dict()} for c in commercial_claims + population_evidence]
    record_items = [{"kind": "record", **r} for r in sources.get("commercial", [])]

    non_inference = record_items + [i for i in claim_items if i.get("type") != "inference"]

    inference_items = [
        i for i in claim_items
        if i.get("type") == "inference" and not _BOILERPLATE_ASSUMPTION.match(i.get("claim") or "")
    ]
    inference_items.sort(key=lambda i: len(i.get("claim") or ""), reverse=True)
    inference = inference_items[:_MAX_INFERENCE_ITEMS]

    return {"view": "list", "items": non_inference + inference}


# ---------------------------------------------------------------------------
# Card 5 — Competitive landscape: Drug / Clinical Stage / Target / Modality /
# Lead Sponsor / MoA / Source.
# Drug, Clinical Stage, MoA come from the Open Targets analog list (Clinical
# Stage was already fetched but not shown before). Target is the report's own
# query mechanism — every analog on this card is, by definition, an analog on
# that one target, so it's accurate per row, not a per-drug fact anyone had
# to look up. Lead Sponsor is a cross-reference against the openFDA
# (regulatory) records already fetched for those same drug names (header
# relabeled from "Labeler" per the team's decision — same underlying data).
# Source links to that same openFDA record. Modality has no source anywhere
# in the pipeline (checked every fetcher) — "Not available", not a guess.
# The card's own competitive_landscape claims are a different, complementary
# narrative, so they're kept below (show_claims_below=True).
# ---------------------------------------------------------------------------
def build_competitive_landscape_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    reg_by_name = {
        (r.get("drug_name") or "").strip().lower(): r
        for r in sources.get("regulatory", [])
        if r.get("drug_name")
    }
    rows = []
    for d in analogs:
        name = d.get("name") or NA
        reg = reg_by_name.get((d.get("name") or "").strip().lower())
        lead_sponsor = reg.get("sponsor") if reg and reg.get("sponsor") else NA
        source_cell = _cell("openFDA Drugs@FDA", reg.get("url")) if reg and reg.get("url") else NA
        rows.append({
            "Drug": name,
            "Clinical Stage": d.get("stage") or NA,
            "Target": mechanism or NA,
            "Modality": NA,
            "Lead Sponsor": lead_sponsor,
            "MoA": d.get("mechanism") or NA,
            "Source": source_cell,
        })
    return {
        "view": "table",
        "columns": ["Drug", "Clinical Stage", "Target", "Modality", "Lead Sponsor", "MoA", "Source"],
        "rows": rows,
        "show_claims_below": True,
    }


# ---------------------------------------------------------------------------
# Card 6 — Pipeline & drugs in development: two separate blocks, Drug then
# Trial, rather than one merged "Drug / Trial" column — the drug list comes
# from the same Open Targets analogs card 5 uses (real same-mechanism drug
# names, not text parsed out of a trial title); the trial list is the raw
# ClinicalTrials.gov registry entries. "Threat" (no scoring source anywhere)
# and "Indication" (redundant — the whole report is already scoped to one
# indication) stay dropped per the team's earlier decision.
# ---------------------------------------------------------------------------
def build_pipeline_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    # Status/Sponsor for a DRUG (not a trial) come from the same openFDA
    # cross-reference card 5 uses for Lead Sponsor — openFDA has no generic
    # "status" field, so Status is derived from whether it has a recorded
    # approval at all, never invented.
    reg_by_name = {
        (r.get("drug_name") or "").strip().lower(): r
        for r in sources.get("regulatory", [])
        if r.get("drug_name")
    }
    drug_rows = []
    for d in analogs:
        name = d.get("name") or ""
        reg = reg_by_name.get(name.strip().lower())
        first_approval = reg.get("first_approval_date") if reg else None
        if first_approval and len(first_approval) == 8:  # openFDA's raw YYYYMMDD
            first_approval = f"{first_approval[:4]}-{first_approval[4:6]}-{first_approval[6:]}"
        drug_rows.append({
            "Drug": name or NA,
            "Clinical Stage": d.get("stage") or NA,
            "MoA": d.get("mechanism") or NA,
            "Status": f"Approved ({first_approval})" if first_approval else NA,
            "Sponsor": (reg.get("sponsor") if reg else None) or NA,
        })

    trial_rows = [
        {
            "NCT ID": _cell(r.get("nctId") or NA, r.get("url")),
            "Trial": r.get("briefTitle") or NA,
            "Status": r.get("overallStatus") or NA,
            "Sponsor": r.get("leadSponsor") or NA,
        }
        for r in sources.get("clinicaltrials", [])
    ]
    return {
        "view": "composite",
        "sections": [
            {
                "kind": "table", "title": "Drug",
                "columns": ["Drug", "Clinical Stage", "MoA", "Status", "Sponsor"],
                "rows": drug_rows,
            },
            {"kind": "table", "title": "Trial", "columns": ["NCT ID", "Trial", "Status", "Sponsor"], "rows": trial_rows},
        ],
    }


# ---------------------------------------------------------------------------
# Card 7 — Patent & IP review: Lens.org fields plus Drug / Route of
# administration / Expiration date. Drug is the patent's own matched_term
# (the name it was searched for). Route comes from a cross-reference against
# the same openFDA (regulatory) records card 5 uses for Lead Sponsor — Lens
# has no route data, openFDA does (now captured, see fetch_regulatory.py).
# Expiration date: checked live against Lens's API — its legal_status object
# carries only a filing-date calculation log and patent_status (PENDING/
# GRANTED/etc.), no expiration/term field at all, so this is honestly "Not
# available" rather than a guessed filing-date-plus-20-years estimate (patent
# term extensions/adjustments make that kind of guess unreliable anyway).
# ---------------------------------------------------------------------------
def build_patent_ip_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    reg_by_name = {
        (r.get("drug_name") or "").strip().lower(): r
        for r in sources.get("regulatory", [])
        if r.get("drug_name")
    }
    rows = []
    for r in sources.get("patents", []):
        reg = reg_by_name.get((r.get("matched_term") or "").strip().lower())
        routes = reg.get("routes") if reg else None
        rows.append({
            "Drug": r.get("matched_term") or NA,
            "Jurisdiction": r.get("jurisdiction") or NA,
            "Doc #": _cell(r.get("doc_number"), r.get("url")),
            "Applicants": ", ".join(r.get("applicants") or []) or NA,
            "Status": r.get("patent_status") or NA,
            "Route of Administration": ", ".join(routes) if routes else NA,
            "Expiration Date": NA,
        })
    return {
        "view": "table",
        "columns": [
            "Drug", "Jurisdiction", "Doc #", "Applicants", "Status",
            "Route of Administration", "Expiration Date",
        ],
        "rows": rows,
        "show_claims_below": False,
    }


# ---------------------------------------------------------------------------
# Card 8 — Clinical trial design intelligence: raw ClinicalTrials.gov facts,
# tabulated, with a Drug column between NCT ID and Phase — the report's own
# query mechanism (the intervention these trials were actually searched
# for), same accurate-by-construction pattern as card 5's Target column. The
# card's own clinical_development_plan claims (proposed trial design, PTRS,
# biomarker strategy) are a different, complementary narrative built from
# these facts, not a duplicate of them — kept below the table.
# ---------------------------------------------------------------------------
def build_trial_design_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    rows = []
    for r in sources.get("clinicaltrials", []):
        enrollment = r.get("enrollmentCount")
        rows.append({
            "NCT ID": _cell(r.get("nctId") or NA, r.get("url")),
            "Drug": mechanism or NA,
            "Phase": ", ".join(r.get("phases") or []) or NA,
            "Status": r.get("overallStatus") or NA,
            "Enrollment": enrollment if enrollment is not None else NA,
            "Sponsor": r.get("leadSponsor") or NA,
            "Start Date": r.get("startDate") or NA,
        })
    return {
        "view": "table",
        "columns": ["NCT ID", "Drug", "Phase", "Status", "Enrollment", "Sponsor", "Start Date"],
        "rows": rows,
        "show_claims_below": True,
    }


# ---------------------------------------------------------------------------
# New card 9 — Risk & regulatory diligence: merges the old "Red flag monitor"
# + "Green flag monitor" cards, plus diligence_questions (which, after the
# verdict panel was removed from the page two rounds ago, had stopped being
# rendered anywhere on the site at all — this restores them). Four stacked
# sections in one "composite" card (a new view type app.js renders by
# iterating `sections`, each already shaped so the existing renderItem/
# renderTable helpers handle it unchanged):
#   1. Recommendation — a short, code-templated sentence, deterministic from
#      this card's own counts (flagged risks / regulatory designations found).
#      Distinct from the page's main Invest/Conditional/Do Not Invest verdict
#      (per the team's decision) — this is a diligence-focused note, not a
#      second investment verdict.
#   2. Precaution — the old red_flags content (key_risks + critical_unknowns
#      claims), relabeled.
#   3. Green flags table — the old green_flags table, unchanged.
#   4. Diligence questions — the diligence_questions claims the chair agent
#      already produces every run.
# ---------------------------------------------------------------------------
def _risk_recommendation_text(key_risks: list[Claim], green_rows: list[dict]) -> str:
    high_risk = sum(
        1 for c in key_risks
        if (c.risk_score or 0) >= 0.3 or (c.probability or 0) >= 0.5
    )
    designation_count = sum(len(r.get("designations") or []) for r in green_rows)
    risk_part = (
        f"{high_risk} high-severity risk(s) flagged" if high_risk
        else (f"{len(key_risks)} risk(s) flagged, none high-severity" if key_risks else "no risks flagged")
    )
    green_part = (
        f"{designation_count} regulatory designation(s) found on same-mechanism drugs" if designation_count
        else "no regulatory designations found on same-mechanism drugs"
    )
    return f"{risk_part.capitalize()}; {green_part}."


def build_risk_diligence_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    key_risks = claims_by_section.get("key_risks", [])
    critical_unknowns = claims_by_section.get("critical_unknowns", [])
    diligence = claims_by_section.get("diligence_questions", [])

    green_rows = []
    for r in sources.get("regulatory", []):
        brands = r.get("brand_names") or []
        designations = r.get("designations") or []
        green_rows.append({
            "Drug": r.get("drug_name") or NA,
            "Sponsor": r.get("sponsor") or NA,
            "Brand": ", ".join(brands) or NA,
            "Designations": ", ".join(designations) or NA,
            "First Approval": _cell(r.get("first_approval_date") or NA, r.get("url")),
        })

    precaution_items = [{"kind": "claim", **c.to_dict()} for c in key_risks + critical_unknowns]
    diligence_items = [{"kind": "claim", **c.to_dict()} for c in diligence]

    return {
        "view": "composite",
        "sections": [
            {"kind": "text", "title": "Recommendation", "text": _risk_recommendation_text(key_risks, green_rows)},
            {"kind": "list", "title": "Precaution", "items": precaution_items},
            {
                "kind": "table", "title": "Green Flags",
                "columns": ["Drug", "Sponsor", "Brand", "Designations", "First Approval"],
                "rows": green_rows,
            },
            {"kind": "list", "title": "Diligence Questions", "items": diligence_items},
        ],
    }


# ---------------------------------------------------------------------------
# New card 10 — was "Regulatory timeline": a visual stage-progress row per
# analog drug (Phase 1 -> Phase 2 -> Phase 3 -> Approved), not a table.
# The furthest-reached stage is derived deterministically: the highest trial
# phase seen for that drug's name in sources["clinicaltrials"], or "Approved"
# if it has a first_approval_date in sources["regulatory"] — never a
# fabricated date, only "which stages this drug's public record has reached".
# app.js renders this as a row of dots/segments, not a Chart.js chart (no
# charting library has a native stage-stepper, and we don't have reliable
# per-stage dates to plot a real timeline axis).
# ---------------------------------------------------------------------------
_STAGES = ["Phase 1", "Phase 2", "Phase 3", "Approved"]


def _phase_rank(phase_text: str) -> int:
    """Highest numbered phase mentioned in a ClinicalTrials.gov phases string
    like "PHASE2, PHASE3" -> 2 (0-indexed into _STAGES), or -1 if none."""
    nums = [int(n) for n in re.findall(r"PHASE(\d)", (phase_text or "").upper())]
    return max(nums) - 1 if nums else -1


def _opentargets_stage_rank(stage: str | None) -> int:
    """Open Targets' own maxClinicalStage per drug, e.g. "PHASE_2"/"APPROVAL"
    — the primary, most reliable signal (one value per drug, straight from
    Open Targets), vs. the ClinicalTrials.gov title-text match below, which
    is only a fallback for drugs Open Targets didn't already rank."""
    stage = (stage or "").upper()
    if stage == "APPROVAL":
        return len(_STAGES) - 1
    m = re.match(r"PHASE_(\d)", stage)
    return int(m.group(1)) - 1 if m else -1


def build_regulatory_timeline_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    reg_by_name = {
        (r.get("drug_name") or "").strip().lower(): r
        for r in sources.get("regulatory", [])
        if r.get("drug_name")
    }

    # Fallback: highest phase rank observed per drug name from CT.gov records
    # whose briefTitle mentions that drug — the only link available between a
    # named analog and a specific trial record (no structured drug-name field
    # exists on ClinicalTrials.gov records, confirmed by the fetcher audit).
    # Only used for a drug Open Targets itself has no stage ranking for.
    best_rank_by_name: dict[str, int] = {}
    for r in sources.get("clinicaltrials", []):
        title = (r.get("briefTitle") or "").lower()
        rank = _phase_rank(", ".join(r.get("phases") or []))
        if rank < 0:
            continue
        for name in reg_by_name.keys() | {d.get("name", "").strip().lower() for d in analogs}:
            if name and name in title:
                best_rank_by_name[name] = max(best_rank_by_name.get(name, -1), rank)

    rows = []
    seen_names: set[str] = set()
    for d in analogs:
        name = (d.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen_names:
            continue
        seen_names.add(key)

        approved = key in reg_by_name and bool(reg_by_name[key].get("first_approval_date"))
        reached = max(
            len(_STAGES) - 1 if approved else -1,
            _opentargets_stage_rank(d.get("stage")),
            best_rank_by_name.get(key, -1),
        )
        rows.append({
            "drug": name,
            "stages": _STAGES,
            "reached_index": reached,  # -1 = nothing observed yet
        })

    return {"view": "timeline", "rows": rows}


VIEW_BUILDERS = {
    "unmet_need": build_unmet_need_view,
    "market_sentiment": build_market_sentiment_view,
    "scientific_evidence": build_scientific_evidence_view,
    "patient_population": build_patient_population_view,
    "competitive_landscape": build_competitive_landscape_view,
    "pipeline": build_pipeline_view,
    "patent_ip": build_patent_ip_view,
    "trial_design": build_trial_design_view,
    "risk_diligence": build_risk_diligence_view,
    "regulatory_timeline": build_regulatory_timeline_view,
}
