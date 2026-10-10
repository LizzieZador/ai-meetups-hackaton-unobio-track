"""Helpers over collect_sources() output — no LLM involved: the set of real
source names (for rejecting hallucinated citations) and the competitor list
the cards render."""


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


