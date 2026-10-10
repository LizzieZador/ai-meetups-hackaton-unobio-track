"""Render collect_sources() output directly — no LLM involved anywhere here.

This is the literal data we pulled from PubMed / Open Targets / ClinicalTrials.gov
/ AACT / the commercial CSV for the queried drug/mechanism, and the list of known
analog/competitor drugs on the same target. The LLM-based committee report
(report.py) is built FROM this data; this module exists so the site can show the
data itself, independent of whether the LLM step succeeds at all.
"""
import datetime

SOURCE_TITLES = {
    "pubmed": "PubMed",
    "opentargets": "Open Targets",
    "clinicaltrials": "ClinicalTrials.gov",
    "aact": "AACT (Aggregate Analysis of ClinicalTrials.gov)",
    "commercial": "Epidemiology / Market Data",
    "regulatory": "openFDA Drugs@FDA (approval records and recorded designations)",
    "market_news": "SEC EDGAR (company filing metadata and exhibits)",
    "patents": "Lens.org (patent records)",
    "drug_profiles": "Open Targets drug profiles (targets, modality, stage; registry competitors)",
    "orange_book": "FDA Orange Book (listed patents and expiration dates)",
}
SOURCE_ORDER = [
    "pubmed", "opentargets", "clinicaltrials", "aact", "commercial", "regulatory", "market_news", "patents",
    "drug_profiles", "orange_book",
]


def collect_valid_sources(sources: dict) -> set:
    """Every exact `source` string actually fetched this run — used by
    schema.parse_claims() to reject evidence claims citing a source we never
    fetched (a hallucinated citation)."""
    return {item["source"] for items in sources.values() for item in items if item.get("source")}


_SALT_WORDS = {
    "besylate", "maleate", "tosylate", "mesylate", "hydrochloride", "citrate", "sodium", "potassium",
    "sulfate", "phosphate", "adipate", "fumarate", "tartrate", "succinate", "acetate", "hcl",
}
_STAGE_ORDER = ["APPROVAL", "PHASE_4", "PREAPPROVAL", "PHASE_3", "PHASE_2_3", "PHASE_2", "PHASE_1_2", "PHASE_1", "EARLY_PHASE_1"]


def _base_name(name: str) -> str:
    return " ".join(w for w in (name or "").lower().split() if w not in _SALT_WORDS)


def stage_rank(stage: str | None) -> int:
    """Lower = further along (approved first) — for ordering analogs."""
    s = (stage or "").upper()
    return _STAGE_ORDER.index(s) if s in _STAGE_ORDER else len(_STAGE_ORDER)


def extract_analogs(sources: dict) -> list[dict]:
    """Known competitor/analog drugs on the same target, from Open Targets
    (the only source that names them directly) — deduplicated by name and by
    salt form (Open Targets lists e.g. "ACALABRUTINIB MALEATE" next to
    "ACALABRUTINIB": the same active moiety, so only the parent is kept),
    ordered furthest-along first so approved drugs lead every table and get
    the per-drug openFDA/patent lookups."""
    by_base: dict[str, dict] = {}
    for item in sources.get("opentargets", []):
        for drug in item.get("known_drugs", []):
            name = drug.get("name")
            if not name:
                continue
            base = _base_name(name)
            kept = by_base.get(base)
            # prefer the parent (salt-free) name; among equals keep the first
            if kept is None or (len(name) < len(kept["name"])):
                by_base[base] = drug
    return sorted(by_base.values(), key=lambda d: stage_rank(d.get("stage")))


def extract_competitors(sources: dict) -> list[dict]:
    """The analog list the cards render: Open Targets' target-level analogs,
    each enriched with its own drug profile (real targets, modality), followed
    by registry competitors that passed fetch_drug_profiles' verification.
    Falls back to the plain analogs when no profiles were fetched (backtests,
    older traces)."""
    profiles = {(p.get("name") or "").strip().lower(): p for p in sources.get("drug_profiles", [])}
    out = []
    for drug in extract_analogs(sources):
        p = profiles.pop((drug.get("name") or "").strip().lower(), None)
        out.append({**drug, **({
            "targets": p.get("targets") or [],
            "drug_type": p.get("drug_type"),
            "profile_url": p.get("url"),
            "origin": "open_targets_target",
        } if p else {})})
    known = {_base_name(d.get("name")) for d in out}
    for p in profiles.values():
        if p.get("origin") != "trial_registry" or _base_name(p.get("name")) in known:
            continue
        out.append({
            "name": p.get("name"),
            "stage": p.get("stage"),
            "mechanism": p.get("mechanism"),
            "targets": p.get("targets") or [],
            "drug_type": p.get("drug_type"),
            "profile_url": p.get("url"),
            "nct_ids": p.get("nct_ids") or [],
            "origin": "trial_registry",
        })
    # stable sort: approved/most advanced first, Open Targets order kept within a stage
    return sorted(out, key=lambda d: stage_rank(d.get("stage")))


def render_raw_data_markdown(indication: str, mechanism: str, sources: dict, analogs: list[dict]) -> str:
    lines = [
        f"# Raw Source Data: {mechanism} — {indication}",
        f"_Generated {datetime.date.today().isoformat()} — pulled directly from the connected "
        f"databases, no LLM involved._",
        "",
    ]
    for key in SOURCE_ORDER:
        items = sources.get(key, [])
        lines.append(f"## {SOURCE_TITLES[key]} ({len(items)} record{'s' if len(items) != 1 else ''})")
        if not items:
            if key == "aact":
                lines.append("_AACT unavailable / 0 trials._")
            else:
                lines.append("_No data returned by this source for this query._")
        else:
            if key == "aact" and any("AACT unavailable / 0 trials" in (item.get("raw_snippet") or "") for item in items):
                lines.append("> **Notice:** AACT unavailable / 0 trials.")
        for item in items:
            link = f"[{item['source']}]({item.get('url')})" if item.get("url") else item["source"]
            lines.append(f"- **{link}** (accessed {item.get('date_accessed')})")
            lines.append(f"  - {item.get('raw_snippet', '')}")
        lines.append("")

    lines.append(f"## Analogs / Competing Drugs ({len(analogs)})")
    if not analogs:
        lines.append("_No known competing drugs on this target were found in Open Targets._")
    else:
        for drug in analogs:
            stage = drug.get("stage") or "stage unknown"
            lines.append(f"- **{drug['name']}** — {stage}")
    lines.append("")

    return "\n".join(lines)
