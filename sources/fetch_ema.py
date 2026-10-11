"""European Medicines Agency — EU marketing authorisation per drug: status,
date, approved indication, and the conditional / orphan / PRIME flags.

EMA publishes its full medicines table as one JSON file (~7 MB); it is cached
and refreshed every REFRESH_DAYS, falling back to the cached copy on failure.
Matched on the active substance / INN, human medicines only, generics and
biosimilars excluded (they'd show the copy's date, not the originator's).
"""
import datetime
import json
import time
from pathlib import Path

import requests

DATA_URL = "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
CACHE_PATH = Path(__file__).resolve().parent.parent / "cache" / "ema_medicines.json"
REFRESH_DAYS = 30

_data: list[dict] | None = None


def _load() -> list[dict]:
    global _data
    if _data is not None:
        return _data
    if not CACHE_PATH.exists() or time.time() - CACHE_PATH.stat().st_mtime > REFRESH_DAYS * 86400:
        try:
            resp = requests.get(DATA_URL, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            resp.json()  # validate before overwriting the cache
            CACHE_PATH.write_bytes(resp.content)
        except (requests.RequestException, ValueError) as e:
            print(f"[EMA] refresh failed ({e}); using cached copy if present")
    if not CACHE_PATH.exists():
        return []
    _data = json.loads(CACHE_PATH.read_text(encoding="utf-8")).get("data") or []
    return _data


def _iso(ddmmyyyy: str | None) -> str | None:
    try:
        return datetime.datetime.strptime(ddmmyyyy, "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def fetch_eu_approvals(drug_names: list[str]) -> list[dict]:
    data = _load()
    today = datetime.date.today().isoformat()
    out = []
    for name in dict.fromkeys(n for n in drug_names if n):
        key = name.strip().lower()
        matches = [
            m for m in data
            if m.get("category") == "Human" and m.get("generic") != "Yes" and m.get("biosimilar") != "Yes"
            and key in {(m.get("international_non_proprietary_name_common_name") or "").lower(), (m.get("active_substance") or "").lower()}
        ]
        if not matches:
            continue
        m = min(matches, key=lambda x: _iso(x.get("marketing_authorisation_date")) or "9999")
        flags = [f for f, col in (("conditional", "conditional_approval"), ("orphan", "orphan_medicine"),
                                  ("PRIME", "prime_priority_medicine")) if m.get(col) == "Yes"]
        indication = " ".join((m.get("therapeutic_indication") or "").split())
        out.append({
            "drug_name": name,
            "brand": m.get("name_of_medicine"),
            "status": m.get("medicine_status"),
            "authorised": _iso(m.get("marketing_authorisation_date")),
            "flags": flags,
            "holder": m.get("marketing_authorisation_developer_applicant_holder"),
            "therapeutic_area": m.get("therapeutic_area_mesh"),
            "indication": indication[:400] + ("…" if len(indication) > 400 else ""),
            "source": f"EMA EPAR {m.get('name_of_medicine')}",
            "url": m.get("medicine_url") or DATA_URL,
            "date_accessed": today,
            "raw_snippet": f"{m.get('name_of_medicine')} ({name}) — EU {m.get('medicine_status')}, authorised "
                           f"{_iso(m.get('marketing_authorisation_date')) or 'n/a'}{' (' + ', '.join(flags) + ')' if flags else ''}; "
                           f"holder {m.get('marketing_authorisation_developer_applicant_holder')}; indication: {indication[:200]}",
        })
    return out
