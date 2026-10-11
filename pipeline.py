"""Orchestration — plain Python functions, no agent framework.

collect_sources() -> scientific_agent() -> clinical_agent() -> commercial_agent()
                   -> deterministic financials -> chair_agent()

Every run also writes a JSON trace to data/reports/ — indication, mechanism,
raw sources, every claim, and model/API usage + approximate cost — so each
evaluation is reproducible and auditable.
"""
import datetime
import json
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# On Windows stdout defaults to the system codepage, which can't encode e.g.
# the Greek letters an LLM writes in scientific text ("β-secretase") — any
# print() of model output would raise UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agents.chair_agent import chair_agent
from agents.clinical_agent import clinical_agent
from agents.commercial_agent import commercial_agent
from agents.scientific_agent import collect_scientific_sources, scientific_agent
from financials import FinancialResult, run_deterministic_financials
from llm import get_usage_summary, reset_usage_log
from raw_data import collect_valid_sources, extract_analogs
from schema import Claim, get_dropped_claims_log, reset_dropped_claims_log, log_dropped_claim
from sources.fetch_biomarker_evidence import fetch_biomarker_evidence
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_commercial import collect_commercial_sources
from sources.fetch_cms_spending import fetch_medicare_spending
from sources.fetch_drug_profiles import fetch_competitor_profiles
from sources.fetch_ema import fetch_eu_approvals
from sources.fetch_faers import fetch_adverse_events
from sources.fetch_fda_labels import fetch_drug_labels
from sources.fetch_purple_book import fetch_biologic_exclusivity
from sources.fetch_lens import fetch_patent_signals
from sources.fetch_opentargets import _resolve_entity
from sources.fetch_orange_book import fetch_orange_book_patents
from sources.fetch_regulatory import fetch_regulatory_signals
from sources.fetch_secedgar import fetch_press_releases

REPORTS_DIR = Path(__file__).resolve().parent / "data" / "reports"
# Fetches are network-bound; each API is hit by at most a couple of threads at once.
FETCH_WORKERS = 8


def collect_sources(
    indication: str,
    mechanism: str,
    cutoff_date: Optional[str] = None,
    details: Optional[dict] = None,
) -> dict:
    """Fetch raw snippets from every connected source.

    details are the optional program inputs; the Biomarkers field adds
    biomarker-matched trials and literature (sources/fetch_biomarker_evidence.py).

    Uses the richer collectors (8-angle PubMed search + target context for
    scientific/translation evidence; AACT direct-competitor analysis +
    verified epidemiology/pricing for the competitive/commercial picture)
    instead of one single-query fetch per source — this is what actually
    produces the depth of evidence the task brief's Required Output asks for
    (contradictory evidence, human vs animal findings, same-mechanism
    competitors, verified vs unverified market data).

    Any source that fails (or returns None) becomes an empty list here — the
    pipeline keeps going with whatever evidence is actually available.

    The sources are fetched in parallel, in three waves dictated by data
    dependencies: (1) everything keyed by indication/mechanism alone;
    (2) everything keyed by the analog drug names wave 1 found; (3) Orange
    Book, which needs the openFDA application numbers from wave 2.
    """
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        return _collect_sources(pool, indication, mechanism, cutoff_date, details or {})


