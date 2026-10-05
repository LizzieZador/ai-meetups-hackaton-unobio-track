"""Open Targets source module — GraphQL API, free, no API key.

Owner: Науковий аналітик (+ Комерційний аналітик — known drugs = competitors).
"""
import datetime
from typing import Optional

import requests

from .cache import cached

API_URL = "https://api.platform.opentargets.org/api/v4/graphql"

SEARCH_QUERY = """
query search($q: String!) {
  search(queryString: $q, entityNames: ["target", "disease"]) {
    hits { id name entity }
  }
}
"""

ASSOCIATION_QUERY = """
query targetDisease($targetId: String!, $diseaseId: String!) {
  target(ensemblId: $targetId) {
    approvedSymbol
    drugAndClinicalCandidates {
      rows { maxClinicalStage drug { name } }
    }
  }
  disease(efoId: $diseaseId) {
    name
  }
}
"""


def fetch_opentargets(mechanism: str, indication: str) -> Optional[list[dict]]:
    """Look up target-disease association + known drugs on the target.

    Returns None on failure — caller must not crash the pipeline.
    """
    key = f"{mechanism}__{indication}"
    return cached("opentargets", key, lambda: _fetch(mechanism, indication))


def _fetch(mechanism: str, indication: str) -> Optional[list[dict]]:
    try:
        target_id = _resolve_id(mechanism, "target")
        disease_id = _resolve_id(indication, "disease")
        if not target_id or not disease_id:
            return []

        resp = requests.post(
            API_URL,
            json={
                "query": ASSOCIATION_QUERY,
                "variables": {"targetId": target_id, "diseaseId": disease_id},
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            return None
        data = body["data"]
        today = datetime.date.today().isoformat()

        target = data.get("target") or {}
        disease = data.get("disease") or {}
        known_drugs = (target.get("drugAndClinicalCandidates") or {}).get("rows", [])
        drug_names = [d["drug"]["name"] for d in known_drugs if d.get("drug")]
        # Structured analogs list (name + stage) for direct, non-LLM display —
        # raw_snippet below carries the same info as text for the agent prompt.
        analogs = [
            {"name": d["drug"]["name"], "stage": d.get("maxClinicalStage")}
            for d in known_drugs
            if d.get("drug")
        ]

        return [{
            "source": f"Open Targets {target_id}-{disease_id}",
            "url": f"https://platform.opentargets.org/evidence/{target_id}/{disease_id}",
            "date_accessed": today,
            "raw_snippet": (
                f"Target {target.get('approvedSymbol')} vs {disease.get('name')}; "
                f"known drugs on this target: {drug_names}"
            ),
            "known_drugs": analogs,
        }]
    except (requests.RequestException, KeyError, TypeError):
        return None


def _resolve_id(term: str, entity: str) -> Optional[str]:
    resp = requests.post(API_URL, json={"query": SEARCH_QUERY, "variables": {"q": term}}, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        return None
    hits = body["data"]["search"]["hits"]
    for hit in hits:
        if hit["entity"] == entity:
            return hit["id"]
    return None
