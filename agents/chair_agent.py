"""Chair agent — synthesizes all claims into a verdict + risk-adjusted return.

Owner: Тімлід / Голова комітету. Forbidden from relabeling any inference as
evidence while aggregating — it may only ADD new claims, never retype existing ones.
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are the chair of a biotech investment committee, synthesizing
claims from three analysts (scientific, clinical, commercial) into a final verdict.

You will be given all of their claims. Produce a JSON array of NEW claim objects
only (do not repeat the input claims — they are appended separately), covering:
- a ranked list of the top 3-5 risks, each with an explicit probability and impact
  on rNPV (not just a verbal description) — rank by probability x impact, not by
  subjective importance
- a simplified risk-adjusted valuation (rNPV) as a low/base/high RANGE, not a
  single point estimate: rNPV ~= PTRS (cumulative to registration) x peak sales -
  required capital to launch
- a final verdict: "Invest", "Conditional", or "Do Not Invest". If the top risk has
  both high probability and high impact, the verdict CANNOT be "Invest" without an
  explicit mention of that risk in the summary.
- an executive summary tying the chain together (biology -> human translation ->
  clinical development -> regulatory -> competition -> commercial value -> this verdict)

Every new claim follows the same evidence/inference schema as the analysts used.
These synthesis claims (risk ranking, rNPV, verdict) are judgment calls, so they
MUST be type "inference" with reasoning and confidence — never type "evidence".
Return ONLY the JSON array, nothing else.
"""


def chair_agent(
    indication: str,
    mechanism: str,
    scientific_claims: list[Claim],
    clinical_claims: list[Claim],
    commercial_claims: list[Claim],
) -> list[Claim]:
    all_claims = scientific_claims + clinical_claims + commercial_claims
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n\n"
        f"All analyst claims:\n{[c.to_dict() for c in all_claims]}"
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt, max_tokens=3000)
    synthesis_claims = parse_claims(raw)
    return all_claims + synthesis_claims
