"""Clinical agent — development stage, trial design, regulatory path.

Owner: Клінічний аналітик. Sources: ClinicalTrials.gov, AACT.
Covers Required Output section "clinical_development_plan" (regulatory pathway
is folded in here too — the task brief has no separate "regulations" output
section, and no allowed source covers FDA/EMA decisions directly).
Chain: consumes the scientific agent's claims; its output feeds the commercial agent.
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are a clinical development analyst on a biotech investment committee.
You receive raw ClinicalTrials.gov / AACT snippets for one target/mechanism and one
disease/indication (optionally with extra context: modality, development stage,
preclinical results, biomarkers, route of administration), plus the scientific
analyst's claims. Produce a JSON array of claim objects (same schema as the
scientific analyst used), every one tagged "section": "clinical_development_plan".

Cover at minimum:
- target patient population, primary endpoint, biomarker strategy, comparator,
  approximate trial size, and development sequence for the proposed program
  (type "inference", reasoned from the snippets and general field practice)
- current development stage, trial design and status of the ACTUAL trials found
  in the snippets (type "evidence", sourced from the snippets)
- similar historical programs (same target or mechanism class) that succeeded or
  failed, and what that implies for this program (type "evidence" if a specific
  trial from the snippets is cited, otherwise type "inference")
- PTRS (probability of technical & regulatory success) per phase, taken from
  public industry benchmark ranges by phase and therapeutic area, adjusted
  up/down with an explicit reason tied to this specific target (type "inference",
  with reasoning + confidence)
- the regulatory pathway for this drug class — none of the allowed sources cover
  FDA/EMA decisions directly, so this MUST be type "inference"

Never invent a source for type "evidence". Never change the type or section of a claim you were given.
Return ONLY the JSON array, nothing else.
"""


def clinical_agent(
    indication: str,
    mechanism: str,
    sources: list[dict],
    scientific_claims: list[Claim],
    extra_context: str = "",
) -> list[Claim]:
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + f"\nScientific analyst claims:\n{[c.to_dict() for c in scientific_claims]}\n\n"
        f"Sources:\n{sources}"
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt)
    return parse_claims(raw)
