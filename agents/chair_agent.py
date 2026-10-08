"""Chair agent — synthesizes all claims into a verdict + risk-adjusted return.
Owner: Тімлід / Голова комітету. Forbidden from relabeling any inference as
evidence, or changing any claim's section, while aggregating — it may only ADD
new claims. Covers Required Output sections "recommendation", "capital_required",
"key_risks", "critical_unknowns", and "diligence_questions".
"""
import datetime
import json
import re
from collections import defaultdict
from llm import ask_claude_json
from schema import Claim, parse_claims, SECTION_TITLES
CHAIR_SECTIONS = {
    "key_risks",
    "capital_required",
    "critical_unknowns",
    "diligence_questions",
    "recommendation",
}

SECTION_ALIASES = {
    # key_risks aliases
    "key_risk": "key_risks",
    "risk": "key_risks",
    "risks": "key_risks",
    "failure_modes": "key_risks",
    "key_risks_and_failure_modes": "key_risks",
    # capital_required aliases
    "capital": "capital_required",
    "capital_requirement": "capital_required",
    "capital_needed": "capital_required",
    "capital_requirements": "capital_required",
    # critical_unknowns aliases
    "unknown": "critical_unknowns",
    "unknowns": "critical_unknowns",
    "critical_unknown": "critical_unknowns",
    # diligence_questions aliases
    "diligence_question": "diligence_questions",
    "diligence": "diligence_questions",
    "questions": "diligence_questions",
    "highest_value_diligence_questions": "diligence_questions",
    # recommendation aliases
    "verdict": "recommendation",
    "recommendations": "recommendation",
    "investment_recommendation": "recommendation",
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
        "claim": MUST follow this EXACT template: "rNPV Scenarios: Low = $[X]M, Base = $[Y]M, High = $[Z]M"
        "reasoning": MUST explicitly calculate all 3 scenarios: "Low: ([PTRS_Low]% x $[PeakSales_Low]M) - $[Cap_High]M = $[X]M | Base: ([PTRS_Base]% x $[PeakSales_Base]M) - $[Cap_Base]M = $[Y]M | High: ([PTRS_High]% x $[PeakSales_High]M) - $[Cap_Low]M = $[Z]M. (PTRS extracted from Clinical Analyst, Peak Sales extracted from Commercial Analyst)".
        "confidence": "medium"
     3) Exactly one final verdict claim:
        "claim": MUST BE EXACTLY ONE WORD: "Invest", "Conditional", or "Do Not Invest".
        "reasoning": "<Definitive verdict rationale citing whether the mechanism solves an important unmet need and addressing top risks>".
        "confidence": "high"|"medium"|"low"

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
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt, max_tokens=8000)
    parsed = parse_claims(raw)
    # Програмна санітизація та захист (Code Enforcement)
    sanitized_synthesis: list[Claim] = []
    verdict_claim_obj: Claim | None = None

    for c in parsed:
        # Усунення мовчазного відкидання: нормалізація секцій через аліаси
        raw_section = (c.section or "").strip().lower()
        normalized_section = SECTION_ALIASES.get(raw_section, raw_section)
        if normalized_section in CHAIR_SECTIONS:
            c.section = normalized_section
        else:
            print(f"[Chair Agent Warning] Unrecognized section '{c.section}' for claim: {c.claim[:60]}... Skipped.")
            continue

        # Примусово робимо inference, навіть якщо модель спробувала підставити evidence
        c.type = "inference"
        if c.confidence not in ("low", "medium", "high"):
            c.confidence = "medium"
        if not c.reasoning:
            c.reasoning = "Committee synthesis based on analyst evidence."

        # Чітка ідентифікація та нормалізація фінального вердикту
        if c.section == "recommendation":
            claim_text = c.claim.strip()
            # Якщо це Executive Summary або rNPV claim — зберігаємо як є
            if claim_text.lower().startswith("executive summary:") or "rnpv" in claim_text.lower():
                sanitized_synthesis.append(c)
                continue

            # Інакше це кандидат у фінальний вердикт:
            claim_lower = claim_text.lower()
            if any(term in claim_lower for term in ("do not invest", "don't invest", "reject")):
                c.claim = "Do Not Invest"
                verdict_claim_obj = c
            elif "conditional" in claim_lower:
                c.claim = "Conditional"
                verdict_claim_obj = c
            elif "invest" in claim_lower:
                c.claim = "Invest"
                verdict_claim_obj = c

        sanitized_synthesis.append(c)

    # Страховка на випадок збою: гарантуємо наявність чіткого вердикту
    if not verdict_claim_obj:
        verdict_claim_obj = Claim(
            claim="Conditional",
            type="inference",
            section="recommendation",
            reasoning="Defaulted to Conditional: Committee requires further primary validation.",
            confidence="low",
        )
        sanitized_synthesis.append(verdict_claim_obj)

    # Програмне забезпечення правил: High Risk Override, Data Deficiency та 5-10 Diligence Questions
    _enforce_risk_override_guardrail(all_claims, sanitized_synthesis)
    _enforce_diligence_questions_guardrail(all_claims, sanitized_synthesis, indication, mechanism)

    # Повертаємо об'єднаний список усіх claims
    return all_claims + sanitized_synthesis


