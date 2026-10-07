"""Web UI backend — serves the static front-end in web/ and exposes the pipeline as JSON.

GET  /              -> web/index.html (search sidebar + the 12-card grid)
POST /api/evaluate  -> {"query": "..."} -> {verdict, verdict_claims, cards, cost}

Card layout and the mapping from claim sections / fetched sources onto cards
lives in cards.py; this module only turns a query into one pipeline run.
The Streamlit app (app.py) stays as an alternative UI; both call run_pipeline().
Run: python server.py  (http://127.0.0.1:5000)
"""
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from cards import build_cards, build_verdict_claims
from pipeline import run_pipeline
from quick_summary import build_quick_summary
from raw_data import extract_analogs
from schema import Claim

WEB_DIR = Path(__file__).resolve().parent / "web"
VERDICTS = {"Invest", "Conditional", "Do Not Invest"}

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


def build_report(query: str, claims: list[Claim], cost: dict, sources: dict) -> dict:
    verdict = next(
        (c.claim for c in claims if c.section == "recommendation" and c.claim in VERDICTS), None
    )
    # The bare verdict word is already shown as the header chip — drop it from the
    # list below so it isn't repeated without its reasoning.
    verdict_claims = [c for c in build_verdict_claims(claims) if c.get("claim") not in VERDICTS]
    analogs = extract_analogs(sources)
    return {
        "query": query,
        "verdict": verdict,
        "quick_summary": build_quick_summary(claims, sources, analogs),
        "verdict_claims": verdict_claims,
        "cards": build_cards(claims, sources, analogs),
        "cost": cost,
    }


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
        claims, cost, sources = run_pipeline(indication, mechanism)
    except Exception as e:  # surface API-key / network problems to the UI instead of a 500 page
        return jsonify(error=f"Pipeline failed: {e}"), 500
    return jsonify(build_report(query, claims, cost, sources))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
