"""Chair agent — synthesizes all claims into a verdict + risk-adjusted return.
Owner: Team Lead / Committee Chair. Forbidden from relabeling any inference as
evidence, or changing any claim's section, while aggregating — it may only ADD
new claims. Covers Required Output sections "recommendation", "capital_required",
"key_risks", "critical_unknowns", and "diligence_questions".
"""
import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from financials import (
    FinancialResult,
    build_deterministic_financial_claims,
    format_financial_brief_for_chair,
    run_deterministic_financials,
)
from llm import ask_claude_json
from schema import Claim, parse_claims, SECTION_TITLES, SECTION_ALIASES, log_dropped_claim
CHAIR_SECTIONS = {
    "key_risks",
    "capital_required",
    "critical_unknowns",
    "diligence_questions",
    "recommendation",
}

REQUIRED_CHAIR_SECTIONS = [
    "recommendation",
    "key_risks",
    "capital_required",
    "critical_unknowns",
    "diligence_questions",
]

VALID_VERDICTS = {"Invest", "Conditional", "Do Not Invest"}

SECTION_PROMPT_RULES = {
    "key_risks": '- "key_risks": Top 3-5 risks ranked by (probability x impact on rNPV). Reasoning MUST state explicit estimated probability (%) and impact on rNPV.',
    "capital_required": '- "capital_required": Explain capital required to reach the next major milestone and total capital to commercial launch, citing the exact deterministic numbers provided in the financial model brief (Low/Base/High, timeline in months/years). Detail value inflection points (Phase 2 PoC) and out-licensing/M&A deal benchmarks.',
    "critical_unknowns": '- "critical_unknowns": Top 2-4 critical unanswerable unknowns or missing links that cannot be resolved from public data alone (e.g. human tissue target engagement, long-term toxicity kinetics, companion biomarker validation). Reasoning must explain why public data cannot answer it and what proprietary experiments or audits would resolve it.',
    "diligence_questions": """- "diligence_questions": YOU MUST PRODUCE BETWEEN 5 AND 10 INDIVIDUAL, DISTINCT CLAIM OBJECTS (min 5, max 10).
      Each question MUST be its OWN SEPARATE claim object in the JSON array. NEVER combine multiple questions into one claim or into a numbered list inside reasoning!
      Format for EACH diligence question:
        "claim": "Clear, direct, high-value question that CANNOT be answered from public evidence (e.g. 'What is the human therapeutic window between target inhibition and off-target cardiotoxicity?')",
        "reasoning": "Explicit non-public data required: [specify exact proprietary data needed: e.g. proprietary Phase 1 PK/PD line listings, unredacted GLP tox reports, FDA meeting correspondence, KOL prescribing interviews, CMC batch analytics, patent FTO legal opinions]",
        "confidence": "high"|"medium"|"low",
        "section": "diligence_questions",
        "type": "inference"
      Ensure cross-discipline coverage across Biology, Clinical/Safety, Regulatory/CMC, and Commercial/IP.""",
    "recommendation": """- "recommendation": Must contain EXACTLY THREE claims in this specific order:
      1) Executive Summary narrative claim:
         "claim": "Executive Summary: <A concise 2-3 sentence cohesive narrative synthesizing target biology, clinical feasibility, commercial position, primary failure mode, and conclusion>"
         "reasoning": "<Holistic synthesis of why the committee reached this decision>"
         "confidence": "high"|"medium"|"low"
      2) Structured rNPV valuation claim:
         "claim": MUST cite the exact calculated values provided in the financial model brief: "rNPV Scenarios: Low = $[Low]M, Base = $[Base]M, High = $[High]M"
         "reasoning": Explain the economic sensitivity, timeline discounting (WACC), commercial net margin (75%), and clinical PTRS assumptions behind these deterministic figures.
         "confidence": "high"
      3) Exactly one final verdict claim:
         "claim": MUST BE EXACTLY ONE WORD: "Invest", "Conditional", or "Do Not Invest".
         "reasoning": "<Definitive verdict rationale citing unmet need and top risks>".
         "confidence": "high"|"medium"|"low" """,
}

