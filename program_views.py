"""The client's own program on the cards — what the optional details change.

Each optional input (modality, development stage, route, biomarkers,
preclinical results) is set against the fetched data instead of only being
passed to the agents as text:
  card 1  target product profile: the program next to the landscape benchmark
  card 3  literature found for the program's biomarkers (PubMed)
  card 4  the financial model run from the program's stage (financials.py)
  card 5  competitors with the same modality / route, marked per row
  card 8  trials that select on the program's biomarkers (ClinicalTrials.gov)
attach_program_sections() is applied by cards.build_cards() after each card's
own view is built; with no details given, cards 1/3/5/8 are left unchanged
(card 4 still shows the model, flagged "stage assumed").
"""
from card_tables import NA, _cell, _short
from financials import normalize_stage
from raw_data import stage_rank
from sources.fetch_biomarker_evidence import biomarker_terms

# Modality classes shared by the form's options and Open Targets' drugType, so
# "Monoclonal antibody" (form) matches "Antibody" (Open Targets).
_MODALITY_CLASSES = (
    ("ADC", ("antibody-drug", "antibody drug", "adc")),
    ("Antibody", ("antibod", "mab")),
    ("Oligonucleotide", ("sirna", "antisense", "oligonucleotide", "aso")),
    ("mRNA / vaccine", ("mrna", "vaccine")),
    ("Gene therapy", ("gene",)),
    ("Cell therapy", ("cell", "car-t")),
    ("Radioligand", ("radio",)),
    ("Protein / peptide", ("protein", "peptide", "enzyme")),
    ("Small molecule", ("small molecule",)),
)


def modality_class(text: str | None) -> str | None:
    t = (text or "").lower()
    for name, keys in _MODALITY_CLASSES:
        if any(k in t for k in keys):
            return name
    return None


def _route_set(text: str | None) -> set[str]:
    return {r.strip().lower() for r in (text or "").replace("/", ",").split(",") if r.strip()}


def _stage_label(stage: str | None) -> str:
    return (stage or "stage unknown").replace("_", " ").title().replace("Phase 2 3", "Phase 2/3").replace("Phase 1 2", "Phase 1/2")


def _labels_by_name(sources: dict) -> dict:
    return {(l.get("drug_name") or "").strip().lower(): l for l in sources.get("fda_labels", [])}


def _names(drugs: list[dict], limit: int = 6) -> str:
    names = [(d.get("name") or "").title() for d in drugs if d.get("name")]
    return ", ".join(names[:limit]) + (f" +{len(names) - limit} more" if len(names) > limit else "") if names else "none"


def _unverified(text: str) -> dict:
    return {"text": f"{text} (client-provided, not verified)"}


# ---------------------------------------------------------------------------
# Card 1 — target product profile
# ---------------------------------------------------------------------------
def _tpp_section(details: dict, sources: dict, analogs: list[dict], mechanism: str) -> dict | None:
    if not details:
        return None
    labels = _labels_by_name(sources)
    rows = [{"Attribute": "Mechanism / Target", "Your program": mechanism or NA,
             "Landscape benchmark": f"{len(analogs)} drug(s) on this target/mechanism in the connected databases"}]

    if details.get("modality"):
        cls = modality_class(details["modality"])
        same = [d for d in analogs if cls and modality_class(d.get("drug_type")) == cls]
        rows.append({"Attribute": "Modality", "Your program": details["modality"],
                     "Landscape benchmark": f"{len(same)} of {len(analogs)} competitors share it: {_names(same)}"
                     if cls else "Modality not recognised for comparison"})
    if details.get("stage"):
        stage, _ = normalize_stage(details["stage"])
        lead = min(analogs, key=lambda d: stage_rank(d.get("stage")), default=None)
        ahead = [d for d in analogs if stage_rank(d.get("stage")) < stage_rank(_OT_STAGE.get(stage))]
        rows.append({"Attribute": "Development stage", "Your program": details["stage"],
                     "Landscape benchmark": (f"Most advanced: {(lead.get('name') or '').title()} ({_stage_label(lead.get('stage'))}); "
                                             f"{len(ahead)} competitor(s) ahead of you") if lead else NA})
    if details.get("route"):
        mine = _route_set(details["route"])
        with_route = [(d, labels.get((d.get("name") or "").lower(), {}).get("route")) for d in analogs]
        same = [d for d, r in with_route if r and mine & _route_set(r)]
        known = [d for d, r in with_route if r]
        rows.append({"Attribute": "Route of administration", "Your program": details["route"],
                     "Landscape benchmark": (f"{len(same)} of {len(known)} labelled competitors use it: {_names(same)}"
                                             if known else "No US label route on record for the competitors")})
    if details.get("biomarkers"):
        markers = biomarker_terms(details["biomarkers"])
        trials = sources.get("biomarker_trials", [])
        rows.append({"Attribute": "Biomarkers", "Your program": details["biomarkers"],
                     "Landscape benchmark": f"{len(trials)} registered trial(s) in this indication select on "
                                            f"{', '.join(markers) or 'these markers'} (card 8)"})
    if details.get("preclinical"):
        rows.append({"Attribute": "Preclinical results", "Your program": _unverified(_short(details["preclinical"], 220)),
                     "Landscape benchmark": "Assessed by the scientific agent against the fetched literature"})
    return {"kind": "table", "title": "Your program — target product profile",
            "columns": ["Attribute", "Your program", "Landscape benchmark"], "rows": rows}


