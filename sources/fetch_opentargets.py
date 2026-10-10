"""Open Targets source module — GraphQL API on platform.opentargets.org.

Fetches target-disease association scores, genetic/somatic evidence scores,
known drug candidates, target safety liabilities, and target synonyms.

Owner: Scientific Analyst (+ Commercial Analyst — known drugs = competitors).
"""
import datetime
import json
import re
from typing import Optional

import requests

from .cache import CACHE_DIR, _slug

API_URL = "https://api.platform.opentargets.org/api/v4/graphql"

SEARCH_QUERY = """
query searchEntity($term: String!) {
  search(queryString: $term, entityNames: ["target", "disease"], page: { size: 5, index: 0 }) {
    total
    hits {
      id
      name
      entity
      description
    }
  }
}
"""

TARGET_DISEASE_QUERY = """
query targetDiseaseDetail($targetId: String!, $diseaseId: String!) {
  target(ensemblId: $targetId) {
    id
    approvedSymbol
    approvedName
    symbolSynonyms { label }
    nameSynonyms { label }
    safetyLiabilities {
      event
      biosamples {
        cellFormat
        tissueLabel
      }
      effects {
        direction
        dosing
      }
    }
    drugAndClinicalCandidates {
      rows {
        maxClinicalStage
        drug {
          id
          name
          mechanismsOfAction {
            rows {
              mechanismOfAction
            }
          }
        }
      }
    }
    associatedDiseases(Bs: [$diseaseId]) {
      count
      rows {
        disease {
          id
          name
        }
        score
        datatypeScores {
          id
          score
        }
      }
    }
  }
  disease(efoId: $diseaseId) {
    id
    name
  }
}
"""


def fetch_opentargets(
    mechanism: str, indication: str, cutoff_date: Optional[str] = None
) -> Optional[list[dict]]:
    """Look up target-disease association and return evidence records for the pipeline.

    Args:
        mechanism: Gene/protein target or mechanism term (e.g., 'KRAS G12C', 'EGFR').
        indication: Disease or condition term (e.g., 'NSCLC', 'non-small cell lung cancer').
        cutoff_date: Optional YYYY-MM-DD. When set, present-day Open Targets records
                     are excluded for LLM evidence (due to lack of date-filtering),
                     returning an explanatory notice.

    Returns:
        List of evidence records {source, url, date_accessed, raw_snippet, ...}
        or None on error (never cached).
    """
    if not mechanism or not indication:
        return []

    # Cache key must match fetch_aact expectations: opentargets_<slug(mechanism__indication)>.json
    base_key = f"{mechanism}__{indication}"
    if cutoff_date:
        cache_key = f"{base_key}__cutoff_{cutoff_date}"
    else:
        cache_key = base_key

    cache_file = CACHE_DIR / f"opentargets_{_slug(cache_key)}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    records = _fetch(mechanism.strip(), indication.strip(), cutoff_date)
    if records is not None:
        try:
            cache_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    return records


def get_target_context(indication: str, mechanism: str) -> dict:
    """Extract structural target identifiers, synonyms, and known drugs for code consumers.

    Used by collect_scientific_sources and fetch_aact to expand search terms
    and detect same-mechanism competitor drugs.
    """
    target_res = _resolve_entity(mechanism, "target")
    disease_res = _resolve_entity(indication, "disease")

    if not target_res.get("id"):
        return {
            "ensembl_id": None,
            "efo_id": disease_res.get("id"),
            "symbol": None,
            "synonyms": [],
            "known_drugs": [],
            "ambiguous": target_res.get("ambiguous", False),
            "candidates": target_res.get("candidates", []),
        }

    target_id = target_res["id"]
    disease_id = disease_res.get("id") or ""

    # Fetch target details directly
    raw_data = _query_graphql(TARGET_DISEASE_QUERY, {"targetId": target_id, "diseaseId": disease_id})
    if not raw_data or not raw_data.get("target"):
        return {
            "ensembl_id": target_id,
            "efo_id": disease_id,
            "symbol": target_res.get("name"),
            "synonyms": [],
            "known_drugs": [],
            "ambiguous": False,
            "candidates": [],
        }

    target = raw_data["target"]
    synonyms = _extract_synonyms(target)
    drugs = _extract_drug_candidates(target)
    drug_names = [d["name"] for d in drugs if d.get("name")]

    return {
        "ensembl_id": target_id,
        "efo_id": disease_id,
        "symbol": target.get("approvedSymbol") or target_res.get("name"),
        "synonyms": synonyms,
        "known_drugs": drug_names,
        "ambiguous": False,
        "candidates": [],
    }


