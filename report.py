"""Render the final list of Claims into a Markdown Investment Underwriting Report.

The report presents:
1. Executive Level (Filtered Core Theses):
   - Recommendation: Invest / Conditional / Do Not Invest (with rationale & rNPV)
   - Scientific thesis
   - Human translation thesis
   - Clinical development plan
   - Competitive landscape
   - Commercial opportunity
   - Capital required to reach the next major milestone
   - Key risks and failure modes
   - Critical unknowns
2. Detailed Diligence & Traceability:
   - 5–10 highest-value diligence questions (with non-public data requirements)
   - Detailed Claims by Section (with [EVIDENCE] and [INFERENCE] badges)
   - Sources supporting major claims (clickable links and access dates)
"""
import datetime
import re
from collections import defaultdict
from typing import Optional

from schema import Claim, SECTION_TITLES

BADGE = {"evidence": "EVIDENCE", "inference": "INFERENCE"}
VALID_VERDICTS = {"Invest", "Conditional", "Do Not Invest"}


def _filter_summary_claims(claims: list[Claim], section: str, limit: int = 2) -> list[str]:
    """Extract 1-2 concise, high-signal summary bullets for a given section."""
    if not claims:
        return ["_No claims produced for this section._"]

    # Filter out verbose executive summaries if they landed in other sections
    clean_claims = [
        c for c in claims
        if not c.claim.lower().startswith("executive summary:")
        and not "rnpv scenarios:" in c.claim.lower()
    ]
    if not clean_claims:
        clean_claims = claims

    # Prefer inferences with high/medium confidence or key evidence claims
    def sort_key(c: Claim):
        score = 0
        if c.type == "inference":
            score += 2
            if c.confidence == "high":
                score += 3
            elif c.confidence == "medium":
                score += 2
        elif c.type == "evidence":
            score += 1
        return score

    sorted_claims = sorted(clean_claims, key=sort_key, reverse=True)
    selected = sorted_claims[:limit]

    bullets = []
    for c in selected:
        badge = "EVIDENCE" if c.type == "evidence" else "INFERENCE"
        bullets.append(f"- **{c.claim.strip()}** `[{badge}]`")
    return bullets


