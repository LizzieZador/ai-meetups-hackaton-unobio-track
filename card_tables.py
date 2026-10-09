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


# ---------------------------------------------------------------------------
# Card 1 — Unmet need: bucket the card's OWN existing claims into 3 fixed
# rows by keyword match. This never writes new text — it only re-sorts real,
# already-generated claims. A bucket with nothing matching shows "Not
# available" rather than an invented sentence.
# ---------------------------------------------------------------------------
_SOC_KEYWORDS = ("standard of care", "current treatment", "current therapy", "first-line", "existing therapy", "approved therapy")
_GAP_KEYWORDS = ("unmet need", "gap", "limitation", "fail", "resistance", "inadequate", "poor outcome")
_TPP_KEYWORDS = ("target product profile", "differentiat", "profile", "positioning", "advantage over")


def build_unmet_need_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    pool = _claims(claims_by_section, "human_translation_thesis", "commercial_opportunity")

    def bucket(keywords: tuple[str, ...]) -> str:
        matches = [c for c in pool if any(k in c.claim.lower() for k in keywords)]
        if not matches:
            return NA
        return " ".join(c.claim.strip().rstrip(".") + "." for c in matches[:2])

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


_DOUGHNUT_PALETTE = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]


def build_market_sentiment_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    records = sources.get("market_news", [])
    if not records:
        return {"view": "graph", "chart": None}

    counts = Counter(_clean_filer_name(r.get("company")) for r in records)
    top = counts.most_common(6)
    total = sum(n for _, n in top) or 1

    # The percentage is baked into the legend label itself (not a tooltip
    # callback) because this chart config is plain JSON handed to Chart.js —
    # there's no live JS function to compute it client-side.
    labels = [
        f"{name} — {n} filing{'s' if n != 1 else ''} ({round(n / total * 100)}%)"
        for name, n in top
    ]

    chart = {
        "type": "doughnut",
        "data": {
            "labels": labels,
            "datasets": [{
                "data": [n for _, n in top],
                "backgroundColor": _DOUGHNUT_PALETTE[: len(top)],
                "borderColor": "#ffffff",
                "borderWidth": 2,
                "hoverOffset": 6,
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "cutout": "62%",
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
    return {"view": "graph", "chart": chart}


# ---------------------------------------------------------------------------
# Card 3 — Scientific evidence: the claims themselves, tabulated.
# ---------------------------------------------------------------------------
def build_scientific_evidence_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    rows = []
    for c in claims_by_section.get("scientific_thesis", []):
        if c.type == "evidence":
            source_cell = _cell(c.source, c.url)
            date_cell = c.date_accessed or NA
        else:
            source_cell = f"Inference (confidence: {c.confidence})" if c.confidence else "Inference"
            date_cell = NA
        rows.append({"Finding": c.claim, "Source": source_cell, "Date": date_cell})
    return {"view": "table", "columns": ["Finding", "Source", "Date"], "rows": rows, "show_claims_below": False}


# ---------------------------------------------------------------------------
# Card 5 — Competitive landscape: Drug / Labeler / MoA.
# Drug + MoA come from the Open Targets analog list; Labeler is a
# cross-reference against the openFDA (regulatory) records already fetched
# for those same drug names — a real join of already-fetched data, not a new
# fact. The card's own competitive_landscape claims are a different,
# complementary narrative, so they're kept below (show_claims_below=True).
# ---------------------------------------------------------------------------
def build_competitive_landscape_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    reg_by_name = {
        (r.get("drug_name") or "").strip().lower(): r
        for r in sources.get("regulatory", [])
        if r.get("drug_name")
    }
    rows = []
    for d in analogs:
        name = d.get("name") or NA
        reg = reg_by_name.get((d.get("name") or "").strip().lower())
        labeler = reg.get("sponsor") if reg and reg.get("sponsor") else NA
        rows.append({"Drug": name, "Labeler": labeler, "MoA": d.get("mechanism") or NA})
    return {"view": "table", "columns": ["Drug", "Labeler", "MoA"], "rows": rows, "show_claims_below": True}


# ---------------------------------------------------------------------------
# Card 6 — Pipeline & drugs in development: Drug / Threat / Sponsor / Indication.
# "Threat" has no scoring source anywhere in the pipeline (confirmed by audit)
# — every row shows "Not available" rather than an invented score.
# ---------------------------------------------------------------------------
def build_pipeline_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    rows = []
    for r in sources.get("clinicaltrials", []):
        rows.append({
            "Drug / Trial": _cell(r.get("briefTitle"), r.get("url")),
            "Threat": NA,
            "Sponsor": r.get("leadSponsor") or NA,
            "Indication": r.get("indication") or NA,
        })
    return {
        "view": "table",
        "columns": ["Drug / Trial", "Threat", "Sponsor", "Indication"],
        "rows": rows,
        "show_claims_below": False,
    }


# ---------------------------------------------------------------------------
# Card 7 — Patent & IP review: direct pass-through of Lens.org fields.
# ---------------------------------------------------------------------------
def build_patent_ip_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    rows = []
    for r in sources.get("patents", []):
        rows.append({
            "Jurisdiction": r.get("jurisdiction") or NA,
            "Doc #": _cell(r.get("doc_number"), r.get("url")),
            "Applicants": ", ".join(r.get("applicants") or []) or NA,
            "Status": r.get("patent_status") or NA,
        })
    return {
        "view": "table",
        "columns": ["Jurisdiction", "Doc #", "Applicants", "Status"],
        "rows": rows,
        "show_claims_below": False,
    }


# ---------------------------------------------------------------------------
# Card 8 — Clinical trial design intelligence: raw ClinicalTrials.gov facts,
# tabulated. The card's own clinical_development_plan claims (proposed trial
# design, PTRS, biomarker strategy) are a different, complementary narrative
# built from those facts, not a duplicate of them — kept below the table.
# ---------------------------------------------------------------------------
def build_trial_design_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    rows = []
    for r in sources.get("clinicaltrials", []):
        enrollment = r.get("enrollmentCount")
        rows.append({
            "NCT ID": _cell(r.get("nctId") or NA, r.get("url")),
            "Phase": ", ".join(r.get("phases") or []) or NA,
            "Status": r.get("overallStatus") or NA,
            "Enrollment": enrollment if enrollment is not None else NA,
            "Sponsor": r.get("leadSponsor") or NA,
            "Start Date": r.get("startDate") or NA,
        })
    return {
        "view": "table",
        "columns": ["NCT ID", "Phase", "Status", "Enrollment", "Sponsor", "Start Date"],
        "rows": rows,
        "show_claims_below": True,
    }


# ---------------------------------------------------------------------------
# Card 10 — Red flag monitor: terminated/stopped trials, tabulated from the
# same raw ClinicalTrials.gov records as card 8 (filtered to whyStopped).
# The card's own key_risks/critical_unknowns claims are a different,
# complementary narrative — kept below the table.
# ---------------------------------------------------------------------------
def build_red_flags_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    rows = []
    for r in sources.get("clinicaltrials", []):
        why = r.get("whyStopped")
        if not why:
            continue
        rows.append({
            "Trial": _cell(r.get("briefTitle") or r.get("nctId") or NA, r.get("url")),
            "Status": r.get("overallStatus") or NA,
            "Why Stopped": why,
        })
    return {
        "view": "table",
        "columns": ["Trial", "Status", "Why Stopped"],
        "rows": rows,
        "show_claims_below": True,
    }


# ---------------------------------------------------------------------------
# Card 11 — Green flag & regulatory advantage monitor: direct pass-through
# of openFDA fields.
# ---------------------------------------------------------------------------
def build_green_flags_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    rows = []
    for r in sources.get("regulatory", []):
        brands = r.get("brand_names") or []
        designations = r.get("designations") or []
        rows.append({
            "Drug": r.get("drug_name") or NA,
            "Sponsor": r.get("sponsor") or NA,
            "Brand": ", ".join(brands) or NA,
            "Designations": ", ".join(designations) or NA,
            "First Approval": _cell(r.get("first_approval_date") or NA, r.get("url")),
        })
    return {
        "view": "table",
        "columns": ["Drug", "Sponsor", "Brand", "Designations", "First Approval"],
        "rows": rows,
        "show_claims_below": False,
    }


# ---------------------------------------------------------------------------
# Card 12 — Regulatory timeline: same openFDA records as card 11, sorted by
# first approval date so it reads as an actual timeline.
# ---------------------------------------------------------------------------
def build_regulatory_timeline_view(claims_by_section: dict, sources: dict, analogs: list[dict]) -> dict:
    records = [r for r in sources.get("regulatory", []) if r.get("first_approval_date")]
    records.sort(key=lambda r: r["first_approval_date"])
    rows = [
        {
            "Drug": r.get("drug_name") or NA,
            "First Approval": r.get("first_approval_date") or NA,
            "Designations": ", ".join(r.get("designations") or []) or NA,
        }
        for r in records
    ]
    return {
        "view": "table",
        "columns": ["Drug", "First Approval", "Designations"],
        "rows": rows,
        "show_claims_below": False,
    }


VIEW_BUILDERS = {
    "unmet_need": build_unmet_need_view,
    "market_sentiment": build_market_sentiment_view,
    "scientific_evidence": build_scientific_evidence_view,
    "competitive_landscape": build_competitive_landscape_view,
    "pipeline": build_pipeline_view,
    "patent_ip": build_patent_ip_view,
    "trial_design": build_trial_design_view,
    "red_flags": build_red_flags_view,
    "green_flags": build_green_flags_view,
    "regulatory_timeline": build_regulatory_timeline_view,
}

# Cards 4 (patient_population) and 9 (manufacturing) deliberately have no
# entry here — see their "data_gap_note" in cards.py.
