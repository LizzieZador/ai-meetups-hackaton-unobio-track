"""Clinical agent — development stage, trial design, regulatory path.

Owner: Clinical Analyst. Sources: ClinicalTrials.gov, AACT.
Covers Required Output section "clinical_development_plan" (regulatory pathway
and historical precedent are folded in here).
Chain: consumes the scientific agent's claims; its output feeds the commercial agent.

Enforces structural verification:
- Evidence claims cite source IDs (e.g. 'S1') with verbatim exact_snippet verified against raw snippets.
- Metadata (source, url, date_accessed) is stamped by code, never by the model.
- Inference claims require explicit reasoning, confidence ('low'|'medium'|'high'), and based_on source IDs.
- Section is stamped as 'clinical_development_plan' by code.
- Automatic retry on empty or malformed LLM outputs.
- Graceful fallback when sources list is empty (low-confidence inference only).
"""
from typing import Optional

from schema import Claim
from .base import label_sources, run_agent_llm_with_retry

SYSTEM_PROMPT = """You are a clinical development analyst on a biotech investment committee.
You receive:
1. Target/mechanism and indication (with optional modality, stage, biomarkers context).
2. Scientific analyst claims (target biology and human translation).
3. Labeled clinical trial snippets from ClinicalTrials.gov and AACT, each identified by an ID like [S1], [S2].

Produce a JSON array of claim objects covering the clinical development thesis.

COVER AT MINIMUM:
1. Proposed trial design (type "inference"): target patient population (line of therapy, biomarker inclusion), primary/secondary endpoints, comparator arm, approximate trial size, and development sequence (e.g., Phase 1/2 dose escalation -> pivotal Phase 3).
2. Actual trials in public registries (type "evidence"): current development stage, design, enrollment, and status of existing trials for this target/drug in this indication.
3. Historical successes and failures (type "evidence" if citing a specific trial, otherwise "inference"): similar programs in this mechanism class that succeeded or failed (e.g. terminated for futility or safety), and direct implications for the proposed program.
4. Biomarker multiplier & patient selection (type "inference"): explicit companion diagnostic or biomarker enrichment strategy (e.g. specific mutation, expression threshold, ctDNA) and how biomarker-guided patient selection acts as a success multiplier (historically ~2x higher PTRS vs unselected populations).
5. Trial economics & development timeline (type "inference"): estimated timeline to primary readout (months for enrollment + follow-up) and approximate capital required for the next clinical phase based on sample size and therapeutic area benchmark costs ($50k-$150k/patient).
6. PTRS (probability of technical & regulatory success) per phase (type "inference"):
   You MUST include an inference claim providing explicit PTRS percentages per phase formatted as:
   "PTRS Benchmarks: Phase 1 = [X]%, Phase 2 = [Y]%, Phase 3 = [Z]%, Cumulative PTRS = [W]%".
   Detail target-specific rationale, historical failure precedent, and biomarker multiplier adjustments in reasoning.
7. Regulatory pathway (type "inference"): expedited designations (Breakthrough, Fast Track), accelerated approval vs standard approval endpoints.
8. Clinical & Safety Diligence Gaps (CRITICAL, type "inference"):
   Produce at least 1-2 inference claims explicitly identifying high-stakes clinical unknowns that cannot be determined from public registries (e.g. unredacted patient-level adverse event narratives, dose-limiting toxicity kinetics, investigator brochure safety data, protocol amendments) and specifying what proprietary clinical data or regulatory audits are required.

STRICT SCHEMA RULES:
For type "evidence":
- "claim": concise factual statement.
- "type": "evidence"
- "evidence_id": ID of the source supporting this claim (e.g. "S1").
- "exact_snippet": VERBATIM text copied directly from that source's Snippet text. Do not paraphrase or edit.
(Code will verify this exact quote in the source and supply the source name, URL, and date).

For type "inference":
- "claim": analytical conclusion, recommendation, or assessment.
- "type": "inference"
- "reasoning": detailed logical justification.
- "confidence": "low", "medium", or "high".
- "based_on": array of source IDs used (e.g. ["S1"] or ["S1", "S2"]). Use [] if based on general clinical knowledge.

IMPORTANT CONSTRAINTS:
- If no sources are provided, you MUST produce ONLY type "inference" claims with "confidence": "low" and "based_on": [].
- Never invent an evidence_id or exact_snippet.
- Return ONLY the JSON array, nothing else.
"""


def clinical_agent(
    indication: str,
    mechanism: str,
    sources: list[dict],
    scientific_claims: list[Claim],
    extra_context: str = "",
    valid_sources: Optional[set] = None,
    max_claims: int = 12,
) -> list[Claim]:
    """Run the clinical development analyst agent with code-enforced verification.

    Args:
        indication: Disease / indication name.
        mechanism: Biological mechanism or target name.
        sources: Raw source records from ClinicalTrials.gov and AACT.
        scientific_claims: Upstream claims from the scientific agent.
        extra_context: Optional user-provided context (modality, biomarkers, etc.).
        valid_sources: Optional set of allowed source names (for pipeline compatibility).
        max_claims: Upper bound on accepted claims returned.

    Returns:
        List of validated Claim objects tagged section 'clinical_development_plan'.
    """
    source_map, prompt_sources_block = label_sources(sources)

    scientific_claims_summary = []
    for c in scientific_claims or []:
        c_dict = c.to_dict() if hasattr(c, "to_dict") else dict(c)
        scientific_claims_summary.append({
            "claim": c_dict.get("claim"),
            "type": c_dict.get("type"),
            "section": c_dict.get("section"),
            "reasoning": c_dict.get("reasoning"),
        })

    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + f"\nScientific analyst claims (upstream context):\n{scientific_claims_summary}\n\n"
        f"Available clinical sources:\n{prompt_sources_block}\n"
    )

    return run_agent_llm_with_retry(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        source_map=source_map,
        section="clinical_development_plan",
        agent_name="ClinicalAgent",
        max_claims=max_claims,
        valid_sources=valid_sources,
    )
