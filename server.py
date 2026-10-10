"""Web UI backend — serves the static front-end in web/ and exposes the pipeline as JSON.

GET  /              -> web/index.html (search sidebar + the 12-card grid)
POST /api/evaluate   -> {"indication": "...", "mechanism": "..."} OR {"query": "mechanism | indication"}
                        -> {verdict, cards, cost}
POST /api/assistant   -> {"message": "...", "history": [...], "report": <loaded report | null>}
                        -> {"reply": "...", "search": {"mechanism","indication"} | None}

Card layout and the mapping from claim sections / fetched sources onto cards
lives in cards.py; this module only turns a query into one pipeline run.
The Streamlit app (app.py) stays as an alternative UI; both call run_pipeline().
Run: python server.py  (http://127.0.0.1:5000)
"""
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from assistant import ask_assistant
from cards import build_cards
from pipeline import run_pipeline
from raw_data import extract_analogs
from schema import Claim

WEB_DIR = Path(__file__).resolve().parent / "web"
VERDICTS = {"Invest", "Conditional", "Do Not Invest"}

app = Flask(__name__, static_folder=None)


def split_query(query: str) -> tuple[str, str]:
    """Quick-search's one combined box -> (indication, mechanism).

    "KRAS G12C inhibitor | NSCLC" / "... / NSCLC" / "... for NSCLC" / "... in
    NSCLC" split into mechanism and indication; a bare query (no separator)
    is used for both so the pipeline still runs — case-insensitivity and the
    mechanism-fallback in collect_sources() already handle a single generic
    word reasonably.

    BUG FIX: "/" used to not be recognized as a separator at all, so typing
    "PCSK9 / hypercholesterolemia" fell through to the no-separator fallback
    and ran with indication = mechanism = the entire unparsed string (visibly
    showing as "PCSK9 / hypercholesterolemia in PCSK9 / hypercholesterolemia"
    in the report head) instead of actually splitting it.
    """
    parts = re.split(r"\s*\|\s*|\s*/\s*|\s+(?:for|in)\s+", query, maxsplit=1, flags=re.I)
    if len(parts) == 2 and all(p.strip() for p in parts):
        return parts[1].strip(), parts[0].strip()
    return query, query


def build_report(indication: str, mechanism: str, claims: list[Claim], cost: dict, sources: dict) -> dict:
    verdict_claim = next(
        (c for c in claims if c.section == "recommendation" and c.claim in VERDICTS), None
    )
    analogs = extract_analogs(sources)
    return {
        "indication": indication,
        "mechanism": mechanism,
        "verdict": verdict_claim.claim if verdict_claim else None,
        "verdict_reasoning": (verdict_claim.reasoning if verdict_claim else None),
        "cards": build_cards(claims, sources, analogs, mechanism),
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
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if query:
        indication, mechanism = split_query(query)
    else:
        indication = (body.get("indication") or "").strip()
        mechanism = (body.get("mechanism") or "").strip()
    if not indication or not mechanism:
        return jsonify(error="Both Indication and Mechanism/Target are required."), 400
    try:
        claims, cost, sources = run_pipeline(indication, mechanism)
    except Exception as e:  # surface API-key / network problems to the UI instead of a 500 page
        return jsonify(error=f"Pipeline failed: {e}"), 500
    return jsonify(build_report(indication, mechanism, claims, cost, sources))


@app.post("/api/assistant")
def assistant():
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify(error="Message is required."), 400
    history = body.get("history") or []
    report = body.get("report")
    try:
        return jsonify(ask_assistant(message, history, report))
    except Exception as e:  # surface API-key / network problems to the chat instead of a 500 page
        return jsonify(error=f"Assistant failed: {e}"), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
