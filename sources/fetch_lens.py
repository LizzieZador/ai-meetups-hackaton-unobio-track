"""Lens.org Patent Search API — live point-queries for patent coverage on
known same-mechanism drugs.

Owner: "Patent & IP review" card. This is the one card in the deck that
genuinely has no free, no-key source: the legacy PatentsView API is shut
down and its USPTO ODP successor needs a separate registration. Lens.org's
free-tier token (LENS_API_TOKEN in .env) covers patent search/landscape
instead — real patent documents, applicants, and legal status (PENDING /
GRANTED / etc.), which is the closest free equivalent to "patent databases
... freedom-to-operate analysis" the task brief asks for. "Litigation" and a
full freedom-to-operate opinion are still not covered by any source wired
here — litigation records need a separate source (e.g. CourtListener), and
freedom-to-operate is a legal judgment call, not a fetchable fact.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

import requests

from sources.cache import atomic_write

API_URL = "https://api.lens.org/patent/search"
INCLUDE_FIELDS = [
    "lens_id", "jurisdiction", "doc_number", "kind", "date_published",
    "biblio.invention_title", "biblio.parties.applicants", "legal_status",
]
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def fetch_patent_signals(drug_names: list[str], max_per_term: int = 5) -> list[dict]:
    """Search Lens.org for patents whose text mentions each drug name.

    Returns [] (not an error) when LENS_API_TOKEN isn't set — same
    "nothing to fetch yet" contract as every other optional-key source here.
    """
    token = os.environ.get("LENS_API_TOKEN")
    if not token:
        return []

    today = datetime.date.today().isoformat()
    results: list[dict] = []
    seen_ids: set[str] = set()

    for name in drug_names or []:
        name = (name or "").strip()
        if not name:
            continue
        for hit in _search_cached(name, token, max_per_term):
            record = _build_record(name, hit, today)
            if record and record["lens_id"] not in seen_ids:
                seen_ids.add(record["lens_id"])
                results.append(record)

    return results


def _search_cached(name: str, token: str, limit: int) -> list[dict]:
    # match_phrase on title/abstract (not a bare free-text "query": name)
    # avoids fuzzy/stemmed cross-field matching pulling in unrelated patents
    # that merely share a token with the term (e.g. a free-text "cancer"
    # query previously matched a labelling-machine patent and an inventor
    # surnamed "Cancer" — neither has anything to do with the disease).
    body = {
        "query": {
            "bool": {
                "should": [
                    {"match_phrase": {"biblio.invention_title": name}},
                    {"match_phrase": {"abstract": name}},
                ]
            }
        },
        "size": limit,
        "include": INCLUDE_FIELDS,
    }
    param_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"lens_patent_{param_hash}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    try:
        resp = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
        if resp.status_code == 401:
            return []  # bad/expired token — nothing to fetch, not a crash
        resp.raise_for_status()
        hits = resp.json().get("data", [])
    except (requests.RequestException, ValueError):
        return []  # outage/rate-limit this run — don't cache a false empty

    atomic_write(cache_path, json.dumps(hits, ensure_ascii=False, indent=2))
    return hits


def _build_record(name: str, hit: dict, today: str) -> Optional[dict]:
    lens_id = hit.get("lens_id")
    if not lens_id:
        return None

    biblio = hit.get("biblio", {}) or {}
    titles = biblio.get("invention_title") or []
    title = next((t.get("text") for t in titles if t.get("text")), None) or "Untitled"

    jurisdiction = hit.get("jurisdiction", "?")
    doc_number = hit.get("doc_number", "?")
    date_published = hit.get("date_published", "?")

    applicants = [
        a.get("extracted_name", {}).get("value")
        for a in (biblio.get("parties", {}) or {}).get("applicants") or []
        if a.get("extracted_name", {}).get("value")
    ]
    applicant_text = ", ".join(applicants) if applicants else "unknown applicant"

    patent_status = (hit.get("legal_status") or {}).get("patent_status")
    status_text = f", legal status: {patent_status}" if patent_status else ""

    snippet = (
        f"{title} — {jurisdiction} {doc_number} (published {date_published}), "
        f"applicant: {applicant_text}{status_text}. Matched search term: \"{name}\"."
    )

    return {
        "lens_id": lens_id,
        "jurisdiction": jurisdiction,
        "doc_number": doc_number,
        "applicants": applicants,
        "patent_status": patent_status,
        "matched_term": name,
        "source": f"Lens.org Patent {jurisdiction}{doc_number}",
        "url": f"https://www.lens.org/lens/patent/{lens_id}",
        "date_accessed": today,
        "raw_snippet": snippet,
    }
