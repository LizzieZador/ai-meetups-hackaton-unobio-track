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
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents.chair_agent import chair_agent
from agents.clinical_agent import clinical_agent
from agents.commercial_agent import commercial_agent
from agents.scientific_agent import scientific_agent
from llm import get_usage_summary, reset_usage_log
from raw_data import collect_valid_sources, extract_analogs, render_raw_data_markdown
from report import render_report
from schema import Claim
from sources.fetch_aact import fetch_aact
from sources.fetch_commercial import fetch_commercial
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_opentargets import fetch_opentargets
from sources.fetch_pubmed import fetch_pubmed

REPORTS_DIR = Path(__file__).resolve().parent / "data" / "reports"


def collect_sources(indication: str, mechanism: str) -> dict:
    """Fetch raw snippets from all five sources.

    Any source that fails (or returns None) becomes an empty list here — the
    pipeline keeps going with whatever evidence is actually available.
    """
    return {
        "pubmed": fetch_pubmed(f"{mechanism} {indication}") or [],
        "opentargets": fetch_opentargets(mechanism, indication) or [],
        "clinicaltrials": fetch_clinicaltrials(indication, mechanism) or [],
        "aact": fetch_aact(indication, mechanism) or [],
        "commercial": fetch_commercial(indication) or [],
    }


def run_pipeline(
    indication: str, mechanism: str, extra_context: str = ""
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
    """
    reset_usage_log()
    sources = collect_sources(indication, mechanism)
    valid_sources = collect_valid_sources(sources)

    # The raw `sources` above must reach the caller even if the LLM is totally
    # down (quota exhausted, provider outage, no key) — the site's primary
    # output is the connected-database data, not the LLM. Each agent call is
    # therefore isolated: a failure there costs that agent's claims, never the
    # whole response.
    scientific_claims = _safe_agent_call(
        "scientific_agent", scientific_agent,
        indication, mechanism, sources["pubmed"] + sources["opentargets"], extra_context, valid_sources,
    )
    clinical_claims = _safe_agent_call(
        "clinical_agent", clinical_agent,
        indication, mechanism, sources["clinicaltrials"] + sources["aact"], scientific_claims,
        extra_context, valid_sources,
    )
    commercial_claims = _safe_agent_call(
        "commercial_agent", commercial_agent,
        indication, mechanism, sources["aact"] + sources["opentargets"] + sources["commercial"],
        clinical_claims, extra_context, valid_sources,
    )
    try:
        final_claims = chair_agent(
            indication, mechanism, scientific_claims, clinical_claims, commercial_claims,
            extra_context=extra_context,
        )
    except Exception as e:
        print(f"[Pipeline Warning] chair_agent failed: {e}")
        final_claims = scientific_claims + clinical_claims + commercial_claims

    cost_summary = get_usage_summary()
    _write_trace(indication, mechanism, extra_context, sources, final_claims, cost_summary)
    return final_claims, cost_summary, sources


def _safe_agent_call(name: str, fn, *args, **kwargs) -> list[Claim]:
    """Run one agent; on any failure (LLM down, bad key, timeout) log it and
    contribute zero claims instead of losing the whole response."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"[Pipeline Warning] {name} failed: {e}")
        return []


def run_pipeline_markdown(indication: str, mechanism: str, extra_context: str = "") -> str:
    """Convenience wrapper for callers (CLI) that just want the rendered report.

    Renders the raw source data + analogs FIRST (no LLM), then the committee's
    LLM-based verdict/analysis below it.
    """
    claims, cost_summary, sources = run_pipeline(indication, mechanism, extra_context)
    analogs = extract_analogs(sources)
    raw_section = render_raw_data_markdown(indication, mechanism, sources, analogs)

    cost_line = (
        f"Pipeline cost for this run: ~${cost_summary['approx_cost_usd']} "
        f"({cost_summary['calls']} calls, {cost_summary['model']}, "
        f"{cost_summary['input_tokens']} in / {cost_summary['output_tokens']} out tokens)"
    )
    committee_section = render_report(indication, mechanism, claims, cost_summary=cost_line)
    return raw_section + "\n\n---\n\n" + committee_section


def _write_trace(
    indication: str,
    mechanism: str,
    extra_context: str,
    sources: dict,
    claims: list[Claim],
    cost_summary: dict,
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = "".join(c if c.isalnum() else "_" for c in f"{indication}_{mechanism}".lower())[:60]
    path = REPORTS_DIR / f"run_{timestamp}_{slug}.json"
    trace = {
        "indication": indication,
        "mechanism": mechanism,
        "extra_context": extra_context,
        "timestamp": timestamp,
        "sources": sources,
        "claims": [c.to_dict() for c in claims],
        "cost_summary": cost_summary,
    }
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
