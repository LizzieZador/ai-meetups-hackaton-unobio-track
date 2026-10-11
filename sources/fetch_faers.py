"""openFDA FAERS — post-marketing adverse event reports per drug: total and
serious report counts, and the most-reported reactions.

FAERS counts are spontaneous reports, not incidence: they show what is being
reported, not causation or rates. Terms that describe the disease or how the
drug was used rather than a reaction (the indication itself, "disease
progression", "off label use", ...) are dropped from the top-reaction list.
Keyless, disk-cached per drug name.
"""
import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import requests

from sources.cache import atomic_write

API_URL = "https://api.fda.gov/drug/event.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
TOP_REACTIONS = 6
_NOT_A_REACTION = {
    "DISEASE PROGRESSION", "OFF LABEL USE", "PRODUCT USE IN UNAPPROVED INDICATION", "DRUG INEFFECTIVE",
    "NO ADVERSE EVENT", "PRODUCT USE ISSUE", "PRODUCT DOSE OMISSION ISSUE", "PRODUCT DOSE OMISSION",
    "INAPPROPRIATE SCHEDULE OF PRODUCT ADMINISTRATION", "MALIGNANT NEOPLASM PROGRESSION",
    "INTENTIONAL PRODUCT USE ISSUE", "THERAPY INTERRUPTED", "THERAPY CESSATION", "HOSPITALISATION",
    "CONDITION AGGRAVATED", "WRONG TECHNIQUE IN PRODUCT USAGE PROCESS", "EXPIRED PRODUCT ADMINISTERED",
}


def _get(params: dict) -> dict | None:
    path = CACHE_DIR / f"faers_{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:24]}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = requests.get(API_URL, params=params, timeout=20)
        data = {} if resp.status_code == 404 else resp.json()
        if resp.status_code not in (200, 404):
            return None
    except (requests.RequestException, ValueError):
        return None
    atomic_write(path, json.dumps(data, ensure_ascii=False))
    return data


def fetch_adverse_events(drug_names: list[str], indication: str = "") -> list[dict]:
    today = datetime.date.today().isoformat()
    disease_words = {w for w in indication.upper().replace("-", " ").split() if len(w) > 3}
    out = []
    for name in dict.fromkeys(n for n in drug_names if n):
        # The harmonized openfda block is missing for some drugs (zanubrutinib:
        # 0 reports by generic name, 4,233 by reported active substance).
        search, total = None, None
        for field in ("patient.drug.openfda.generic_name", "patient.drug.activesubstance.activesubstancename"):
            candidate = f'{field}:"{name.lower()}"'
            total = ((_get({"search": candidate, "limit": 1}) or {}).get("meta") or {}).get("results", {}).get("total")
            if total:
                search = candidate
                break
        if not search:
            continue
        serious = ((_get({"search": f"{search} AND serious:1", "limit": 1}) or {}).get("meta") or {}).get("results", {}).get("total") or 0
        counted = (_get({"search": search, "count": "patient.reaction.reactionmeddrapt.exact", "limit": 30}) or {}).get("results") or []
        reactions = [
            (r["term"].capitalize(), r["count"]) for r in counted
            if r["term"] not in _NOT_A_REACTION and not (disease_words and disease_words <= set(r["term"].replace("-", " ").split()))
        ][:TOP_REACTIONS]
        top = ", ".join(f"{t} ({c})" for t, c in reactions)
        out.append({
            "drug_name": name,
            "reports": total,
            "serious_reports": serious,
            "top_reactions": reactions,
            "source": f"FDA FAERS {name.title()}",
            "url": f"{API_URL}?search={quote(search)}&count=patient.reaction.reactionmeddrapt.exact",
            "date_accessed": today,
            "raw_snippet": f"{name.title()}: {total} FAERS reports ({serious} serious); most reported reactions: {top or 'n/a'}. "
                           "Spontaneous reports — not incidence or causation.",
        })
    return out
