"""Chair agent — synthesizes all claims into a verdict + risk-adjusted return.
Owner: Тімлід / Голова комітету. Forbidden from relabeling any inference as
evidence, or changing any claim's section, while aggregating — it may only ADD
new claims. Covers Required Output sections "recommendation", "capital_required",
"key_risks", "critical_unknowns", and "diligence_questions".
"""
import json
from llm import ask_claude_json
from schema import Claim, parse_claims
CHAIR_SECTIONS = {
    "key_risks",
    "capital_required",
    "critical_unknowns",
    "diligence_questions",
    "recommendation",
}
VALID_VERDICTS = {"Invest", "Conditional", "Do Not Invest"}
SYSTEM_PROMPT = """You are the chair of a biotech investment committee, synthesizing
claims from three analysts (scientific, clinical, commercial) into a final verdict.
You will be given all of their claims and optional additional context.
Produce a JSON array of NEW claim objects only (do not repeat input claims).
CRITICAL RULES:
1. Every new claim MUST have "type": "inference" (these are committee judgments).
2. "section" MUST be one of exactly these five values:
   - "key_risks": Top 3-5 risks ranked by (probability x impact on rNPV). Reasoning MUST state explicit estimated probability (%) and impact on rNPV.
   - "capital_required": Capital required to reach the next major milestone, time to that milestone, future financing needs, and exit/licensing outcomes (use transparent ranges, e.g. $15M-$25M, 18-24 months).
   - "critical_unknowns": The most important unknowns that cannot be answered from public evidence and materially affect the investment case.
   - "diligence_questions": 5 to 10 highest-value diligence questions. For each, reasoning MUST specify what non-public data is required (e.g. proprietary assays, patient-level data, KOL interviews, CMC audit).
   - "recommendation": Must include:
     a) One claim estimating the simplified rNPV range: rNPV ~= (PTRS x Peak Sales) - Capital Required (Low / Base / High scenarios).
     b) One claim explaining whether the mechanism solves a major unmet need if it works.
     c) EXACTLY ONE final verdict claim where the "claim" field is EXACTLY one of these three strings: "Invest", "Conditional", or "Do Not Invest".
3. CONTRADICTIONS & RISK OVERRIDE:
   - Actively synthesize contradictions between analysts.
   - If the top key_risk has high probability and high impact (or if contradictory/toxic evidence is provided in context), the verdict CANNOT be "Invest". It must be "Conditional" or "Do Not Invest".
4. Never produce type "evidence" claims. Return ONLY the JSON array, nothing else.
"""
def chair_agent(
    indication: str,
    mechanism: str,
    scientific_claims: list[Claim],
    clinical_claims: list[Claim],
    commercial_claims: list[Claim],
    extra_context: str = "",
) -> list[Claim]:
    all_claims = scientific_claims + clinical_claims + commercial_claims
    # Групуємо claims для кращої читабельності моделлю
    structured_input = {
        "scientific_and_translation": [c.to_dict() for c in scientific_claims],
        "clinical_and_trials": [c.to_dict() for c in clinical_claims],
        "commercial_and_market": [c.to_dict() for c in commercial_claims],
    }
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional Context / Updates: {extra_context}\n" if extra_context else "")
        + f"\nAnalyst Claims by Discipline:\n"
        f"{json.dumps(structured_input, indent=2, ensure_ascii=False)}"
    )
    # 4000 токенів гарантують, що 5 секцій не обірвуться посеред валідації JSON
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt, max_tokens=4000)
    parsed = parse_claims(raw)
    # Програмна санітизація та захист (Code Enforcement)
    sanitized_synthesis: list[Claim] = []
    has_valid_verdict = False
    for c in parsed:
        # Голова має право писати ТІЛЬКИ у свої 5 секцій
        if c.section not in CHAIR_SECTIONS:
            continue
        # Примусово робимо inference, навіть якщо модель спробувала підставити evidence
        c.type = "inference"
        if not c.confidence:
            c.confidence = "medium"
        if not c.reasoning:
            c.reasoning = "Committee synthesis based on analyst evidence."
        # Нормалізація вердикту
        if c.section == "recommendation":
            cleaned_claim = c.claim.strip()
            for v in VALID_VERDICTS:
                if v.lower() in cleaned_claim.lower():
                    c.claim = v
                    has_valid_verdict = True
                    break
        sanitized_synthesis.append(c)
    # Страховка на випадок збою: гарантуємо наявність чіткого вердикту
    if not has_valid_verdict:
        sanitized_synthesis.append(
            Claim(
                claim="Conditional",
                type="inference",
                section="recommendation",
                reasoning="Defaulted to Conditional: Committee requires further primary validation.",
                confidence="low",
            )
        )
    return all_claims + sanitized_synthesis