def _enforce_risk_override_guardrail(upstream_claims: list[Claim], synthesis_claims: list[Claim]) -> None:
    """Гарантує виконання правил ТЗ:
    1. Якщо топ-ризик має високу ймовірність і високий вплив, вердикт не може бути 'Invest' без прямої згадки цього ризику в reasoning.
    2. Якщо у вхідних даних аналітиків брак доказів ('insufficient evidence'), вердикт не може бути 'Invest' без підтвердженої бази.
    """
    key_risks = [c for c in synthesis_claims if c.section == "key_risks"]
    verdict_claim = next((c for c in synthesis_claims if c.section == "recommendation" and c.claim in VALID_VERDICTS), None)

    if not verdict_claim or verdict_claim.claim != "Invest":
        return

    # Правило 1: Перевірка на брак вхідних доказів (Data Deficiency)
    upstream_text = " ".join(f"{c.claim} {c.reasoning or ''}" for c in upstream_claims).lower()
    insufficient_markers = (
        "insufficient evidence",
        "недостатньо доказів",
        "недостатньо даних",
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

    # Правило 2: Top-Risk Override (High Probability & High Impact)
    if not key_risks:
        return

    top_risk = key_risks[0]
    risk_text = f"{top_risk.claim} {top_risk.reasoning or ''}".lower()

    # Детекція високої ймовірності та високого впливу
    high_prob_markers = ("high prob", "probability: high", "ймовірність: висок", ">50%", "60%", "70%", "80%", "90%")
    high_impact_markers = ("high impact", "вплив: висок", "critical", "severe", "termination", "zero sales", "negative rpnv", "loss")

    has_high_prob = any(m in risk_text for m in high_prob_markers)
    has_high_impact = any(m in risk_text for m in high_impact_markers)

    if has_high_prob and has_high_impact:
        reasoning_lower = (verdict_claim.reasoning or "").lower()
        # Перевіряємо, чи ключові слова з опису ризику згадані у вердикті
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
    """Перевіряє, чи питання вже присутнє в списку (за семантичним перекриттям значущих слів)."""
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
    """Визначає дисциплінарну категорію запитання для збалансованого покриття комітету."""
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
    """Багатофакторна оцінка важливості запитання для андеррайтингу:
    - Deal-killers & ТОП-ризики: найвищий бал (+6..+9)
    - Розриви трансляції та Target Engagement (+5..+8)
    - Critical Unknowns (+4)
    - Фактори безпеки та регуляторної летальності (+4)
    - Комерційна диференціація та FTO патенти (+3)
    - Специфічність необхідних закритих даних (+2.5)
    - Рівень впевненості LLM (+1..+2)
    """
    score = 0.0
    text_q = f"{q.claim} {q.reasoning or ''}".lower()
    words_q = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", text_q)}

    # 1. Співпадіння з ТОП-ризиками (Deal-killers: Найвищий пріоритет)
    for idx, r in enumerate(top_risks[:3]):
        r_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", f"{r.claim} {r.reasoning or ''}")}
        overlap = len(words_q & r_words)
        if overlap >= 2:
            multiplier = 6.0 if idx == 0 else (4.0 if idx == 1 else 2.5)
            score += multiplier
            if any(term in f"{r.claim} {r.reasoning or ''}".lower() for term in ("high prob", "high impact", "fatal", "death", "toxic", "hold", "negative")):
                score += 3.0
            break

    # 2. Співпадіння з критичними розривами трансляції (Missing links / Target engagement)
    for ml in missing_links:
        ml_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", ml.claim)}
        if len(words_q & ml_words) >= 2:
            score += 5.0
            break
    if any(k in text_q for k in ("target engagement", "receptor occupancy", "tissue biopsy", "exposure-response", "pharmacodynamic")):
        score += 3.0

    # 3. Співпадіння з Critical Unknowns
    for u in critical_unknowns:
        u_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", u.claim)}
        if len(words_q & u_words) >= 2:
            score += 4.0
            break

    # 4. Фактори безпеки та регуляторної летальності (Safety / Lethal risks / FDA)
    if any(k in text_q for k in ("safety", "toxic", "cardiotox", "hepatotox", "death", "mortality", "adverse event", "sae", "tolerab", "therapeutic window", "dose-limiting")):
        score += 4.0
    if any(k in text_q for k in ("fda", "clinical hold", "crl", "complete response", "regulatory", "ind", "accelerated approval")):
        score += 3.5

    # 5. Комерційна диференціація, конкуренція та патенти
    if any(k in text_q for k in ("head-to-head", "superiority", "differentiat", "standard of care", "displacement", "freedom-to-operate", "fto", "patent")):
        score += 3.0
    if any(k in text_q for k in ("reimbursement", "payer", "formulary", "pricing", "budget impact")):
        score += 2.5

    # 6. Специфічність необхідних закритих даних (Concrete Non-Public Data)
    reason_lower = (q.reasoning or "").lower()
    if any(k in reason_lower for k in ("phase 1", "pk/pd", "unredacted", "patient-level", "crf", "line listing", "biopsy", "glp", "fto legal opinion", "batch yield", "advisory board")):
        score += 2.5

    # 7. Впевненість моделі (Confidence)
    if q.confidence == "high":
        score += 2.0
    elif q.confidence == "medium":
        score += 1.0

    return score


def _unpack_diligence_questions(existing_questions: list[Claim]) -> list[Claim]:
    """Розпаковує згруповані списки питань на окремі об'єкти Claim."""
    unpacked: list[Claim] = []
    for c in existing_questions:
        claim_text = (c.claim or "").strip()
        reason_text = (c.reasoning or "").strip()

        # Спроба 1: Розбиття за нумерацією або маркерами списку
        split_items = re.split(r"(?:^|\n|\s+)(?:\d+[\.\)]|[-*])\s+", claim_text)
        claim_items = [item.strip() for item in split_items if item and len(item.strip()) > 10]

        # Спроба 2: Якщо списку немає, але в тексті кілька окремих речень зі знаком '?'
        if len(claim_items) < 2 and claim_text.count("?") >= 2:
            raw_qs = [q.strip() for q in re.split(r"\?\s+", claim_text) if len(q.strip()) > 10]
            if len(raw_qs) >= 2:
                claim_items = [q if q.endswith("?") else f"{q}?" for q in raw_qs]

        if len(claim_items) >= 2:
            # Спробуємо також розбити reasoning за номерами, якщо він згрупований
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
    """Генерує додаткові запитання Дью-Ділідженс ВИКЛЮЧНО на основі наявних фактів у базі даних:
    - critical_unknowns (синтезовані невідомі)
    - human_translation_thesis (прогалини ланцюжка трансляції та MISSING LINKs)
    - key_risks (ідентифіковані ризики)
    - clinical_development_plan (сигнали безпеки, зупинки трайлів, стратифікація)
    - competitive_landscape & commercial_opportunity (конкуренти, відшкодування)
    Жодне твердження не береться 'з повітря'.
    """
    candidates: list[Claim] = []

    # 1. Базування на Critical Unknowns (найнадійніша база: це невідомі, які комітет уже виявив)
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

    # 2. Базування на розривах трансляції людини (MISSING LINK у scientific_claims)
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

    # 3. Базування на ідентифікованих топ-ризиках (key_risks)
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

    # 4. Базування на клінічних сигналах безпеки або випробуваннях
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

    # 5. Базування на конкурентному ландшафті та комерційній диференціації
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

    # 6. Базування на відшкодуванні та доступі на ринок
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

    # 7. Фундаментальні запитання валідації мішені та FTO (завжди прив'язані до конкретного mechanism та indication)
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
    """Вибирає найбільш критичні запитання із забезпеченням міждисциплінарного балансу."""
    if len(candidates) <= target_count:
        return sorted(
            candidates,
            key=lambda q: _score_diligence_question(q, top_risks, critical_unknowns, missing_links, indication, mechanism),
            reverse=True,
        )

    # Обчислюємо оцінки для всіх кандидатів
    scored = [
        (q, _score_diligence_question(q, top_risks, critical_unknowns, missing_links, indication, mechanism))
        for q in candidates
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    selected: list[Claim] = []
    selected_claims_set = set()

    # 1. Гарантоване покриття: беремо ТОП-1 запитання з кожної представленої дисципліни
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

    # 2. Заповнюємо решту слотів (до target_count) за абсолютним балом важливості
    for q, sc in scored:
        if len(selected) >= target_count:
            break
        if q.claim not in selected_claims_set:
            selected.append(q)
            selected_claims_set.add(q.claim)

    # Фінальне сортування за важливістю
    selected.sort(
        key=lambda q: _score_diligence_question(q, top_risks, critical_unknowns, missing_links, indication, mechanism),
        reverse=True,
    )
    return selected


def _enforce_diligence_questions_guardrail(
    upstream_claims: list[Claim], synthesis_claims: list[Claim], indication: str, mechanism: str
) -> None:
    """Гарантує виконання вимоги ТЗ: від 5 до 10 окремих, найважливіших питань дью-ділідженс.
    1. Розпаковує агреговані або нумеровані списки на окремі Claim об'єкти.
    2. Якщо питань < 5 — доповнює питаннями виключно на основі наявних у базі даних тверджень
       (critical_unknowns, missing links у трансляції, key_risks, зареєстровані клінічні випробування, конкуренти).
       Жодні твердження не вигадуються з повітря.
    3. Якщо питань > 10 — застосовує багатофакторне ранжування за критичністю (deal-killers, безпека, трансляційні розриви,
       специфічність вимог до data room, дисциплінарне покриття) і залишає ТОП-10 справді найважливіших.
    """
    existing_questions = [c for c in synthesis_claims if c.section == "diligence_questions"]
    unpacked_questions: list[Claim] = _unpack_diligence_questions(existing_questions)

    # Очищуємо синтез від старих питань перед формуванням остаточного списку
    synthesis_claims[:] = [c for c in synthesis_claims if c.section != "diligence_questions"]

    # Витягуємо ключові ризики, невідомі та прогалини для прив'язки
    top_risks = [c for c in synthesis_claims if c.section == "key_risks"]
    critical_unknowns = [c for c in synthesis_claims if c.section == "critical_unknowns"]
    missing_links = [
        c for c in upstream_claims
        if c.section == "human_translation_thesis" and any(k in c.claim.lower() for k in ("missing link", "not demonstrated", "gap"))
    ]

    # Видаляємо дублікати серед розпакованих питань
    deduped_questions: list[Claim] = []
    for q in unpacked_questions:
        if not _is_duplicate_question(q.claim, deduped_questions):
            deduped_questions.append(q)

    # Якщо питань менше 5 — доповнюємо виключно з наявних у системі даних (data-grounded)
    if len(deduped_questions) < 5:
        grounded_candidates = _synthesize_grounded_candidates(
            upstream_claims, synthesis_claims, indication, mechanism
        )
        for cand in grounded_candidates:
            if len(deduped_questions) >= 5:
                break
            if not _is_duplicate_question(cand.claim, deduped_questions):
                deduped_questions.append(cand)

    # Застосовуємо розумний відбір та ранжування (ТОП-10 максимум, мінімум 5)
    final_questions = _select_top_diligence_questions(
        deduped_questions, top_risks, critical_unknowns, missing_links, indication, mechanism, target_count=10
    )

    synthesis_claims.extend(final_questions)


def generate_executive_summary(claims: list[Claim]) -> str:
    """Генерує коротку саммарі (Executive Summary) виключно на основі даних,
    отриманих від інших аналітиків (scientific, clinical, commercial) та голови.
    """
    by_section: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_section[c.section].append(c)

    # 1. Вердикт
    verdict_claims = [c for c in by_section.get("recommendation", []) if c.claim in VALID_VERDICTS]
    verdict = verdict_claims[0].claim if verdict_claims else "Conditional"

    # 2. Якщо LLM сформулювала цілісний наратив Executive Summary:
    llm_summary_claim = next(
        (c for c in by_section.get("recommendation", []) if c.claim.lower().startswith("executive summary:")),
        None,
    )
    llm_narrative = ""
    if llm_summary_claim:
        llm_narrative = llm_summary_claim.claim.replace("Executive Summary:", "").strip()
        if llm_summary_claim.reasoning:
            llm_narrative += f" (Rationale: {llm_summary_claim.reasoning})"

    # 3. Scientific & Human Translation (тільки дані від наукового аналітика)
    sci_claims = by_section.get("scientific_thesis", [])
    trans_claims = by_section.get("human_translation_thesis", [])
    sci_pts = [f"- **Target Biology:** {c.claim}" for c in sci_claims[:2]]
    trans_pts = [f"- **Human Translation:** {c.claim}" for c in trans_claims[:1]]

    # 4. Clinical Development Plan (тільки дані від клінічного аналітика)
    clin_claims = by_section.get("clinical_development_plan", [])
    clin_pts = [f"- **Clinical Development:** {c.claim}" for c in clin_claims[:2]]

    # 5. Competitive Landscape & Commercial Opportunity (тільки дані від комерційного аналітика)
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
    """Вивід фінального звіту Investment Underwriting Report (делегує до report.render_report)."""
    from report import render_report
    return render_report(indication, mechanism, claims, cost_summary)