def _collect_sources(
    pool: ThreadPoolExecutor, indication: str, mechanism: str, cutoff_date: Optional[str], details: dict
) -> dict:
    def submit(name: str, fn, *args, **kwargs) -> Future:
        """Run one fetch in the pool; a failure there yields [] (logged), never a failed run."""
        def run():
            try:
                return fn(*args, **kwargs) or []
            except Exception as e:
                print(f"[Pipeline Warning] {name} lookup failed: {e}")
                return []
        return pool.submit(run)

    # Wave 1 — keyed by indication/mechanism only.
    sci_future = submit("scientific", collect_scientific_sources, indication=indication, mechanism=mechanism, cutoff_date=cutoff_date)
    comm_future = submit("commercial", collect_commercial_sources, indication=indication, mechanism=mechanism, cutoff_date=cutoff_date)
    trials_future = submit("clinicaltrials", fetch_clinicaltrials, condition=indication, intervention=mechanism)
    biomarker_future = submit(
        "biomarker evidence", lambda: [fetch_biomarker_evidence(indication, mechanism, details.get("biomarkers"), cutoff_date)]
    )
    target_future = (
        submit("target resolution", lambda: [(_resolve_entity(mechanism, "target") or {}).get("name") or ""])
        if not cutoff_date else None
    )
    sci_bundle = sci_future.result() or {}
    comm_bundle = comm_future.result() or {}

    # sci_bundle's two lists overlap by design (the "clinical"/"opentargets"
    # query groups feed both scientific_thesis and human_translation) — dedupe
    # by source once here, split by the "group" tag into pubmed/opentargets
    # buckets. scientific_agent() re-splits this same merged, tagged list back
    # into its two groups by filtering on "group" membership, which correctly
    # reproduces the overlap — a record tagged "clinical" is simply matched by
    # both filters again, exactly as if the two original lists were kept separate.
    # Dedupe key is (source, raw_snippet), not source alone: fetch_opentargets()
    # deliberately reuses the same "source" label (e.g. "Open Targets
    # {targetId}-{diseaseId}") for several distinct records about the same
    # target/disease pair (association score, known-drugs compatibility
    # record, etc.) — deduping on source alone would drop all but the first
    # of those, including the one record that carries "known_drugs" (which
    # raw_data.extract_analogs() needs for the Analogs/competitors display).
    seen_records: set = set()
    pubmed_records: list[dict] = []
    opentargets_records: list[dict] = []
    for r in sci_bundle.get("scientific_thesis", []) + sci_bundle.get("human_translation", []):
        key = (r.get("source"), r.get("raw_snippet"))
        if not key[0] or key in seen_records:
            continue
        seen_records.add(key)
        (opentargets_records if r.get("group") == "opentargets" else pubmed_records).append(r)

    # comm_bundle["competitive"] mixes AACT records with Open Targets records
    # (both of which collect_scientific_sources() may have already fetched and
    # cached) — route by source prefix and dedupe against what's already collected.
    aact_records: list[dict] = []
    for r in comm_bundle.get("competitive", []):
        key = (r.get("source") or "", r.get("raw_snippet"))
        if key in seen_records:
            continue
        seen_records.add(key)
        (opentargets_records if key[0].startswith("Open Targets") else aact_records).append(r)

    # Regulatory precedent (openFDA), company disclosures (SEC EDGAR) and
    # patents (Lens.org) are keyed by drug name, not target/indication — they
    # mainly ride on the analog drug list Open Targets already gave us
    # (extract_analogs() reads the "known_drugs" field attached to
    # opentargets_records above). That list is empty whenever Open Targets
    # can't resolve the mechanism to a specific gene/target with recorded
    # drugs (e.g. a broad single-word query like "cancer" typed without the
    # "mechanism | indication" search syntax) — in that case the raw
    # mechanism string is tried directly too, since it's sometimes itself a
    # drug/gene name these APIs can match even without an Open Targets
    # analog (e.g. a user searching "sotorasib" alone).
    analog_names = [d["name"] for d in extract_analogs(sources={"opentargets": opentargets_records}) if d.get("name")]
    query_terms = analog_names or ([mechanism] if mechanism else [])
    clinicaltrials_records = trials_future.result()

    # Wave 2 — keyed by the analog drug names.
    # 10, not 5: analogs now come furthest-along first, and the top of that list
    # often holds drugs approved only outside the US (no openFDA record), which
    # pushed FDA-approved ones past a 5-name cut. openFDA is keyless and cached.
    futures: dict[str, Future] = {}
    if query_terms:
        futures["regulatory"] = submit("openFDA", fetch_regulatory_signals, query_terms[:10])
        futures["market_news"] = submit("SEC EDGAR", fetch_press_releases, query_terms[:3], max_per_term=4)
        futures["patents"] = submit("Lens.org", fetch_patent_signals, query_terms[:3], max_per_term=5)

    # Drug profiles, Orange/Purple Book, labels, FAERS, Medicare spending and
    # EMA are present-day snapshots with no historical view, so a backtest
    # (cutoff_date set) skips them rather than leaking later approvals,
    # competitors or safety signals into a past-dated evaluation.
    present_day_keys = ("drug_profiles", "orange_book", "fda_labels", "adverse_events", "purple_book", "medicare_spending", "ema")
    if not cutoff_date:
        target_symbol = target_future.result()[0] if target_future.result() else ""
        futures["drug_profiles"] = submit(
            "drug profile", fetch_competitor_profiles, target_symbol, analog_names, clinicaltrials_records
        )
        # Labels, safety, biologic exclusivity, Medicare spending and EU status
        # for the same furthest-along competitors openFDA was queried for.
        names = query_terms[:10] if analog_names else []
        if names:
            futures["fda_labels"] = submit("FDA labels", fetch_drug_labels, names)
            futures["adverse_events"] = submit("FAERS", fetch_adverse_events, names, indication)
            futures["purple_book"] = submit("Purple Book", fetch_biologic_exclusivity, names)
            futures["medicare_spending"] = submit("Medicare spending", fetch_medicare_spending, names)
            futures["ema"] = submit("EMA", fetch_eu_approvals, names)

    regulatory_records = futures["regulatory"].result() if "regulatory" in futures else []
    # Wave 3 — Orange Book reads the application numbers openFDA returned.
    if not cutoff_date and regulatory_records:
        futures["orange_book"] = submit("Orange Book", fetch_orange_book_patents, regulatory_records)

    results = {key: future.result() for key, future in futures.items()}
    biomarker = (biomarker_future.result() or [{}])[0]
    return {
        "pubmed": pubmed_records,
        "opentargets": opentargets_records,
        "clinicaltrials": clinicaltrials_records,
        "aact": aact_records,
        "commercial": comm_bundle.get("commercial", []),
        "regulatory": regulatory_records,
        "market_news": results.get("market_news", []),
        "patents": results.get("patents", []),
        **{key: results.get(key, []) for key in present_day_keys},
        "biomarker_trials": biomarker.get("biomarker_trials", []),
        "biomarker_literature": biomarker.get("biomarker_literature", []),
    }