def _fetch(mechanism: str, indication: str, cutoff_date: Optional[str]) -> Optional[list[dict]]:
    """Internal GraphQL executor building atomic evidence records."""
    target_res = _resolve_entity(mechanism, "target")
    disease_res = _resolve_entity(indication, "disease")

    today = datetime.date.today().isoformat()

    # Ambiguity handling
    if target_res.get("ambiguous") or disease_res.get("ambiguous"):
        ambiguous_records = []
        if target_res.get("ambiguous"):
            cands = ", ".join(f"{c['name']} ({c['id']})" for c in target_res.get("candidates", [])[:3])
            ambiguous_records.append({
                "source": "Open Targets Resolution",
                "url": "https://platform.opentargets.org/",
                "date_accessed": today,
                "raw_snippet": f"Open Targets target resolution was ambiguous for '{mechanism}'. Top candidates: {cands}.",
            })
        if disease_res.get("ambiguous"):
            cands = ", ".join(f"{c['name']} ({c['id']})" for c in disease_res.get("candidates", [])[:3])
            ambiguous_records.append({
                "source": "Open Targets Resolution",
                "url": "https://platform.opentargets.org/",
                "date_accessed": today,
                "raw_snippet": f"Open Targets disease resolution was ambiguous for '{indication}'. Top candidates: {cands}.",
            })
        return ambiguous_records

    target_id = target_res.get("id")
    disease_id = disease_res.get("id")

    if not target_id or not disease_id:
        return []

    evidence_url = f"https://platform.opentargets.org/evidence/{target_id}/{disease_id}"
    target_url = f"https://platform.opentargets.org/target/{target_id}"

    data = _query_graphql(TARGET_DISEASE_QUERY, {"targetId": target_id, "diseaseId": disease_id})
    if data is None:
        return None

    target = data.get("target") or {}
    disease = data.get("disease") or {}

    symbol = target.get("approvedSymbol") or mechanism
    disease_name = disease.get("name") or indication

    analogs = _extract_drug_candidates(target)
    drug_names = [d["name"] for d in analogs if d.get("name")]

    # If cutoff_date is active: Open Targets is a present-day snapshot, so we exclude
    # present-day scores from LLM evidence, but retain the known_drugs compatibility field for code.
    if cutoff_date:
        return [{
            "source": f"Open Targets Platform {target_id}-{disease_id}",
            "url": evidence_url,
            "date_accessed": today,
            "raw_snippet": (
                f"Open Targets evidence for {symbol} in {disease_name} was excluded from the clinical/scientific thesis "
                f"because cutoff_date={cutoff_date} was specified, and Open Targets lacks historical date filtering. "
                f"known drugs on this target: {drug_names}"
            ),
            "known_drugs": analogs,
            "cutoff_excluded": True,
        }]

    records: list[dict] = []

    # 1. Overall target-disease association score & datatype breakdown
    assoc_rows = (target.get("associatedDiseases") or {}).get("rows", [])
    if assoc_rows:
        assoc = assoc_rows[0]
        overall_score = round(float(assoc.get("score") or 0.0), 3)
        records.append({
            "source": f"Open Targets {target_id}-{disease_id}",
            "url": evidence_url,
            "date_accessed": today,
            "raw_snippet": (
                f"Open Targets overall association score for {symbol} and {disease_name} is {overall_score} "
                f"(range: 0.0 to 1.0)."
            ),
        })

        for dt in assoc.get("datatypeScores") or []:
            dt_id = dt.get("id")
            score = round(float(dt.get("score") or 0.0), 3)
            if score > 0.0:
                records.append({
                    "source": f"Open Targets {target_id}-{disease_id}",
                    "url": evidence_url,
                    "date_accessed": today,
                    "raw_snippet": (
                        f"Open Targets evidence score for {symbol} in {disease_name} via datatype '{dt_id}' "
                        f"is {score} (0.0 to 1.0)."
                    ),
                })
    else:
        records.append({
            "source": f"Open Targets {target_id}-{disease_id}",
            "url": evidence_url,
            "date_accessed": today,
            "raw_snippet": (
                f"Open Targets records no direct target-disease association between {symbol} and {disease_name}."
            ),
        })

    # 2. Known drug candidates on target
    for drug in analogs[:6]:
        d_name = drug.get("name")
        stage = drug.get("stage") or "unspecified stage"
        mech = drug.get("mechanism") or "unspecified mechanism"
        records.append({
            "source": f"Open Targets Drug {d_name}",
            "url": target_url,
            "date_accessed": today,
            "raw_snippet": (
                f"Known drug candidate against {symbol}: {d_name} (maximum clinical stage: {stage}, "
                f"mechanism of action: {mech})."
            ),
        })

    # 3. Target safety liabilities
    safety = target.get("safetyLiabilities") or []
    if safety:
        for s in safety[:5]:
            event = s.get("event") or "unspecified event"
            effects = [e.get("direction") for e in (s.get("effects") or []) if e.get("direction")]
            eff_str = f" with effect: {', '.join(effects)}" if effects else ""
            records.append({
                "source": f"Open Targets Safety {symbol}",
                "url": target_url,
                "date_accessed": today,
                "raw_snippet": f"Target safety liability for {symbol}: event '{event}'{eff_str}.",
            })
    else:
        records.append({
            "source": f"Open Targets Safety {symbol}",
            "url": target_url,
            "date_accessed": today,
            "raw_snippet": f"No target safety liabilities reported for {symbol} in Open Targets.",
        })

    # 4. Target synonyms
    synonyms = _extract_synonyms(target)
    if synonyms:
        records.append({
            "source": f"Open Targets Target {symbol}",
            "url": target_url,
            "date_accessed": today,
            "raw_snippet": f"Target {symbol} approved name '{target.get('approvedName', '')}' and synonyms: {', '.join(synonyms[:8])}.",
        })

    # 5. Mandatory compatibility record for fetch_aact and raw_data.py
    # fetch_aact._lookup_opentargets_known_drugs searches for: "known drugs on this target: [Python-list]"
    records.append({
        "source": f"Open Targets {target_id}-{disease_id}",
        "url": evidence_url,
        "date_accessed": today,
        "raw_snippet": (
            f"Target {symbol} vs {disease_name}; known drugs on this target: {drug_names}"
        ),
        "known_drugs": analogs,
    })

    return records


