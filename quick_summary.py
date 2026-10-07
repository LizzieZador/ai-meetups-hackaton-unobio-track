"""A single very-short, plain-language line per Required Output category.

Built purely from data the pipeline already produced — the Claim objects the
agents generated, and the raw fetched records — no new LLM call, no new
network call, nothing invented. This is meant to be the very first thing a
non-expert reads, above both the raw source dump and the detailed committee
report: one line per item of the task brief's "Required Output" list, in
plain words.

Each row also carries a "view more info" payload:
  detail_text    — a short, readable paragraph (2-4 sentences) composed by
                   joining the claims'/records' own text — never a fresh LLM
                   rewrite, just the already-generated wording, stitched
                   together. None when there is nothing to expand into.
  detail_sources — the short citation list backing detail_text (source + url
                   + date), shown as a secondary footer, not the main content.
"""
from schema import Claim, SECTION_TITLES

VERDICTS = {"Invest", "Conditional", "Do Not Invest"}

# Which raw sources a section could fall back on describing, when the LLM
# hasn't produced any claims for it yet (down, no key, still running).
_FALLBACK_SOURCE_HINT: dict[str, tuple] = {
    "scientific_thesis": ("pubmed", "opentargets"),
    "human_translation_thesis": ("pubmed", "opentargets"),
    "clinical_development_plan": ("clinicaltrials", "aact"),
    "competitive_landscape": ("opentargets", "aact"),
    "commercial_opportunity": ("commercial",),
}

# Cap on how many raw records get folded into detail_text when there are no
# claims yet — the full list is already shown, uncompressed, further down the
# page in "Raw source data"; this is a short taste, not a duplicate of that.
_MAX_FALLBACK_RECORDS_IN_DETAIL = 3


def _short(text: str, limit: int = 220) -> str:
    """Trim text without ever cutting mid-word or mid-sentence. A safety net
    for pathologically long text — real claim text almost always fits whole."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    if cut != -1:
        return text[: cut + 1]
    cut = text.rfind(" ", 0, limit)
    return (text[:cut] if cut != -1 else text) + "…"


def _as_sentence(text: str) -> str:
    text = (text or "").strip()
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return text


def _compose_detail_text(parts: list[str]) -> str:
    """Join short statements into one readable paragraph."""
    return " ".join(_as_sentence(p) for p in parts if p and p.strip())


def _claim_detail_sources(claims: list[Claim]) -> list[dict]:
    seen, out = set(), []
    for c in claims:
        if c.type == "evidence" and c.source and c.source not in seen:
            seen.add(c.source)
            out.append({"source": c.source, "url": c.url, "date_accessed": c.date_accessed})
    return out


def build_quick_summary(claims: list[Claim], sources: dict, analogs: list[dict]) -> list[dict]:
    """One short, plain-language row per Required Output category, plus a
    final row for 'Sources supporting major claims'. Each row:
    {"id", "label", "text", "detail_text", "detail_sources"}.
    """
    by_section: dict[str, list[Claim]] = {}
    for c in claims:
        by_section.setdefault(c.section, []).append(c)

    rows = [_recommendation_row(by_section)]
    for section in SECTION_TITLES:
        if section == "recommendation":
            continue
        rows.append(_section_row(section, by_section.get(section, []), sources))
    rows.append(_sources_row(claims, sources, analogs))
    return rows


def _recommendation_row(by_section: dict) -> dict:
    rec_claims = by_section.get("recommendation", [])
    verdict_claim = next((c for c in rec_claims if c.claim in VERDICTS), None)
    other_claims = [c for c in rec_claims if c is not verdict_claim]

    if verdict_claim:
        text = verdict_claim.claim
        if verdict_claim.reasoning:
            text += f" — {_short(verdict_claim.reasoning)}"
    else:
        text = "Not decided yet — committee analysis still pending"

    detail_parts = [c.claim if c.claim not in VERDICTS else "" for c in other_claims]
    detail_parts += [c.reasoning for c in rec_claims if c.reasoning]
    detail_text = _compose_detail_text(detail_parts) or None
    return {
        "id": "recommendation",
        "label": "Recommendation",
        "text": text,
        "detail_text": detail_text,
        "detail_sources": [],
    }


def _section_row(section: str, section_claims: list[Claim], sources: dict) -> dict:
    label = SECTION_TITLES[section]

    if section_claims:
        headline = _short(section_claims[0].claim)
        detail_parts = []
        for c in section_claims:
            part = c.claim
            if c.type == "inference" and c.reasoning:
                part += f" — {_short(c.reasoning, 160)}"
            detail_parts.append(part)
        return {
            "id": section,
            "label": label,
            "text": headline,
            "detail_text": _compose_detail_text(detail_parts) or None,
            "detail_sources": _claim_detail_sources(section_claims),
        }

    hint_keys = _FALLBACK_SOURCE_HINT.get(section, ())
    records = [r for k in hint_keys for r in sources.get(k, [])]
    if records:
        text = f"{len(records)} record(s) collected so far — committee analysis still pending"
        sample = records[:_MAX_FALLBACK_RECORDS_IN_DETAIL]
        detail_text = _compose_detail_text([_short(r.get("raw_snippet", ""), 180) for r in sample])
        detail_sources = [
            {"source": r.get("source"), "url": r.get("url"), "date_accessed": r.get("date_accessed")}
            for r in sample
        ]
    else:
        text = "Nothing found yet for this topic"
        detail_text, detail_sources = None, []
    return {"id": section, "label": label, "text": text, "detail_text": detail_text, "detail_sources": detail_sources}


def _sources_row(claims: list[Claim], sources: dict, analogs: list[dict]) -> dict:
    cited = _claim_detail_sources(claims)
    raw_total = sum(len(v) for v in sources.values())
    text = f"{len(cited)} cited source(s), out of {raw_total} records collected across 5 databases"

    detail_parts = []
    if analogs:
        text += f"; {len(analogs)} competing drug(s) identified"
        names = ", ".join(d.get("name", "") for d in analogs if d.get("name"))
        if names:
            detail_parts.append(f"Known competing drugs on this target: {names}")

    return {
        "id": "sources",
        "label": "Sources supporting major claims",
        "text": text,
        "detail_text": _compose_detail_text(detail_parts) or None,
        "detail_sources": cited,
    }


def render_quick_summary_markdown(rows: list[dict]) -> str:
    """Plain-text markdown — printed straight to a console by main.py, so this
    has no interactivity (that's the web UI's "view more info" toggle); it just
    appends the readable detail paragraph right below the headline."""
    lines = ["# At a Glance", ""]
    for row in rows:
        lines.append(f"- **{row['label']}:** {row['text']}")
        if row.get("detail_text"):
            lines.append(f"  - {row['detail_text']}")
    return "\n".join(lines)
