"""CMS Medicare spending by drug — what Medicare actually paid for each drug
per year: Part D (pharmacy) and Part B (physician-administered, e.g. infused
biologics). Real, public US spending figures for approved competitors — a
floor on their US revenue (Medicare only, not commercial insurance), not total
sales. Keyless data.cms.gov API, disk-cached per drug name.
"""
import datetime
import hashlib
import json
from pathlib import Path

import requests

# data.cms.gov dataset ids (annual "Spending by Drug" releases)
DATASETS = {
    "Part D": "https://data.cms.gov/data-api/v1/dataset/7e0b4365-fd63-4a29-8f5e-e0ac9f66a81b/data",
    "Part B": "https://data.cms.gov/data-api/v1/dataset/76a714ad-3a2c-43ac-b76d-9dadf8f7d890/data",
}
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


_SALTS = {"maleate", "hydrochloride", "hcl", "mesylate", "besylate", "tosylate", "citrate", "fumarate",
          "succinate", "tartrate", "phosphate", "sulfate", "sodium", "potassium", "acetate", "dimesylate"}


def _same_drug(generic: str, name: str) -> bool:
    """CMS lists salt forms under their own generic name ("Acalabrutinib
    Maleate" — Calquence tablets, $1.7B — vs "Acalabrutinib", the old
    capsules): count those, but not biosimilars like "Rituximab-Abbs"."""
    words = (generic or "").lower().split()
    target = name.lower().split()
    return words[: len(target)] == target and all(w in _SALTS for w in words[len(target):])


def _rows(part: str, name: str) -> list[dict] | None:
    params = {
        "filter[g][condition][path]": "Gnrc_Name",
        "filter[g][condition][operator]": "STARTS_WITH",
        "filter[g][condition][value]": name.strip().title(),
        "size": 50,
    }
    path = CACHE_DIR / f"cms_{hashlib.sha256((part + json.dumps(params)).encode()).hexdigest()[:24]}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = requests.get(DATASETS[part], params=params, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _latest_year(row: dict) -> int | None:
    years = [int(k.rsplit("_", 1)[1]) for k, v in row.items() if k.startswith("Tot_Spndng_") and v not in (None, "")]
    return max(years) if years else None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_medicare_spending(drug_names: list[str]) -> list[dict]:
    today = datetime.date.today().isoformat()
    out = []
    for name in dict.fromkeys(n for n in drug_names if n):
        for part in DATASETS:
            rows = _rows(part, name) or []
            # Part D lists one row per manufacturer plus an "Overall" total;
            # Part B one row per billing code. Sum the totals per brand.
            rows = [
                r for r in rows
                if _same_drug(r.get("Gnrc_Name"), name) and (part == "Part B" or r.get("Mftr_Name") == "Overall")
            ]
            if not rows:
                continue
            year = max(filter(None, (_latest_year(r) for r in rows)), default=None)
            if not year:
                continue
            spend = sum(_num(r.get(f"Tot_Spndng_{year}")) or 0 for r in rows)
            prior = sum(_num(r.get(f"Tot_Spndng_{year - 1}")) or 0 for r in rows)
            benes = sum(_num(r.get(f"Tot_Benes_{year}")) or 0 for r in rows)
            claims = sum(_num(r.get(f"Tot_Clms_{year}")) or 0 for r in rows)
            if not spend:
                continue
            brands = ", ".join(dict.fromkeys(r.get("Brnd_Name") for r in rows if r.get("Brnd_Name")))
            growth = (spend / prior - 1) * 100 if prior else None
            out.append({
                "drug_name": name,
                "brand": brands or None,
                "part": part,
                "year": year,
                "spending_usd": round(spend),
                "prior_year_spending_usd": round(prior) if prior else None,
                "growth_pct": round(growth, 1) if growth is not None else None,
                "claims": round(claims),
                "beneficiaries": round(benes),
                "source": f"CMS Medicare {part} Spending by Drug {year}",
                "url": DATASETS[part] + f"?filter[Gnrc_Name]={rows[0].get('Gnrc_Name')}",
                "date_accessed": today,
                "raw_snippet": f"{brands or name.title()} ({name}) — Medicare {part} {year}: ${spend / 1e6:,.1f}M spending, "
                               f"{round(claims):,} claims, {round(benes):,} beneficiaries"
                               + (f", {growth:+.0f}% vs {year - 1}" if growth is not None else "") + ".",
            })
    return out
