"""Chair agent — synthesizes all claims into a verdict + risk-adjusted return.
Owner: Тімлід / Голова комітету. Forbidden from relabeling any inference as
evidence, or changing any claim's section, while aggregating — it may only ADD
new claims. Covers Required Output sections "recommendation", "capital_required",
"key_risks", "critical_unknowns", and "diligence_questions".
"""
import datetime
import json
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
   - "critical_unknowns": The most important unknowns that cannot be answered from public evidence and materially affect the investment case.
   - "diligence_questions": 5 to 10 highest-value diligence questions. For each, reasoning MUST specify what non-public data is required (e.g. proprietary assays, patient-level data, KOL interviews, CMC audit).
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

    # Програмне забезпечення правил: High Risk Override & Data Deficiency
    _enforce_risk_override_guardrail(all_claims, sanitized_synthesis)

    # Повертаємо CommitteeClaims — об'єкт, сумісний з list[Claim], але зі збереженою зв'язністю
    return CommitteeClaims(
        claims=all_claims + sanitized_synthesis,
        indication=indication,
        mechanism=mechanism,
        scientific_claims=scientific_claims,
        clinical_claims=clinical_claims,
        commercial_claims=commercial_claims,
        chair_claims=sanitized_synthesis,
    )


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
    """Вивід фінального звіту Investment Underwriting Report згідно з Required Output ТЗ.

    Структура виводу:
    1. Заголовок та метадані
    2. Recommendation: Invest / Conditional / Do Not Invest
    3. Executive Summary (коротка саммарі на основі даних інших аналітиків)
    4. Required Output (усі 10 обов'язкових секцій)
    5. Sources Supporting Major Claims (клікабельні джерела доказів)
    """
    by_section: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_section[c.section].append(c)

    verdict = next(
        (c.claim for c in by_section.get("recommendation", []) if c.claim in VALID_VERDICTS),
        "Conditional",
    )

    lines = [
        f"# Investment Underwriting Report: {mechanism} — {indication}",
        f"_Generated {datetime.date.today().isoformat()}_",
        "",
        f"**Recommendation: {verdict}**",
        "",
        generate_executive_summary(claims),
        "",
        "---",
        "",
    ]

    # Required Output секції точно за списком з ТЗ
    for section, title in SECTION_TITLES.items():
        section_claims = by_section.get(section, [])
        lines.append(f"## {title}")
        if not section_claims:
            lines.append("_No claims generated for this section — insufficient evidence or agent did not respond._")
        for c in section_claims:
            badge = "EVIDENCE" if c.type == "evidence" else "INFERENCE"
            lines.append(f"- **{c.claim}** `[{badge}]`")
            if c.type == "evidence":
                link = f"[{c.source}]({c.url})" if c.url else (c.source or "Unknown Source")
                lines.append(f"  - source: {link} (accessed {c.date_accessed or 'N/A'})")
            else:
                lines.append(f"  - reasoning: {c.reasoning} (confidence: {c.confidence})")
        lines.append("")

    # Sources supporting major claims (збирається кодом із evidence-claims)
    lines.append("## Sources Supporting Major Claims")
    seen_sources = set()
    sources = []
    for c in claims:
        if c.type == "evidence" and c.source and c.source not in seen_sources:
            seen_sources.add(c.source)
            sources.append((c.source, c.url, c.date_accessed))

    if sources:
        for source, url, date_accessed in sources:
            link = f"[{source}]({url})" if url else source
            lines.append(f"- {link} (accessed {date_accessed or 'N/A'})")
    else:
        lines.append("_No evidence-backed sources were collected for this run._")

    if cost_summary:
        lines.append("")
        lines.append("---")
        lines.append(cost_summary)

    return "\n".join(lines)


class CommitteeClaims(list):
    """Розширений список Claims із збереженням наскрізної зв'язності та структури звіту.

    100% сумісний зі звичайним list[Claim], але надає прямий доступ до:
    - .verdict : Фінальний вердикт (Invest / Conditional / Do Not Invest)
    - .executive_summary : Зв'язне резюме для інвесторів
    - .report : Повний відформатований Investment Underwriting Report (Markdown)
    - .by_section : Словник claims за 10 обов'язковими секціями
    - .scientific_claims / .clinical_claims / .commercial_claims / .chair_claims
    """

    def __init__(
        self,
        claims: list[Claim],
        indication: str = "",
        mechanism: str = "",
        scientific_claims: list[Claim] | None = None,
        clinical_claims: list[Claim] | None = None,
        commercial_claims: list[Claim] | None = None,
        chair_claims: list[Claim] | None = None,
    ):
        super().__init__(claims)
        self.indication = indication
        self.mechanism = mechanism
        self.scientific_claims = scientific_claims or []
        self.clinical_claims = clinical_claims or []
        self.commercial_claims = commercial_claims or []
        self.chair_claims = chair_claims or []

        self.by_section: dict[str, list[Claim]] = defaultdict(list)
        for c in self:
            self.by_section[c.section].append(c)

        verdict_claim = next(
            (c.claim for c in self.by_section.get("recommendation", []) if c.claim in VALID_VERDICTS),
            "Conditional",
        )
        self.verdict = verdict_claim
        self.executive_summary = generate_executive_summary(self)
        self.report = render_underwriting_report(self.indication, self.mechanism, self)

    def render(self, cost_summary: str | None = None) -> str:
        """Повертає відрендерений Markdown звіт."""
        return render_underwriting_report(self.indication, self.mechanism, self, cost_summary=cost_summary)

    def __str__(self) -> str:
        return self.report


