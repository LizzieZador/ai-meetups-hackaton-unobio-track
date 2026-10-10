"""FDA Orange Book — listed US patents (with expiration dates) and regulatory
exclusivities for approved NDAs.

Lens.org (fetch_lens.py) has no patent-term field at all, so expiration dates
come from here: the Orange Book is the FDA's own list of patents an NDA holder
has certified as covering the drug, each with its expiration date, plus the
statutory exclusivities (NCE, orphan, new indication) — together these are
what actually decides when generics can enter. Coverage is therefore US-only
and approved-NDA-only: pending or non-US filings stay "Not available".

The data ships as one public zip (~1 MB, tilde-delimited text files). It is
cached on disk and refreshed every REFRESH_DAYS; if a refresh fails, the stale
copy is used, and with no copy at all the source simply returns [].
"""
import csv
import datetime
import io
import re
import time
import zipfile
from pathlib import Path

import requests

DATA_URL = "https://www.fda.gov/media/76860/download"
CACHE_PATH = Path(__file__).resolve().parent.parent / "cache" / "orange_book.zip"
REFRESH_DAYS = 30
APP_URL = "https://www.accessdata.fda.gov/scripts/cder/ob/results_product.cfm?Appl_Type={t}&Appl_No={n}"

_tables: dict | None = None


def _ensure_zip() -> Path | None:
    fresh = CACHE_PATH.exists() and (time.time() - CACHE_PATH.stat().st_mtime) < REFRESH_DAYS * 86400
    if fresh:
        return CACHE_PATH
    try:
        # fda.gov rejects requests without a browser-like User-Agent.
        resp = requests.get(DATA_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        zipfile.ZipFile(io.BytesIO(resp.content)).testzip()
        CACHE_PATH.parent.mkdir(exist_ok=True)
        CACHE_PATH.write_bytes(resp.content)
    except (requests.RequestException, zipfile.BadZipFile) as e:
        print(f"[Orange Book] refresh failed ({e}); using cached copy if present")
    return CACHE_PATH if CACHE_PATH.exists() else None


def _load() -> dict | None:
    global _tables
    if _tables is not None:
        return _tables
    path = _ensure_zip()
    if not path:
        return None
    with zipfile.ZipFile(path) as z:
        def rows(name: str) -> list[dict]:
            with z.open(name) as f:
                return list(csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter="~"))

        by_appl: dict[str, dict] = {}
        for r in rows("products.txt"):
            entry = by_appl.setdefault(r["Appl_No"], {"products": [], "patents": {}, "exclusivities": {}})
            entry["products"].append(r)
        for r in rows("patent.txt"):
            if r.get("Delist_Flag") == "Y":
                continue
            entry = by_appl.setdefault(r["Appl_No"], {"products": [], "patents": {}, "exclusivities": {}})
            entry["patents"].setdefault(r["Patent_No"], r)
        for r in rows("exclusivity.txt"):
            entry = by_appl.setdefault(r["Appl_No"], {"products": [], "patents": {}, "exclusivities": {}})
            entry["exclusivities"].setdefault((r["Exclusivity_Code"], r["Exclusivity_Date"]), r)
    _tables = by_appl
    return _tables


def _iso(date_text: str) -> str | None:
    try:
        return datetime.datetime.strptime(date_text.strip(), "%b %d, %Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


_SALTS = {"hydrochloride", "hcl", "sodium", "potassium", "maleate", "mesylate", "besylate", "tosylate",
          "citrate", "fumarate", "succinate", "tartrate", "phosphate", "sulfate", "acetate"}


def _ingredient_base(text: str) -> str:
    return " ".join(w for w in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split() if w not in _SALTS)


def first_us_approval(drug_name: str) -> tuple[str, str] | None:
    """(YYYYMMDD, trade name) of the earliest NDA product for this ingredient.

    openFDA misses old originators (no harmonized `openfda` block, and no
    record of the original approval — riluzole's earliest is a 2003
    supplement, while Rilutek was approved in 1995); the Orange Book lists every
    product's approval date. "Approved Prior to Jan 1, 1982" maps to 19811231.
    Single-ingredient products only, so a combination product's date never
    stands in for the drug itself. Biologics (BLAs) aren't in the Orange Book.
    """
    tables = _load()
    target = _ingredient_base(drug_name)
    if not tables or not target:
        return None
    best = None
    for entry in tables.values():
        for p in entry["products"]:
            if p.get("Appl_Type") != "N" or ";" in p.get("Ingredient", "") or _ingredient_base(p.get("Ingredient")) != target:
                continue
            text = (p.get("Approval_Date") or "").strip()
            date = "19811231" if text.startswith("Approved Prior") else (_iso(text) or "").replace("-", "")
            if date and (best is None or date < best[0]):
                best = (date, (p.get("Trade_Name") or "").strip())
    return best


def fetch_orange_book_patents(regulatory_records: list[dict]) -> list[dict]:
    """One record per approved application found in the Orange Book."""
    tables = _load()
    if not tables:
        return []
    today = datetime.date.today().isoformat()
    out: list[dict] = []
    seen: set[str] = set()
    for reg in regulatory_records or []:
        appl = re.sub(r"\D", "", reg.get("application_number") or "").lstrip("0")
        entry = tables.get(appl) or tables.get(appl.zfill(6))
        if not appl or appl in seen or not entry:
            continue
        seen.add(appl)
        product = entry["products"][0] if entry["products"] else {}
        appl_type = (product.get("Appl_Type") or "N").strip() or "N"
        patents = sorted(
            (
                {
                    "patent_no": p["Patent_No"],
                    "expires": _iso(p["Patent_Expire_Date_Text"]),
                    "drug_substance": p.get("Drug_Substance_Flag") == "Y",
                    "drug_product": p.get("Drug_Product_Flag") == "Y",
                    "use_code": p.get("Patent_Use_Code") or None,
                }
                for p in entry["patents"].values()
            ),
            key=lambda p: p["expires"] or "",
        )
        exclusivities = sorted(
            ({"code": e["Exclusivity_Code"], "expires": _iso(e["Exclusivity_Date"])} for e in entry["exclusivities"].values()),
            key=lambda e: e["expires"] or "",
        )
        last_patent = max((p["expires"] for p in patents if p["expires"]), default=None)
        out.append({
            "drug_name": reg.get("drug_name"),
            "application_number": reg.get("application_number"),
            "trade_name": product.get("Trade_Name"),
            "applicant_full_name": (product.get("Applicant_Full_Name") or "").strip() or None,
            "patents": patents,
            "exclusivities": exclusivities,
            "source": f"FDA Orange Book {reg.get('application_number') or appl}",
            "url": APP_URL.format(t=appl_type, n=appl.zfill(6)),
            "date_accessed": today,
            "raw_snippet": (
                f"{(reg.get('drug_name') or '').title()} ({product.get('Trade_Name') or 'trade name unknown'}) — "
                f"{len(patents)} Orange Book-listed US patent(s), latest expiring {last_patent or 'unknown'}; "
                f"exclusivities: {', '.join(f'{e['code']} until {e['expires']}' for e in exclusivities) or 'none listed'}."
            ),
        })
    return out
