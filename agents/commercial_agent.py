"""Commercial agent — competitive landscape & commercial opportunity.

Owner: Commercial Analyst.
Sources: AACT (competitors, stop_categories), Open Targets, manual epidemiology & pricing datasets.
Covers Required Output sections: "competitive_landscape" and "commercial_opportunity".

Executes two focused agent runs via agents/base.py:
1. competitive_landscape:
   - Approved therapies (Standard of Care benchmarks)
   - Clinical-stage competitors (Phase 1-3, sponsors, modalities)
   - Same-target vs Alternative therapies (with mandatory caveat regarding unverified mechanisms in AACT)
   - Discontinued programs categorized by AACT stop_category (business/enrollment vs safety/efficacy_futility)
   - Required differentiation to win in this indication
2. commercial_opportunity:
   - Standard of Care and unmet medical need
   - Addressable patient population
   - Pricing analogues
   - Deterministic market model (market_model.estimate_market) integration
   - Scientifically interesting vs Commercially valuable distinction
   - Critical commercial unknowns
"""
from typing import Optional

from llm import ask_claude_json
from market_model import REQUIRED_PARAMETERS, estimate_market
from schema import Claim
from sources.fetch_commercial import (
    collect_commercial_sources,
    get_epidemiology,
    get_pricing_analogues,
)
from .base import (
    claim_cap_instruction,
    label_sources,
    make_claim_object,
    run_agent_llm_with_retry,
    validate_and_build_claims,
)

SYSTEM_PROMPT_COMPETITIVE = """You are a biotech commercial and competitive intelligence analyst on an investment committee.
You evaluate the competitive dynamics, clinical pipeline, and target-level competition for a drug program.
You receive labeled snippets from AACT and Open Targets (each with an ID like [S1], [S2]).

Produce a JSON array of claim objects strictly for section "competitive_landscape".

COVER AT MINIMUM:
1. Approved therapies:
   Identify therapies currently approved for this indication and the lines of therapy they occupy.
2. Clinical-stage competitors:
   Identify competing programs in active clinical development (Phase 1, 2, or 3), noting modalities and leading sponsors.
3. Same-target vs Alternative therapies (CRITICAL DISTINCTION):
   Explicitly distinguish direct competitors sharing the same target/mechanism from alternative therapies with different mechanisms.
   MANDATORY CAVEAT: In AACT snippets, mechanisms of alternative therapies are not exhaustively cataloged; some may share the queried mechanism. You must explicitly state this caveat when discussing alternative therapies.
4. Discontinued programs & stop_category (CRITICAL DISTINCTION):
   Review terminated/withdrawn trials and classify them using the AACT stop_category:
   - "business" or "enrollment": terminations due to commercial strategy, corporate mergers, or slow accrual DO NOT indicate biological or mechanistic failure.
   - "safety" or "efficacy_futility": terminations due to toxicity, adverse events, or lack of clinical efficacy represent direct RED FLAGS for the target's biological thesis.
   If no discontinued trials are found, produce an inference claim noting that no terminated programs were identified in the queried dataset.
5. Required differentiation:
   State what specific clinical differentiation (superior efficacy/ORR/PFS, wider therapeutic window/safety, oral vs IV administration, or biomarker-defined subgroup) this program must demonstrate to displace incumbent therapies.

STRICT SCHEMA RULES:
For type "evidence":
- "claim": concise factual statement citing competitive trial data.
- "type": "evidence"
- "evidence_id": ID of the source supporting this claim (e.g. "S1").
- "exact_snippet": VERBATIM text copied directly from that source's Snippet text. Do not alter quotes.
For type "inference":
- "claim": analytical competitive conclusion.
- "type": "inference"
- "reasoning": concise logical justification (1-2 sentences).
- "confidence": "low", "medium", or "high".
- "based_on": array of source IDs used (e.g. ["S1"] or []).

IMPORTANT CONSTRAINTS:
- Never invent source IDs or exact snippets.
- Return ONLY the JSON array of claim objects, nothing else.
"""

