"""FDA Purple Book — licensed biologics (BLAs) and their exclusivity dates:
the biologics counterpart of the Orange Book, which lists small molecules only.

Per product: BLA number, brand, applicant, date of first licensure, the
12-year reference-product exclusivity and orphan exclusivity expiry. FDA
publishes one CSV per month; the newest link is read from the downloads page
(file names vary in case month to month, so it can't be built), cached and
refreshed every REFRESH_DAYS. A refresh failure falls back to the cached copy.
"""
import csv
import datetime
import io
import re
import time
from pathlib import Path

import requests

DOWNLOADS_PAGE = "https://purplebooksearch.fda.gov/downloads"
CACHE_PATH = Path(__file__).resolve().parent.parent / "cache" / "purple_book.csv"
REFRESH_DAYS = 30
_HEADERS = {"User-Agent": "Mozilla/5.0"}

_rows: list[dict] | None = None
_source_url: str | None = None


def _ensure_csv() -> Path | None:
    global _source_url
    if CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime < REFRESH_DAYS * 86400:
        return CACHE_PATH
    try:
        page = requests.get(DOWNLOADS_PAGE, headers=_HEADERS, timeout=30).text
        url = re.findall(r'href="(https://[^"]+data-download\.csv)"', page)[0]
        resp = requests.get(url, headers=_HEADERS, timeout=90)
        resp.raise_for_status()
        CACHE_PATH.write_bytes(resp.content)
        _source_url = url
    except (requests.RequestException, IndexError) as e:
        print(f"[Purple Book] refresh failed ({e}); using cached copy if present")
    return CACHE_PATH if CACHE_PATH.exists() else None


def _load() -> list[dict]:
    """Every product row; the file repeats a header line before each section."""
    global _rows
    if _rows is not None:
        return _rows
    path = _ensure_csv()
    if not path:
        return []
    header, rows = None, []
    for r in csv.reader(io.StringIO(path.read_bytes().decode("utf-8-sig", errors="replace"))):
        if r and "BLA Number" in r:
            header = r
        elif header and len(r) >= len(header) - 1 and r[2].strip().isdigit():
            rows.append(dict(zip(header, r)))
    _rows = rows
    return rows


def _iso(text: str) -> str | None:
    try:
        return datetime.datetime.strptime(text.strip(), "%d-%b-%y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def fetch_biologic_exclusivity(drug_names: list[str]) -> list[dict]:
    rows = _load()
    today = datetime.date.today().isoformat()
    out = []
    for name in dict.fromkeys(n for n in drug_names if n):
        key = name.strip().lower()
        matches = [r for r in rows if (r.get("Proper Name") or "").strip().lower() == key
                   and (r.get("BLA Type") or "").startswith("351(a)")]
        if not matches:
            continue
        def latest(col: str) -> str | None:
            dates = [d for d in (_iso(r.get(col) or "") for r in matches) if d]
            return max(dates) if dates else None
        first = min((d for d in (_iso(r.get("Date of First Licensure") or "") or _iso(r.get("Approval Date") or "") for r in matches) if d), default=None)
        ref_excl, orphan_excl = latest("Ref. Product Exclusivity Exp. Date") or latest("Exclusivity Expiration Date"), latest("Orphan Exclusivity Exp. Date")
        top = matches[0]
        out.append({
            "drug_name": name,
            "bla": top.get("BLA Number"),
            "brand": top.get("Proprietary Name"),
            "applicant": top.get("Applicant"),
            "first_licensure": first,
            "reference_exclusivity": ref_excl,
            "orphan_exclusivity": orphan_excl,
            "source": f"FDA Purple Book BLA {top.get('BLA Number')}",
            "url": _source_url or DOWNLOADS_PAGE,
            "date_accessed": today,
            "raw_snippet": f"{top.get('Proprietary Name')} ({name}) — BLA {top.get('BLA Number')}, {top.get('Applicant')}; "
                           f"first licensed {first or 'n/a'}; reference-product exclusivity until {ref_excl or 'not listed'}; "
                           f"orphan exclusivity until {orphan_excl or 'not listed'}.",
        })
    return out