_PHARMA_WORDS = re.compile(
    r"\b(inhibitors?|agonists?|antagonists?|antibod(?:y|ies)|blockers?|modulators?|degraders?|"
    r"activators?|therapy|therapies|targeted|small[- ]molecule|bispecific|ADC)\b",
    re.I,
)
_TARGET_SYNONYMS_QUERY = """query t($ids: [String!]!) {
  targets(ensemblIds: $ids) { id approvedSymbol symbolSynonyms { label } nameSynonyms { label } }
}"""


def _clean_target_term(term: str) -> str:
    """'anti-PD-1 antibody' -> 'PD-1', 'GLP-1 receptor agonist' -> 'GLP-1 receptor',
    'CDK4/6 inhibitor' -> 'CDK4': pharmacology words name the drug, not the target."""
    t = re.sub(r"\banti[- ]", "", term.strip(), flags=re.I)
    t = re.sub(r"(\w)/\d+\b", r"\1", t)
    return " ".join(_PHARMA_WORDS.sub(" ", t).split()) or term.strip()


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _synonym_match(term: str, hits: list[dict]) -> dict | None:
    """First search hit (search order) whose symbol or synonyms equal the term
    ignoring case and punctuation — "PD-1" -> PDCD1 (synonym "PD1"),
    "CD20" -> MS4A1, "CGRP" -> CALCA. Search alone ranks these as ambiguous."""
    data = _query_graphql(_TARGET_SYNONYMS_QUERY, {"ids": [h["id"] for h in hits[:5]]})
    by_id = {t["id"]: t for t in (data or {}).get("targets") or []}
    key = _alnum(term)
    for h in hits[:5]:
        t = by_id.get(h["id"])
        if not t:
            continue
        labels = [t.get("approvedSymbol")] + [s["label"] for s in t.get("symbolSynonyms") or []] + [
            s["label"] for s in t.get("nameSynonyms") or []
        ]
        if any(_alnum(l) == key for l in labels if l):
            return {"id": t["id"], "name": t["approvedSymbol"]}
    return None