SYSTEM_PROMPT_COMMERCIAL = """You are a biotech commercial and market opportunity analyst on an investment committee.
You evaluate the commercial viability, patient population sizing, pricing benchmarks, and economic thesis for a drug program.
You receive labeled snippets from epidemiology/market datasets and clinical summaries (each with an ID like [S1], [S2]), plus clinical analyst findings.

Produce a JSON array of claim objects strictly for section "commercial_opportunity".

COVER AT MINIMUM:
1. Standard of Care (SoC) and Unmet Medical Need:
   Detail current first-line and second-line SoC efficacy limitations, treatment resistance, and remaining patient need.
2. Addressable Patient Population:
   Analyze prevalence, annual incidence, and subpopulation segmentation (disease stage, biomarker prevalence).
3. Pricing Analogues:
   Evaluate annual treatment cost benchmarks based on approved targeted small molecules or biologics in this or adjacent indications.
4. Scientifically Interesting vs Commercially Valuable (MANDATORY DISTINCTION):
   Explicitly contrast scientific interest with commercial value. Explain whether this program addresses an attractive commercial market even if the underlying biology is completely sound (e.g. consider whether the patient pool is too niche, whether cheap generic SoC dominates, or whether administration/manufacturing burdens impair commercial viability).
5. Critical Commercial Unknowns & Diligence Gaps (CRITICAL):
   Identify commercial risks that could undermine the investment case (e.g. payer reimbursement hurdles, rapid competitor leapfrogging, diagnostic adoption friction, patent freedom-to-operate). For each, explicitly state what proprietary/non-public diligence is required (e.g. blinded KOL interviews, payer advisory boards, freedom-to-operate patent opinions, unredacted license agreements).
6. Deterministic Market Model Assumptions:
   To support patient funnel and peak sales modeling, include claims proposing parameter assumptions for the market model across:
   - "prevalence_or_incidence": total patient population
   - "diagnosed_fraction": fraction diagnosed (0.0 to 1.0)
   - "drug_eligible_fraction": fraction eligible for this mechanism (0.0 to 1.0)
   - "treated_penetration": peak market penetration among eligible patients (0.0 to 1.0)
   - "annual_price": annual price in USD
   - "compliance_rate": treatment compliance/duration rate (0.0 to 1.0)
   For each parameter, provide numeric estimates in your reasoning or as fields "parameter", "low", "base", "high", "basis" ("evidence:<id>" or "inference"), and "note".

STRICT SCHEMA RULES:
For type "evidence":
- "claim": concise factual statement citing epidemiology, pricing, or registry data.
- "type": "evidence"
- "evidence_id": source ID (e.g. "S1").
- "exact_snippet": VERBATIM text copied directly from that source's Snippet text.
For type "inference":
- "claim": analytical commercial conclusion or market assumption.
- "type": "inference"
- "reasoning": logical explanation.
- "confidence": "low", "medium", or "high".
- "based_on": array of source IDs used (e.g. ["S1"] or []).

IMPORTANT CONSTRAINTS:
- If epidemiology/pricing sources are unverified or missing, state market sizing as type "inference" with confidence "low".
- Return ONLY the JSON array of claim objects, nothing else.
"""


# Basis id of a fallback row from data/market_data.csv (verified rows only) —
# a real label, not a borrowed "S1" that belongs to some other fetched source.
VERIFIED_CSV_BASIS = "market_data.csv (verified)"


def _grounded_basis(basis, source_map: Optional[dict]) -> str:
    """'evidence:<id>' only when <id> is a source the model was shown; anything
    else (an invented or mistyped id, free text) is downgraded to 'inference'."""
    text = str(basis or "").strip()
    if text.lower().startswith("evidence:"):
        source_id = text.split(":", 1)[1].strip()
        if source_map and source_id in source_map:
            return f"evidence:{source_id}"
        return "inference"
    return "inference"


