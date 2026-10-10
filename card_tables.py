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
import datetime
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


def _format_fda_date(raw: str | None) -> str | None:
    """openFDA dates come back as raw YYYYMMDD — "2021-05-28" reads better."""
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _orange_book_by_appl(sources: dict) -> dict[str, dict]:
    return {(r.get("application_number") or ""): r for r in sources.get("orange_book", [])}


def _sponsor_display(reg: dict | None, orange_book: dict[str, dict] | None = None) -> str:
    """Application holder's name. The FDA Orange Book's Applicant_Full_Name
    ("BRISTOL MYERS SQUIBB CO") wins when present — openFDA's sponsor_name is
    often abbreviated ("BRISTOL"). Without it: the openFDA holder, plus the
    label's manufacturer when that's a different company (e.g. after an
    acquisition). All verbatim FDA fields."""
    if not reg:
        return NA
    ob = (orange_book or {}).get(reg.get("application_number") or "")
    sponsor = ((ob or {}).get("applicant_full_name") or reg.get("sponsor") or "").strip()
    labeler = (reg.get("labeler") or "").strip()
    if not sponsor:
        return labeler or NA
    if labeler and labeler.split()[0].lower() not in sponsor.lower():
        return f"{sponsor} (label: {labeler})"
    return sponsor


def _short(text: str, limit: int = 130) -> str:
    """Trim to the point, never mid-word/mid-sentence: a table cell needs
    the fact, not the full sentence it came wrapped in."""
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

    def bucket(keywords: tuple[str, ...]):
        """Joined claim text for this Point, linked to the first underlying
        evidence claim's source (so this row — like every other table's —
        can be cross-referenced against the card's Sources list)."""
        matches = [c for c in pool if id(c) not in used and any(k in c.claim.lower() for k in keywords)]
        if not matches:
            return NA
        selected = matches[:2]
        used.update(id(c) for c in selected)
        text = " ".join(c.claim.strip().rstrip(".") + "." for c in selected)
        url = next((c.url for c in selected if c.type == "evidence" and c.url), None)
        return _cell(text, url)

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


# Series colours are NOT chosen here any more: they live as --series-1..6 in
# style.css, with a separate validated row per theme, and the front-end paints
# both the slices and the table swatches from those. This module only decides
# WHICH slot each company gets (1-based, matching the CSS token names), so the
# ring and its table rows always agree. SERIES_SLOTS is the slot count the CSS
# defines — more companies than that would need new tokens, so the pie stops here.
SERIES_SLOTS = 6