# Form stage -> the Open Targets stage it corresponds to, for "who is ahead".
_OT_STAGE = {"Discovery": "PRECLINICAL", "Preclinical": "PRECLINICAL", "IND-enabling": "PRECLINICAL",
             "Phase 1": "PHASE_1", "Phase 2": "PHASE_2", "Phase 3": "PHASE_3", "Filed": "PREAPPROVAL",
             "Approved": "APPROVAL"}


# ---------------------------------------------------------------------------
# Card 3 — literature on the program's biomarkers
# ---------------------------------------------------------------------------
def _biomarker_literature_section(sources: dict) -> dict | None:
    records = sources.get("biomarker_literature", [])
    if not records:
        return None
    rows = [{"Biomarker": r.get("matched_biomarker") or NA,
             "Publication": _cell(_short(r.get("raw_snippet") or r.get("source") or "", 150), r.get("url")),
             "PubMed": r.get("source") or NA} for r in records]
    return {"kind": "table", "title": "Literature on your biomarkers — PubMed",
            "columns": ["Biomarker", "Publication", "PubMed"], "rows": rows}


# ---------------------------------------------------------------------------
# Card 4 — the financial model, from the program's stage
# ---------------------------------------------------------------------------
def _money(v: float) -> str:
    return f"${v / 1000:,.2f}B" if abs(v) >= 1000 else f"${v:,.1f}M"


def _financial_section(sources: dict) -> dict | None:
    model = (sources.get("financial_model") or [None])[0]
    if not model:
        return None
    stage = model.get("current_stage") or "Phase 1"
    title_stage = f"{stage} (assumed — set Development stage in + Details)" if model.get("stage_assumed") else stage
    rows = [{"Step": st["name"], "Duration": f"{st['duration_years']:g} yrs",
             "Cost (base)": _money(st["cost_millions"]["base"]) if st["cost_millions"]["base"] else "—",
             "Success probability (base)": f"{st['ptrs']['base'] * 100:.0f}%" if st["ptrs"]["base"] < 1 else "—"}
            for st in model.get("development_steps") or []]
    ptrs, cap, rnpv = model["cumulative_ptrs"], model["total_capital_to_launch_millions"], model["rnpv_millions"]
    rows.append({"Step": "Total from current stage", "Duration": f"{model['timeline_to_launch_years']:g} yrs to launch",
                 "Cost (base)": f"{_money(cap['base'])} incl. launch prep",
                 "Success probability (base)": f"{ptrs['base'] * 100:.1f}% to approval"})
    rows.append({"Step": "rNPV (low / base / high)", "Duration": "",
                 "Cost (base)": f"{_money(rnpv['low'])} / {_money(rnpv['base'])} / {_money(rnpv['high'])}",
                 "Success probability (base)": ""})
    return {"kind": "table", "title": f"Financial model from your stage: {title_stage}",
            "columns": ["Step", "Duration", "Cost (base)", "Success probability (base)"], "rows": rows}


