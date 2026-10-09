"""ClinicalTrials.gov API v2 — details of individual trials.

Owner: Clinical Analyst.
"""
import datetime
import hashlib
import json
from pathlib import Path
from typing import Optional, Union

import requests

API_URL = "https://clinicaltrials.gov/api/v2/studies"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def fetch_clinicaltrials(
    condition: str,
    intervention: str,
    max_results: int = 10,
    status_filter: Optional[Union[str, list[str]]] = None,
) -> Optional[list[dict]]:
    """Search trials for a condition + intervention pair, with optional status filter.

    Caches results in cache/ using a SHA-256 hash of the query parameters.
    Returns None on failure — caller must not crash the pipeline.
    """
    params = {
        "query.cond": condition,
        "query.intr": intervention,
        "pageSize": max_results,
    }
    if status_filter:
        if isinstance(status_filter, (list, tuple, set)):
            status_val = ",".join(s.strip() for s in status_filter if s.strip())
        else:
            cleaned = str(status_filter).replace("/", ",").replace("|", ",")
            status_val = ",".join(s.strip() for s in cleaned.split(",") if s.strip())
        if status_val:
            params["filter.overallStatus"] = status_val

    param_hash = hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"clinicaltrials_{param_hash}.json"
    alt_cache_path = CACHE_DIR / f"{param_hash}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if alt_cache_path.exists():
        return json.loads(alt_cache_path.read_text(encoding="utf-8"))

    results = _fetch(params)
    if results is not None:
        cache_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def _fetch(params: dict) -> Optional[list[dict]]:
    try:
        resp = requests.get(
            API_URL,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
        today = datetime.date.today().isoformat()

        results = []
        for study in studies:
            protocol = study.get("protocolSection", {})
            ident = protocol.get("identificationModule", {})
            status = protocol.get("statusModule", {})
            design = protocol.get("designModule", {})
            sponsor = protocol.get("sponsorCollaboratorsModule", {})

            nct_id = ident.get("nctId", "")
            brief_title = ident.get("briefTitle", "")
            phases = design.get("phases", [])
            overall_status = status.get("overallStatus", "")
            why_stopped = status.get("whyStopped", "")
            enrollment_count = (design.get("enrollmentInfo") or {}).get("count")
            lead_sponsor_name = (sponsor.get("leadSponsor") or {}).get("name", "")
            start_date = (status.get("startDateStruct") or {}).get("date", "")

            phases_str = ", ".join(phases) if phases else "N/A"

            snippet_parts = [
                f"{brief_title} — phases: {phases_str}",
                f"status: {overall_status}",
            ]
            if why_stopped:
                snippet_parts.append(f"whyStopped: {why_stopped}")
            if enrollment_count is not None:
                snippet_parts.append(f"enrollment: {enrollment_count}")
            if lead_sponsor_name:
                snippet_parts.append(f"leadSponsor: {lead_sponsor_name}")
            if start_date:
                snippet_parts.append(f"startDate: {start_date}")

            raw_snippet = ", ".join(snippet_parts)

            results.append({
                "nctId": nct_id,
                "briefTitle": brief_title,
                "phases": phases,
                "overallStatus": overall_status,
                "whyStopped": why_stopped,
                "enrollmentCount": enrollment_count,
                "leadSponsor": lead_sponsor_name,
                "startDate": start_date,
                # Fields used across the underwriting pipeline for provenance:
                "source": f"ClinicalTrials.gov {nct_id}" if nct_id else "ClinicalTrials.gov",
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else API_URL,
                "date_accessed": today,
                "raw_snippet": raw_snippet,
            })
        return results
    except requests.RequestException:
        return None


if __name__ == "__main__":
    import sys

    cond = sys.argv[1] if len(sys.argv) > 1 else "NSCLC"
    intr = sys.argv[2] if len(sys.argv) > 2 else "pembrolizumab"
    stat = sys.argv[3] if len(sys.argv) > 3 else None
    trials = fetch_clinicaltrials(cond, intr, max_results=3, status_filter=stat)
    print(json.dumps(trials, indent=2))