def build_market_sentiment_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    records = sources.get("market_news", [])
    if not records:
        return {"view": "graph", "chart": None}

    by_name: dict[str, list[dict]] = {}
    for r in records:
        by_name.setdefault(_clean_filer_name(r.get("company")), []).append(r)

    counts = Counter({name: len(recs) for name, recs in by_name.items()})
    top = counts.most_common(SERIES_SLOTS)
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
                # Slice fill and the ring between slices are set by the
                # front-end from the theme's tokens (a baked-in white border
                # drew hard white rings on a dark card).
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
    # top 6 the pie slices show, each tagged with the same series slot as its pie
    # slice (so a reader can connect a row back to the chart at a glance) —
    # a company past the chart's top slots gets no slot, it simply isn't a
    # slice. Grouped by company (same colour together) then newest-first.
    slot_by_name = {name: i + 1 for i, (name, _) in enumerate(top)}
    all_rows = []
    for name, _n in counts.most_common():  # every company, most filings first — matches the pie's own ranking
        for r in sorted(by_name[name], key=lambda r: r.get("file_date") or "", reverse=True):
            all_rows.append({
                "Company": name,
                "Form": r.get("form") or NA,
                "Filed": r.get("file_date") or NA,
                "Matched Term": r.get("matched_term") or NA,
                "Source": _cell(r.get("source"), r.get("url")),
                "_slot": slot_by_name.get(name),
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

    # Financials: the deterministic rNPV scenarios (financials.py, stamped onto
    # a "recommendation"-section claim by the chair) and the capital needed to
    # reach the next milestone — previously shown on no card at all. Grouped
    # under one "Financials" label and exempt from the inference cap above:
    # these are the investment model's own outputs, not filler assumptions.
    financial_items = [
        {"kind": "claim", **c.to_dict(), "group": "Financials"}
        for c in claims_by_section.get("recommendation", [])
        if (c.claim or "").lower().startswith("rnpv")
    ] + [
        {"kind": "claim", **c.to_dict(), "group": "Financials"}
        for c in claims_by_section.get("capital_required", [])
    ]

    return {"view": "list", "items": non_inference + inference + financial_items}


_UNCURATED = "Not curated in Open Targets"


def _analog_moa(d: dict) -> str:
    if d.get("mechanism"):
        return d["mechanism"]
    if d.get("origin") == "trial_registry":
        trials = ", ".join(d.get("nct_ids") or [])
        return f"{_UNCURATED} — trial intervention ({trials})" if trials else _UNCURATED
    return NA


def _analog_target(d: dict) -> str:
    """The drug's own curated targets (Open Targets drug profile). A drug with
    no curated mechanism yet says so — the query string is never reused as a
    stand-in (that labelled pan-RAS salirasib as "KRAS G12C")."""
    if d.get("targets"):
        return ", ".join(d["targets"])
    if "origin" in d:
        return _UNCURATED
    return NA


# ---------------------------------------------------------------------------
# Card 5 — Competitive landscape: Drug / Clinical Stage / Target / Modality /
# Lead Sponsor / MoA / Source.
# Rows: Open Targets' target-level analogs, plus registry competitors that
# passed sources/fetch_drug_profiles.py's verification (exact Open Targets
# name match; curated on this target, or no mechanism curated yet). Target,
# Modality (drugType) and stage come from each drug's own Open Targets
# profile. Lead Sponsor: openFDA cross-reference, full name from the Orange
# Book when listed. Source: the openFDA record, else the Open Targets drug page.
# The card's own competitive_landscape claims are a different, complementary
# narrative, so they're kept below (show_claims_below=True).
# ---------------------------------------------------------------------------
def build_competitive_landscape_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    reg_by_name = {
        (r.get("drug_name") or "").strip().lower(): r
        for r in sources.get("regulatory", [])
        if r.get("drug_name")
    }
    orange_book = _orange_book_by_appl(sources)
    rows = []
    for d in analogs:
        name = d.get("name") or NA
        reg = reg_by_name.get((d.get("name") or "").strip().lower())
        if reg and reg.get("url"):
            source_cell = _cell("openFDA Drugs@FDA", reg["url"])
        elif d.get("profile_url"):
            source_cell = _cell("Open Targets drug profile", d["profile_url"])
        else:
            source_cell = NA
        rows.append({
            "Drug": name,
            "Clinical Stage": d.get("stage") or NA,
            "Target": _analog_target(d),
            "Modality": d.get("drug_type") or NA,
            "Lead Sponsor": _sponsor_display(reg, orange_book),
            "MoA": _analog_moa(d),
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
    orange_book = _orange_book_by_appl(sources)
    drug_rows = []
    for d in analogs:
        name = d.get("name") or ""
        reg = reg_by_name.get(name.strip().lower())
        first_approval = _format_fda_date(reg.get("first_approval_date") if reg else None)
        link = (reg or {}).get("url") or d.get("profile_url")
        drug_rows.append({
            "Drug": _cell(name, link) if link else (name or NA),
            "Clinical Stage": d.get("stage") or NA,
            "MoA": _analog_moa(d),
            "Status": (
                f"Approved ({first_approval})" if first_approval
                # Open Targets says approved but openFDA has no US record: an ex-US approval
                else "Approved outside US (no FDA record)" if (d.get("stage") or "").upper() == "APPROVAL"
                else NA
            ),
            "Sponsor": _sponsor_display(reg, orange_book),
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


# Generic FDA definitions of each Orange Book exclusivity code family (true of
# the code itself, not a claim about any drug) — matched by prefix, since the
# file carries numbered variants like "ODE-352", "ODE*", "I-962".
_EXCLUSIVITY_EXPLANATIONS = [
    ("NCE", "New Chemical Entity: 5 years from approval in which FDA cannot accept a generic application (4 if it challenges a patent)."),
    ("ODE", "Orphan Drug Exclusivity: 7 years in which FDA cannot approve the same drug for the same orphan indication."),
    ("NPP", "New Patient Population: 3 years for a newly approved patient population."),
    ("NP", "New Product: 3 years for a change backed by new clinical studies (e.g. a new formulation)."),
    ("I-", "New Indication: 3 years for a newly approved use backed by new clinical studies."),
    ("D-", "New Dosing Schedule: 3 years for a newly approved dosing regimen."),
    ("M-", "Miscellaneous: 3 years for another FDA-recognised change backed by new clinical studies."),
    ("PED", "Pediatric: 6 months added to existing patents and exclusivities for completed pediatric studies."),
    ("GAIN", "Qualified Infectious Disease Product: 5 additional years of exclusivity."),
    ("CGT", "Competitive Generic Therapy: 180 days for the first approved generic."),
]


def _exclusivity_meaning(code: str) -> str:
    code = (code or "").upper()
    return next((text for prefix, text in _EXCLUSIVITY_EXPLANATIONS if code.startswith(prefix)), NA)


def _term_status(expires: str | None) -> str:
    """Active/Expired relative to today — the number an investor actually needs."""
    if not expires:
        return NA
    try:
        end = datetime.date.fromisoformat(expires)
    except ValueError:
        return NA
    days = (end - datetime.date.today()).days
    if days < 0:
        return "Expired"
    return f"Active ({days / 365.25:.1f} yrs left)"


def _patent_covers(p: dict) -> str:
    parts = []
    if p.get("drug_substance"):
        parts.append("Compound")
    if p.get("drug_product"):
        parts.append("Formulation")
    if p.get("use_code"):
        parts.append(f"Method of use ({p['use_code']})")
    return ", ".join(parts) or NA


# ---------------------------------------------------------------------------
# Card 7 — Patent & IP review, three stacked tables:
#   1. US patents from the FDA Orange Book — the patents an NDA holder listed
#      for the approved drug, each with its expiration date and Active/Expired
#      status (Lens.org has no term field at all, so these are the only real
#      expiration dates available; never a filing-date-plus-20-years guess).
#   2. Regulatory exclusivities from the Orange Book (NCE, orphan, ...) — the
#      other half of when generics can enter.
#   3. Lens.org patent filings — worldwide, mostly pending applications. Route
#      comes from the openFDA cross-reference. Expiration is filled only when
#      the filing's number is itself an Orange Book-listed US patent.
# ---------------------------------------------------------------------------
def build_patent_ip_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    reg_by_name = {
        (r.get("drug_name") or "").strip().lower(): r
        for r in sources.get("regulatory", [])
        if r.get("drug_name")
    }
    patent_rows, exclusivity_rows = [], []
    ob_expiry: dict[str, str] = {}
    for ob in sources.get("orange_book", []):
        drug = (ob.get("drug_name") or NA).title()
        if ob.get("trade_name"):
            drug = f"{drug} ({ob['trade_name'].title()})"
        holder = ob.get("applicant_full_name") or NA
        for p in ob.get("patents", []):
            ob_expiry[p["patent_no"]] = p.get("expires")
            patent_rows.append({
                "Drug": drug,
                "US Patent #": _cell(p["patent_no"], ob.get("url")),
                "Covers": _patent_covers(p),
                "Expiration Date": p.get("expires") or NA,
                "Status": _term_status(p.get("expires")),
                "Holder": holder,
            })
        for e in ob.get("exclusivities", []):
            exclusivity_rows.append({
                "Drug": drug,
                "Exclusivity": _cell(e.get("code") or NA, ob.get("url")),
                "What It Means": _exclusivity_meaning(e.get("code")),
                "Expires": e.get("expires") or NA,
                "Status": _term_status(e.get("expires")),
            })

    lens_rows = []
    for r in sources.get("patents", []):
        reg = reg_by_name.get((r.get("matched_term") or "").strip().lower())
        routes = reg.get("routes") if reg else None
        digits = re.sub(r"\D", "", r.get("doc_number") or "")
        lens_rows.append({
            "Drug": r.get("matched_term") or NA,
            "Jurisdiction": r.get("jurisdiction") or NA,
            "Doc #": _cell(r.get("doc_number"), r.get("url")),
            "Applicants": ", ".join(r.get("applicants") or []) or NA,
            "Status": r.get("patent_status") or NA,
            "Route of Administration": ", ".join(routes) if routes else NA,
            "Expiration Date": (ob_expiry.get(digits) if r.get("jurisdiction") == "US" else None) or NA,
        })

    return {
        "view": "composite",
        "sections": [
            {
                "kind": "table", "title": "US Patents — FDA Orange Book",
                "columns": ["Drug", "US Patent #", "Covers", "Expiration Date", "Status", "Holder"],
                "rows": patent_rows,
            },
            {
                "kind": "table", "title": "Regulatory Exclusivity — FDA Orange Book",
                "columns": ["Drug", "Exclusivity", "What It Means", "Expires", "Status"],
                "rows": exclusivity_rows,
            },
            {
                "kind": "table", "title": "Patent Filings — Lens.org",
                "columns": [
                    "Drug", "Jurisdiction", "Doc #", "Applicants", "Status",
                    "Route of Administration", "Expiration Date",
                ],
                "rows": lens_rows,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Card 8 — Clinical trial design intelligence: raw ClinicalTrials.gov facts,
# tabulated, with a Drug column between NCT ID and Phase — each trial's own
# registered drug/biologic interventions from ClinicalTrials.gov. The
# card's own clinical_development_plan claims (proposed trial design, PTRS,
# biomarker strategy) are a different, complementary narrative built from
# these facts, not a duplicate of them — kept below the table.
# ---------------------------------------------------------------------------
def build_trial_design_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    rows = []
    for r in sources.get("clinicaltrials", []):
        enrollment = r.get("enrollmentCount")
        # The registry's own intervention names (e.g. "Calderasib", "JDQ443") —
        # previously this column repeated the queried target ("KRAS G12C") on
        # every row, which is a target, not a drug. Records cached before the
        # fetcher captured interventions show "Not available" until refetched.
        rows.append({
            "NCT ID": _cell(r.get("nctId") or NA, r.get("url")),
            "Drug": ", ".join(r.get("interventions") or []) or NA,
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
def _top_risk_claim(key_risks: list[Claim]) -> Claim | None:
    """The single most severe flagged risk, by whatever structured risk
    metric is present (risk_score first, else probability+impact) — picking
    *a* claim to headline, never writing a new one."""
    scored = [c for c in key_risks if c.claim]
    if not scored:
        return None
    return max(scored, key=lambda c: (c.risk_score or 0) + (c.probability or 0) + (c.impact_pct or 0))


# What each FDA designation code actually means and why it matters to an
# investment decision — plain, factual, and generic (true of the designation
# itself, not a per-drug claim), so this is static reference text, never a
# fabricated fact about any specific drug. Confirmed live against openFDA
# that "Orphan" and "Priority Review" are what our own data actually
# produces today; Fast Track/Breakthrough/Accelerated Approval are kept as a
# defensive fallback in case openFDA's data later surfaces them — an
# unrecognized code still shows plainly rather than a made-up explanation.
_DESIGNATION_EXPLANATIONS = {
    "Orphan": "Orphan Drug Designation: granted for rare-disease therapies; gives the sponsor 7 years of US market exclusivity plus tax credits and fee waivers.",
    "Priority Review": "FDA committed to a 6-month review instead of the standard 10 — granted when the agency sees a meaningful improvement over existing treatment.",
    "Fast Track": "Fast Track Designation: more frequent FDA interaction and rolling review, meant to speed development for a serious unmet medical need.",
    "Breakthrough Therapy": "Breakthrough Therapy Designation: intensive FDA guidance plus rolling review, granted when early evidence shows substantial improvement over available therapy.",
    "Accelerated Approval": "Accelerated Approval: approved on a surrogate endpoint reasonably likely to predict clinical benefit, pending a confirmatory trial.",
}


def _top_green_row(green_rows: list[dict]) -> dict | None:
    with_designation = [r for r in green_rows if r.get("Designation") != NA]
    return with_designation[0] if with_designation else None


def _risk_summary_text(key_risks: list[Claim], green_rows: list[dict]) -> str:
    """The card's headline — counts of both flag types plus, when present,
    which specific risk and which specific drug/designation are the most
    important ones, so the client sees the two things that matter most
    before scrolling into the full Red Flags / Green Flags groups below."""
    high_risk = sum(
        1 for c in key_risks
        if (c.risk_score or 0) >= 0.3 or (c.probability or 0) >= 0.5
    )
    drugs_with_designation = {r["Drug"] for r in green_rows if r.get("Designation") != NA}

    risk_part = (
        f"{high_risk} high-severity risk(s) flagged" if high_risk
        else (f"{len(key_risks)} risk(s) flagged, none high-severity" if key_risks else "no risks flagged")
    )
    green_part = (
        f"{len(drugs_with_designation)} drug(s) with a regulatory designation" if drugs_with_designation
        else "no regulatory designations found on same-mechanism drugs"
    )
    lines = [f"{risk_part.capitalize()}; {green_part}."]

    top_risk = _top_risk_claim(key_risks)
    if top_risk:
        lines.append(f"Top red flag: {_short(top_risk.claim)}")

    top_green = _top_green_row(green_rows)
    if top_green:
        lines.append(f"Top green flag: {top_green['Drug']} — {top_green['Designation']}.")

    return " ".join(lines)


def build_risk_diligence_view(claims_by_section: dict, sources: dict, analogs: list[dict], mechanism: str = "") -> dict:
    key_risks = claims_by_section.get("key_risks", [])
    critical_unknowns = claims_by_section.get("critical_unknowns", [])
    diligence = claims_by_section.get("diligence_questions", [])

    # One row per designation, not per drug — cramming "Orphan, Priority
    # Review" into one cell left no room to say what either one actually
    # means. A drug with no designation at all still gets one row (so it
    # isn't silently missing), with Designation/What It Means as "Not
    # available" rather than skipped.
    green_rows = []
    orange_book = _orange_book_by_appl(sources)
    for r in sources.get("regulatory", []):
        drug = r.get("drug_name") or NA
        sponsor = _sponsor_display(r, orange_book)
        first_approval = _cell(_format_fda_date(r.get("first_approval_date")) or NA, r.get("url"))
        designations = r.get("designations") or []
        if not designations:
            green_rows.append({
                "Drug": drug, "Designation": NA, "What It Means": NA,
                "Sponsor": sponsor, "First Approval": first_approval,
            })
            continue
        for code in designations:
            green_rows.append({
                "Drug": drug,
                "Designation": code,
                "What It Means": _DESIGNATION_EXPLANATIONS.get(code, NA),
                "Sponsor": sponsor,
                "First Approval": first_approval,
            })

    red_flag_items = [{"kind": "claim", **c.to_dict()} for c in key_risks + critical_unknowns]
    diligence_items = [{"kind": "claim", **c.to_dict()} for c in diligence]

    # Order matters here: the headline summary (naming the top red flag and
    # top green flag) comes first, then the full groups — Red Flags / Green
    # Flags / Diligence Questions — in order of how decision-relevant they
    # are to a client scanning the card, general detail last.
    return {
        "view": "composite",
        "sections": [
            {"kind": "text", "title": "Summary", "text": _risk_summary_text(key_risks, green_rows)},
            {"kind": "list", "title": "Red Flags", "items": red_flag_items},
            {
                "kind": "table", "title": "Green Flags",
                "columns": ["Drug", "Designation", "What It Means", "Sponsor", "First Approval"],
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
    if stage == "PREAPPROVAL":  # filed for approval — Phase 3 is complete, not yet approved
        return len(_STAGES) - 2
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
