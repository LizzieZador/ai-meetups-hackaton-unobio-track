"""Drug candidates for an indication — the assistant's evidence base for
"which drugs exist / could be considered for X?" questions.

Combines three of our own sources, so every suggestion is a real record:
  * Open Targets — every drug with clinical evidence for the disease, its stage
    for THIS disease, the reports behind that stage, mechanism and targets;
  * openFDA Drugs@FDA — the drug's first US approval and brand (any indication);
  * ClinicalTrials.gov — active registered trials naming it for this condition.

Approval for the disease is only taken as confirmed when a US label (DailyMed)
or at least two independent sources say so: Open Targets also carries
single-source text-mining slips (atomoxetine "approved for Hd" from one PMDA
record, almost certainly AD/HD), which are kept but marked unverified and
ranked last. A mechanism ("BTK inhibitor") doesn't filter — it flags which
candidates act through it, so "what else besides BTK?" still has an answer.
Results are disk-cached per (indication, mechanism).
"""
import datetime
import hashlib
import json
import re
from pathlib import Path

from raw_data import _base_name, stage_rank
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_opentargets import _query_graphql, _resolve_entity
from sources.fetch_regulatory import fetch_regulatory_signals

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
MAX_CANDIDATES = 12
MAX_MECHANISM_MATCHES = 6
ACTIVE_STATUSES = "RECRUITING,ACTIVE_NOT_RECRUITING,NOT_YET_RECRUITING,ENROLLING_BY_INVITATION"

_DISEASE_DRUGS = """query d($id: String!) {
  disease(efoId: $id) {
    id name
    drugAndClinicalCandidates {
      rows {
        maxClinicalStage
        clinicalReports { clinicalStage source }
        drug { id name drugType mechanismsOfAction { rows { mechanismOfAction targets { approvedSymbol } } } }
      }
    }
  }
}"""


def _evidence(row: dict) -> tuple[str | None, str]:
    """(stage to rank by, approval status) from the reports behind a row."""
    reports = row.get("clinicalReports") or []
    approval_sources = {r.get("source") for r in reports if (r.get("clinicalStage") or "").upper() == "APPROVAL"}
    if approval_sources:
        if "DailyMed" in approval_sources:
            return "APPROVAL", "approved for this disease — US label (DailyMed)"
        if len(approval_sources) >= 2:
            return "APPROVAL", f"approved for this disease ({', '.join(sorted(approval_sources))})"
        other = [r.get("clinicalStage") for r in reports if (r.get("clinicalStage") or "").upper() != "APPROVAL"]
        best = min(other, key=stage_rank) if other else None
        return best or "UNVERIFIED", f"approval claimed by a single source only ({next(iter(approval_sources))}) — unverified"
    return row.get("maxClinicalStage"), "not approved for this disease"


def _rank(c: dict) -> int:
    return stage_rank(c["stage"]) if c["stage"] != "UNVERIFIED" else 99


def _acts_through(mechanism: str | None):
    if not mechanism:
        return lambda c: False
    gene = ((_resolve_entity(mechanism, "target") or {}).get("name") or "").upper()
    words = [w for w in re.findall(r"[a-z0-9]{3,}", mechanism.lower())
             if w not in {"inhibitor", "agonist", "antagonist", "antibody", "modulator", "degrader"}]
    return lambda c: bool((gene and gene in c["targets"]) or any(w in (c["mechanism"] or "").lower() for w in words))


def fetch_indication_drugs(indication: str, mechanism: str | None = None) -> list[dict]:
    key = json.dumps({"indication": indication.strip().lower(), "mechanism": (mechanism or "").strip().lower()})
    cache_path = CACHE_DIR / f"indication_drugs_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    disease = _resolve_entity(indication, "disease") or {}
    if not disease.get("id"):
        return []
    data = _query_graphql(_DISEASE_DRUGS, {"id": disease["id"]})
    if data is None:
        return []  # network/API failure — not cached, retried next time
    rows = ((data.get("disease") or {}).get("drugAndClinicalCandidates") or {}).get("rows") or []

    by_base: dict[str, dict] = {}
    for r in rows:
        drug = r.get("drug") or {}
        if not drug.get("name"):
            continue
        moa = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        stage, approval = _evidence(r)
        cand = {
            "name": drug["name"],
            "chembl_id": drug.get("id"),
            "drug_type": drug.get("drugType"),
            "stage": stage,
            "disease_approval": approval,
            "mechanism": ", ".join(dict.fromkeys(m["mechanismOfAction"] for m in moa if m.get("mechanismOfAction"))) or None,
            "targets": sorted({t["approvedSymbol"] for m in moa for t in (m.get("targets") or []) if t.get("approvedSymbol")}),
        }
        base = _base_name(cand["name"])
        kept = by_base.get(base)
        if kept is None or _rank(cand) < _rank(kept) or (_rank(cand) == _rank(kept) and len(cand["name"]) < len(kept["name"])):
            by_base[base] = cand

    trials = fetch_clinicaltrials(indication, "", max_results=100, status_filter=ACTIVE_STATUSES) or []
    acts = _acts_through(mechanism)
    for c in by_base.values():
        base = _base_name(c["name"])
        c["via_queried_mechanism"] = acts(c)
        c["active_trials"] = [t["nctId"] for t in trials if any(base and base in _base_name(iv) for iv in t.get("interventions") or [])]

    # A balanced picture: established options AND the investigational pipeline.
    # Ranking purely by stage filled every slot with approved drugs (in MS, a
    # dozen of them), hiding exactly the late-stage assets an investor asks about.
    pool = [c for c in by_base.values() if not c["via_queried_mechanism"]]
    approved = sorted((c for c in pool if c["stage"] == "APPROVAL"), key=lambda c: -len(c["active_trials"]))
    pipeline = sorted(
        (c for c in pool if c["stage"] not in ("APPROVAL", "UNVERIFIED")),
        key=lambda c: (_rank(c), -len(c["active_trials"])),
    )
    matches = sorted((c for c in by_base.values() if c["via_queried_mechanism"]), key=_rank)
    half = MAX_CANDIDATES // 2
    candidates = matches[:MAX_MECHANISM_MATCHES] + approved[:half] + pipeline[:half]

    fda = {(r.get("drug_name") or "").lower(): r for r in fetch_regulatory_signals([c["name"] for c in candidates])}
    today = datetime.date.today().isoformat()
    out = []
    for c in candidates:
        reg = fda.get(c["name"].lower())
        first = reg.get("first_approval_date") if reg else None
        out.append({
            **c,
            "disease": disease.get("name"),
            "us_first_approval": f"{first[:4]}-{first[4:6]}-{first[6:]}" if first else None,
            "us_brand": ", ".join(reg.get("brand_names") or []) if reg else None,
            "source": f"Open Targets drug {c['chembl_id']}",
            "url": f"https://platform.opentargets.org/drug/{c['chembl_id']}",
            "date_accessed": today,
        })
    cache_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out
