"""Open Targets drug profiles — per-drug target, modality and stage, plus
competitor drugs found in the ClinicalTrials.gov interventions list.

Open Targets' target-level drug list (fetch_opentargets.py's
drugAndClinicalCandidates) only holds drugs with a curated mechanism of action
on that target — for a fast-moving class like KRAS G12C that is a handful of
names, while the trial registry already shows many more late-stage agents.
This module looks every name up by itself, so the competitive picture can use:

  * the drug's own targets (instead of repeating the user's query string),
  * its modality (Open Targets drugType — "Small molecule", "Antibody", ...),
  * its maximum clinical stage,

and adds registry interventions that pass a conservative check:
  1. an EXACT (normalized) name match to an Open Targets drug entity — fuzzy
     hits are rejected (a ChEMBL fuzzy search maps "HRS-7058" to fosazepam);
  2. its curated targets include the queried gene, OR it has no curated
     mechanism yet (typical for new agents) — a drug with a curated mechanism
     on some OTHER target (docetaxel, durvalumab — trial comparators) is
     excluded. Uncurated ones are labelled as such, never given a target.

No key needed; every lookup is disk-cached (a network failure is not cached).
"""
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

import requests

from sources.cache import atomic_write

API_URL = "https://api.platform.opentargets.org/api/v4/graphql"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
MAX_TRIAL_CANDIDATES = 12

_SEARCH = """query s($t: String!) {
  search(queryString: $t, entityNames: ["drug"], page: {size: 5, index: 0}) { hits { id name } }
}"""
_DRUG = """query d($id: String!) {
  drug(chemblId: $id) {
    id name drugType maximumClinicalStage
    mechanismsOfAction { rows { mechanismOfAction targets { approvedSymbol } } }
  }
}"""

# Dosage-form / salt words the registry appends to a drug name
# ("Glecirasib Citrate Tablets") that are not part of the drug's own name.
_FORM_WORDS = {
    "tablet", "tablets", "capsule", "capsules", "injection", "infusion", "oral", "solution",
    "citrate", "hydrochloride", "hcl", "mesylate", "sodium", "potassium", "adipate",
    "sulfate", "maleate", "tosylate", "phosphate", "for", "suspension",
}


def _norm(name: str) -> str:
    words = re.sub(r"[^\w\s-]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in _FORM_WORDS).strip()


def _gql(query: str, variables: dict) -> Optional[dict]:
    try:
        resp = requests.post(API_URL, json={"query": query, "variables": variables}, timeout=15)
        if resp.status_code != 200:
            return None
        body = resp.json()
        return None if body.get("errors") else body.get("data")
    except (requests.RequestException, ValueError):
        return None


def fetch_drug_profile(name: str) -> Optional[dict]:
    """Open Targets profile for an exact drug-name match, else None."""
    key = _norm(name)
    if not key:
        return None
    cache_path = CACHE_DIR / f"ot_drug_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    search = _gql(_SEARCH, {"t": key})
    if search is None:
        return None  # network/API failure — don't cache, retry next run
    hit = next((h for h in search["search"]["hits"] if _norm(h["name"]) == key), None)
    profile = None
    if hit:
        detail = _gql(_DRUG, {"id": hit["id"]})
        if detail is None:
            return None
        drug = detail.get("drug") or {}
        rows = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        profile = {
            "name": drug.get("name") or hit["name"],
            "chembl_id": drug.get("id") or hit["id"],
            "drug_type": drug.get("drugType"),
            "stage": drug.get("maximumClinicalStage"),
            "mechanism": ", ".join(dict.fromkeys(r["mechanismOfAction"] for r in rows if r.get("mechanismOfAction"))) or None,
            "targets": sorted({t["approvedSymbol"] for r in rows for t in (r.get("targets") or []) if t.get("approvedSymbol")}),
        }
    atomic_write(cache_path, json.dumps(profile, ensure_ascii=False))
    return profile


def _record(profile: dict, origin: str, nct_ids: list[str], today: str) -> dict:
    targets = ", ".join(profile["targets"]) or "not curated"
    where = f"; listed as an intervention in {', '.join(nct_ids)}" if nct_ids else ""
    return {
        **profile,
        "origin": origin,
        "nct_ids": nct_ids,
        "source": f"Open Targets drug {profile['chembl_id']}",
        "url": f"https://platform.opentargets.org/drug/{profile['chembl_id']}",
        "date_accessed": today,
        "raw_snippet": (
            f"{profile['name']} ({profile['chembl_id']}) — type: {profile.get('drug_type') or 'unknown'}, "
            f"max clinical stage: {profile.get('stage') or 'unknown'}, "
            f"mechanism: {profile.get('mechanism') or 'not curated'}, targets: {targets}{where}."
        ),
    }


def fetch_competitor_profiles(target_symbol: str, analog_names: list[str], trial_records: list[dict]) -> list[dict]:
    """Profiles for the Open Targets analogs, plus verified registry competitors."""
    today = datetime.date.today().isoformat()
    target = (target_symbol or "").upper()
    out: list[dict] = []
    seen: set[str] = set()

    for name in analog_names:
        p = fetch_drug_profile(name)
        if p and _norm(p["name"]) not in seen:
            seen.add(_norm(p["name"]))
            out.append(_record(p, "open_targets_target", [], today))

    trials_by_name: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    for r in trial_records or []:
        for iv in r.get("interventions") or []:
            key = _norm(iv)
            if key and key not in seen:
                trials_by_name.setdefault(key, []).append(r.get("nctId") or "")
                display.setdefault(key, iv)

    for key in list(trials_by_name)[:MAX_TRIAL_CANDIDATES]:
        p = fetch_drug_profile(display[key])
        if not p or _norm(p["name"]) in seen:
            continue
        if p["targets"] and target not in p["targets"]:
            continue  # curated on a different target: a comparator/combination partner
        seen.add(_norm(p["name"]))
        out.append(_record(p, "trial_registry", [n for n in trials_by_name[key] if n], today))
    return out