_DISEASE_SYNONYMS_QUERY = """query d($ids: [String!]!) {
  diseases(efoIds: $ids) { id name synonyms { terms } }
}"""


def _disease_norm(s: str) -> str:
    """'Non small cell lung cancer' == 'non-small cell lung carcinoma',
    "Crohn's disease" == 'Crohn disease'."""
    s = (s or "").lower().replace("’", "'").replace("'s ", " ").replace("'", "").replace("-", " ")
    s = re.sub(r"\bcarcinoma\b", "cancer", s)
    return " ".join(s.split())


def _disease_synonym_match(term: str, hits: list[dict]) -> dict | None:
    """First hit (search order) whose name or a synonym equals the term —
    abbreviations (T2D, RA, AML, SLE, IPF…) and alternative names (atopic
    dermatitis = atopic eczema) live in synonyms, not in the display name."""
    data = _query_graphql(_DISEASE_SYNONYMS_QUERY, {"ids": [h["id"] for h in hits[:5]]})
    by_id = {d["id"]: d for d in (data or {}).get("diseases") or []}
    key = _disease_norm(term)
    for h in hits[:5]:
        d = by_id.get(h["id"])
        if not d:
            continue
        terms = [d.get("name")] + [t for s in d.get("synonyms") or [] for t in s.get("terms") or []]
        if any(_disease_norm(t) == key for t in terms if t):
            return {"id": d["id"], "name": d["name"]}
    return None


def _resolve_entity(term: str, entity: str) -> dict:
    """Resolve term to entity ID; returns {'id': str, 'name': str, 'ambiguous': bool, 'candidates': list}."""
    result = _resolve_once(term, entity)
    # "HER2-positive breast cancer" has no disease entity of its own; the
    # disease without its leading qualifier ("breast cancer") does.
    # Only an exact/synonym hit on a real disease is accepted here: a fuzzy hit
    # on a shortened phrase ("ejection fraction") would be worse than nothing.
    words = term.split()
    while entity == "disease" and not result["id"] and len(words) > 1:
        words = words[1:]
        shorter = _resolve_once(" ".join(words), entity)
        if shorter.get("how") in ("exact", "synonym") and not _is_trait(shorter):
            result = shorter
    return result


def _is_trait(hit: dict) -> bool:
    """A lab measurement / quantitative trait rather than a disease. Some
    abbreviations collide with one ("IPF" is also "immature platelet
    fraction"), so these rank after real diseases. "<disease> symptom
    measurement" is the exception: that is how Open Targets currently files
    some diseases themselves (multiple sclerosis, with all its drugs)."""
    name = (hit.get("name") or "").lower()
    if "symptom measurement" in name:
        return False
    return hit.get("id", "").startswith("OBA_") or "quantity" in name or name.startswith("level of") or "measurement" in name