SYSTEM_PROMPT = """You are the chair of a biotech investment committee, synthesizing
claims from three analysts (scientific, clinical, commercial) into a final verdict.
You will be given all of their claims, optional additional context, and a DETERMINISTIC FINANCIAL MODEL calculated in code.
Produce a JSON array of NEW claim objects only (do not repeat input claims).

CRITICAL RULES:
1. Every new claim MUST have "type": "inference" (these are committee judgments).
2. "section" MUST be one of exactly these five values:
   - "key_risks": Top 3-5 risks ranked by (probability x impact on rNPV). Reasoning MUST state explicit estimated probability (%) and impact on rNPV.
   - "capital_required": Detail capital required to reach the next major milestone and commercial launch, citing the exact deterministic numbers from the financial model brief (Low/Base/High, timeline). Detail value inflection points (Phase 2 PoC) and licensing/M&A exit terms.
   - "critical_unknowns": Top 2-4 critical unanswerable unknowns or missing links that cannot be resolved from public data alone (e.g. human tissue target engagement, long-term toxicity kinetics, companion biomarker validation). Reasoning must explain why public data cannot answer it and what proprietary experiments or audits would resolve it.
   - "diligence_questions": YOU MUST PRODUCE BETWEEN 5 AND 10 INDIVIDUAL, DISTINCT CLAIM OBJECTS (min 5, max 10).
     Each question MUST be its OWN SEPARATE claim object in the JSON array. NEVER combine multiple questions into one claim or into a numbered list inside reasoning!
     Format for EACH diligence question:
       "claim": "Clear, direct, high-value question that CANNOT be answered from public evidence (e.g. 'What is the human therapeutic window between target inhibition and off-target cardiotoxicity?')",
       "reasoning": "Explicit non-public data required: [specify exact proprietary data needed: e.g. proprietary Phase 1 PK/PD line listings, unredacted GLP tox reports, FDA meeting correspondence, KOL prescribing interviews, CMC batch analytics, patent FTO legal opinions]",
       "confidence": "high"|"medium"|"low",
       "section": "diligence_questions",
       "type": "inference"
     You MUST ensure cross-discipline coverage across:
       - Biology / Translation (1-2 questions on target engagement, selectivity, biomarkers)
       - Clinical Development / Safety (2-3 questions on trial execution, patient-level safety, dosing)
       - Regulatory / CMC / Manufacturing (1-2 questions on FDA meeting minutes, formulation, yield)
       - Commercial / IP / Competition (1-2 questions on patent FTO, payer reimbursement, KOL adoption)
   - "recommendation": Must contain EXACTLY THREE claims in this specific order:
     1) Executive Summary narrative claim:
        "claim": "Executive Summary: <A concise 2-3 sentence cohesive narrative synthesizing target biology, clinical development feasibility, commercial market position, primary failure mode, and overarching committee conclusion>"
        "reasoning": "<Holistic synthesis of why the committee reached this decision based on cross-discipline evidence>"
        "confidence": "high"|"medium"|"low"
     2) Structured rNPV valuation claim:
        "claim": MUST cite the exact calculated values provided in the financial model brief: "rNPV Scenarios: Low = $[Low]M, Base = $[Base]M, High = $[High]M"
        "reasoning": Explain the economic drivers, sensitivity, commercial net margin (75%), and clinical PTRS assumptions behind these deterministic figures.
        "confidence": "high"
     3) Exactly one final verdict claim:
        "claim": MUST BE EXACTLY ONE WORD: "Invest", "Conditional", or "Do Not Invest".
        "reasoning": "<Definitive verdict rationale citing whether the mechanism solves an important unmet need and addressing top risks>".
        "confidence": "high"|"medium"|"low"

3. CONTRADICTIONS & RISK OVERRIDE:
   - Actively synthesize contradictions between analysts.
   - If the top key_risk has high probability and high impact (or if contradictory/toxic evidence is provided in context), the verdict CANNOT be "Invest". It must be "Conditional" or "Do Not Invest".
4. Never produce type "evidence" claims. Return ONLY the JSON array, nothing else.
"""


def _sanitize_chair_claims(
    claims: list[Claim],
    sanitized_synthesis: list[Claim],
    stage: str = "chair_agent",
) -> Claim | None:
    verdict_obj: Claim | None = None
    for c in claims:
        raw_section = (c.section or "").strip().lower()
        normalized_section = SECTION_ALIASES.get(raw_section, raw_section)
        if normalized_section in CHAIR_SECTIONS:
            c.section = normalized_section
        else:
            log_dropped_claim(stage, c.to_dict(), f"Unrecognized section '{c.section}'")
            print(f"[Chair Agent Warning] Unrecognized section '{c.section}' for claim: {c.claim[:60]}... Skipped.")
            continue

        c.type = "inference"
        if c.confidence not in ("low", "medium", "high"):
            c.confidence = "medium"
        if not c.reasoning:
            c.reasoning = "Committee synthesis based on analyst evidence."

        if c.section == "recommendation":
            claim_text = c.claim.strip()
            if claim_text.lower().startswith("executive summary:") or "rnpv" in claim_text.lower():
                sanitized_synthesis.append(c)
                continue

            claim_lower = claim_text.lower()
            if any(term in claim_lower for term in ("do not invest", "don't invest", "reject")):
                c.claim = "Do Not Invest"
                verdict_obj = c
            elif "conditional" in claim_lower:
                c.claim = "Conditional"
                verdict_obj = c
            elif "invest" in claim_lower:
                c.claim = "Invest"
                verdict_obj = c

        sanitized_synthesis.append(c)
    return verdict_obj


