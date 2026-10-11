"""openFDA drug labels (SPL) — the official US indications and boxed warning
for each approved drug, straight from its current label on DailyMed.

Keyless, disk-cached per drug name. A drug with no US label (not approved, or
a biologic filed under a name openFDA doesn't harmonize) is simply skipped.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path

import requests

from sources.cache import atomic_write

API_URL = "https://api.fda.gov/drug/label.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


def _clean(text: str, limit: int) -> str:
    """Label sections start with their own heading and section numbers
    ("1 INDICATIONS AND USAGE LUMAKRAS is ...") — keep the content, trimmed."""
    text = re.sub(r"^\s*(?:\d+(?:\.\d+)*\s+)?(INDICATIONS AND USAGE|BOXED WARNING|WARNING)[:\s]*", "", text or "", flags=re.I)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: text.rfind(" ", 0, limit)] + "…"


def _query(name: str) -> list[dict] | None:
    """By harmonized generic name, else by the label's own product text —
    some labels lack the openfda block entirely (zanubrutinib / Brukinsa)."""
    results = _query_field("openfda.generic_name", name)
    if results == []:
        results = _query_field("spl_product_data_elements", name)
    return results


def _query_field(field: str, name: str) -> list[dict] | None:
    params = {"search": f'{field}:"{name.lower()}"', "limit": 10}
    path = CACHE_DIR / f"fda_label_{hashlib.sha256(json.dumps(params).encode()).hexdigest()[:24]}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = requests.get(API_URL, params=params, timeout=15)
        if resp.status_code == 404:
            data = []
        else:
            resp.raise_for_status()
            data = resp.json().get("results", [])
    except (requests.RequestException, ValueError):
        return None  # outage: not cached, retried next run
    atomic_write(path, json.dumps(data, ensure_ascii=False))
    return data


def fetch_drug_labels(drug_names: list[str]) -> list[dict]:
    today = datetime.date.today().isoformat()
    out = []
    for name in dict.fromkeys(n for n in drug_names if n):
        results = _query(name)
        if not results:
            continue
        # The single-ingredient label, not a combination product that merely
        # contains the drug (pembrolizumab -> Keytruda, not Keytruda Qlex).
        single = [r for r in results if [g.lower() for g in (r.get("openfda") or {}).get("generic_name") or []] == [name.lower()]]
        label = (single or results)[0]
        indication = _clean((label.get("indications_and_usage") or [""])[0], 400)
        boxed = _clean((label.get("boxed_warning") or [""])[0], 300) or None
        set_id = label.get("set_id")
        brand = ", ".join(((label.get("openfda") or {}).get("brand_name") or [])[:2])
        if not brand:  # no openfda block: the product text starts with the brand
            first = ((label.get("spl_product_data_elements") or [""])[0].split() or [""])[0]
            brand = first if first.isupper() and first.lower() != name.lower() else ""
        out.append({
            "drug_name": name,
            "brand": brand or None,
            "indication": indication or None,
            "boxed_warning": boxed,
            "label_date": label.get("effective_time"),
            "source": f"FDA label (DailyMed) {brand or name.title()}",
            "url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}" if set_id else API_URL,
            "date_accessed": today,
            "raw_snippet": f"{name.title()} US label — indication: {indication or 'n/a'}"
                           + (f" BOXED WARNING: {boxed}" if boxed else " No boxed warning."),
        })
    return out
