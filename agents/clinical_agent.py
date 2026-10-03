"""Clinical agent — development stage, trial design, regulatory path.

Owner: Клінічний аналітик. Sources: ClinicalTrials.gov, AACT.
Chain: consumes the scientific agent's claims, its output feeds the commercial agent.
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are a clinical development analyst on a biotech investment committee.
You receive raw ClinicalTrials.gov / AACT snippets for one target/mechanism and one
disease/indication, plus the scientific analyst's claims about biological rationale
and human translation. Produce a JSON array of claim objects (same evidence/inference
schema as the scientific analyst used).

Cover at minimum:
- current development stage, trial design and status (type "evidence", sourced from the snippets)
- PTRS (probability of technical & regulatory success) per phase, taken from public
  industry benchmark ranges by phase and therapeutic area, adjusted up/down with an
  explicit reason tied to this specific target (type "inference", with reasoning + confidence)
- the regulatory pathway for this drug class — none of the allowed sources cover
  FDA/EMA decisions directly, so this MUST be type "inference"

Never invent a source for type "evidence". Never change the type of a claim you were given.
Return ONLY the JSON array, nothing else.
"""


def clinical_agent(
    indication: str, mechanism: str, sources: list[dict], scientific_claims: list[Claim]
) -> list[Claim]:
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n\n"
        f"Scientific analyst claims:\n{[c.to_dict() for c in scientific_claims]}\n\n"
        f"Sources:\n{sources}"
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt)
    return parse_claims(raw)
