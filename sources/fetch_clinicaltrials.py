"""ClinicalTrials.gov API v2 — details of individual trials.

Owner: Клінічний аналітик.
"""
import datetime
from typing import Optional

import requests

from .cache import cached

API_URL = "https://clinicaltrials.gov/api/v2/studies"


def fetch_clinicaltrials(condition: str, intervention: str, max_results: int = 10) -> Optional[list[dict]]:
    """Search trials for a condition + intervention pair.

    Returns None on failure — caller must not crash the pipeline.
    """
    key = f"{condition}__{intervention}"
    return cached("clinicaltrials", key, lambda: _fetch(condition, intervention, max_results))


def _fetch(condition: str, intervention: str, max_results: int) -> Optional[list[dict]]:
    try:
        resp = requests.get(
            API_URL,
            params={
                "query.cond": condition,
                "query.intr": intervention,
                "pageSize": max_results,
            },
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
            nct_id = ident.get("nctId", "")
            results.append({
                "source": f"ClinicalTrials.gov {nct_id}",
                "url": f"https://clinicaltrials.gov/study/{nct_id}",
                "date_accessed": today,
                "raw_snippet": (
                    f"{ident.get('briefTitle', '')} — phase {design.get('phases', [])}, "
                    f"status {status.get('overallStatus', '')}"
                ),
            })
        return results
    except requests.RequestException:
        return None
