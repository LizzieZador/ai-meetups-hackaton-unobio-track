"""Render collect_sources() output directly — no LLM involved anywhere here.

This is the literal data we pulled from PubMed / Open Targets / ClinicalTrials.gov
/ AACT / the commercial CSV for the queried drug/mechanism, and the list of known
analog/competitor drugs on the same target. The LLM-based committee report
(report.py) is built FROM this data; this module exists so the site can show the
data itself, independent of whether the LLM step succeeds at all.
"""
import datetime

SOURCE_TITLES = {
    "pubmed": "PubMed",
    "opentargets": "Open Targets",
    "clinicaltrials": "ClinicalTrials.gov",
    "aact": "AACT (Aggregate Analysis of ClinicalTrials.gov)",
    "commercial": "Epidemiology / Market Data",
}
SOURCE_ORDER = ["pubmed", "opentargets", "clinicaltrials", "aact", "commercial"]


def collect_valid_sources(sources: dict) -> set:
    """Every exact `source` string actually fetched this run — used by
    schema.parse_claims() to reject evidence claims citing a source we never
    fetched (a hallucinated citation)."""
    return {item["source"] for items in sources.values() for item in items if item.get("source")}


def extract_analogs(sources: dict) -> list[dict]:
    """Known competitor/analog drugs on the same target, from Open Targets
    (the only source that names them directly) — deduplicated by name."""
    seen, analogs = set(), []
    for item in sources.get("opentargets", []):
        for drug in item.get("known_drugs", []):
            name = drug.get("name")
            if name and name not in seen:
                seen.add(name)
                analogs.append(drug)
    return analogs


def render_raw_data_markdown(indication: str, mechanism: str, sources: dict, analogs: list[dict]) -> str:
    lines = [
        f"# Raw Source Data: {mechanism} — {indication}",
        f"_Generated {datetime.date.today().isoformat()} — pulled directly from the connected "
        f"databases, no LLM involved._",
        "",
    ]
    for key in SOURCE_ORDER:
        items = sources.get(key, [])
        lines.append(f"## {SOURCE_TITLES[key]} ({len(items)} record{'s' if len(items) != 1 else ''})")
        if not items:
            lines.append("_No data returned by this source for this query._")
        for item in items:
            link = f"[{item['source']}]({item.get('url')})" if item.get("url") else item["source"]
            lines.append(f"- **{link}** (accessed {item.get('date_accessed')})")
            lines.append(f"  - {item.get('raw_snippet', '')}")
        lines.append("")

    lines.append(f"## Analogs / Competing Drugs ({len(analogs)})")
    if not analogs:
        lines.append("_No known competing drugs on this target were found in Open Targets._")
    else:
        for drug in analogs:
            stage = drug.get("stage") or "stage unknown"
            lines.append(f"- **{drug['name']}** — {stage}")
    lines.append("")

    return "\n".join(lines)