def render_report(
    indication: str, mechanism: str, claims: list[Claim], cost_summary: Optional[str] = None
) -> str:
    """Генерація єдиного фінального Investment Underwriting Report.

    Структура:
    - Загальна відфільтрована інформація (Recommendation + 8 тез)
    - Детальна інформація (5-10 Diligence Questions + Повна деталізація Claims + Sources)
    """
    by_section: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_section[c.section].append(c)

    # 1. Recommendation, Verdict & rNPV
    rec_claims = by_section.get("recommendation", [])
    verdict = next((c.claim for c in rec_claims if c.claim in VALID_VERDICTS), "Conditional")
    verdict_obj = next((c for c in rec_claims if c.claim in VALID_VERDICTS), None)
    rnpv_obj = next((c for c in rec_claims if "rnpv" in c.claim.lower()), None)
    exec_obj = next((c for c in rec_claims if c.claim.lower().startswith("executive summary:")), None)

    verdict_rationale = verdict_obj.reasoning if (verdict_obj and verdict_obj.reasoning) else "Committee consensus."
    rnpv_text = rnpv_obj.claim if rnpv_obj else "rNPV valuation model pending clinical/commercial calibration."
    rnpv_calc = f" ({rnpv_obj.reasoning})" if (rnpv_obj and rnpv_obj.reasoning) else ""

    lines = [
        f"# Investment Underwriting Report: {mechanism.title()} — {indication.title()}",
        f"_Generated {datetime.date.today().isoformat()} | Underwriting Engine_",
        "",
        f"## Recommendation: {verdict}",
        f"- **Core Rationale:** {verdict_rationale}",
        f"- **Valuation ({rnpv_text}):**{rnpv_calc}",
    ]

    if exec_obj:
        narrative = exec_obj.claim.replace("Executive Summary:", "").strip()
        lines.append(f"- **Executive Narrative:** {narrative}")

    lines.append("")

    # 2. Відфільтровані загальні тези (Core Theses)
    core_sections = [
        ("scientific_thesis", "Scientific thesis"),
        ("human_translation_thesis", "Human translation thesis"),
        ("clinical_development_plan", "Clinical development plan"),
        ("competitive_landscape", "Competitive landscape"),
        ("commercial_opportunity", "Commercial opportunity"),
        ("capital_required", "Capital required to reach the next major milestone"),
    ]

    for sec_key, title in core_sections:
        lines.append(f"## {title}")
        bullets = _filter_summary_claims(by_section.get(sec_key, []), sec_key, limit=2)
        lines.extend(bullets)
        lines.append("")

    # 3. Key risks and failure modes (Топ 2-3 ризики з імовірністю та впливом)
    lines.append("## Key risks and failure modes")
    risks = by_section.get("key_risks", [])
    if risks:
        for r in risks[:3]:
            impact_text = f" — _{r.reasoning}_" if r.reasoning else ""
            lines.append(f"- **{r.claim}** `[INFERENCE]`{impact_text}")
    else:
        lines.append("_No critical failure modes reported._")
    lines.append("")

    # 4. Critical unknowns (2-3 найважливіші невідомі)
    lines.append("## Critical unknowns")
    unknowns = by_section.get("critical_unknowns", [])
    if unknowns:
        for u in unknowns[:3]:
            lines.append(f"- **{u.claim}** `[INFERENCE]`")
    else:
        lines.append("_No critical unknowns identified._")
    lines.append("")

    # 5. Детальна інформація: 5–10 highest-value diligence questions
    lines.append("---")
    lines.append("## 5–10 highest-value diligence questions")
    lines.append("_Key non-public items required to validate the thesis before capital deployment (min 5, max 10):_")
    lines.append("")
    questions = by_section.get("diligence_questions", [])
    if questions:
        for idx, q in enumerate(questions[:10], 1):
            q_clean = q.claim.strip()
            q_clean = re.sub(r"^\d+[\.\)]\s*", "", q_clean)
            q_clean = re.sub(r"^[-*]\s*", "", q_clean)
            if not q_clean.endswith("?"):
                q_clean += "?"
            reason_text = q.reasoning.strip() if q.reasoning else ""
            reason_text = re.sub(r"^(?:explicit\s+)?(?:non-public\s+)?(?:data\s+)?(?:required:?|needed:?)\s*", "", reason_text, flags=re.I)
            reason_text = re.sub(r"^requires:?\s*", "", reason_text, flags=re.I)
            req_data = f"\n  - _Required Non-Public Data:_ {reason_text.strip()}" if reason_text else ""
            lines.append(f"{idx}. **{q_clean}**{req_data}")
    else:
        lines.append("_No diligence questions submitted by the committee._")
    lines.append("")

    # 6. Повна деталізація тверджень (Detailed Claims Traceability)
    lines.append("---")
    lines.append("## Detailed Claims & Traceability by Section")
    for sec_key, sec_title in SECTION_TITLES.items():
        if sec_key in ("recommendation", "diligence_questions"):
            continue
        sec_claims = by_section.get(sec_key, [])
        if not sec_claims:
            continue
        lines.append(f"### {sec_title} ({len(sec_claims)} claims)")
        for c in sec_claims:
            badge = "EVIDENCE" if c.type == "evidence" else "INFERENCE"
            lines.append(f"- **{c.claim}** `[{badge}]`")
            if c.type == "evidence":
                source_link = f"[{c.source}]({c.url})" if c.url else (c.source or "Unknown Source")
                lines.append(f"  - source: {source_link} (accessed {c.date_accessed or 'N/A'})")
                if c.exact_snippet:
                    lines.append(f"  - quote: \"{c.exact_snippet}\"")
            else:
                lines.append(f"  - reasoning: {c.reasoning} (confidence: {c.confidence})")
        lines.append("")

    # 7. Sources supporting major claims
    lines.append("---")
    lines.append("## Sources supporting major claims")
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
        lines.append("_No primary evidence sources cited._")

    if cost_summary:
        lines.append("")
        lines.append("---")
        lines.append(cost_summary)

    return "\n".join(lines)
