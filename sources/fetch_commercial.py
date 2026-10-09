"""Commercial source — market/epidemiology figures and competitive landscape aggregator.

Owner: Commercial Analyst.
Gathers manual epidemiology/pricing datasets (data/market_data.csv, data/pricing_data.csv)
and integrates AACT competitor landscapes and Open Targets known drugs.

Enforces evidence integrity:
- Only CSV rows with a verified primary source URL and publication year are returned as evidence.
- Rows lacking a primary URL or year are flagged as unverified and excluded from evidence.
- Full support for historical cutoff_date filtering (excludes future publications and Open Targets).
- Preserves backward compatibility with legacy callers (pipeline.py, test_local_run.py).
"""
import csv
import datetime
from pathlib import Path
import re
from typing import Any, Optional

from sources.fetch_aact import fetch_aact
from sources.fetch_opentargets import fetch_opentargets, get_target_context

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_PATH = DATA_DIR / "market_data.csv"
PRICING_CSV_PATH = DATA_DIR / "pricing_data.csv"

INDICATION_SYNONYMS: dict[str, str] = {
    "nsclc": "non-small cell lung cancer",
    "non small cell lung cancer": "non-small cell lung cancer",
    "lung cancer": "non-small cell lung cancer",
    "ad": "alzheimers disease",
    "alzheimer's": "alzheimers disease",
    "alzheimer's disease": "alzheimers disease",
    "alzheimer disease": "alzheimers disease",
    "ms": "multiple sclerosis",
    "t2d": "type 2 diabetes",
    "type 2 diabetes mellitus": "type 2 diabetes",
    "t2dm": "type 2 diabetes",
    "parkinson's": "parkinsons disease",
    "parkinson's disease": "parkinsons disease",
    "parkinson disease": "parkinsons disease",
    "ra": "rheumatoid arthritis",
    "atopic eczema": "atopic dermatitis",
    "eczema": "atopic dermatitis",
}


