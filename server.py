"""Web UI backend — serves the static front-end in web/ and exposes the pipeline as JSON.

GET  /              -> web/index.html (search sidebar + the 10-card grid)
POST /api/evaluate   -> {"indication": "...", "mechanism": "..."} OR {"query": "mechanism | indication"},
                        optional "details": {modality, stage, route, biomarkers, preclinical}
                        -> {verdict, cards, cost, details}
POST /api/assistant   -> {"message": "...", "history": [...], "report": <loaded report | null>}
                        -> {"reply": "...", "search": {"mechanism","indication"} | None}

Card layout and the mapping from claim sections / fetched sources onto cards
lives in cards.py; this module only turns a query into one pipeline run.
Run: python server.py  (http://127.0.0.1:5000)
"""
import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from assistant import ask_assistant
from cards import build_cards
from pipeline import run_pipeline
from raw_data import extract_competitors
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
    # Separators tried in priority order, not as one alternation: a single regex
    # splits at the LEFTMOST match of any of them, so "PD-1/PD-L1 | NSCLC" or
    # "CDK4/6 inhibitor for breast cancer" were cut at the slash inside the
    # target name. "/" now only counts as a separator with whitespace on both
    # sides — slashes inside names (PD-1/PD-L1, CDK4/6, HER2/neu) never do.
    # "×" ("BTK inhibitor × multiple sclerosis") is a common way to write a
    # target-indication pair; a plain "x" only counts with spaces on both sides.
    for sep in (r"\s*\|\s*", r"\s*×\s*", r"\s+(?:for|in)\s+", r"\s+/\s+", r"\s+x\s+"):
        parts = re.split(sep, query, maxsplit=1, flags=re.I)
        if len(parts) == 2 and all(p.strip() for p in parts):
            return parts[1].strip(), parts[0].strip()
    # Nothing but separators/whitespace ("  |  ") leaves "" -> rejected by the caller.
    cleaned = re.sub(r"^[\s|/×]+|[\s|/×]+$", "", query)
    return cleaned, cleaned


def build_report(
    indication: str, mechanism: str, claims: list[Claim], cost: dict, sources: dict, details: dict | None = None
) -> dict:
    verdict_claim = next(
        (c for c in claims if c.section == "recommendation" and c.claim in VERDICTS), None
    )
    analogs = extract_competitors(sources)
    return {
        "indication": indication,
        "mechanism": mechanism,
        "details": details or {},
        "verdict": verdict_claim.claim if verdict_claim else None,
        "verdict_reasoning": (verdict_claim.reasoning if verdict_claim else None),
        "cards": build_cards(claims, sources, analogs, mechanism),
        "cost": cost,
    }


# The task brief's optional program inputs: field -> (label, max length).
DETAIL_FIELDS = {
    "modality": ("Modality", 120),
    "stage": ("Development stage", 60),
    "route": ("Route of administration", 120),
    "biomarkers": ("Biomarkers", 300),
    "preclinical": ("Preclinical results", 1500),
}


def parse_details(raw) -> dict:
    """Optional details from the request: known fields only, text only, capped."""
    raw = raw if isinstance(raw, dict) else {}
    return {key: _text(raw.get(key))[:limit] for key, (_, limit) in DETAIL_FIELDS.items() if _text(raw.get(key))}


def details_context(details: dict) -> str:
    """The agents' extra_context: one labelled line per provided detail."""
    return "\n".join(f"{DETAIL_FIELDS[k][0]}: {v}" for k, v in details.items())


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(WEB_DIR, name)


def _json_body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _text(value) -> str:
    """A request field as text — anything that isn't a string counts as empty
    (a number or object there used to crash the route into an HTML 500)."""
    return value.strip() if isinstance(value, str) else ""


@app.post("/api/evaluate")
def evaluate():
    body = _json_body()
    query = _text(body.get("query"))
    if query:
        indication, mechanism = split_query(query)
    else:
        indication = _text(body.get("indication"))
        mechanism = _text(body.get("mechanism"))
    if not indication or not mechanism:
        return jsonify(error="Both Indication and Mechanism/Target are required."), 400
    details = parse_details(body.get("details"))
    try:
        claims, cost, sources = run_pipeline(indication, mechanism, extra_context=details_context(details))
    except Exception as e:  # surface API-key / network problems to the UI instead of a 500 page
        return jsonify(error=f"Pipeline failed: {e}"), 500
    return jsonify(build_report(indication, mechanism, claims, cost, sources, details))


@app.post("/api/assistant")
def assistant():
    body = _json_body()
    message = _text(body.get("message"))
    if not message:
        return jsonify(error="Message is required."), 400
    # Everything below comes from the browser's own state (localStorage) and
    # is only trusted in shape once checked here.
    history = body.get("history") if isinstance(body.get("history"), list) else []
    report = body.get("report") if isinstance(body.get("report"), dict) else None
    screen = body.get("screen") if isinstance(body.get("screen"), dict) else {}
    try:
        return jsonify(ask_assistant(message, history, report, screen))
    except Exception as e:  # surface API-key / network problems to the chat instead of a 500 page
        return jsonify(error=f"Assistant failed: {e}"), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