def chair_agent(
    indication: str,
    mechanism: str,
    scientific_claims: list[Claim],
    clinical_claims: list[Claim],
    commercial_claims: list[Claim],
    extra_context: str = "",
    financial_result: Optional[FinancialResult] = None,
    sources: Optional[dict] = None,
) -> list[Claim]:
    all_claims = scientific_claims + clinical_claims + commercial_claims
    structured_input = {
        "scientific_and_translation": [c.to_dict() for c in scientific_claims],
        "clinical_and_trials": [c.to_dict() for c in clinical_claims],
        "commercial_and_market": [c.to_dict() for c in commercial_claims],
    }

    if financial_result is None:
        try:
            financial_result = run_deterministic_financials(
                commercial_claims=commercial_claims,
                clinical_claims=clinical_claims,
                sources=sources,
                indication=indication,
                mechanism=mechanism,
            )
        except Exception as e:
            print(f"[Chair Agent Warning] Failed to compute deterministic financials: {e}")

    financial_brief = format_financial_brief_for_chair(financial_result) if financial_result else ""

    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional Context / Updates: {extra_context}\n" if extra_context else "")
        + f"\nAnalyst Claims by Discipline:\n"
        f"{json.dumps(structured_input, indent=2, ensure_ascii=False)}\n\n"
        + (f"{financial_brief}\n" if financial_brief else "")
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt, max_tokens=8000)
    parsed = parse_claims(raw, stage="chair_agent_initial")

    sanitized_synthesis: list[Claim] = []
    verdict_claim_obj = _sanitize_chair_claims(parsed, sanitized_synthesis, stage="chair_agent_initial")

    # Check that all required sections exist. If one is missing, re-ask only for missing sections (one retry)
    present_sections = {c.section for c in sanitized_synthesis}
    missing_sections = [s for s in REQUIRED_CHAIR_SECTIONS if s not in present_sections]

    if missing_sections:
        print(f"[Chair Agent] Missing required sections after initial pass: {missing_sections}. Re-asking model (1 retry)...")
        rules_text = "\n".join(SECTION_PROMPT_RULES[s] for s in missing_sections if s in SECTION_PROMPT_RULES)
        retry_system_prompt = f"""You are the chair of a biotech investment committee.
Your initial synthesis omitted the following REQUIRED section(s): {', '.join(missing_sections)}.

Produce a JSON array of NEW claim objects ONLY for these missing section(s):
{rules_text}

CRITICAL RULES:
1. Every new claim MUST have "type": "inference".
2. "section" MUST be one of: {missing_sections}.
3. Return ONLY the JSON array of claim objects, nothing else.
"""
        retry_user_prompt = (
            f"Indication: {indication}\n"
            f"Mechanism/target: {mechanism}\n"
            f"Please generate the missing committee claims for section(s): {', '.join(missing_sections)}.\n\n"
            + (f"{financial_brief}\n\n" if financial_brief else "")
            + f"Analyst Claims Summary:\n"
            f"{json.dumps(structured_input, indent=2, ensure_ascii=False)}"
        )
        try:
            raw_retry = ask_claude_json(retry_system_prompt, retry_user_prompt, max_tokens=4000)
            parsed_retry = parse_claims(raw_retry, stage="chair_agent_retry")
            retry_verdict = _sanitize_chair_claims(parsed_retry, sanitized_synthesis, stage="chair_agent_retry")
            if retry_verdict and not verdict_claim_obj:
                verdict_claim_obj = retry_verdict
        except Exception as e:
            print(f"[Chair Agent Warning] Retry for missing sections failed: {e}")

    # Safety net: ensure critical_unknowns has at least 1 grounded claim if still absent
    if not any(c.section == "critical_unknowns" for c in sanitized_synthesis):
        missing_links = [
            c for c in all_claims
            if c.section == "human_translation_thesis" and any(k in c.claim.lower() for k in ("missing link", "not demonstrated", "gap"))
        ]
        if missing_links:
            for ml in missing_links[:2]:
                gap_text = re.sub(r"(?i)^missing\s+link:?\s*", "", ml.claim).strip().rstrip(".")
                sanitized_synthesis.append(Claim(
                    claim=f"Critical Unknown: Human translation gap regarding {gap_text}",
                    type="inference",
                    section="critical_unknowns",
                    reasoning=ml.reasoning or "Requires non-public Phase 1 PK/PD serial tissue biopsy data and receptor occupancy assays.",
                    confidence="medium",
                ))
        else:
            sanitized_synthesis.append(Claim(
                claim=f"Critical Unknown: Exposure-response threshold and durable target engagement for {mechanism} in {indication} patients.",
                type="inference",
                section="critical_unknowns",
                reasoning="Public registry and literature data cannot confirm if efficacious tissue exposure is achieved safely without proprietary Phase 1 PK/PD datasets.",
                confidence="low",
            ))

    # Fallback: ensure a definitive verdict claim exists
    if not verdict_claim_obj:
        verdict_claim_obj = Claim(
            claim="Conditional",
            type="inference",
            section="recommendation",
            reasoning="Defaulted to Conditional: Committee requires further primary validation.",
            confidence="low",
        )
        sanitized_synthesis.append(verdict_claim_obj)

    # Programmatic rule enforcement: Deterministic Financials, High Risk Override, and 5-10 Diligence Questions
    if financial_result is not None:
        _enforce_deterministic_financials(sanitized_synthesis, financial_result)
    _enforce_risk_override_guardrail(all_claims, sanitized_synthesis)
    _enforce_diligence_questions_guardrail(all_claims, sanitized_synthesis, indication, mechanism)

    # Return combined list of all claims
    return all_claims + sanitized_synthesis


