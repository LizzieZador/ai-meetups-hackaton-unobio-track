"""The site's 12 intelligence cards — the view model behind the card grid.

The claim taxonomy in schema.py follows the task brief's "Required Output" list
verbatim, and report.py renders the markdown report in exactly that shape (a
graded deliverable — it is not changed here). The *site* presents the same
material as 12 topic cards instead, so this module is the single place that maps
brief sections and fetched source records onto what the UI shows.

Each card declares where its content comes from:
  claim_sections — schema.SECTION_TITLES keys whose claims land in this card
  raw_sources    — raw_data.SOURCE_ORDER keys whose fetched records land here

A card with neither has no connected source yet (we fetch nothing that answers
it — e.g. patent databases, earnings calls). The UI says exactly that instead of
"No results yet", so an unwired card is never mistaken for a run that found
nothing.

`recommendation` and `diligence_questions` claims deliberately map to no card:
they are the committee's verdict layer, rendered above the grid by the front-end,
not one of the twelve topics.
"""
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
        "title": "Company disclosures (SEC EDGAR)",
        "subtitle": "8-K filing metadata and exhibits; not earnings-call transcripts or sentiment analysis",
        "claim_sections": [],
        "raw_sources": ["market_news"],
    },
    {
        "id": "scientific_evidence",
        "title": "Scientific evidence: efficacy, safety & differentiation",
        "subtitle": "Data room, published studies, FDA and EMA resources",
        "claim_sections": ["scientific_thesis"],
        "raw_sources": ["pubmed"],
    },
    {
        "id": "patient_population",
        "title": "Patient population, treatment dynamics & TAM",
        "subtitle": "Epidemiology, claims and EMR, treatment patterns, market research",
        "claim_sections": ["commercial_opportunity"],
        "raw_sources": ["commercial"],
    },
    {
        "id": "competitive_landscape",
        "title": "Existing competitive landscape",
        "subtitle": "Approved products and late-stage pipeline",
        "claim_sections": ["competitive_landscape"],
        "raw_sources": ["opentargets", "aact"],
        "include_analogs": True,
    },
    {
        "id": "pipeline",
        "title": "Pipeline & drugs in development",
        "subtitle": "Industry pipeline, clinicaltrials.gov, company disclosures",
        "claim_sections": [],
        "raw_sources": ["clinicaltrials"],
    },
    {
        "id": "patent_ip",
        "title": "Patent & IP review",
        "subtitle": "Not connected yet: needs searchable patent records and claim-level review",
        "claim_sections": [],
        "raw_sources": [],
    },
    {
        "id": "trial_design",
        "title": "Clinical trial design intelligence",
        "subtitle": "Protocols, clinicaltrials.gov, publications",
        "claim_sections": ["clinical_development_plan"],
        "raw_sources": [],
    },
    {
        "id": "manufacturing",
        "title": "Manufacturing, supply chain & cost of goods",
        "subtitle": "",
        "claim_sections": ["capital_required"],
        "raw_sources": [],
    },
    {
        "id": "red_flags",
        "title": "Red flag monitor",
        "subtitle": "Safety reports, regulatory actions, litigation, data-room review",
        "claim_sections": ["key_risks", "critical_unknowns"],
        "raw_sources": [],
    },
    {
        "id": "green_flags",
        "title": "Green flag & regulatory advantage monitor",
        "subtitle": "Regulatory databases, company disclosures, academic literature",
        "claim_sections": [],
        "raw_sources": ["regulatory"],
    },
    {
        "id": "regulatory_timeline",
        "title": "Regulatory timeline",
        "subtitle": "Trial registries, FDA approval records",
        "claim_sections": [],
        "raw_sources": ["aact"],
    },
]

# Claim sections rendered above the grid as the committee's verdict, not in a card.
VERDICT_SECTIONS = ["recommendation", "diligence_questions"]


def build_cards(claims: list[Claim], sources: dict, analogs: list[dict]) -> list[dict]:
    """Group claims + fetched records into the 12 cards the front-end renders."""
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
                {"kind": "analog", "name": d.get("name"), "stage": d.get("stage") or "stage unknown"}
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
        cards.append({
            "id": spec["id"],
            "number": number,
            "title": spec["title"],
            "subtitle": spec["subtitle"],
            "wired": bool(spec["claim_sections"] or spec["raw_sources"]),
            "items": items,
            "sources": _card_sources(items),
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


def build_verdict_claims(claims: list[Claim]) -> list[dict]:
    """Recommendation + diligence-question claims, in VERDICT_SECTIONS order."""
    return [
        c.to_dict()
        for section in VERDICT_SECTIONS
        for c in claims
        if c.section == section
    ]