def _resolve_once(term: str, entity: str) -> dict:
    clean_term = _clean_target_term(term) if entity == "target" else term.strip()
    data = _query_graphql(SEARCH_QUERY, {"term": clean_term})
    if not data or not data.get("search"):
        return {"id": None, "name": None, "ambiguous": False, "candidates": []}

    hits = data["search"].get("hits") or []
    matching = [h for h in hits if h.get("entity") == entity]

    if not matching:
        # Retry with simpler term if term had qualifiers (e.g. 'KRAS G12C' -> 'KRAS')
        words = clean_term.split()
        if len(words) > 1 and entity == "target":
            data_retry = _query_graphql(SEARCH_QUERY, {"term": words[0]})
            hits_retry = (data_retry or {}).get("search", {}).get("hits") or []
            matching = [h for h in hits_retry if h.get("entity") == entity]

    if not matching:
        return {"id": None, "name": None, "ambiguous": False, "candidates": []}

    # Exact name match, ignoring a possessive: "Parkinson's disease" is
    # Open Targets' "Parkinson disease" (without this the term came back
    # ambiguous and resolved to nothing).
    def norm(s: str) -> str:
        return " ".join(s.lower().replace("’", "'").replace("'s ", " ").replace("'", "").split())

    if entity == "disease":  # stable: real diseases first, search order otherwise kept
        matching = sorted(matching, key=_is_trait)

    exact = next((h for h in matching if norm(h.get("name") or "") == norm(clean_term)), None)
    if not exact and entity == "disease":
        exact = next((h for h in matching if _disease_norm(h.get("name")) == _disease_norm(clean_term)), None)
    if exact:
        return {"id": exact["id"], "name": exact["name"], "ambiguous": False, "candidates": [], "how": "exact"}

    synonym = (_synonym_match if entity == "target" else _disease_synonym_match)(clean_term, matching)
    if synonym:
        return {**synonym, "ambiguous": False, "candidates": [], "how": "synonym"}

    # Top hit
    top = matching[0]
    if entity == "disease" and _is_trait(top):
        # Only measurements matched, and not by name: no disease found.
        return {"id": None, "name": None, "ambiguous": False, "candidates": []}
    # Check if ambiguous (multiple hits with close relevancy)
    if len(matching) > 1 and matching[1].get("name", "").lower() != top.get("name", "").lower():
        # If top hit doesn't clearly match term, flag as ambiguous
        top_name = (top.get("name") or "").lower()
        if clean_term.lower() not in top_name and top_name not in clean_term.lower():
            return {
                "id": None,
                "name": None,
                "ambiguous": True,
                "candidates": [{"id": h["id"], "name": h["name"]} for h in matching[:3]],
            }

    return {"id": top["id"], "name": top["name"], "ambiguous": False, "candidates": [], "how": "top"}


def _extract_synonyms(target: dict) -> list[str]:
    """Extract clean list of unique target synonyms."""
    syns = set()
    for s in (target.get("symbolSynonyms") or []):
        lbl = (s.get("label") or "").strip()
        if lbl and len(lbl) > 1:
            syns.add(lbl)
    for s in (target.get("nameSynonyms") or []):
        lbl = (s.get("label") or "").strip()
        if lbl and len(lbl) > 1:
            syns.add(lbl)
    return sorted(syns)


def _extract_drug_candidates(target: dict) -> list[dict]:
    """Extract list of known drug candidates with stage and mechanism."""
    rows = (target.get("drugAndClinicalCandidates") or {}).get("rows", [])
    drugs = []
    seen = set()
    for r in rows:
        d = r.get("drug")
        if not d or not d.get("name"):
            continue
        name = d["name"].strip()
        if name in seen:
            continue
        seen.add(name)
        stage = r.get("maxClinicalStage")
        mechs = []
        for mr in (d.get("mechanismsOfAction") or {}).get("rows", []):
            m = mr.get("mechanismOfAction")
            if m:
                mechs.append(m)
        drugs.append({
            "name": name,
            "stage": stage,
            "mechanism": ", ".join(mechs) if mechs else None,
        })
    return drugs


def _query_graphql(query: str, variables: dict) -> Optional[dict]:
    """Execute GraphQL query with timeout and error checking."""
    try:
        resp = requests.post(
            API_URL,
            json={"query": query, "variables": variables},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("errors"):
            return None
        return body.get("data")
    except (requests.RequestException, ValueError, KeyError):
        return None