def _enforce_deterministic_financials(
    synthesis_claims: list[Claim],
    financial_result: FinancialResult,
) -> None:
    """Enforce deterministic rNPV valuation and capital requirements calculated in Python.

    The chair agent explains the numbers; code guarantees exact mathematical correctness.
    """
    val = financial_result.rnpv_millions
    expected_rnpv_prefix = f"rNPV Scenarios: Low = ${val['low']:.1f}M, Base = ${val['base']:.1f}M, High = ${val['high']:.1f}M"

    # 1. Enforce rNPV claim in recommendation section
    rnpv_claim = next((c for c in synthesis_claims if c.section == "recommendation" and "rnpv" in c.claim.lower()), None)
    if rnpv_claim:
        rnpv_claim.claim = expected_rnpv_prefix
        rnpv_claim.confidence = "high"
        if not rnpv_claim.reasoning or "rnpv" not in rnpv_claim.reasoning.lower():
            rnpv_claim.reasoning = (
                f"Discounted cash flow rNPV modeled deterministically at {financial_result.discount_rate*100:.1f}% WACC. "
                + (rnpv_claim.reasoning or "")
            )
    else:
        det_claims = build_deterministic_financial_claims(financial_result)
        verdict_idx = next(
            (i for i, c in enumerate(synthesis_claims) if c.section == "recommendation" and c.claim in VALID_VERDICTS),
            len(synthesis_claims),
        )
        synthesis_claims.insert(verdict_idx, det_claims[0])

    # 2. Enforce capital_required claims
    det_claims = build_deterministic_financial_claims(financial_result)
    cap_claims = [c for c in synthesis_claims if c.section == "capital_required"]
    has_numbers = any(re.search(r"\$\d+", c.claim) for c in cap_claims)
    if not cap_claims or not has_numbers:
        synthesis_claims.extend(det_claims[1:])


def _enforce_risk_override_guardrail(upstream_claims: list[Claim], synthesis_claims: list[Claim]) -> None:
    """Enforces committee rules:
    1. If top risk has high probability and high impact, verdict cannot be 'Invest' without explicit mitigation in reasoning.
    2. If upstream analyst evidence reports data deficiency ('insufficient evidence'), verdict cannot be 'Invest'.
    """
    key_risks = [c for c in synthesis_claims if c.section == "key_risks"]
    verdict_claim = next((c for c in synthesis_claims if c.section == "recommendation" and c.claim in VALID_VERDICTS), None)

    if not verdict_claim or verdict_claim.claim != "Invest":
        return

    # Rule 1: Data deficiency check (insufficient evidence)
    upstream_text = " ".join(f"{c.claim} {c.reasoning or ''}" for c in upstream_claims).lower()
    insufficient_markers = (
        "insufficient evidence",
        "no target association",
        "no clinical trials found",
        "lack of evidence",
    )
    if any(m in upstream_text for m in insufficient_markers):
        verdict_claim.claim = "Conditional"
        verdict_claim.reasoning = (
            "[Rule Enforcement: Data Deficiency] Upstream analysts reported insufficient evidence or missing foundational data. "
            "Per investment committee rules, verdict cannot be 'Invest' without validated evidence. Downgraded to Conditional. "
            + (verdict_claim.reasoning or "")
        )
        return

    # Rule 2: Top-Risk Override (High Probability & High Impact)
    if not key_risks:
        return

    top_risk = key_risks[0]
    risk_text = f"{top_risk.claim} {top_risk.reasoning or ''}".lower()

    # Detect high probability and high impact
    high_prob_markers = ("high prob", "probability: high", ">50%", "60%", "70%", "80%", "90%")
    high_impact_markers = ("high impact", "critical", "severe", "termination", "zero sales", "negative rpnv", "loss")

    has_high_prob = any(m in risk_text for m in high_prob_markers)
    has_high_impact = any(m in risk_text for m in high_impact_markers)

    if has_high_prob and has_high_impact:
        reasoning_lower = (verdict_claim.reasoning or "").lower()
        # Verify whether key terms from the risk description are addressed in the verdict
        risk_words = [w for w in top_risk.claim.lower().split() if len(w) > 4]
        is_explicitly_addressed = any(w in reasoning_lower for w in risk_words)

        if not is_explicitly_addressed:
            verdict_claim.claim = "Conditional"
            verdict_claim.reasoning = (
                f"[Rule Enforcement: High Risk Override] Top risk ('{top_risk.claim}') has both high probability "
                f"and high impact on rNPV. The committee downgraded the verdict from Invest to Conditional "
                f"because this critical risk was not explicitly mitigated. "
                + (verdict_claim.reasoning or "")
            )


