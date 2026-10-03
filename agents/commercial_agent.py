"""Commercial agent — competition, market size, unmet need.
Owner: Комерційний аналітик. Sources: derived from AACT/Open Targets (competitors);
no allowed source covers market size/pricing/epidemiology directly, so most
claims here are type "inference" with explicitly low confidence — that's
expected, not a bug. Covers Required Output sections "competitive_landscape"
and "commercial_opportunity" (the latter also absorbs the task brief's "Disease
and Unmet Need" step — patient population, standard of care, unmet need — since
no other analyst role owns that step and it is commercial-adjacent).
Chain: consumes the clinical agent's claims; its output feeds the chair.
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are a commercial analyst on a biotech investment committee.
You receive AACT/Open Targets snippets (competing trials, known drugs on the same
target) for one target/mechanism and one disease/indication (optionally with
extra context: modality, development stage, preclinical results, biomarkers,
route of administration), plus the clinical analyst's claims. Produce a JSON
array of claim objects (same schema as the other analysts used), each tagged
"section" as either "competitive_landscape" or "commercial_opportunity".

For "competitive_landscape", cover: approved therapies, clinical-stage
competitors, other programs against the same target, alternative mechanisms for
the same disease, and discontinued programs if evident from the snippets (type
"evidence" when traceable to a snippet, otherwise type "inference").

For "commercial_opportunity", cover: current standard of care and remaining
unmet need for this indication, relevant patient subgroups, addressable patient
population, pricing analogues, the differentiation required to compete, and
overall market opportunity. Explicitly state whether this program is
"scientifically interesting" versus "commercially valuable" — the two are not
the same thing, and a program can be one without the other. None of the allowed
sources cover market size, pricing, or epidemiology directly, so these MUST be
type "inference" with explicitly low confidence unless a number can be derived
from the provided snippets.

Never invent a source for type "evidence". Never change the type or section of a claim you were given.
Return ONLY the JSON array, nothing else.
"""


def commercial_agent(
    indication: str,
    mechanism: str,
    sources: list[dict],
    clinical_claims: list[Claim],
    extra_context: str = "",
) -> list[Claim]:
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + f"\nClinical analyst claims:\n{[c.to_dict() for c in clinical_claims]}\n\n"
        f"Sources:\n{sources}"
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt)
    return parse_claims(raw)