def _canonical_indication(name: str) -> str:
    """Normalize indication name for robust synonym comparison."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return INDICATION_SYNONYMS.get(cleaned, cleaned)


def _match_indication(query: str, target_name: str) -> bool:
    """Exact or canonical synonym indication matching without silent false substitutions."""
    q_can = _canonical_indication(query)
    t_can = _canonical_indication(target_name)
    if not q_can or not t_can:
        return False
    if q_can == t_can:
        return True

    q_words = set(q_can.split())
    t_words = set(t_can.split())
    # Substring match only if one is a complete subset and shares key nouns
    if (q_words.issubset(t_words) or t_words.issubset(q_words)) and len(q_words & t_words) >= 2:
        return True
    return False


def _is_valid_url(url: Optional[str]) -> bool:
    """Check if string looks like an authentic web URL or DOI link."""
    if not url:
        return False
    u = str(url).strip().lower()
    return u.startswith("http://") or u.startswith("https://") or u.startswith("www.") or u.startswith("doi.org")


def _parse_year(val: Any) -> Optional[int]:
    """Extract a 4-digit year (1900-2099) from string or number."""
    if val is None:
        return None
    s = str(val).strip()
    match = re.search(r"\b(19\d\d|20\d\d)\b", s)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def get_epidemiology_from_csv(indication_query: str) -> Optional[dict]:
    """Legacy helper: return raw CSV row if Indication matches substring."""
    if not CSV_PATH.exists():
        return None

    query = indication_query.lower()
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = row.get("Indication", "").lower()
            if name in query or query in name:
                return row
    return None


def get_epidemiology(indication: str, cutoff_date: Optional[str] = None) -> Optional[dict]:
    """Look up epidemiology data for an indication.

    Enforces evidence requirements:
    - If row lacks primary URL or publication year, it is marked is_evidence=False.
    - If cutoff_date is specified, rows with year > cutoff_date are excluded.
    - If no exact match or explicit synonym exists, returns None (no silent guessing).
    """
    if not CSV_PATH.exists():
        return None

    cutoff_year = None
    if cutoff_date:
        try:
            cutoff_year = int(cutoff_date[:4])
        except (ValueError, TypeError):
            cutoff_year = None

    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_ind = row.get("Indication", "")
            if not _match_indication(indication, raw_ind):
                continue

            # Check year and cutoff
            year_val = _parse_year(row.get("Year") or row.get("Publication_Year"))
            if year_val is None:
                # Check if embedded in source text
                year_val = _parse_year(row.get("Primary_Epidemiology_Source"))

            if cutoff_year and year_val and year_val > cutoff_year:
                # Newer than cutoff date -> exclude
                return None

            primary_url = (
                row.get("Source_URL")
                or row.get("Primary_Source_URL")
                or row.get("url")
                or row.get("URL")
                or ""
            ).strip()

            source_name = row.get("Primary_Epidemiology_Source") or "Epidemiology Database"
            region = row.get("Region") or "US+EU"
            prev = row.get("Prevalence_US_EU", "N/A")
            inc = row.get("Incidence_Annual_US_EU", "N/A")

            # Must have BOTH primary URL and publication year to count as verified evidence
            is_evidence = bool(_is_valid_url(primary_url) and year_val is not None)

            if is_evidence:
                source_label = f"Manual dataset: {source_name}"
                raw_snippet = (
                    f"{raw_ind}: prevalence {prev} patients, "
                    f"annual incidence {inc} patients/year, "
                    f"publication year {year_val}, region {region}."
                )
                url = primary_url
            else:
                source_label = f"Manual dataset: {source_name} (unverified)"
                raw_snippet = (
                    f"Unverified epidemiology for {raw_ind}: prevalence {prev}, "
                    f"annual incidence {inc}, region {region} "
                    f"(lacks verified primary source URL and publication year)."
                )
                url = primary_url if _is_valid_url(primary_url) else ""

            today = datetime.date.today().isoformat()
            return {
                "is_evidence": is_evidence,
                "indication": raw_ind,
                "source": source_label,
                "url": url,
                "date_accessed": today,
                "raw_snippet": raw_snippet,
                "prevalence": prev,
                "incidence": inc,
                "year": year_val,
                "region": region,
            }

    return None


def get_pricing_analogues(indication: str, cutoff_date: Optional[str] = None) -> Optional[dict]:
    """Look up pricing analogue data for an indication from market_data.csv or pricing_data.csv.

    Enforces evidence requirements:
    - If row lacks primary URL or year, it is marked is_evidence=False.
    - If cutoff_date is specified, pricing data published after cutoff is excluded.
    - If no exact match or explicit synonym exists, returns None.
    """
    cutoff_year = None
    if cutoff_date:
        try:
            cutoff_year = int(cutoff_date[:4])
        except (ValueError, TypeError):
            cutoff_year = None

    # Check dedicated pricing CSV first if available
    files_to_check = [PRICING_CSV_PATH, CSV_PATH]
    for pth in files_to_check:
        if not pth.exists():
            continue
        with open(pth, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_ind = row.get("Indication", "")
                if not _match_indication(indication, raw_ind):
                    continue

                cost_raw = (
                    row.get("Avg_Annual_Treatment_Cost_USD")
                    or row.get("Annual_Cost_USD")
                    or row.get("WAC_USD")
                )
                if not cost_raw:
                    continue

                year_val = _parse_year(
                    row.get("Pricing_Year")
                    or row.get("Year")
                    or row.get("Publication_Year")
                )
                if cutoff_year and year_val and year_val > cutoff_year:
                    return None

                pricing_url = (
                    row.get("Pricing_Source_URL")
                    or row.get("Source_URL")
                    or row.get("Primary_Source_URL")
                    or ""
                ).strip()

                drug_name = (
                    row.get("Pricing_Analogue_Drug")
                    or row.get("Drug_Name")
                    or f"Standard-of-care analogue for {raw_ind}"
                )
                # Do NOT fall back to Primary_Epidemiology_Source here — that column
                # names the epidemiology source, not the pricing one; borrowing it
                # mislabels two distinct facts (prevalence vs. drug price) under the
                # same source name, which collapses them into one entry wherever
                # sources are deduplicated/grounded by name (e.g. pipeline.py,
                # schema.parse_claims's valid_sources check).
                source_name = row.get("Pricing_Source") or "Pharmaceutical Pricing Benchmark"
                region = row.get("Region") or "US"

                is_evidence = bool(_is_valid_url(pricing_url) and year_val is not None)

                if is_evidence:
                    source_label = f"Manual dataset: {source_name}"
                    raw_snippet = (
                        f"Pricing analogue for {raw_ind} ({drug_name}): "
                        f"annual treatment cost {cost_raw} USD, "
                        f"year {year_val}, region {region}."
                    )
                    url = pricing_url
                else:
                    source_label = f"Manual dataset: {source_name} (unverified)"
                    raw_snippet = (
                        f"Unverified pricing analogue for {raw_ind} ({drug_name}): "
                        f"annual treatment cost {cost_raw} USD "
                        f"(lacks verified primary source URL and publication year)."
                    )
                    url = pricing_url if _is_valid_url(pricing_url) else ""

                today = datetime.date.today().isoformat()
                return {
                    "is_evidence": is_evidence,
                    "indication": raw_ind,
                    "drug_name": drug_name,
                    "annual_cost_usd": cost_raw,
                    "source": source_label,
                    "url": url,
                    "date_accessed": today,
                    "raw_snippet": raw_snippet,
                    "year": year_val,
                    "region": region,
                }

    return None


def fetch_commercial(indication: str) -> Optional[list[dict]]:
    """Pipeline-compatible interface: returns verified commercial evidence snippets or None."""
    epi = get_epidemiology(indication)
    pricing = get_pricing_analogues(indication)

    records: list[dict] = []
    if epi and epi.get("is_evidence"):
        records.append({
            "source": epi["source"],
            "url": epi["url"],
            "date_accessed": epi["date_accessed"],
            "raw_snippet": epi["raw_snippet"],
        })
    if pricing and pricing.get("is_evidence"):
        records.append({
            "source": pricing["source"],
            "url": pricing["url"],
            "date_accessed": pricing["date_accessed"],
            "raw_snippet": pricing["raw_snippet"],
        })

    return records if records else None


def run_commercial_analysis(indication: str, target: str) -> Optional[dict]:
    """Legacy helper for test_local_run.py: epidemiology claims + naive financial metrics."""
    row = get_epidemiology_from_csv(indication)
    if row is None:
        return None

    today = datetime.date.today().isoformat()
    try:
        incidence = int(row.get("Incidence_Annual_US_EU", 0))
    except (ValueError, TypeError):
        incidence = 0
    try:
        cost = int(row.get("Avg_Annual_Treatment_Cost_USD", 0))
    except (ValueError, TypeError):
        cost = 0

    scenarios = {
        "peak_sales_low": 0.05,
        "peak_sales_base": 0.10,
        "peak_sales_high": 0.20,
    }
    return {
        "claims": [{
            "claim": (
                f"{row.get('Indication')}: prevalence {row.get('Prevalence_US_EU')} and annual "
                f"incidence {row.get('Incidence_Annual_US_EU')} in US+EU."
            ),
            "type": "evidence",
            "section": "commercial_opportunity",
            "source": row.get("Primary_Epidemiology_Source", "Market Data CSV"),
            "date_accessed": today,
        }],
        "financial_metrics": {
            **{k: round(incidence * share * cost / 1e6) for k, share in scenarios.items()},
            "currency": "USD_millions",
        },
    }


def collect_commercial_sources(
    indication: str,
    mechanism: str,
    cutoff_date: Optional[str] = None,
) -> dict:
    """Collect competitive and commercial source records for commercial agent.

    Aggregates:
    1. AACT competitor landscape (with same-mechanism drugs from Open Targets).
    2. Open Targets known drug candidate records (excluded if cutoff_date is set).
    3. Epidemiology dataset (verified as evidence or recorded in notes as unverified).
    4. Pricing analogues dataset (verified as evidence or recorded in notes as unverified).

    Returns:
        dict: {"competitive": list[dict], "commercial": list[dict], "notes": list[str]}
    """
    notes: list[str] = []
    competitive: list[dict] = []
    commercial: list[dict] = []

    # 1. Resolve target context for same-mechanism drugs
    try:
        target_ctx = get_target_context(indication, mechanism)
        same_mech_drugs = target_ctx.get("known_drugs", [])
        target_symbols = (
            ([target_ctx.get("symbol")] if target_ctx.get("symbol") else [])
            + target_ctx.get("synonyms", [])
        )
    except Exception as e:
        notes.append(f"Failed to query target context for '{mechanism}': {e}")
        same_mech_drugs = []
        target_symbols = []

    # 2. Fetch AACT competitor records
    try:
        aact_records = fetch_aact(
            condition=indication,
            intervention=mechanism,
            cutoff_date=cutoff_date,
            same_mechanism_drugs=same_mech_drugs,
            target_symbols=target_symbols,
        ) or []
        competitive.extend(aact_records)
        if not aact_records or any("AACT unavailable / 0 trials" in r.get("raw_snippet", "") for r in aact_records):
            notes.append("AACT unavailable / 0 trials: No matching trials found or database unavailable.")
    except Exception as e:
        notes.append(f"AACT competitive search failed: {e}")
        notes.append("AACT unavailable / 0 trials: Search encountered an exception.")

    # 3. Open Targets (lookahead protection if cutoff_date is specified)
    if cutoff_date:
        notes.append(
            f"Open Targets excluded from competitive evidence due to cutoff_date={cutoff_date} "
            f"(Open Targets GraphQL API does not support historical date filtering)."
        )
    else:
        try:
            ot_records = fetch_opentargets(mechanism, indication) or []
            competitive.extend(ot_records)
        except Exception as e:
            notes.append(f"Open Targets search failed: {e}")

    # 4. Epidemiology
    epi = get_epidemiology(indication, cutoff_date=cutoff_date)
    if epi:
        if epi.get("is_evidence"):
            commercial.append({
                "source": epi["source"],
                "url": epi["url"],
                "date_accessed": epi["date_accessed"],
                "raw_snippet": epi["raw_snippet"],
            })
        else:
            notes.append(
                f"Epidemiology dataset available for '{indication}' but unverified "
                f"(missing primary URL/year): {epi.get('raw_snippet')}"
            )
    else:
        notes.append(f"No epidemiology data available in manual dataset for indication '{indication}'.")

    # 5. Pricing analogues
    pricing = get_pricing_analogues(indication, cutoff_date=cutoff_date)
    if pricing:
        if pricing.get("is_evidence"):
            commercial.append({
                "source": pricing["source"],
                "url": pricing["url"],
                "date_accessed": pricing["date_accessed"],
                "raw_snippet": pricing["raw_snippet"],
            })
        else:
            notes.append(
                f"Pricing analogue dataset available for '{indication}' but unverified "
                f"(missing primary URL/year): {pricing.get('raw_snippet')}"
            )
    else:
        notes.append(f"No pricing analogue available in manual dataset for indication '{indication}'.")

    return {
        "competitive": competitive,
        "commercial": commercial,
        "notes": notes,
    }