def _is_duplicate_question(q_text: str, questions: list[Claim]) -> bool:
    """Checks whether question is already present (via semantic word overlap)."""
    words_q = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", q_text)}
    if not words_q:
        return False
    for existing in questions:
        words_ex = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", existing.claim)}
        if not words_ex:
            continue
        overlap = len(words_q & words_ex)
        if overlap / max(len(words_q), 1) >= 0.65 or overlap / max(len(words_ex), 1) >= 0.65:
            return True
    return False


def _classify_question_discipline(text: str) -> str:
    """Classifies question discipline for balanced committee coverage."""
    t = text.lower()
    if any(k in t for k in ("target", "biology", "biomarker", "selectivity", "kinome", "receptor", "occupancy", "translation", "mutation", "biopsy", "pathway")):
        return "BIOLOGY"
    elif any(k in t for k in ("fda", "ema", "regulatory", "hold", "crl", "cmc", "formulation", "yield", "manufacturing", "stability", "meeting minutes", "ind")):
        return "REGULATORY_CMC"
    elif any(k in t for k in ("patent", "fto", "freedom-to-operate", "payer", "reimbursement", "pricing", "competitor", "standard of care", "market", "commercial", "sales")):
        return "COMMERCIAL_IP"
    else:
        return "CLINICAL_SAFETY"


def _score_diligence_question(
    q: Claim,
    top_risks: list[Claim],
    critical_unknowns: list[Claim],
    missing_links: list[Claim],
    indication: str,
    mechanism: str,
) -> float:
    """Multi-factor diligence question scoring:
    - Deal-killers & top risks: highest score (+6..+9)
    - Translation gaps & Target Engagement (+5..+8)
    - Critical Unknowns (+4)
    - Safety and regulatory lethality factors (+4)
    - Commercial differentiation and FTO patents (+3)
    - Specificity of required non-public data (+2.5)
    - Model confidence (+1..+2)
    """
    score = 0.0
    text_q = f"{q.claim} {q.reasoning or ''}".lower()
    words_q = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", text_q)}

    # 1. Match with top risks (Deal-killers: highest priority)
    for idx, r in enumerate(top_risks[:3]):
        r_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", f"{r.claim} {r.reasoning or ''}")}
        overlap = len(words_q & r_words)
        if overlap >= 2:
            multiplier = 6.0 if idx == 0 else (4.0 if idx == 1 else 2.5)
            score += multiplier
            if any(term in f"{r.claim} {r.reasoning or ''}".lower() for term in ("high prob", "high impact", "fatal", "death", "toxic", "hold", "negative")):
                score += 3.0
            break

    # 2. Match with critical translation gaps (Missing links / Target engagement)
    for ml in missing_links:
        ml_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", ml.claim)}
        if len(words_q & ml_words) >= 2:
            score += 5.0
            break
    if any(k in text_q for k in ("target engagement", "receptor occupancy", "tissue biopsy", "exposure-response", "pharmacodynamic")):
        score += 3.0

    # 3. Match with Critical Unknowns
    for u in critical_unknowns:
        u_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", u.claim)}
        if len(words_q & u_words) >= 2:
            score += 4.0
            break

    # 4. Safety and regulatory lethality factors (Safety / Lethal risks / FDA)
    if any(k in text_q for k in ("safety", "toxic", "cardiotox", "hepatotox", "death", "mortality", "adverse event", "sae", "tolerab", "therapeutic window", "dose-limiting")):
        score += 4.0
    if any(k in text_q for k in ("fda", "clinical hold", "crl", "complete response", "regulatory", "ind", "accelerated approval")):
        score += 3.5

    # 5. Commercial differentiation, competition, and patents
    if any(k in text_q for k in ("head-to-head", "superiority", "differentiat", "standard of care", "displacement", "freedom-to-operate", "fto", "patent")):
        score += 3.0
    if any(k in text_q for k in ("reimbursement", "payer", "formulary", "pricing", "budget impact")):
        score += 2.5

    # 6. Specificity of required confidential data (Concrete Non-Public Data)
    reason_lower = (q.reasoning or "").lower()
    if any(k in reason_lower for k in ("phase 1", "pk/pd", "unredacted", "patient-level", "crf", "line listing", "biopsy", "glp", "fto legal opinion", "batch yield", "advisory board")):
        score += 2.5

    # 7. Model confidence
    if q.confidence == "high":
        score += 2.0
    elif q.confidence == "medium":
        score += 1.0

    return score


