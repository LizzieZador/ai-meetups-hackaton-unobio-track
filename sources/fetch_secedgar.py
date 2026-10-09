"""SEC EDGAR full-text search — live point-queries for company disclosures
mentioning a drug name, target or indication.

Owner: "Market sentiment & expert intelligence" card. SEC EDGAR indexes every
8-K (material event), 10-K/10-Q and press-release exhibit public biotech/pharma
companies file — the closest free, no-key, no-login source this project has to
"press releases" and "company disclosures." It does not cover earnings-call
audio/transcripts or analyst/KOL commentary (no free source exists for those;
see sources/fetch_cms.py's Open Payments helpers for the adjacent "industry
money to physicians" signal, which is a different kind of market intelligence
and is intentionally not wired into this card — it's about conflicts of
interest for named physicians, not sentiment on a specific program).
"""
import datetime
import hashlib
import json
from pathlib import Path
from typing import Optional

import requests

SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
# SEC's fair-access policy asks every client to send a descriptive UA with a
# contact point; override via SEC_EDGAR_USER_AGENT in .env if deploying this
# beyond the hackathon.
import os

USER_AGENT = os.environ.get(
    "SEC_EDGAR_USER_AGENT", "UNOBIO-Hackathon-InvestmentCommittee research@unobio-hackathon.local"
)
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def fetch_press_releases(terms: list[str], max_per_term: int = 5, forms: str = "8-K") -> list[dict]:
    """Full-text-search EDGAR for each term; return one record per matching filing.

    terms: company/drug/target names most likely to appear verbatim in a
    filing (e.g. known same-mechanism drug names) — broad disease names
    produce too much unrelated noise and are deliberately not passed here by
    the caller.
    """
    today = datetime.date.today().isoformat()
    results: list[dict] = []
    seen_ids: set[str] = set()

    for term in terms or []:
        term = (term or "").strip()
        if not term:
            continue

        hits = _search_cached(term, max_per_term, forms)
        for hit in hits:
            record = _build_record(term, hit, today)
            if record and record["_hit_id"] not in seen_ids:
                seen_ids.add(record["_hit_id"])
                record.pop("_hit_id")
                results.append(record)

    return results


def _search_cached(term: str, limit: int, forms: str) -> list[dict]:
    params = {"q": f'"{term}"', "forms": forms}
    param_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"sec_edgar_{param_hash}.json"

    if cache_path.exists():
        hits = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        hits = _fetch(params)
        if hits is None:
            return []
        cache_path.write_text(json.dumps(hits, ensure_ascii=False, indent=2), encoding="utf-8")

    return hits[:limit]


def _fetch(params: dict) -> Optional[list[dict]]:
    try:
        resp = requests.get(SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("hits", {}).get("hits", [])
    except (requests.RequestException, ValueError):
        return None


def _build_record(term: str, hit: dict, today: str) -> Optional[dict]:
    src = hit.get("_source", {})
    hit_id = hit.get("_id", "")
    if ":" not in hit_id:
        return None
    accession, filename = hit_id.split(":", 1)

    ciks = src.get("ciks") or []
    cik = ciks[0].lstrip("0") if ciks else ""
    if not cik:
        # A generic company-search URL would not substantiate this filing.
        return None
    accession_nodash = accession.replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}"
        if cik else ""
    )

    company = (src.get("display_names") or ["Unknown filer"])[0]
    form = src.get("form", "?")
    file_date = src.get("file_date", "?")
    items = ", ".join(src.get("items") or []) or None
    description = src.get("file_description") or filename

    snippet_parts = [f"{company} — {form} filed {file_date}", f"exhibit: {description}"]
    if items:
        snippet_parts.append(f"items: {items}")
    snippet_parts.append(f"matched search term: \"{term}\"")

    return {
        "company": company,
        "form": form,
        "file_date": file_date,
        "matched_term": term,
        "source": f"SEC EDGAR {company} {form} ({file_date})",
        "url": url,
        "date_accessed": today,
        "raw_snippet": ", ".join(snippet_parts),
        "_hit_id": hit_id,
    }


if __name__ == "__main__":
    import sys

    names = sys.argv[1:] or ["sotorasib"]
    print(json.dumps(fetch_press_releases(names), indent=2))
