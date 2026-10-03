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
from report import render_report
from schema import Claim
from sources.fetch_aact import fetch_aact
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_opentargets import fetch_opentargets
from sources.fetch_pubmed import fetch_pubmed

REPORTS_DIR = Path(__file__).resolve().parent / "data" / "reports"


def collect_sources(indication: str, mechanism: str) -> dict:
    """Fetch raw snippets from all four sources.

    Any source that fails (or returns None) becomes an empty list here — the
    pipeline keeps going with whatever evidence is actually available.
    """
    return {
        "pubmed": fetch_pubmed(f"{mechanism} {indication}") or [],
        "opentargets": fetch_opentargets(mechanism, indication) or [],
        "clinicaltrials": fetch_clinicaltrials(indication, mechanism) or [],
        "aact": fetch_aact(indication, mechanism) or [],
    }


def run_pipeline(
    indication: str, mechanism: str, extra_context: str = ""
) -> tuple[list[Claim], dict]:
    """Run the full committee and return (final_claims, cost_summary).

    extra_context carries the task brief's optional input info (modality,
    development stage, preclinical results, biomarkers, route of administration)
    as free text — every agent receives it if provided, but it's never required.
    """
    reset_usage_log()
    sources = collect_sources(indication, mechanism)

    scientific_claims = scientific_agent(
        indication, mechanism, sources["pubmed"] + sources["opentargets"], extra_context
    )
    clinical_claims = clinical_agent(
        indication, mechanism, sources["clinicaltrials"] + sources["aact"], scientific_claims, extra_context
    )
    commercial_claims = commercial_agent(
        indication, mechanism, sources["aact"] + sources["opentargets"], clinical_claims, extra_context
    )
    final_claims = chair_agent(
        indication, mechanism, scientific_claims, clinical_claims, commercial_claims
    )

    cost_summary = get_usage_summary()
    _write_trace(indication, mechanism, extra_context, sources, final_claims, cost_summary)
    return final_claims, cost_summary


def run_pipeline_markdown(indication: str, mechanism: str, extra_context: str = "") -> str:
    """Convenience wrapper for callers (CLI) that just want the rendered report."""
    claims, cost_summary = run_pipeline(indication, mechanism, extra_context)
    cost_line = (
        f"Pipeline cost for this run: ~${cost_summary['approx_cost_usd']} "
        f"({cost_summary['calls']} calls, {cost_summary['model']}, "
        f"{cost_summary['input_tokens']} in / {cost_summary['output_tokens']} out tokens)"
    )
    return render_report(indication, mechanism, claims, cost_summary=cost_line)


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
