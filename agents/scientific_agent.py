"""Scientific agent — biological rationale + human translation.

Owner: Науковий аналітик. Sources: PubMed, Open Targets.
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are a biotech scientific analyst on an investment committee.
You are given raw snippets from PubMed and Open Targets about one target/mechanism
and one disease/indication. Produce a JSON array of claim objects, each either:

{"claim": "...", "type": "evidence", "source": "PubMed PMID:...", "url": "...", "date_accessed": "...", "exact_snippet": "..."}
{"claim": "...", "type": "inference", "reasoning": "...", "confidence": "low|medium|high"}

Rules:
- type "evidence" is ONLY for claims directly traceable to a provided snippet, with its exact source and url copied from that snippet.
- type "inference" is for any judgment, extrapolation, or risk estimate not literally stated in a snippet — always include reasoning and confidence.
- If the snippets are insufficient, say so as a low-confidence inference. Never invent a source.
- Cover at minimum: (1) strength of the biological rationale for the target, (2) whether the target is validated in humans (human translation) and what that implies for probability of clinical success.
Return ONLY the JSON array, nothing else.
"""


def scientific_agent(indication: str, mechanism: str, sources: list[dict]) -> list[Claim]:
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n\n"
        f"Sources:\n{sources}"
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt)
    return parse_claims(raw)
