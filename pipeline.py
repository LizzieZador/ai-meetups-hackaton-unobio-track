"""Orchestration — plain Python functions, no agent framework.

collect_sources() -> scientific_agent() -> clinical_agent() -> commercial_agent()
                   -> chair_agent() -> render_report()
"""
from agents.chair_agent import chair_agent
from agents.clinical_agent import clinical_agent
from agents.commercial_agent import commercial_agent
from agents.scientific_agent import scientific_agent
from report import render_report
from sources.fetch_aact import fetch_aact
from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_opentargets import fetch_opentargets
from sources.fetch_pubmed import fetch_pubmed


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


def run_pipeline(indication: str, mechanism: str) -> str:
    sources = collect_sources(indication, mechanism)

    scientific_claims = scientific_agent(
        indication, mechanism, sources["pubmed"] + sources["opentargets"]
    )
    clinical_claims = clinical_agent(
        indication, mechanism, sources["clinicaltrials"] + sources["aact"], scientific_claims
    )
    commercial_claims = commercial_agent(
        indication, mechanism, sources["aact"] + sources["opentargets"], clinical_claims
    )
    final_claims = chair_agent(
        indication, mechanism, scientific_claims, clinical_claims, commercial_claims
    )

    return render_report(indication, mechanism, final_claims)