# ---------------------------------------------------------------------------
# Card 5 — competitors vs the program's modality / route
# ---------------------------------------------------------------------------
def _mark_competitors(view: dict, details: dict, sources: dict, analogs: list[dict]) -> dict:
    cls = modality_class(details.get("modality"))
    mine = _route_set(details.get("route"))
    if not (cls or mine) or view.get("view") != "table":
        return view
    labels = _labels_by_name(sources)
    by_name = {(d.get("name") or NA): d for d in analogs}
    columns = list(view["columns"])
    if cls:
        columns.insert(columns.index("Modality") + 1, "Same Modality as Yours")
    if mine:
        columns.insert(columns.index("Modality") + 1 + bool(cls), "Route (US label)")
    rows = []
    for row in view["rows"]:
        d = by_name.get(row.get("Drug"), {})
        row = dict(row)
        if cls:
            theirs = modality_class(d.get("drug_type"))
            row["Same Modality as Yours"] = ("Yes" if theirs == cls else "No") if theirs else NA
        if mine:
            route = labels.get((d.get("name") or "").lower(), {}).get("route")
            row["Route (US label)"] = (f"{route} — same as yours" if mine & _route_set(route) else route) if route else NA
        rows.append(row)
    # Same-modality competitors first: they are the direct threat to this program.
    if cls:
        rows.sort(key=lambda r: r.get("Same Modality as Yours") != "Yes")
    return {**view, "columns": columns, "rows": rows}


# ---------------------------------------------------------------------------
# Card 8 — trials that select on the program's biomarkers
# ---------------------------------------------------------------------------
def _biomarker_trials_section(sources: dict) -> dict | None:
    trials = sources.get("biomarker_trials", [])
    if not trials:
        return None
    rows = [{"Biomarker": t.get("matched_biomarker") or NA,
             "NCT ID": _cell(t.get("nctId") or NA, t.get("url")),
             "Title": _short(t.get("briefTitle") or "", 110) or NA,
             "Phase": ", ".join(t.get("phases") or []) or NA,
             "Status": t.get("overallStatus") or NA,
             "Drug": ", ".join(t.get("interventions") or []) or NA,
             "Sponsor": t.get("leadSponsor") or NA} for t in trials]
    return {"kind": "table", "title": "Trials selecting on your biomarkers — ClinicalTrials.gov",
            "columns": ["Biomarker", "NCT ID", "Title", "Phase", "Status", "Drug", "Sponsor"], "rows": rows}


# ---------------------------------------------------------------------------
def _with_section(view: dict, section: dict | None, title: str, first: bool) -> dict:
    """The card's view with one more section — a table view becomes a
    composite of [section, the original table] (or the reverse)."""
    if not section:
        return view
    if view.get("view") == "composite":
        sections = [section, *view["sections"]] if first else [*view["sections"], section]
        return {**view, "sections": sections}
    if view.get("view") == "table":
        original = {"kind": "table", "title": title, "columns": view["columns"], "rows": view["rows"]}
        return {"view": "composite", "sections": [section, original] if first else [original, section],
                "show_claims_below": view.get("show_claims_below", False)}
    if view.get("view") == "list":
        items = {"kind": "list", "title": title, "items": view.get("items", [])}
        return {"view": "composite", "sections": [section, items] if first else [items, section]}
    return view


def attach_program_sections(card_id: str, view: dict, details: dict, sources: dict,
                            analogs: list[dict], mechanism: str) -> dict:
    details = details or {}
    if card_id == "unmet_need":
        return _with_section(view, _tpp_section(details, sources, analogs, mechanism), "Unmet need & standard of care", True)
    if card_id == "scientific_evidence":
        return _with_section(view, _biomarker_literature_section(sources), "", False)
    if card_id == "patient_population":
        return _with_section(view, _financial_section(sources), "Evidence & Financials", True)
    if card_id == "competitive_landscape":
        return _mark_competitors(view, details, sources, analogs)
    if card_id == "trial_design":
        return _with_section(view, _biomarker_trials_section(sources), "Trials for this target & indication", True)
    return view
