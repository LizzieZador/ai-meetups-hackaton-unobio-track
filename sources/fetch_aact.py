"""AACT (Aggregate Analysis of ClinicalTrials.gov) — live Postgres DB, with a
static flat-file fallback.

AACT and the ClinicalTrials.gov API are one dataset in two forms: AACT is for
aggregated queries ("all trials on target X"), the API is for one study's detail
(see fetch_clinicaltrials.py).

fetch_aact() returns a LIST of pipeline-shaped evidence records
{source, url, date_accessed, raw_snippet}:
  1. one aggregate record (counts, phases, median enrollment, primary outcomes),
  2. one record per early-terminated/withdrawn/suspended trial (max 10) with its own
     NCT, stop reason and a keyword-based stop_category,
  3. one landscape record (comparators, combination partners, alternative
     therapies, direct same-mechanism competitors, sponsors).
Each raw_snippet is short and self-contained so the LLM can quote it verbatim.

Important limits (stated in the records themselves):
- AACT does not know the mechanism of a drug. "Direct competitors" appear only
  if a same-mechanism drug list is supplied (Open Targets / ChEMBL).
- cutoff_date hides everything that reflects the future (status, stop reasons).
- stop_category is keyword-based, a heuristic.

Data access: tries a live read-only connection to the AACT public Postgres DB
(AACT_DB_HOST/PORT/NAME/USER/PASSWORD in the environment, statement_timeout 30 s);
if that is not set or fails, falls back to flat files in data/aact/
(conditions.txt, studies.txt, interventions.txt, sponsors.txt, design_outcomes.txt).
The flat-file path has no design-group data, so partner/comparator split there is
heuristic (study title). Results are cached in cache/ (None is never cached).

Owner: Клінічний аналітик (trial design) + Комерційний аналітик (competitors via sponsors).
"""
import ast
import csv
import datetime
import hashlib
import json
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

try:
    from .cache import CACHE_DIR, _slug
except ImportError:
    from cache import CACHE_DIR, _slug

AACT_DIR = Path(__file__).resolve().parent.parent / "data" / "aact"

STATEMENT_TIMEOUT_MS = 30000
LIVE_ROW_LIMIT = 500
MAX_FAILURE_RECORDS = 10

