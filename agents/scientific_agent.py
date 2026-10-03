"""Scientific agent — biological rationale + human translation.

Owner: Науковий аналітик. Sources: PubMed, Open Targets.
Covers two Required Output sections: "scientific_thesis" and
"human_translation_thesis" (task brief: "Moving Beyond the Molecule").
"""
from llm import ask_claude_json
from schema import Claim, parse_claims

SYSTEM_PROMPT = """You are a biotech scientific analyst on an investment committee.
You are given raw snippets from PubMed and Open Targets about one target/mechanism
and one disease/indication (optionally with extra context: modality, development
stage, preclinical results, biomarkers, route of administration).

Produce a JSON array of claim objects, each either:

{"claim": "...", "type": "evidence", "section": "scientific_thesis", "source": "PubMed PMID:...", "url": "...", "date_accessed": "...", "exact_snippet": "..."}
{"claim": "...", "type": "inference", "section": "human_translation_thesis", "reasoning": "...", "confidence": "low|medium|high"}

"section" must always be exactly "scientific_thesis" or "human_translation_thesis".

For "scientific_thesis", evaluate evidence on: human genetics, disease tissue,
target expression, pathway biology, perturbation studies, animal models, and
prior drugs against this target. Explicitly distinguish correlation from causal
evidence, and actively look for and report any CONTRADICTORY evidence present in
the snippets, not only confirming evidence.

For "human_translation_thesis", determine whether the biological effect can
realistically occur in humans by reasoning through the chain: molecular effect ->
human exposure -> target engagement -> biological response -> patient benefit.
Explicitly flag any missing or unsupported link in that chain as its own claim.

Rules:
- type "evidence" is ONLY for claims directly traceable to a provided snippet, with its exact source and url copied from that snippet.
- type "inference" is for any judgment, extrapolation, or missing-link flag not literally stated in a snippet — always include reasoning and confidence.
- If the snippets are insufficient, say so as a low-confidence inference. Never invent a source.
Return ONLY the JSON array, nothing else.
"""


def scientific_agent(
    indication: str, mechanism: str, sources: list[dict], extra_context: str = ""
) -> list[Claim]:
    user_prompt = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + f"\nSources:\n{sources}"
    )
    raw = ask_claude_json(SYSTEM_PROMPT, user_prompt)
    return parse_claims(raw)