def _extract_market_assumptions_from_raw(
    raw_claims: list[dict],
    indication: str,
    cutoff_date: Optional[str] = None,
    source_map: Optional[dict] = None,
) -> dict[str, dict]:
    """Extract or construct market model parameter assumptions from LLM output and local data.

    These are read before the claim-level source check, so a basis of
    "evidence:<id>" is verified here: the id must be a source the model was
    actually shown (source_map), else the parameter counts as inference.
    """
    assumptions: dict[str, dict] = {}

    for item in raw_claims:
        if not isinstance(item, dict):
            continue

        param = item.get("parameter")
        if param in REQUIRED_PARAMETERS:
            base_val = item.get("base") or item.get("value")
            if base_val is not None:
                low_val = item.get("low", base_val)
                high_val = item.get("high", base_val)
                basis_val = _grounded_basis(item.get("basis"), source_map)
                note_val = item.get("note", item.get("reasoning", ""))
                assumptions[param] = {
                    "low": low_val,
                    "base": base_val,
                    "high": high_val,
                    "basis": basis_val,
                    "note": note_val,
                }

        # Check for nested assumptions dict if present
        nested = item.get("market_assumptions")
        if isinstance(nested, dict):
            for k, v in nested.items():
                if k in REQUIRED_PARAMETERS and isinstance(v, dict):
                    assumptions[k] = {**v, "basis": _grounded_basis(v.get("basis"), source_map)}

    # Fallback from the manual dataset if the LLM omitted a parameter — verified
    # rows only: an unverified hand-entered figure must never feed the rNPV.
    if "prevalence_or_incidence" not in assumptions:
        epi = get_epidemiology(indication, cutoff_date=cutoff_date)
        if epi and epi.get("is_evidence"):
            try:
                inc_val = float(str(epi.get("incidence", "")).replace(",", "").strip())
                assumptions["prevalence_or_incidence"] = {
                    "low": round(inc_val * 0.8),
                    "base": round(inc_val),
                    "high": round(inc_val * 1.2),
                    "basis": f"evidence:{VERIFIED_CSV_BASIS}",
                    "note": f"Grounded in verified epidemiology data for {indication}",
                }
            except (ValueError, TypeError):
                pass

    if "annual_price" not in assumptions:
        pricing = get_pricing_analogues(indication, cutoff_date=cutoff_date)
        if pricing and pricing.get("is_evidence"):
            try:
                price_val = float(str(pricing.get("annual_cost_usd", "")).replace(",", "").strip())
                assumptions["annual_price"] = {
                    "low": round(price_val * 0.8),
                    "base": round(price_val),
                    "high": round(price_val * 1.2),
                    "basis": f"evidence:{VERIFIED_CSV_BASIS}",
                    "note": f"Grounded in verified pricing data ({pricing.get('drug_name')})",
                }
            except (ValueError, TypeError):
                pass

    return assumptions


def _run_commercial_opportunity_pass(
    indication: str,
    mechanism: str,
    source_map: dict[str, dict],
    prompt_sources_block: str,
    clinical_claims: Optional[list[Claim]],
    extra_context: str,
    valid_sources: Optional[set],
    cutoff_date: Optional[str] = None,
    max_claims: int = 12,
) -> list[Claim]:
    """Execute commercial opportunity LLM pass, extract assumptions, and inject deterministic market model claim."""
    clinical_summary = []
    for c in clinical_claims or []:
        c_dict = c.to_dict() if hasattr(c, "to_dict") else dict(c)
        clinical_summary.append({
            "claim": c_dict.get("claim"),
            "type": c_dict.get("type"),
            "section": c_dict.get("section"),
            "reasoning": c_dict.get("reasoning"),
        })

    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + f"\nClinical analyst findings (upstream context):\n{clinical_summary}\n\n"
        f"Available commercial & competitive sources:\n{prompt_sources_block}\n"
        + claim_cap_instruction(max_claims)
        + " Market-parameter objects do not count toward this limit."
    )

    # 8000, not 4000: a reasoning-tier model (ChatGPT's gpt-6-luna included) spends
    # tokens on its own hidden reasoning before writing the visible answer — at a
    # tight ceiling that reasoning alone can exhaust the budget and the call comes
    # back completely empty. Confirmed live: this exact call ("Opportunity") was
    # the one that failed in the historical backtest, at this same 4000 ceiling,
    # while chair_agent's 8000-ceiling call in the same run didn't.
    raw = ask_claude_json(SYSTEM_PROMPT_COMMERCIAL, user_prompt, max_tokens=8000)
    if not raw:
        print("[CommercialAgent] Initial LLM response was empty or unparseable. Retrying once...")
        raw = ask_claude_json(SYSTEM_PROMPT_COMMERCIAL, user_prompt, max_tokens=8000)

    # 1. Extract market model assumptions before validating claim schema
    assumptions = _extract_market_assumptions_from_raw(raw, indication, cutoff_date=cutoff_date, source_map=source_map)

    # 2. Validate standard LLM claims
    accepted_claims = validate_and_build_claims(
        raw_claims=raw,
        source_map=source_map,
        section="commercial_opportunity",
        agent_name="CommercialAgent:Opportunity",
        max_claims=max_claims,
        valid_sources=valid_sources,
    )

    # 3. Deterministic market model execution in Python
    if assumptions:
        market_res = estimate_market(assumptions)
        if market_res["status"] in ("complete", "partial"):
            sales_m = market_res["peak_sales_millions"]
            funnel = market_res["funnel"]

            base_sales = sales_m.get("base")
            low_sales = sales_m.get("low")
            high_sales = sales_m.get("high")

            low_str = f"${low_sales:.1f}M" if low_sales is not None else "N/A"
            high_str = f"${high_sales:.1f}M" if high_sales is not None else "N/A"
            base_str = f"${base_sales:.1f}M" if base_sales is not None else "N/A"

            tot_b = f"{int(funnel['total']['base']):,}" if funnel["total"].get("base") is not None else "N/A"
            diag_b = f"{int(funnel['diagnosed']['base']):,}" if funnel["diagnosed"].get("base") is not None else "N/A"
            elig_b = f"{int(funnel['eligible']['base']):,}" if funnel["eligible"].get("base") is not None else "N/A"
            trt_b = f"{int(funnel['treated']['base']):,}" if funnel["treated"].get("base") is not None else "N/A"

            funnel_claim_text = (
                f"Deterministic patient funnel and peak sales estimate: Base peak sales projected at {base_str} "
                f"(Range: {low_str} - {high_str}). Funnel progression (Base): "
                f"{tot_b} total -> {diag_b} diagnosed -> {elig_b} eligible -> {trt_b} treated patients."
            )

            inf_params = market_res.get("inference_parameters", [])
            ev_params = market_res.get("evidence_parameters", [])

            reasoning_text = (
                f"Calculated deterministically by market_model.estimate_market from model assumptions. "
                f"Inference-based assumptions ({len(inf_params)}): {', '.join(inf_params) or 'none'}; "
                f"Evidence-grounded assumptions ({len(ev_params)}): {', '.join(ev_params) or 'none'}."
            )

            # Resolve based_on source IDs for evidence parameters
            based_ids: list[str] = []
            for ep in ev_params:
                b_str = str(assumptions[ep].get("basis", ""))
                if ":" in b_str:
                    clean_id = b_str.split(":", 1)[1].strip()
                    if clean_id in source_map and clean_id not in based_ids:
                        based_ids.append(clean_id)

            calc_confidence = "medium" if len(ev_params) >= 2 else "low"

            try:
                calc_claim = make_claim_object(
                    claim=funnel_claim_text,
                    claim_type="inference",
                    section="commercial_opportunity",
                    reasoning=reasoning_text,
                    confidence=calc_confidence,
                    based_on=based_ids,
                )
                accepted_claims.append(calc_claim)
            except Exception as e:
                print(f"[CommercialAgent] Failed to construct market model claim: {e}")

    return accepted_claims