ACTIVE_STATUS_KEYWORDS = ("recruit", "active", "enrolling")
INACTIVE_STATUS_KEYWORDS = ("completed", "terminated", "withdrawn", "suspended")
FAILED_STATUSES = ("TERMINATED", "WITHDRAWN", "SUSPENDED")
PLACEBO_KEYWORDS = (
    "placebo",
    "vehicle",
    "sham",
    "no intervention",
    "standard of care",
    "best supportive care",
    "bsc",
)
DRUG_LIKE_TYPES = {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT", "GENETIC"}
NON_ACTIVE_GROUPS = {"PLACEBO_COMPARATOR", "SHAM_COMPARATOR", "NO_INTERVENTION"}

UNSPECIFIED_PHASE = "Not Applicable / Unspecified"
_PHASE_LABELS = {
    "EARLYPHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
}
_PHASE_RANK = {"Early Phase 1": 0.5, "Phase 1": 1, "Phase 2": 2, "Phase 3": 3, "Phase 4": 4}
_PHASE_ORDER = [
    "Early Phase 1", "Phase 1", "Phase 1/Phase 2", "Phase 2",
    "Phase 2/Phase 3", "Phase 3", "Phase 4", UNSPECIFIED_PHASE,
]

# "not due to safety concerns", "no safety issues" etc. must NOT count as a safety stop
_NEGATED_SAFETY = re.compile(
    r"\b(?:not|no|non|without|unrelated to)\b(?:\s+(?:due to|related to|because of|"
    r"based on|driven by|owing to|a|an|any))*\s+(?:safety|toxicity|adverse)",
    re.IGNORECASE,
)
_STOP_RULES = (
    ("safety", ("safety", "toxicit", "adverse", "side effect")),
    ("efficacy_futility", (
        "futility", "futile", "lack of efficacy", "lack of effect", "no benefit",
        "insufficient efficacy", "interim analysis", "did not meet", "unlikely to", "failed to",
    )),
    ("business", (
        "business", "sponsor decision", "strategic", "funding", "portfolio", "commercial",
        "company decision", "financial", "priorit", "merger", "acquisition",
    )),
    ("enrollment", ("enrol", "recruit", "accrual", "accrue")),
)


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------
def _is_placebo_or_non_drug(name: str) -> bool:
    d = name.strip().lower()
    return any(p in d for p in PLACEBO_KEYWORDS) or len(d) <= 1


def _is_drug_like(intervention_type) -> bool:
    t = re.sub(r"\s+", "_", (intervention_type or "").strip().upper())
    return (not t) or t in DRUG_LIKE_TYPES


def _norm_group_type(gt) -> str:
    return re.sub(r"\s+", "_", (gt or "").strip().upper())


def _norm_phase(raw) -> str:
    s = re.sub(r"[\s_]+", "", (raw or "").upper())
    if not s:
        return UNSPECIFIED_PHASE
    labels = [_PHASE_LABELS[p] for p in s.split("/") if p in _PHASE_LABELS]
    return "/".join(labels) if labels else UNSPECIFIED_PHASE


def _phase_rank(label: str) -> float:
    return max((_PHASE_RANK.get(p, 0) for p in label.split("/")), default=0)


def _highest_phase(phases) -> str:
    best, best_rank = None, -1.0
    for p in phases:
        r = _phase_rank(p)
        if r > best_rank:
            best, best_rank = p, r
    return best if best and best_rank > 0 else "unspecified"


def _to_int(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _is_active_status(status: str) -> bool:
    s = (status or "").lower()
    return any(k in s for k in ACTIVE_STATUS_KEYWORDS) and not any(
        k in s for k in INACTIVE_STATUS_KEYWORDS
    )


def _stop_category(why_stopped: str) -> str:
    text = _NEGATED_SAFETY.sub(" ", (why_stopped or "")).lower().strip()
    if not text:
        return "unknown"
    for category, keywords in _STOP_RULES:
        if any(k in text for k in keywords):
            return category
    return "other"


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _validate_cutoff(cutoff_date: Optional[str]) -> None:
    if cutoff_date is None:
        return
    try:
        datetime.date.fromisoformat(cutoff_date)
    except ValueError:
        raise ValueError("cutoff_date must be in YYYY-MM-DD format")


def _raise_csv_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = int(limit / 10)


def _search_url(condition: str, intervention: str) -> str:
    return f"https://clinicaltrials.gov/search?cond={quote_plus(condition)}&intr={quote_plus(intervention)}"


def _lookup_opentargets_known_drugs(mechanism: str, indication: str) -> list:
    """Look up known drugs on this target from the Open Targets cache if available."""
    try:
        key = f"{mechanism}__{indication}"
        cache_file = CACHE_DIR / f"opentargets_{_slug(key)}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            for item in data or []:
                snippet = item.get("raw_snippet", "")
                if "known drugs on this target:" in snippet:
                    part = snippet.split("known drugs on this target:", 1)[1].strip()
                    parsed = ast.literal_eval(part)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if x]
    except Exception:
        pass
    return []


# ----------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------
def fetch_aact(
    condition: str,
    intervention: str,
    cutoff_date: Optional[str] = None,
    same_mechanism_drugs: Optional[list] = None,
    own_program_drug: Optional[str] = None,
    known_mechanism_drugs: Optional[list] = None,  # old name, kept for compatibility
) -> Optional[list]:
    """Pipeline-shaped source: a list of {source, url, date_accessed, raw_snippet}
    records, or None if AACT is unreachable (neither the live DB nor the local
    flat-files are available) — the caller must not crash the pipeline on None.

    same_mechanism_drugs: drugs with the same mechanism (from Open Targets/ChEMBL).
    own_program_drug: optional; only if given are its sponsors tagged "own_program".
    """
    _validate_cutoff(cutoff_date)
    if same_mechanism_drugs is None:
        same_mechanism_drugs = known_mechanism_drugs
    if same_mechanism_drugs is None:
        same_mechanism_drugs = _lookup_opentargets_known_drugs(intervention, condition)

    mech_sig = hashlib.sha1(
        "|".join(sorted(str(d).lower() for d in same_mechanism_drugs)).encode("utf-8")
    ).hexdigest()[:8]
    key = f"{condition}__{intervention}__{cutoff_date or 'all'}__{mech_sig}__{own_program_drug or ''}"
    cache_file = CACHE_DIR / (
        "aact_" + re.sub(r"\W+", "_", key)[:80] + "_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:8] + ".json"
    )

    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # broken cache file: recompute

    analysis = get_competitors_from_aact(
        condition,
        intervention,
        cutoff_date=cutoff_date,
        same_mechanism_drugs=same_mechanism_drugs,
        own_program_drug=own_program_drug,
    )
    if analysis is None:
        return None  # never cache a failure

    records = analysis["records"]
    try:
        cache_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return records


def get_competitors_from_aact(
    condition: str,
    intervention: str,
    cutoff_date: Optional[str] = None,
    same_mechanism_drugs: Optional[list] = None,
    own_program_drug: Optional[str] = None,
) -> Optional[dict]:
    """Full analysis dict (includes the pipeline "records" list). Live DB first,
    then the local flat-file dump. None if neither is available."""
    live = _query_live(condition, intervention, cutoff_date, same_mechanism_drugs, own_program_drug)
    if live is not None:
        return live
    return _query_flatfile(condition, intervention, cutoff_date, same_mechanism_drugs, own_program_drug)


# ----------------------------------------------------------------------------
# data access
# ----------------------------------------------------------------------------
def _build_live_sql(cutoff_date: Optional[str], with_groups: bool) -> str:
    cutoff_clause = "AND s.start_date IS NOT NULL AND s.start_date <= %s" if cutoff_date else ""
    group_cols = (
        "dg.id, UPPER(COALESCE(dg.group_type, '')), COALESCE(dg.title, '')"
        if with_groups
        else "NULL, '', ''"
    )
    group_joins = (
        "LEFT JOIN ctgov.design_group_interventions dgi ON dgi.intervention_id = i.id "
        "LEFT JOIN ctgov.design_groups dg ON dg.id = dgi.design_group_id"
        if with_groups
        else ""
    )
    return f"""
        WITH target_studies AS (
            SELECT DISTINCT s.nct_id
            FROM ctgov.studies s
            JOIN ctgov.conditions c ON c.nct_id = s.nct_id
            LEFT JOIN ctgov.interventions i ON i.nct_id = s.nct_id
            WHERE (c.name ILIKE %s OR c.downcase_name ILIKE %s)
              AND (i.name ILIKE %s OR s.brief_title ILIKE %s)
              {cutoff_clause}
            ORDER BY s.nct_id
            LIMIT {LIVE_ROW_LIMIT}
        ),
        competitor_studies AS (
            SELECT DISTINCT s.nct_id
            FROM ctgov.studies s
            JOIN ctgov.conditions c ON c.nct_id = s.nct_id
            WHERE (c.name ILIKE %s OR c.downcase_name ILIKE %s)
              AND s.brief_title NOT ILIKE %s
              AND NOT EXISTS (
                  SELECT 1 FROM ctgov.interventions i2
                  WHERE i2.nct_id = s.nct_id AND i2.name ILIKE %s
              )
              {cutoff_clause}
            ORDER BY s.nct_id
            LIMIT {LIVE_ROW_LIMIT}
        ),
        all_matched AS (
            SELECT nct_id, TRUE AS is_target_study FROM target_studies
            UNION ALL
            SELECT nct_id, FALSE AS is_target_study FROM competitor_studies
        )
        SELECT
            s.nct_id,
            s.brief_title,
            s.overall_status,
            s.phase,
            s.enrollment,
            s.why_stopped,
            s.start_date,
            m.is_target_study,
            sp.name AS sponsor_name,
            i.name AS drug_name,
            i.intervention_type,
            {group_cols}
        FROM all_matched m
        JOIN ctgov.studies s ON s.nct_id = m.nct_id
        LEFT JOIN ctgov.sponsors sp
               ON sp.nct_id = s.nct_id
              AND (sp.lead_or_collaborator = 'lead' OR sp.lead_or_collaborator IS NULL)
        LEFT JOIN ctgov.interventions i ON i.nct_id = s.nct_id
        {group_joins}
    """


def _query_live(condition, intervention, cutoff_date=None, same_mechanism_drugs=None,
                own_program_drug=None) -> Optional[dict]:
    host = os.environ.get("AACT_DB_HOST")
    if not host:
        return None
    try:
        import psycopg2
    except ImportError:
        return None

    try:
        conn = psycopg2.connect(
            host=host,
            port=os.environ.get("AACT_DB_PORT", "5432"),
            dbname=os.environ.get("AACT_DB_NAME", "aact"),
            user=os.environ.get("AACT_DB_USER", ""),
            password=os.environ.get("AACT_DB_PASSWORD", ""),
            connect_timeout=10,
            options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        )
    except psycopg2.Error:
        return None

    lc, li = _like(condition), _like(intervention)
    cut = [cutoff_date] if cutoff_date else []
    params = tuple([lc, lc, li, li] + cut + [lc, lc, li, li] + cut)

    raw_records = []
    outcome_rows = None
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS};")
            try:
                cur.execute(_build_live_sql(cutoff_date, True), params)
                rows = cur.fetchall()
            except psycopg2.Error:  # e.g. design_groups tables unavailable
                conn.rollback()
                cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS};")
                cur.execute(_build_live_sql(cutoff_date, False), params)
                rows = cur.fetchall()

            for row in rows:
                raw_records.append({
                    "nct_id": row[0], "brief_title": row[1], "overall_status": row[2],
                    "phase": row[3], "enrollment": row[4], "why_stopped": row[5],
                    "start_date": row[6], "is_target_study": bool(row[7]),
                    "sponsor_name": row[8], "drug_name": row[9], "drug_type": row[10],
                    "group_id": row[11], "group_type": row[12] or "", "group_title": row[13] or "",
                })

            nct_ids = sorted({r["nct_id"] for r in raw_records if r["nct_id"]})
            if nct_ids:
                try:
                    cur.execute(
                        "SELECT nct_id, measure FROM ctgov.design_outcomes "
                        "WHERE outcome_type ILIKE 'primary' AND nct_id = ANY(%s)",
                        (nct_ids,),
                    )
                    outcome_rows = [(a, b) for a, b in cur.fetchall()]
                except psycopg2.Error:
                    conn.rollback()
                    outcome_rows = None  # outcomes unavailable; not fatal
            else:
                outcome_rows = []
    except psycopg2.Error:
        return None
    finally:
        conn.close()

    return _process_and_summarize(
        raw_records, condition, intervention,
        "AACT live database (aact-db.ctti-clinicaltrials.org)",
        same_mechanism_drugs=same_mechanism_drugs, own_program_drug=own_program_drug,
        cutoff_date=cutoff_date, primary_outcome_rows=outcome_rows, row_limit=LIVE_ROW_LIMIT,
    )