def run_pipeline(
    indication: str,
    mechanism: str,
    extra_context: str = "",
    cutoff_date: Optional[str] = None,
    details: Optional[dict] = None,
) -> tuple[list[Claim], dict, dict]:
    """Run the full committee and return (final_claims, cost_summary, sources).

    `sources` is the raw collect_sources() output — the main site output is meant
    to be built from this directly (see raw_data.py), independent of the LLM step;
    the LLM only adds a verdict/analysis layer on top, and every evidence claim it
    produces is cross-checked against this exact data (schema.parse_claims) rather
    than trusted at face value.

    extra_context carries the task brief's optional input info (modality,
    development stage, preclinical results, biomarkers, route of administration)
    as free text — every agent receives it if provided, but it's never required.
    details is the same input as fields, used deterministically: the stage sets
    where the financial model starts, the biomarkers drive extra trial and
    literature searches.

    indication/mechanism are normalized (trimmed + lowercased) right here, once,
    before touching any fetcher, cache key, or LLM prompt — "cancer" and "Cancer"
    must be the exact same query internally. The text-search sources match search
    terms case-insensitively (confirmed directly against PubMed/Open
    Targets/ClinicalTrials.gov/AACT), so this never changes what's found, only
    whether two spellings of the same term share one cache entry and one set of
    LLM calls instead of two independent (and not necessarily identical) ones.
    The caller's own variables are untouched, so the web UI still shows
    whatever case the user actually typed.
    """
    indication = indication.strip().lower()
    mechanism = mechanism.strip().lower()

    reset_usage_log()
    reset_dropped_claims_log()
    details = details or {}
    sources = collect_sources(indication=indication, mechanism=mechanism, cutoff_date=cutoff_date, details=details)
    valid_sources = collect_valid_sources(sources=sources)

    # The raw `sources` above must reach the caller even if the LLM is totally
    # down (quota exhausted, provider outage, no key) — the site's primary
    # output is the connected-database data, not the LLM. Each agent call is
    # therefore isolated: a failure there costs that agent's claims, never the
    # whole response.
    # Pass all agent arguments by explicit keyword arguments to prevent positional inversion
    scientific_claims = _safe_agent_call(
        "scientific_agent",
        scientific_agent,
        indication=indication,
        mechanism=mechanism,
        sources=sources["pubmed"] + sources["opentargets"] + sources["biomarker_literature"],
        extra_context=extra_context,
        valid_sources=valid_sources,
        cutoff_date=cutoff_date,
    )
    clinical_claims = _safe_agent_call(
        "clinical_agent",
        clinical_agent,
        indication=indication,
        mechanism=mechanism,
        sources=sources["clinicaltrials"] + sources["aact"],
        scientific_claims=scientific_claims,
        extra_context=extra_context,
        valid_sources=valid_sources,
        cutoff_date=cutoff_date,
    )
    commercial_claims = _safe_agent_call(
        "commercial_agent",
        commercial_agent,
        indication=indication,
        mechanism=mechanism,
        sources=sources["aact"] + sources["opentargets"] + sources["commercial"],
        clinical_claims=clinical_claims,
        extra_context=extra_context,
        valid_sources=valid_sources,
        cutoff_date=cutoff_date,
    )

    financial_result: Optional[FinancialResult] = None
    try:
        financial_result = run_deterministic_financials(
            commercial_claims=commercial_claims,
            clinical_claims=clinical_claims,
            sources=sources,
            indication=indication,
            mechanism=mechanism,
            current_stage=details.get("stage"),
            biomarker_selected=bool(details.get("biomarkers")),
        )
    except Exception as e:
        print(f"[Pipeline Warning] Deterministic financials calculation failed: {e}")
    # The cards show the model's stage, remaining steps and figures directly.
    sources["financial_model"] = [financial_result.to_dict()] if financial_result else []

    try:
        final_claims = chair_agent(
            indication=indication,
            mechanism=mechanism,
            scientific_claims=scientific_claims,
            clinical_claims=clinical_claims,
            commercial_claims=commercial_claims,
            extra_context=extra_context,
            financial_result=financial_result,
            sources=sources,
        )
    except Exception as e:
        print(f"[Pipeline Warning] chair_agent failed: {e}")
        log_dropped_claim("chair_agent", None, f"chair_agent failed with exception: {e}")
        final_claims = scientific_claims + clinical_claims + commercial_claims

    cost_summary = get_usage_summary()
    _write_trace(
        indication=indication,
        mechanism=mechanism,
        extra_context=extra_context,
        sources=sources,
        claims=final_claims,
        cost_summary=cost_summary,
        financial_result=financial_result,
        cutoff_date=cutoff_date,
    )
    return final_claims, cost_summary, sources


def _safe_agent_call(name: str, fn, *args, **kwargs) -> list[Claim]:
    """Run one agent; on any failure (LLM down, bad key, timeout) log it and
    contribute zero claims instead of losing the whole response."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"[Pipeline Warning] {name} failed: {e}")
        log_dropped_claim(name, None, f"Agent call failed with exception: {e}")
        return []


def _write_trace(
    indication: str,
    mechanism: str,
    extra_context: str,
    sources: dict,
    claims: list[Claim],
    cost_summary: dict,
    financial_result: Optional[FinancialResult] = None,
    cutoff_date: Optional[str] = None,
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = "".join(c if c.isalnum() else "_" for c in f"{indication}_{mechanism}".lower())[:60]
    path = REPORTS_DIR / f"run_{timestamp}_{slug}.json"
    trace = {
        "indication": indication,
        "mechanism": mechanism,
        "extra_context": extra_context,
        "cutoff_date": cutoff_date,
        "timestamp": timestamp,
        "sources": sources,
        "claims": [c.to_dict() for c in claims],
        "financial_model": financial_result.to_dict() if financial_result else None,
        "dropped_claims": get_dropped_claims_log(),
        "cost_summary": cost_summary,
    }
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