def commercial_agent(
    indication: str,
    mechanism: str,
    sources: Optional[list[dict]] = None,
    clinical_claims: Optional[list[Claim]] = None,
    extra_context: str = "",
    valid_sources: Optional[set] = None,
    cutoff_date: Optional[str] = None,
    max_claims: int = 12,
) -> list[Claim]:
    """Commercial analyst agent with code-enforced dual-run verification.

    Args:
        indication: Disease or condition name.
        mechanism: Biological target or mechanism name.
        sources: Optional list of raw source dicts (from AACT, Open Targets, commercial CSV).
                 If None, calls collect_commercial_sources().
        clinical_claims: Upstream claims from the clinical development analyst.
        extra_context: Optional context string (modality, stage, biomarkers).
        valid_sources: Optional set of allowed source names for grounding validation.
        cutoff_date: Optional historical cutoff date (YYYY-MM-DD).
        max_claims: Maximum accepted claims per section.

    Returns:
        List of validated Claim objects for 'competitive_landscape' and 'commercial_opportunity'.
    """
    if sources is None:
        collected = collect_commercial_sources(indication=indication, mechanism=mechanism, cutoff_date=cutoff_date)
        all_sources = collected.get("competitive", []) + collected.get("commercial", [])
        if valid_sources is None:
            valid_sources = {s["source"] for s in all_sources if isinstance(s, dict) and s.get("source")}
    else:
        all_sources = sources

    source_map, prompt_sources_block = label_sources(all_sources)

    # -------------------------------------------------------------
    # Pass 1: Competitive Landscape
    # -------------------------------------------------------------
    competitive_user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + f"\nAvailable competitive & clinical sources:\n{prompt_sources_block}\n"
    )

    claims_competitive = run_agent_llm_with_retry(
        system_prompt=SYSTEM_PROMPT_COMPETITIVE,
        user_prompt=competitive_user_prompt,
        source_map=source_map,
        section="competitive_landscape",
        agent_name="CommercialAgent:Competitive",
        max_claims=max_claims,
        valid_sources=valid_sources,
    )

    # -------------------------------------------------------------
    # Pass 2: Commercial Opportunity & Deterministic Funnel
    # -------------------------------------------------------------
    claims_commercial = _run_commercial_opportunity_pass(
        indication=indication,
        mechanism=mechanism,
        source_map=source_map,
        prompt_sources_block=prompt_sources_block,
        clinical_claims=clinical_claims,
        extra_context=extra_context,
        valid_sources=valid_sources,
        cutoff_date=cutoff_date,
        max_claims=max_claims,
    )

    return claims_competitive + claims_commercial