def _query_flatfile(condition, intervention, cutoff_date=None, same_mechanism_drugs=None,
                    own_program_drug=None) -> Optional[dict]:
    conditions_file = AACT_DIR / "conditions.txt"
    studies_file = AACT_DIR / "studies.txt"
    interventions_file = AACT_DIR / "interventions.txt"
    sponsors_file = AACT_DIR / "sponsors.txt"
    outcomes_file = AACT_DIR / "design_outcomes.txt"

    if not conditions_file.exists() or not studies_file.exists():
        return None

    source = "AACT flat-file dump"
    common = dict(same_mechanism_drugs=same_mechanism_drugs, own_program_drug=own_program_drug,
                  cutoff_date=cutoff_date)
    _raise_csv_limit()
    try:
        cond_l, iv = condition.lower(), intervention.lower()

        condition_ncts = set()
        with open(conditions_file, mode="r", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                name = row.get("downcase_name") or row.get("name") or ""
                if cond_l in name.lower():
                    condition_ncts.add(row.get("nct_id"))

        if not condition_ncts:
            return _process_and_summarize([], condition, intervention, source, **common)

        drug_map: dict = {}  # nct_id -> {drug name: intervention_type}
        target_ncts = set()
        if interventions_file.exists():
            with open(interventions_file, mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter="|"):
                    nct = row.get("nct_id")
                    if nct in condition_ncts:
                        name = (row.get("name") or "").strip()
                        if name:
                            drug_map.setdefault(nct, {})[name] = row.get("intervention_type") or ""
                            if iv in name.lower():
                                target_ncts.add(nct)

        sponsor_map: dict = {}
        if sponsors_file.exists():
            with open(sponsors_file, mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter="|"):
                    nct = row.get("nct_id")
                    if nct in condition_ncts:
                        name = (row.get("name") or "").strip()
                        lead = (row.get("lead_or_collaborator") or "").lower()
                        if name and (lead == "lead" or not lead):
                            sponsor_map.setdefault(nct, set()).add(name)

        raw_records = []
        with open(studies_file, mode="r", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                nct = row.get("nct_id")
                if nct not in condition_ncts:
                    continue
                start_date = str(row.get("start_date") or "").strip()
                if cutoff_date and (not start_date or start_date[:10] > cutoff_date):
                    continue

                title = row.get("brief_title") or ""
                is_target = nct in target_ncts or iv in title.lower()
                drug_items = list(drug_map.get(nct, {}).items()) or [("", "")]
                sponsors = sponsor_map.get(nct) or {"Unknown"}

                for drug, dtype in drug_items:
                    for sp in sponsors:
                        raw_records.append({
                            "nct_id": nct, "brief_title": title,
                            "overall_status": row.get("overall_status", ""),
                            "phase": row.get("phase", ""), "enrollment": row.get("enrollment"),
                            "why_stopped": row.get("why_stopped", ""), "start_date": start_date,
                            "is_target_study": is_target, "sponsor_name": sp,
                            "drug_name": drug, "drug_type": dtype,
                            "group_id": None, "group_type": "", "group_title": "",
                        })

        outcome_rows = None
        if outcomes_file.exists():
            matched = {r["nct_id"] for r in raw_records}
            outcome_rows = []
            with open(outcomes_file, mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter="|"):
                    if (row.get("outcome_type") or "").strip().lower() == "primary" \
                            and row.get("nct_id") in matched:
                        outcome_rows.append((row.get("nct_id"), row.get("measure") or ""))

        return _process_and_summarize(
            raw_records, condition, intervention, source,
            primary_outcome_rows=outcome_rows, **common,
        )
    except (OSError, KeyError, ValueError, csv.Error):
        return None


# ----------------------------------------------------------------------------
# analysis
# ----------------------------------------------------------------------------
def _classify_study_drugs(study: dict, iv: str):
    """Return (comparators, partners, other_arm_drugs, alternatives) as sets of drug
    names for ONE study, so every drug is counted once per trial."""
    arms = study["arms"]
    comp, part, other, alt = set(), set(), set(), set()
    has_groups = any(a[2] for a in arms)

    def is_target_drug(d: str) -> bool:
        return bool(iv) and iv in d.lower()

    if study["is_target"]:
        if has_groups:
            target_groups = {gid for d, gid, gt, _t in arms if gid is not None and is_target_drug(d)}
            for d, gid, gt, _t in arms:
                if is_target_drug(d) or _is_placebo_or_non_drug(d) or gt in NON_ACTIVE_GROUPS:
                    continue
                if "COMPARATOR" in gt:
                    comp.add(d)
                elif gid is not None and gid in target_groups:
                    part.add(d)
                else:
                    other.add(d)
        else:  # no design-group data: heuristic on the study title
            title = study["title"].lower()
            versus = any(k in title for k in ("versus", " vs ", " vs. ", "compared to", "comparator"))
            for d in study["drugs"]:
                if is_target_drug(d) or _is_placebo_or_non_drug(d):
                    continue
                (comp if versus else part).add(d)
    else:
        if has_groups:
            for d, gid, gt, _t in arms:
                if _is_placebo_or_non_drug(d) or gt in NON_ACTIVE_GROUPS or "COMPARATOR" in gt:
                    continue
                alt.add(d)
        else:
            for d in study["drugs"]:
                if not _is_placebo_or_non_drug(d):
                    alt.add(d)
    return comp, part, other, alt


def _fmt_drugs(tally: dict, names: dict, drug_phases: dict, n: int = 5) -> str:
    if not tally:
        return "None identified"
    items = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return ", ".join(
        f"{names.get(k, k)} ({c} trials, highest phase: {_highest_phase(drug_phases.get(k, set()))})"
        for k, c in items
    )


def _fmt_sponsors(tally: dict, n: int = 5) -> str:
    if not tally:
        return "None identified"
    items = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return ", ".join(f"{k} ({c} trials)" for k, c in items)


def _process_and_summarize(
    raw_records: list,
    condition: str,
    intervention: str,
    source: str,
    same_mechanism_drugs: Optional[list] = None,
    own_program_drug: Optional[str] = None,
    cutoff_date: Optional[str] = None,
    primary_outcome_rows: Optional[list] = None,
    row_limit: Optional[int] = None,
) -> dict:
    today = datetime.date.today().isoformat()
    iv = intervention.lower().strip()
    own = (own_program_drug or "").lower().strip()
    hide_future = bool(cutoff_date)
    search_url = _search_url(condition, intervention)

    # 1. Group raw rows into one dict per study
    target_ncts = {r["nct_id"] for r in raw_records if r.get("nct_id") and r.get("is_target_study")}
    studies: dict = {}
    for r in raw_records:
        nct = r.get("nct_id")
        if not nct:
            continue
        s = studies.get(nct)
        if s is None:
            s = studies[nct] = {
                "nct_id": nct,
                "title": r.get("brief_title") or "",
                "status": (r.get("overall_status") or "").strip(),
                "phase": _norm_phase(r.get("phase")),
                "enrollment": _to_int(r.get("enrollment")),
                "why_stopped": (r.get("why_stopped") or "").strip(),
                "start_date": str(r.get("start_date") or "")[:10],
                "is_target": nct in target_ncts,
                "drugs": set(), "sponsors": set(), "arms": set(),
            }
        drug = (r.get("drug_name") or "").strip()
        if drug and _is_drug_like(r.get("drug_type")):
            s["drugs"].add(drug)
            s["arms"].add((drug, r.get("group_id"), _norm_group_type(r.get("group_type")),
                           (r.get("group_title") or "").strip()))
        sp = (r.get("sponsor_name") or "").strip()
        if sp and sp.lower() != "unknown":
            s["sponsors"].add(sp)

    target_studies = [s for s in studies.values() if s["is_target"]]
    landscape_studies = [s for s in studies.values() if not s["is_target"]]
    total_target = len(target_studies)
    limit_hit = bool(row_limit) and (len(target_studies) >= row_limit or len(landscape_studies) >= row_limit)

    # 2. Same-mechanism drug list (never contains the queried drug itself)
    mech_names: list = []
    for m in same_mechanism_drugs or []:
        m = str(m).strip()
        if not m or m.lower() in [x.lower() for x in mech_names]:
            continue
        if iv and (m.lower() in iv or iv in m.lower()):
            continue
        mech_names.append(m)

    # 3. Drug roles, counted once per trial
    names: dict = {}
    comparators: dict = {}
    partners: dict = {}
    other_arm: dict = {}
    alternatives: dict = {}
    drug_phases: dict = {}

    def bump(tally: dict, drug: str) -> None:
        k = drug.lower()
        names.setdefault(k, drug)
        tally[k] = tally.get(k, 0) + 1

    for s in studies.values():
        for d in s["drugs"]:
            drug_phases.setdefault(d.lower(), set()).add(s["phase"])
        comp, part, oth, alt = _classify_study_drugs(s, iv)
        for d in comp:
            bump(comparators, d)
        for d in part:
            bump(partners, d)
        for d in oth:
            bump(other_arm, d)
        for d in alt:
            if not any(m.lower() in d.lower() for m in mech_names):
                bump(alternatives, d)

    # 4. Direct same-mechanism competitors (only if a list was supplied)
    direct: dict = {}
    for m in mech_names:
        ml = m.lower()
        hits = [s for s in studies.values() if any(ml in d.lower() for d in s["drugs"])]
        direct[m] = {
            "trials": len(hits),
            "highest_phase": _highest_phase({s["phase"] for s in hits}) if hits else None,
        }

    # 5. Sponsors
    own_sponsors = set()
    if own:
        for s in studies.values():
            if any(own in d.lower() for d in s["drugs"]):
                own_sponsors |= s["sponsors"]
    queried_sponsors: dict = {}
    own_program_sponsors: dict = {}
    competitor_sponsors: dict = {}
    for s in studies.values():
        for sp in s["sponsors"]:
            if sp in own_sponsors:
                own_program_sponsors[sp] = own_program_sponsors.get(sp, 0) + 1
            elif s["is_target"]:
                queried_sponsors[sp] = queried_sponsors.get(sp, 0) + 1
            else:
                competitor_sponsors[sp] = competitor_sponsors.get(sp, 0) + 1

    # 6. Counts and phase distribution for studies matching the queried intervention
    active = inactive = 0
    phases_distribution: dict = {}
    for s in target_studies:
        if _is_active_status(s["status"]):
            active += 1
        else:
            inactive += 1
        phases_distribution[s["phase"]] = phases_distribution.get(s["phase"], 0) + 1
    sorted_phases = {p: phases_distribution[p] for p in _PHASE_ORDER if p in phases_distribution}
    for p, c in phases_distribution.items():
        sorted_phases.setdefault(p, c)
    phase_sum = sum(sorted_phases.values())

    # 7. Median enrollment (all matched studies in the indication) for Phase 2 / Phase 3
    median_parts = []
    for label in ("Phase 2", "Phase 3"):
        vals = [s["enrollment"] for s in studies.values()
                if s["phase"] == label and s["enrollment"] and s["enrollment"] > 0]
        median_parts.append(
            f"{label}: median {int(statistics.median(vals))} (n={len(vals)} trials)" if vals
            else f"{label}: no enrollment data"
        )

    # 8. Most common primary outcomes
    outcomes_text = "primary outcome data unavailable"
    if primary_outcome_rows is not None:
        per: dict = {}
        shown: dict = {}
        for nct, measure in primary_outcome_rows:
            if nct not in studies or not measure:
                continue
            clean = re.sub(r"\s+", " ", str(measure).strip())
            norm = clean.lower()[:120]
            per.setdefault(norm, set()).add(nct)
            shown.setdefault(norm, clean[:90])
        top = sorted(per.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]
        outcomes_text = (
            "; ".join(f"{shown[k]} ({len(v)} trials)" for k, v in top) if top else "none recorded"
        )

    # 9. Early terminated / withdrawn / suspended trials (hidden when a cutoff is set)
    failed = []
    if not hide_future:
        for s in studies.values():
            status_key = re.sub(r"[\s,]+", "_", s["status"].upper())
            if status_key in FAILED_STATUSES:
                failed.append(s)

        def _fail_key(s):
            return (1 if s["why_stopped"] else 0, s["enrollment"] or 0)

        failed.sort(key=_fail_key, reverse=True)
    top_failed = failed[:MAX_FAILURE_RECORDS]

    # 10. Top studies (compat), ranked by enrollment
    def _sort_key(s):
        return (s["enrollment"] or 0, s["start_date"])

    ranked = sorted(target_studies or landscape_studies, key=_sort_key, reverse=True)
    top_trials = []
    for s in ranked[:10]:
        top_trials.append({
            "nct_id": s["nct_id"],
            "drug_name": ", ".join(sorted(s["drugs"])) if s["drugs"] else intervention,
            "sponsor": ", ".join(sorted(s["sponsors"])) if s["sponsors"] else "Unknown",
            "phase": s["phase"],
            "status": "" if hide_future else s["status"],
            "enrollment": s["enrollment"],
            "why_stopped": "" if hide_future else s["why_stopped"],
            "start_date": s["start_date"],
        })
    nct_ids = [s["nct_id"] for s in ranked]

    # 11. Build the pipeline records
    # --- record A: aggregate
    a = [f"AACT analysis for indication '{condition}' and queried intervention/target '{intervention}'."]
    if total_target == 0:
        a.append("No trials matched the queried intervention in this indication in the queried dataset.")
    elif hide_future:
        a.append(
            f"Evidence restricted to trials started before {cutoff_date}; later status information is hidden. "
            "Active/inactive status, stop reasons and failure analysis are therefore not reported. "
            f"Total studies matching the queried intervention: {total_target}."
        )
    else:
        a.append(
            f"{active} active trials, {inactive} inactive trials "
            f"(total studies matching the queried intervention: {total_target})."
        )
    if total_target:
        a.append(f"Phase distribution (total: {phase_sum}): [{', '.join(f'{p}: {c}' for p, c in sorted_phases.items())}].")
        if phase_sum != total_target:
            a.append(f"Phase counts do not sum to total: {total_target - phase_sum} unaccounted.")
        a.append(f"Top NCT IDs by enrollment: {', '.join(nct_ids[:10])}.")
    a.append(
        "Median enrollment across all matched studies in the indication: " + "; ".join(median_parts)
        + (" (enrollment may reflect final figures)." if hide_future else ".")
    )
    a.append(f"Most common primary outcomes across matched studies: {outcomes_text}.")
    if limit_hit:
        a.append(f"Result limit of {row_limit} studies per group was reached; counts are lower bounds.")
    records = [{
        "source": source,
        "url": search_url,
        "date_accessed": today,
        "raw_snippet": " ".join(a),
    }]

    # --- records B: one per failed trial
    for s in top_failed:
        why = s["why_stopped"] or "not stated"
        enroll = s["enrollment"] if s["enrollment"] is not None else "N/A"
        tested = ", ".join(sorted(s["drugs"])[:6]) if s["drugs"] else "N/A"
        sponsors = ", ".join(sorted(s["sponsors"])) if s["sponsors"] else "Unknown"
        records.append({
            "source": f"ClinicalTrials.gov {s['nct_id']}",
            "url": f"https://clinicaltrials.gov/study/{s['nct_id']}",
            "date_accessed": today,
            "raw_snippet": (
                f"{s['nct_id']} ({s['title'][:150]}): status {s['status']}, phase {s['phase']}, "
                f"enrollment {enroll}, whyStopped: {why}, "
                f"stop_category: {_stop_category(s['why_stopped'])} (keyword-based), "
                f"lead sponsor: {sponsors}, start date: {s['start_date'] or 'N/A'}. "
                f"Tested interventions: {tested}."
            ),
        })

    # --- record C: landscape
    if mech_names and any(v["trials"] for v in direct.values()):
        direct_text = ", ".join(
            f"{m} ({v['trials']} trials, highest phase: {v['highest_phase']})"
            for m, v in sorted(direct.items(), key=lambda kv: -kv[1]["trials"]) if v["trials"]
        )
        zero = [m for m, v in direct.items() if not v["trials"]]
        if zero:
            direct_text += f". Known same-mechanism drugs with no trials found in this indication: {', '.join(zero)}"
    elif mech_names:
        direct_text = (
            "Known same-mechanism drugs (" + ", ".join(mech_names)
            + ") have no trials found in this indication in the queried dataset"
        )
    else:
        direct_text = "No same-mechanism competitors could be determined from the available data"

    c = [
        f"AACT landscape for indication '{condition}' (queried intervention/target: '{intervention}').",
        f"Active comparators: {_fmt_drugs(comparators, names, drug_phases)}.",
        f"Combination partners: {_fmt_drugs(partners, names, drug_phases)}.",
        f"Other drugs in other arms of the queried-intervention trials: {_fmt_drugs(other_arm, names, drug_phases)}.",
        f"Alternative therapies in indication: {_fmt_drugs(alternatives, names, drug_phases)}. "
        "Mechanism of these drugs was not verified; some may share the mechanism of the target program.",
        f"Direct competitors (same mechanism): {direct_text}.",
        f"Sponsors of trials matching the queried intervention: {_fmt_sponsors(queried_sponsors)}.",
    ]
    if own_sponsors:
        c.append(f"Own program sponsors (own_program_drug '{own_program_drug}'): {_fmt_sponsors(own_program_sponsors)}.")
    c.append(f"Competitor sponsors (other trials in the indication): {_fmt_sponsors(competitor_sponsors)}.")
    c.append(
        f"Landscape context: {len(landscape_studies)} landscape studies are trials in this indication that do not "
        f"involve '{intervention}'; they indicate the broader competitive space and standard-of-care benchmarks."
        + (" The landscape sample is capped, so counts are lower bounds." if limit_hit else "")
    )
    records.append({
        "source": source,
        "url": search_url,
        "date_accessed": today,
        "raw_snippet": " ".join(c),
    })

    sponsor_breakdown = (
        [{"sponsor": k, "trials_count": v, "category": "own_program"} for k, v in own_program_sponsors.items()]
        + [{"sponsor": k, "trials_count": v, "category": "queried_intervention"} for k, v in queried_sponsors.items()]
        + [{"sponsor": k, "trials_count": v, "category": "competitor"} for k, v in competitor_sponsors.items()]
    )
    sponsor_breakdown.sort(key=lambda x: x["trials_count"], reverse=True)

    def _named(tally: dict) -> dict:
        return {names.get(k, k): v for k, v in sorted(tally.items(), key=lambda kv: -kv[1])}

    return {
        "source": source,
        "url": search_url,
        "date_accessed": today,
        "records": records,
        "raw_snippet": records[0]["raw_snippet"],
        "active_trials_count": None if hide_future else active,
        "inactive_trials_count": None if hide_future else inactive,
        "total_trials_count": total_target,
        "landscape_trials_count": len(landscape_studies),
        "phases_distribution": sorted_phases,
        "top_trials": top_trials,
        "comparators": _named(comparators),
        "combination_partners": _named(partners),
        "other_arm_drugs": _named(other_arm),
        "alternative_therapies_in_indication": _named(alternatives),
        "direct_competitors_same_mechanism": direct,
        "sponsors": sponsor_breakdown,
        "top_terminated_trials": [
            {"nct_id": s["nct_id"], "status": s["status"], "enrollment": s["enrollment"],
             "why_stopped": s["why_stopped"], "stop_category": _stop_category(s["why_stopped"]),
             "drugs": sorted(s["drugs"])}
            for s in top_failed
        ],
        "nct_ids": nct_ids,
    }


if __name__ == "__main__":
    cond = sys.argv[1] if len(sys.argv) > 1 else "NSCLC"
    intr = sys.argv[2] if len(sys.argv) > 2 else "pembrolizumab"
    cutoff = (sys.argv[3] if len(sys.argv) > 3 else "") or None
    mech = [x.strip() for x in sys.argv[4].split(",")] if len(sys.argv) > 4 and sys.argv[4] else None
    res = fetch_aact(cond, intr, cutoff_date=cutoff, same_mechanism_drugs=mech)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))