"""The site's 10 intelligence cards — the view model behind the card grid.

The claim taxonomy in schema.py follows the task brief's "Required Output"
list; the site presents that material as topic cards, and this module is the
single place that maps claim sections and fetched source records onto them.

Each card declares where its content comes from:
  claim_sections — schema.SECTION_TITLES keys whose claims land in this card
  raw_sources    — pipeline.collect_sources() keys whose fetched records land here

A card with neither has no connected source yet (we fetch nothing that answers
it — e.g. litigation records). The UI says exactly that instead of "No results
yet", so an unwired card is never mistaken for a run that found nothing.

`recommendation` claims deliberately map to no card: that's the one top-line
verdict rendered above the grid by the front-end. `diligence_questions`
claims land in the "risk_diligence" card below, alongside key_risks/
critical_unknowns and the green-flag regulatory table.
"""
from card_tables import VIEW_BUILDERS
from program_views import attach_program_sections
from schema import Claim

CARDS: list[dict] = [
    {
        "id": "unmet_need",
        "title": "Unmet need, standard of care & target product profile",
        "subtitle": "Guidelines, claims and EMR, KOL and patient research",
        "claim_sections": ["human_translation_thesis"],
        "raw_sources": ["commercial"],
    },
    {
        "id": "market_sentiment",
        "title": "Market sentiment & expert intelligence",
        "subtitle": "Company 8-K filing activity from SEC EDGAR",
        "claim_sections": [],
        "raw_sources": ["market_news"],
        "data_gap_note": "Filing counts only — no sentiment scoring, earnings-call, KOL or analyst transcript source is connected.",
    },
    {
        "id": "scientific_evidence",
        "title": "Scientific evidence: efficacy, safety & differentiation",
        "subtitle": "Data room, published studies, FDA and EMA resources",
        "claim_sections": ["scientific_thesis"],
        "raw_sources": ["pubmed", "biomarker_literature"],
    },
    {
        "id": "patient_population",
        "title": "Patient population, TAM & financials",
        "subtitle": "Medicare spending (CMS), epidemiology, market research, rNPV model and capital to next milestone",
        "claim_sections": ["commercial_opportunity", "capital_required"],
        "raw_sources": ["commercial", "medicare_spending"],
        "data_gap_note": (
            "Epidemiology and pricing (market_data.csv) are not yet verified, so peak sales in the rNPV come from the "
            "commercial agent's model or a disease-area placeholder (stated in each figure's reasoning). Medicare "
            "spending (CMS) is Part B/D only — not total sales or TAM — and is not an input to the rNPV."
        ),
    },
    {
        "id": "competitive_landscape",
        "title": "Existing competitive landscape",
        "subtitle": "Approved products and late-stage pipeline",
        "claim_sections": ["competitive_landscape"],
        "raw_sources": ["opentargets", "aact", "regulatory", "drug_profiles", "fda_labels"],
        "include_analogs": True,
    },
    {
        "id": "pipeline",
        "title": "Pipeline & drugs in development",
        "subtitle": "Industry pipeline, clinicaltrials.gov, company disclosures",
        "claim_sections": [],
        "raw_sources": ["clinicaltrials", "opentargets", "regulatory", "ema"],
    },
    {
        "id": "patent_ip",
        "title": "Patent & IP review",
        "subtitle": "FDA Orange Book (small molecules) & Purple Book (biologics) exclusivity, Lens.org filings; litigation and freedom-to-operate not covered",
        "claim_sections": [],
        "raw_sources": ["patents", "regulatory", "orange_book", "purple_book"],
        "data_gap_note": (
            "Patent documents and legal status only — not a freedom-to-operate or litigation analysis. Lens.org "
            "(requires LENS_API_TOKEN) is searched by the three leading analog drug names."
        ),
    },
    {
        "id": "trial_design",
        "title": "Clinical trial design intelligence",
        "subtitle": "Protocols, clinicaltrials.gov, publications",
        "claim_sections": ["clinical_development_plan"],
        "raw_sources": ["clinicaltrials", "biomarker_trials"],
    },
    {
        "id": "risk_diligence",
        "title": "Risk & regulatory diligence",
        "subtitle": "Red flags, safety signals (FDA labels, FAERS), regulatory advantage and open diligence questions",
        "claim_sections": ["key_risks", "critical_unknowns", "diligence_questions"],
        "raw_sources": ["regulatory", "fda_labels", "adverse_events"],
    },
    {
        "id": "regulatory_timeline",
        "title": "Regulatory timeline",
        "subtitle": "Trial registries, FDA approval records — stage reached per drug",
        "claim_sections": [],
        "raw_sources": ["aact", "regulatory", "clinicaltrials", "ema"],
        "include_analogs": True,
    },
]


def build_cards(
    claims: list[Claim], sources: dict, analogs: list[dict], mechanism: str = "", details: dict | None = None
) -> list[dict]:
    """Group claims + fetched records into the 10 cards the front-end renders.
    details (the optional program inputs) add the program's own sections — see program_views.py."""
    by_section: dict[str, list[Claim]] = {}
    for c in claims:
        by_section.setdefault(c.section, []).append(c)

    cards = []
    for number, spec in enumerate(CARDS, start=1):
        items = [
            {"kind": "claim", **c.to_dict()}
            for section in spec["claim_sections"]
            for c in by_section.get(section, [])
        ]
        items += [
            {"kind": "record", **record}
            for key in spec["raw_sources"]
            for record in sources.get(key, [])
        ]
        if spec.get("include_analogs"):
            items += [
                {
                    "kind": "analog",
                    "name": d.get("name"),
                    "stage": d.get("stage") or "stage unknown",
                    "mechanism": d.get("mechanism"),
                }
                for d in analogs
            ]
        if spec["id"] == "regulatory_timeline" and not items:
            items.append({
                "kind": "record",
                "source": "AACT",
                "url": "",
                "date_accessed": "",
                "raw_snippet": "AACT unavailable / 0 trials",
            })

        view_builder = VIEW_BUILDERS.get(spec["id"])
        view = view_builder(by_section, sources, analogs, mechanism) if view_builder else {"view": "list"}
        view = attach_program_sections(spec["id"], view, details or {}, sources, analogs, mechanism)

        cards.append({
            "id": spec["id"],
            "number": number,
            "title": spec["title"],
            "subtitle": spec["subtitle"],
            "wired": bool(spec["claim_sections"] or spec["raw_sources"]),
            "items": items,
            "sources": _card_sources(items),
            "data_gap_note": spec.get("data_gap_note"),
            **view,
        })
    return cards


def _card_sources(items: list[dict]) -> list[dict]:
    """Unique (source, url) pairs backing a card — evidence claims and fetched records."""
    seen, out = set(), []
    for item in items:
        source = item.get("source")
        if item.get("kind") == "claim" and item.get("type") != "evidence":
            continue  # an inference cites no source by definition
        if not isinstance(source, str) or (source, item.get("url")) in seen:
            continue
        seen.add((source, item.get("url")))
        out.append({
            "source": source,
            "url": item.get("url"),
            "date_accessed": item.get("date_accessed"),
        })
    return out
