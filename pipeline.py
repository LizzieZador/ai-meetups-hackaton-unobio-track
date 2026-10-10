"""Orchestration — plain Python functions, no agent framework.

collect_sources() -> scientific_agent() -> clinical_agent() -> commercial_agent()
                   -> chair_agent() -> render_report()

Every run also writes a JSON trace to data/reports/ — indication, mechanism,
raw sources, every claim, and model/API usage + approximate cost — so the
pipeline is reproducible and auditable (the "Reproducible Pipeline" Required
Deliverable: architecture, source attribution, model/API usage, cost, logs/traces).
"""
import datetime
import json
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# BUG FIX: on Windows, stdout defaults to the system codepage (cp1251/cp1252),
# which can't encode e.g. Greek letters an LLM writes freely in scientific
# text ("β-secretase" for BACE1) — any print() of model-generated text then
# raises UnicodeEncodeError. Every entry point (server.py, main.py) imports
# this module first, so reconfiguring stdout once here covers all of them.
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
from quick_summary import build_quick_summary, render_quick_summary_markdown
from raw_data import collect_valid_sources, extract_analogs, render_raw_data_markdown
from report import render_report
from schema import Claim, get_dropped_claims_log, reset_dropped_claims_log, log_dropped_claim
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_commercial import collect_commercial_sources
from sources.fetch_lens import fetch_patent_signals
from sources.fetch_regulatory import fetch_regulatory_signals
from sources.fetch_secedgar import fetch_press_releases

REPORTS_DIR = Path(__file__).resolve().parent / "data" / "reports"


def collect_sources(
    indication: str,
    mechanism: str,
    cutoff_date: Optional[str] = None,
) -> dict:
    """Fetch raw snippets from all five sources.

    Uses the richer collectors (8-angle PubMed search + target context for
    scientific/translation evidence; AACT direct-competitor analysis +
    verified epidemiology/pricing for the competitive/commercial picture)
    instead of one single-query fetch per source — this is what actually
    produces the depth of evidence the task brief's Required Output asks for
    (contradictory evidence, human vs animal findings, same-mechanism
    competitors, verified vs unverified market data).

    Any source that fails (or returns None) becomes an empty list here — the
    pipeline keeps going with whatever evidence is actually available.
    """
    sci_bundle = collect_scientific_sources(indication=indication, mechanism=mechanism, cutoff_date=cutoff_date) or {}
    comm_bundle = collect_commercial_sources(indication=indication, mechanism=mechanism, cutoff_date=cutoff_date) or {}

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

    return {
        "pubmed": pubmed_records,
        "opentargets": opentargets_records,
        "clinicaltrials": fetch_clinicaltrials(condition=indication, intervention=mechanism) or [],
        "aact": aact_records,
        "commercial": comm_bundle.get("commercial", []),
        "regulatory": fetch_regulatory_signals(query_terms[:5]) if query_terms else [],
        "market_news": fetch_press_releases(query_terms[:3], max_per_term=4) if query_terms else [],
        "patents": fetch_patent_signals(query_terms[:3], max_per_term=5) if query_terms else [],
    }


def run_pipeline(
    indication: str,
    mechanism: str,
    extra_context: str = "",
    cutoff_date: Optional[str] = None,
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

    indication/mechanism are normalized (trimmed + lowercased) right here, once,
    before touching any fetcher, cache key, or LLM prompt — "cancer" and "Cancer"
    must be the exact same query internally. All 5 sources already match search
    terms case-insensitively (confirmed directly against PubMed/Open
    Targets/ClinicalTrials.gov/AACT), so this never changes what's found, only
    whether two spellings of the same term share one cache entry and one set of
    LLM calls instead of two independent (and not necessarily identical) ones.
    This local reassignment doesn't affect the caller's own variables, so
    callers that build a report title from their original indication/mechanism
    (run_pipeline_markdown, the web UI's `query`) still show whatever case the
    user actually typed.
    """
    indication = indication.strip().lower()
    mechanism = mechanism.strip().lower()

    reset_usage_log()
    reset_dropped_claims_log()
    sources = collect_sources(indication=indication, mechanism=mechanism, cutoff_date=cutoff_date)
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
        sources=sources["pubmed"] + sources["opentargets"],
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
        )
    except Exception as e:
        print(f"[Pipeline Warning] Deterministic financials calculation failed: {e}")

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


def run_pipeline_markdown(
    indication: str,
    mechanism: str,
    extra_context: str = "",
    cutoff_date: Optional[str] = None,
) -> str:
    """Convenience wrapper for callers (CLI) that just want the rendered report."""
    claims, cost_summary, sources = run_pipeline(
        indication=indication,
        mechanism=mechanism,
        extra_context=extra_context,
        cutoff_date=cutoff_date,
    )

    cost_line = (
        f"Pipeline cost for this run: ~${cost_summary['approx_cost_usd']} "
        f"({cost_summary['calls']} calls, {cost_summary['model']}, "
        f"{cost_summary['input_tokens']} in / {cost_summary['output_tokens']} out tokens)"
    )
    return render_report(
        indication=indication,
        mechanism=mechanism,
        claims=claims,
        cost_summary=cost_line,
    )


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
