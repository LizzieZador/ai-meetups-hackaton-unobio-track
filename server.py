"""Web UI backend — serves the static front-end in web/ and exposes the pipeline as JSON.

GET  /              -> web/index.html (search sidebar + report cards)
POST /api/evaluate  -> {"query": "..."} -> structured report (sections, claims, sources)

The Streamlit app (app.py) stays as an alternative UI; both call run_pipeline().
Run: python server.py  (http://127.0.0.1:5000)
"""
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from pipeline import run_pipeline
from schema import SECTION_TITLES, Claim

WEB_DIR = Path(__file__).resolve().parent / "web"
VERDICTS = {"Invest", "Conditional", "Do Not Invest"}

SECTION_SUBTITLES = {
    "recommendation": "Committee verdict and rationale",
    "scientific_thesis": "PubMed, Open Targets",
    "human_translation_thesis": "PubMed, Open Targets, genetics",
    "clinical_development_plan": "ClinicalTrials.gov, AACT",
    "competitive_landscape": "Approved products and late-stage pipeline",
    "commercial_opportunity": "Epidemiology, market data, treatment patterns",
    "capital_required": "Budget to the next major milestone",
    "key_risks": "Failure modes and what could go wrong",
    "critical_unknowns": "What the evidence does not tell us",
    "diligence_questions": "Questions to resolve before committing capital",
}

app = Flask(__name__, static_folder=None)


def split_query(query: str) -> tuple[str, str]:
    """One search box -> (indication, mechanism).

    "KRAS G12C inhibitor | NSCLC" / "... for NSCLC" / "... in NSCLC" split into
    mechanism and indication; a bare query is used for both so the pipeline still runs.
    """
    parts = re.split(r"\s*\|\s*|\s+(?:for|in)\s+", query, maxsplit=1, flags=re.I)
    if len(parts) == 2 and all(p.strip() for p in parts):
        return parts[1].strip(), parts[0].strip()
    return query, query


def build_report(query: str, claims: list[Claim], cost: dict) -> dict:
    by_section: dict[str, list[Claim]] = {}
    for c in claims:
        by_section.setdefault(c.section, []).append(c)

    verdict = next(
        (c.claim for c in by_section.get("recommendation", []) if c.claim in VERDICTS), None
    )
    sections = []
    for i, (sid, title) in enumerate(SECTION_TITLES.items(), start=1):
        items = by_section.get(sid, [])
        seen, sources = set(), []
        for c in items:
            if c.type == "evidence" and c.source and (c.source, c.url) not in seen:
                seen.add((c.source, c.url))
                sources.append({"source": c.source, "url": c.url, "date_accessed": c.date_accessed})
        sections.append({
            "id": sid,
            "number": i,
            "title": title,
            "subtitle": SECTION_SUBTITLES.get(sid, ""),
            "claims": [c.to_dict() for c in items if not (sid == "recommendation" and c.claim in VERDICTS)],
            "sources": sources,
        })
    return {"query": query, "verdict": verdict, "sections": sections, "cost": cost}


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(WEB_DIR, name)


@app.post("/api/evaluate")
def evaluate():
    query = ((request.get_json(silent=True) or {}).get("query") or "").strip()
    if not query:
        return jsonify(error="Query is required."), 400
    indication, mechanism = split_query(query)
    try:
        claims, cost = run_pipeline(indication, mechanism)
    except Exception as e:  # surface API-key / network problems to the UI instead of a 500 page
        return jsonify(error=f"Pipeline failed: {e}"), 500
    return jsonify(build_report(query, claims, cost))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