def _unpack_diligence_questions(existing_questions: list[Claim]) -> list[Claim]:
    """Unpacks grouped question lists into individual Claim objects."""
    unpacked: list[Claim] = []
    for c in existing_questions:
        claim_text = (c.claim or "").strip()
        reason_text = (c.reasoning or "").strip()

        # Attempt 1: Split by numbering or list markers
        split_items = re.split(r"(?:^|\n|\s+)(?:\d+[\.\)]|[-*])\s+", claim_text)
        claim_items = [item.strip() for item in split_items if item and len(item.strip()) > 10]

        # Attempt 2: If no list format, split multiple sentences ending with '?'
        if len(claim_items) < 2 and claim_text.count("?") >= 2:
            raw_qs = [q.strip() for q in re.split(r"\?\s+", claim_text) if len(q.strip()) > 10]
            if len(raw_qs) >= 2:
                claim_items = [q if q.endswith("?") else f"{q}?" for q in raw_qs]

        if len(claim_items) >= 2:
            # Attempt to split reasoning by item number if grouped
            split_reasons = re.split(r"(?:^|\n|\s+)(?:\d+[\.\)]|[-*])\s+", reason_text)
            reason_items = [item.strip() for item in split_reasons if item and len(item.strip()) > 5]

            for idx, item in enumerate(claim_items):
                parts = re.split(r"(?i)\s*(?:requires?|requiring|needing|data required:?|non-public:?)\s*", item, maxsplit=1)
                q_text = parts[0].strip(" -:;,")

                if len(parts) > 1:
                    data_text = parts[1].strip()
                elif idx < len(reason_items):
                    data_text = reason_items[idx]
                else:
                    data_text = reason_text or "Requires proprietary non-public documentation and audit data."

                q_text = re.sub(r"^\d+[\.\)]\s*", "", q_text)
                if not q_text.endswith("?"):
                    q_text += "?"

                data_clean = re.sub(r"^(?:requires:?\s*)+", "", data_text, flags=re.I).strip()
                unpacked.append(
                    Claim(
                        claim=q_text,
                        type="inference",
                        section="diligence_questions",
                        reasoning=f"Requires {data_clean}" if data_clean else "Requires proprietary non-public documentation and audit data.",
                        confidence=c.confidence or "high",
                    )
                )
        else:
            c_text = re.sub(r"^\d+[\.\)]\s*", "", claim_text)
            c_text = re.sub(r"^[-*]\s*", "", c_text)
            c.claim = c_text if c_text.endswith("?") else f"{c_text}?"
            unpacked.append(c)

    return unpacked


def _synthesize_grounded_candidates(
    upstream_claims: list[Claim], synthesis_claims: list[Claim], indication: str, mechanism: str
) -> list[Claim]:
    """Synthesizes additional diligence questions strictly grounded in existing database claims:
    - critical_unknowns
    - human_translation_thesis (translation gaps and MISSING LINKs)
    - key_risks
    - clinical_development_plan (safety signals, trial terminations, stratification)
    - competitive_landscape & commercial_opportunity (competitors, reimbursement)
    No claims are hallucinated.
    """
    candidates: list[Claim] = []

    # 1. Grounded on Critical Unknowns (highest confidence base: committee identified unknowns)
    for u in synthesis_claims:
        if u.section == "critical_unknowns":
            u_clean = u.claim.strip().rstrip(".")
            q_claim = u_clean if u_clean.endswith("?") else f"How will the clinical development plan empirically resolve the critical unknown: {u_clean}?"
            reason = u.reasoning or "Requires confidential Phase 1/2 clinical study reports and primary bioanalytical data."
            if not reason.lower().startswith("requires"):
                reason = f"Requires proprietary data room documentation: {reason}"
            candidates.append(
                Claim(
                    claim=q_claim,
                    type="inference",
                    section="diligence_questions",
                    reasoning=reason,
                    confidence="high",
                )
            )

    # 2. Grounded on human translation gaps (MISSING LINK in scientific_claims)
    for c in upstream_claims:
        if c.section == "human_translation_thesis" and any(k in c.claim.lower() for k in ("missing link", "not demonstrated", "gap")):
            gap_clean = re.sub(r"(?i)^missing\s+link:?\s*", "", c.claim).strip().rstrip(".")
            candidates.append(
                Claim(
                    claim=f"What human translation assays or clinical biopsy data bridge the identified gap: {gap_clean}?",
                    type="inference",
                    section="diligence_questions",
                    reasoning="Requires proprietary human tissue target engagement, serial biopsies, and quantitative pharmacodynamic assay data.",
                    confidence="high",
                )
            )

    # 3. Grounded on identified top risks (key_risks)
    for r in synthesis_claims:
        if r.section == "key_risks":
            risk_clean = re.sub(r"(?i)\s*\(estimated\s+probability.*?\)", "", r.claim).strip().rstrip(".")
            candidates.append(
                Claim(
                    claim=f"What preclinical or clinical mitigation protocol and stopping criteria address the key risk of {risk_clean}?",
                    type="inference",
                    section="diligence_questions",
                    reasoning=f"Requires confidential risk mitigation protocols, safety monitoring committee charters, and: {r.reasoning or 'investigator brochures'}.",
                    confidence="high",
                )
            )

    # 4. Grounded on clinical safety signals or trials
    for c in upstream_claims:
        if c.section == "clinical_development_plan":
            c_lower = c.claim.lower()
            if any(k in c_lower for k in ("halt", "terminat", "suspend", "withdrawn", "safety", "toxic", "cardiotox", "mortality", "death")):
                candidates.append(
                    Claim(
                        claim=f"What specific protocol modifications or safety margins address the clinical signal observed in {c.claim.strip().rstrip('.')}?",
                        type="inference",
                        section="diligence_questions",
                        reasoning="Requires unredacted patient-level serious adverse event (SAE) line listings, DSMB correspondence, and investigator brochures.",
                        confidence="high",
                    )
                )
            elif any(k in c_lower for k in ("biomarker", "companion diagnostic", "stratification", "enrichment")):
                candidates.append(
                    Claim(
                        claim=f"What analytical and clinical validation data confirms the companion diagnostic threshold for patient selection in {indication}?",
                        type="inference",
                        section="diligence_questions",
                        reasoning="Requires companion diagnostic analytical validation dossiers, retrospective ROC curves, and prospective stratification protocols.",
                        confidence="medium",
                    )
                )

    # 5. Grounded on competitive landscape and commercial differentiation
    for c in upstream_claims:
        if c.section == "competitive_landscape" and any(k in c.claim.lower() for k in ("competitor", "approved", "standard of care")):
            candidates.append(
                Claim(
                    claim=f"What head-to-head or comparative clinical evidence demonstrates meaningful differentiation over standard of care in {indication}?",
                    type="inference",
                    section="diligence_questions",
                    reasoning="Requires target product profile (TPP) benchmark tables, comparative clinical readouts, and qualitative payer advisory board feedback.",
                    confidence="medium",
                )
            )
            break

    # 6. Grounded on reimbursement and market access
    for c in upstream_claims:
        if c.section == "commercial_opportunity" and any(k in c.claim.lower() for k in ("pricing", "reimbursement", "payer", "market access")):
            candidates.append(
                Claim(
                    claim=f"What health economic budget impact models and clinical benefit margins support commercial reimbursement at target pricing in {indication}?",
                    type="inference",
                    section="diligence_questions",
                    reasoning="Requires confidential payer advisory board transcripts, health-economic models, and pricing dossier submissions.",
                    confidence="medium",
                )
            )
            break

    # 7. Fundamental target validation and FTO questions (tied to mechanism and indication)
    candidates.append(
        Claim(
            claim=f"Has sustained {mechanism} target engagement and pathway modulation been quantitatively confirmed in human {indication} tissue at tolerable exposures?",
            type="inference",
            section="diligence_questions",
            reasoning="Requires proprietary Phase 1 PK/PD serial tissue biopsy data, radiolabeled PET receptor occupancy studies, or ex-vivo target engagement assays.",
            confidence="high",
        )
    )
    candidates.append(
        Claim(
            claim=f"Does {mechanism} hold a defensible freedom-to-operate (FTO) posture against competitor composition-of-matter and method-of-use patents in {indication}?",
            type="inference",
            section="diligence_questions",
            reasoning="Requires third-party privileged patent freedom-to-operate legal opinions, claim chart matrices, and unredacted prosecution history.",
            confidence="medium",
        )
    )

    return candidates


