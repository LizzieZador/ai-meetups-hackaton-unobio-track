"""openFDA Drugs@FDA — live point-queries for regulatory designations and
approval precedent on known same-mechanism drugs.

Owner: "Green flag & regulatory advantage monitor" card. Feeds collect_sources()
with evidence of priority review / orphan drug / accelerated approval
precedent for drugs that already share this program's target (the analog
list Open Targets returns) — i.e. "has the FDA already rewarded this
mechanism class with an expedited pathway."

Small, targeted per-drug-name queries against the public API — no key
needed, same live-fetch pattern as the rest of this package.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

import requests

API_URL = "https://api.fda.gov/drug/drugsfda.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def fetch_regulatory_signals(drug_names: list[str], max_per_drug: int = 5) -> list[dict]:
    """Look up Drugs@FDA application records for each drug name.

    Extracts designations explicitly present in Drugs@FDA submission metadata
    (commonly orphan-drug property and priority review) and the earliest recorded approval
    action per application — raw, code-verifiable facts, not an LLM opinion.
    Any name that fails to resolve (typo, never-approved drug, API hiccup) is
    silently skipped; the caller gets whatever the rest resolved to.
    """
    today = datetime.date.today().isoformat()
    results: list[dict] = []
    seen_names: set[str] = set()

    for name in drug_names or []:
        key = (name or "").strip().lower()
        if not key or key in seen_names:
            continue
        seen_names.add(key)

        data = _query_cached(key, max_per_drug)
        if not data:
            continue

        # One record per drug: its earliest-approved application. A drug often
        # has several (capsule NDA, later tablet NDA, ...) and the first one is
        # the true first US approval — taking whichever the API listed first
        # showed Brukinsa as approved in 2025 (its tablet NDA) instead of 2019.
        records = [r for r in (_build_record(name, app, today) for app in data) if r]
        if records:
            results.append(min(records, key=lambda r: r.get("first_approval_date") or "99999999"))

    return results


def _query_cached(name_lower: str, limit: int) -> Optional[list[dict]]:
    """generic_name first; if that finds nothing, the product's active
    ingredient. openFDA's harmonized `openfda.*` block is missing on some
    applications, so a generic_name search silently misses approved drugs —
    e.g. zanubrutinib (Brukinsa, NDA218785) is only found by ingredient."""
    data = _query_one(f'openfda.generic_name:"{name_lower}"', limit)
    if data == []:
        data = _query_one(f'products.active_ingredients.name:"{name_lower.upper()}"', limit)
    return data


def _query_one(search: str, limit: int) -> Optional[list[dict]]:
    params = {"search": search, "limit": limit}
    param_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"fda_drugsfda_{param_hash}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    try:
        resp = requests.get(API_URL, params=params, timeout=15)
        if resp.status_code == 404:
            # A well-formed query with no matching approved application.
            cache_path.write_text("[]", encoding="utf-8")
            return []
        resp.raise_for_status()
        data = resp.json().get("results", [])
    except (requests.RequestException, ValueError):
        # Do not cache outages, rate limits, or malformed responses as an
        # authoritative empty result; the next run should retry the API.
        return None

    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _build_record(name: str, app: dict, today: str) -> Optional[dict]:
    app_number = app.get("application_number", "") or ""
    sponsor = app.get("sponsor_name", "") or ""
    # sponsor_name is openFDA's (often abbreviated) application holder, e.g.
    # "BRISTOL"; manufacturer_name is the full labeler name on the label
    # itself, e.g. "Mirati Therapeutics, Inc" — after an acquisition the two
    # legitimately differ, so both are kept rather than one replacing the other.
    labeler = ((app.get("openfda") or {}).get("manufacturer_name") or [""])[0]
    products = app.get("products", []) or []
    brand_names = sorted({p.get("brand_name") for p in products if p.get("brand_name")})
    routes = sorted({p.get("route") for p in products if p.get("route")})
    dosage_forms = sorted({p.get("dosage_form") for p in products if p.get("dosage_form")})

    designations: set[str] = set()
    approval_dates: list[str] = []
    for sub in app.get("submissions", []) or []:
        if sub.get("submission_status") != "AP":
            continue
        for prop in sub.get("submission_property_type", []) or []:
            code = prop.get("code")
            if code:
                designations.add(code)
        if sub.get("review_priority") == "PRIORITY":
            designations.add("Priority Review")
        date = sub.get("submission_status_date")
        if date:
            approval_dates.append(date)

    if not approval_dates and not designations:
        return None  # nothing approved / no designation on record — not a signal

    first_approval = min(approval_dates) if approval_dates else None
    first_approval_display = (
        f"{first_approval[:4]}-{first_approval[4:6]}-{first_approval[6:]}" if first_approval else "N/A"
    )
    designation_text = ", ".join(sorted(designations)) if designations else "none recorded"
    brand_text = f" ({'/'.join(brand_names)})" if brand_names else ""

    appl_no_digits = re.sub(r"\D", "", app_number)
    url = (
        f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={appl_no_digits}"
        if appl_no_digits
        else API_URL
    )

    snippet = (
        f"{name.title()}{brand_text} — application {app_number or 'unknown'}, sponsor {sponsor or 'unknown'}: "
        f"regulatory designations on record — {designation_text}. "
        f"Earliest recorded approval action: {first_approval_display}."
    )

    return {
        "drug_name": name,
        "application_number": app_number,
        "sponsor": sponsor,
        "labeler": labeler,
        "brand_names": brand_names,
        "routes": routes,
        "dosage_forms": dosage_forms,
        "designations": sorted(designations),
        "first_approval_date": first_approval,
        "source": f"openFDA Drugs@FDA {app_number or name}",
        "url": url,
        "date_accessed": today,
        "raw_snippet": snippet,
    }
