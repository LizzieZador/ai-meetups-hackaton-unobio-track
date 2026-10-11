"""ClinicalTrials.gov API v2 — details of individual trials.

Owner: Clinical Analyst.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Optional, Union

import requests

from sources.cache import atomic_write

API_URL = "https://clinicaltrials.gov/api/v2/studies"
_NON_THERAPY = re.compile(r"placebo|contrast|gadolin|tracer|radioligand for imaging|vehicle", re.I)
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def fetch_clinicaltrials(
    condition: str,
    intervention: str,
    max_results: int = 10,
    status_filter: Optional[Union[str, list[str]]] = None,
    term: str = "",
) -> Optional[list[dict]]:
    """Search trials for a condition + intervention pair, with optional status filter.

    `term` is a free-text filter over the whole record (e.g. a biomarker such
    as "KRAS G12C" in eligibility criteria or arm titles).
    Caches results in cache/ using a SHA-256 hash of the query parameters.
    Returns None on failure — caller must not crash the pipeline.
    """
    params = {"query.cond": condition, "pageSize": max_results}
    if intervention:  # condition-only searches (e.g. "what is in trials for X?") omit it
        params["query.intr"] = intervention
    if term:
        params["query.term"] = term
    if status_filter:
        if isinstance(status_filter, (list, tuple, set)):
            status_val = ",".join(s.strip() for s in status_filter if s.strip())
        else:
            cleaned = str(status_filter).replace("/", ",").replace("|", ",")
            status_val = ",".join(s.strip() for s in cleaned.split(",") if s.strip())
        if status_val:
            params["filter.overallStatus"] = status_val

    # "_schema" is part of the cache key only, never sent to the API: bumping it
    # makes records cached before a new field existed (here: "interventions")
    # refetch once instead of silently lacking it forever.
    param_hash = hashlib.sha256(
        json.dumps({**params, "_schema": 3}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_path = CACHE_DIR / f"clinicaltrials_{param_hash}.json"
    alt_cache_path = CACHE_DIR / f"{param_hash}.json"

    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    if alt_cache_path.exists():
        return json.loads(alt_cache_path.read_text(encoding="utf-8"))

    results = _fetch(params)
    if results is not None:
        atomic_write(cache_path, json.dumps(results, ensure_ascii=False, indent=2))
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
            # The trial's actual investigational products, from the registry's own
            # interventions list — drugs/biologics only, without placebo and
            # without imaging/diagnostic agents (registries file MRI contrast
            # media as type DRUG too, which is not a therapy being tested).
            interventions = list(dict.fromkeys(
                (iv.get("name") or "").strip()
                for iv in (protocol.get("armsInterventionsModule") or {}).get("interventions", [])
                if iv.get("type") in ("DRUG", "BIOLOGICAL")
                and iv.get("name")
                and not _NON_THERAPY.search(iv.get("name", ""))
            ))

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
                "interventions": interventions,
                # The query's own condition string, verbatim — not a per-trial
                # field from the API, but a 100% accurate one since it's the
                # exact value this search was run for (needed by the Pipeline
                # card's "Indication" column, which has no API field of its own).
                "indication": params["query.cond"],
                # Fields used across the underwriting pipeline for provenance:
                "source": f"ClinicalTrials.gov {nct_id}" if nct_id else "ClinicalTrials.gov",
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else API_URL,
                "date_accessed": today,
                "raw_snippet": raw_snippet,
            })
        return results
    except requests.RequestException:
        return None