def _select_top_diligence_questions(
    candidates: list[Claim],
    top_risks: list[Claim],
    critical_unknowns: list[Claim],
    missing_links: list[Claim],
    indication: str,
    mechanism: str,
    target_count: int = 10,
) -> list[Claim]:
    """Selects top critical questions while ensuring cross-disciplinary balance."""
    if len(candidates) <= target_count:
        return sorted(
            candidates,
            key=lambda q: _score_diligence_question(q, top_risks, critical_unknowns, missing_links, indication, mechanism),
            reverse=True,
        )

    # Calculate scores for all candidates
    scored = [
        (q, _score_diligence_question(q, top_risks, critical_unknowns, missing_links, indication, mechanism))
        for q in candidates
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    selected: list[Claim] = []
    selected_claims_set = set()

    # 1. Guaranteed discipline coverage: select top-1 question from each discipline
    by_disc = defaultdict(list)
    for q, sc in scored:
        disc = _classify_question_discipline(f"{q.claim} {q.reasoning or ''}")
        by_disc[disc].append((q, sc))

    for disc in ("CLINICAL_SAFETY", "BIOLOGY", "REGULATORY_CMC", "COMMERCIAL_IP"):
        if disc in by_disc and by_disc[disc]:
            top_q = by_disc[disc][0][0]
            if top_q.claim not in selected_claims_set:
                selected.append(top_q)
                selected_claims_set.add(top_q.claim)

    # 2. Fill remaining slots up to target_count by absolute score
    for q, sc in scored:
        if len(selected) >= target_count:
            break
        if q.claim not in selected_claims_set:
            selected.append(q)
            selected_claims_set.add(q.claim)

    # Final sort by importance score
    selected.sort(
        key=lambda q: _score_diligence_question(q, top_risks, critical_unknowns, missing_links, indication, mechanism),
        reverse=True,
    )
    return selected


def _enforce_diligence_questions_guardrail(
    upstream_claims: list[Claim], synthesis_claims: list[Claim], indication: str, mechanism: str
) -> None:
    """Enforces 5 to 10 distinct, prioritized diligence questions.
    1. Unpacks aggregated or numbered lists into individual Claim objects.
    2. If questions < 5, supplements with questions grounded in existing claims
       (critical_unknowns, human translation missing links, key_risks, clinical trials, competitors).
       No claims are hallucinated.
    3. If questions > 10, applies multi-factor ranking by criticality (deal-killers, safety,
       translation gaps, data room specificity, disciplinary coverage) and retains top 10.
    """
    existing_questions = [c for c in synthesis_claims if c.section == "diligence_questions"]
    unpacked_questions: list[Claim] = _unpack_diligence_questions(existing_questions)

    # Clear existing questions before building final list
    synthesis_claims[:] = [c for c in synthesis_claims if c.section != "diligence_questions"]

    # Extract key risks, unknowns, and gaps for grounding
    top_risks = [c for c in synthesis_claims if c.section == "key_risks"]
    critical_unknowns = [c for c in synthesis_claims if c.section == "critical_unknowns"]
    missing_links = [
        c for c in upstream_claims
        if c.section == "human_translation_thesis" and any(k in c.claim.lower() for k in ("missing link", "not demonstrated", "gap"))
    ]

    # Remove duplicates among unpacked questions
    deduped_questions: list[Claim] = []
    for q in unpacked_questions:
        if not _is_duplicate_question(q.claim, deduped_questions):
            deduped_questions.append(q)

    # If fewer than 5 questions, supplement from existing database claims (data-grounded)
    if len(deduped_questions) < 5:
        grounded_candidates = _synthesize_grounded_candidates(
            upstream_claims, synthesis_claims, indication, mechanism
        )
        for cand in grounded_candidates:
            if len(deduped_questions) >= 5:
                break
            if not _is_duplicate_question(cand.claim, deduped_questions):
                deduped_questions.append(cand)

    # Smart selection and ranking (5 to 10 questions)
    final_questions = _select_top_diligence_questions(
        deduped_questions, top_risks, critical_unknowns, missing_links, indication, mechanism, target_count=10
    )

    synthesis_claims.extend(final_questions)


def generate_executive_summary(claims: list[Claim]) -> str:
    """Generates an Executive Summary synthesized from analyst and chair claims."""
    by_section: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_section[c.section].append(c)

    # 1. Verdict
    verdict_claims = [c for c in by_section.get("recommendation", []) if c.claim in VALID_VERDICTS]
    verdict = verdict_claims[0].claim if verdict_claims else "Conditional"

    # 2. Cohesive narrative synthesis if provided by LLM:
    llm_summary_claim = next(
        (c for c in by_section.get("recommendation", []) if c.claim.lower().startswith("executive summary:")),
        None,
    )
    llm_narrative = ""
    if llm_summary_claim:
        llm_narrative = llm_summary_claim.claim.replace("Executive Summary:", "").strip()
        if llm_summary_claim.reasoning:
            llm_narrative += f" (Rationale: {llm_summary_claim.reasoning})"

    # 3. Scientific & Human Translation (from scientific analyst)
    sci_claims = by_section.get("scientific_thesis", [])
    trans_claims = by_section.get("human_translation_thesis", [])
    sci_pts = [f"- **Target Biology:** {c.claim}" for c in sci_claims[:2]]
    trans_pts = [f"- **Human Translation:** {c.claim}" for c in trans_claims[:1]]

    # 4. Clinical Development Plan (from clinical analyst)
    clin_claims = by_section.get("clinical_development_plan", [])
    clin_pts = [f"- **Clinical Development:** {c.claim}" for c in clin_claims[:2]]

    # 5. Competitive Landscape & Commercial Opportunity (from commercial analyst)
    comp_claims = by_section.get("competitive_landscape", [])
    comm_claims = by_section.get("commercial_opportunity", [])
    comp_pts = [f"- **Competitive Field:** {c.claim}" for c in comp_claims[:1]]
    comm_pts = [f"- **Market & Unmet Need:** {c.claim}" for c in comm_claims[:1]]

    # 6. Risks & rNPV
    risk_claims = by_section.get("key_risks", [])
    top_risk_pt = [f"- **Primary Threat:** {risk_claims[0].claim}"] if risk_claims else []
    rnpv_claims = [c for c in by_section.get("recommendation", []) if "rnpv" in c.claim.lower()]
    rnpv_pt = [f"- **Valuation (rNPV):** {rnpv_claims[0].claim}"] if rnpv_claims else []

    summary = [
        "## Executive Summary",
        f"**Underwriting Recommendation:** `{verdict}`",
        "",
    ]
    if llm_narrative:
        summary.extend([f"**Committee Synthesis Narrative:** {llm_narrative}", ""])

    summary.append("**Core Evidence Drivers Across Dimensions:**")
    summary.extend(sci_pts)
    summary.extend(trans_pts)
    summary.extend(clin_pts)
    summary.extend(comp_pts)
    summary.extend(comm_pts)
    summary.extend(top_risk_pt)
    summary.extend(rnpv_pt)

    return "\n".join(summary)


def render_underwriting_report(
    indication: str, mechanism: str, claims: list[Claim], cost_summary: str | None = None
) -> str:
    """Renders final Investment Underwriting Report (delegates to report.render_report)."""
    from report import render_report
    return render_report(indication, mechanism, claims, cost_summary)