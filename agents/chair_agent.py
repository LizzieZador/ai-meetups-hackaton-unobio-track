"""Chair agent — synthesizes all claims into a verdict + risk-adjusted return.

Owner: Тімлід / Голова комітету. Forbidden from relabeling any inference as
evidence, or changing any claim's section, while aggregating — it may only ADD
new claims. Covers Required Output sections "recommendation", "capital_required",
"key_risks", "critical_unknowns", and "diligence_questions".
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are the chair of a biotech investment committee, synthesizing
claims from three analysts (scientific, clinical, commercial) into a final verdict.

You will be given all of their claims. Produce a JSON array of NEW claim objects
only (do not repeat the input claims — they are appended separately). Every new
claim must be type "inference" (these are all judgment calls) with reasoning and
confidence, and "section" must be one of exactly these five values:

- "key_risks": the top 3-5 risks ranked by probability x impact on rNPV (not
  subjective importance) — each claim's reasoning MUST state an explicit
  probability and its impact on rNPV, not just a verbal description.
- "capital_required": capital required to reach the next major milestone, the
  approximate time to that milestone, major value inflection points, likely
  future financing needs, and plausible licensing/acquisition/development
  outcomes. Use transparent assumptions and ranges, never false precision.
- "critical_unknowns": the most important things that cannot be determined from
  the evidence gathered so far and materially affect the investment case.
- "diligence_questions": 5 to 10 of the highest-value diligence questions for a
  human investor to pursue next. For each, the reasoning MUST state what kind of
  non-public information would answer it (e.g. proprietary experimental data,
  patient-level clinical data, KOL interviews, additional experiments, CMC or IP
  diligence) rather than inventing an answer yourself.
- "recommendation": a simplified risk-adjusted valuation (rNPV) as a low/base/high
  RANGE (rNPV ~= PTRS to registration x peak sales - required capital), PLUS
  exactly one final verdict claim whose "claim" field is exactly "Invest",
  "Conditional", or "Do Not Invest". If the top key_risk has both high probability
  and high impact, the verdict CANNOT be "Invest" without explicitly naming that
  risk in its reasoning. The recommendation's reasoning should also state whether,
  if the mechanism works, it solves an important problem for this patient population.

Never produce type "evidence" claims in this step — you have no new source to cite.
Never change the type or section of any claim you were given.
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
