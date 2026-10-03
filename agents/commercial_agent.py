"""Commercial agent — competition, market size, unmet need.

Owner: Комерційний аналітик. Sources: derived from AACT/Open Targets (competitors);
no allowed source covers market size/pricing directly, so most claims here are
type "inference" with explicitly low confidence — that's expected, not a bug.
Chain: consumes the clinical agent's claims, its output feeds the chair.
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are a commercial analyst on a biotech investment committee.
You receive AACT/Open Targets snippets (competing trials, known drugs on the same
target) for one target/mechanism and one disease/indication, plus the clinical
analyst's claims about development stage and risk. Produce a JSON array of claim
objects (same evidence/inference schema as the other analysts used).

Cover at minimum:
- number and phase of competing trials on the same target/disease (type "evidence", from the AACT/Open Targets snippets)
- achievable market share given that competitive landscape (type "inference")
- market size, pricing, and unmet need — no allowed source covers this directly, so
  these MUST be type "inference" with explicitly low confidence unless you can derive
  a number from the provided snippets

Never invent a source for type "evidence". Never change the type of a claim you were given.
Return ONLY the JSON array, nothing else.
"""


def commercial_agent(
    indication: str, mechanism: str, sources: list[dict], clinical_claims: list[Claim]
) -> list[Claim]:
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n\n"
        f"Clinical analyst claims:\n{[c.to_dict() for c in clinical_claims]}\n\n"
        f"Sources:\n{sources}"
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt)
    return parse_claims(raw)
