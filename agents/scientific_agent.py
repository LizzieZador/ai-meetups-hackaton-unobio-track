"""Scientific agent — biological rationale + human translation thesis.

Owner: Scientific Analyst. Sources: PubMed (via NCBI E-utilities), Open Targets.
Covers Required Output sections: "scientific_thesis" and "human_translation_thesis".

Executes two focused agent runs via agents/base.py:
1. scientific_thesis: human genetics, tissue expression, perturbation, animal models,
   pathway biology, prior clinical results, and contradictory evidence.
2. human_translation_thesis: 5-link translation chain:
   molecular effect -> human exposure -> target engagement -> biological response -> patient benefit
   with explicit detection of MISSING LINKs.
"""
import sys
from pathlib import Path
from typing import Any, Optional

# Ensure project root is in path for standalone execution
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from schema import Claim
from llm import get_usage_summary, reset_usage_log
from sources.fetch_pubmed import fetch_pubmed
from sources.fetch_opentargets import fetch_opentargets, get_target_context
from .base import label_sources, run_agent_llm_with_retry

SYSTEM_PROMPT_SCIENTIFIC = """You are a biotech scientific analyst on an investment committee.
You evaluate the biological rationale and target validation for a proposed target/mechanism in an indication.
You receive labeled snippets from PubMed and Open Targets (each with an ID like [S1], [S2] and a research group tag).

Produce a JSON array of claim objects, each strictly for section "scientific_thesis".

COVER AT MINIMUM:
1. Evidence classification:
   Every evidence claim MUST begin with one of these exact bracketed tags:
   [human genetics], [human tissue], [animal model], [in vitro], [clinical], or [review].
2. Causal vs Correlational distinction:
   Explicitly distinguish CAUSAL evidence (human loss/gain-of-function variants, Mendelian randomization, randomized trials of target modulators) from CORRELATIONAL evidence (expression differences, biomarker associations). Never call correlation causal.
3. CONTRADICTORY EVIDENCE:
   Actively search for and report any contradictory evidence against the target (negative results, lack of efficacy, toxicity, or failed attempts).
   If no contradictory evidence is found in the snippets, produce exactly one inference claim with confidence "low":
   "No contradictory evidence found in the retrieved snippets; absence of evidence is not proof of absence."
4. Human vs Animal distinction:
   Clearly separate human findings from animal models. Never extrapolate animal model results to humans without an explicit qualification.
5. Overall scientific thesis strength:
   Produce an inference claim assessing overall biological validation strength. Confidence CANNOT be "high" unless supported by direct human genetics (e.g. protective or causal human variants).
6. Preclinical & Target Diligence Gaps (CRITICAL):
   Produce at least 1-2 inference claims explicitly identifying unanswerable biological unknowns and what non-public/proprietary laboratory data is required (e.g. off-target kinome/proteome selectivity screens, tissue-specific target engagement assays, isoform crystal structures).

STRICT SCHEMA RULES:
For type "evidence":
- "claim": factual statement starting with an evidence tag (e.g. "[human genetics] Heterozygous mutation in...")
- "type": "evidence"
- "evidence_id": source ID (e.g. "S1")
- "exact_snippet": VERBATIM text copied directly from that source's Snippet text.
For type "inference":
- "claim": analytical conclusion
- "type": "inference"
- "reasoning": max 2 sentences explaining the conclusion
- "confidence": "low", "medium", or "high"
- "based_on": array of source IDs used (e.g. ["S1"] or [])

CONSTRAINTS:
- Maximum 15 claims.
- Never cite facts, PMIDs, or numbers not present in the snippets.
- If cutoff_date is active, never reference events after that date.
- Never declare a target "validated" on correlation or animal models alone.
- Return ONLY the JSON array, nothing else.
"""

SYSTEM_PROMPT_TRANSLATION = """You are a biotech translation analyst on an investment committee.
You evaluate whether the biological effect can realistically translate into patient benefit in humans.
You receive labeled snippets on pharmacokinetics, exposure, target engagement, biomarkers, and clinical activity.

Produce a JSON array of claim objects, each strictly for section "human_translation_thesis".

TRANSLATION CHAIN (5 MANDATORY LINKS):
You must evaluate the translation chain link by link. Each of the following 5 links MUST be addressed by at least one dedicated claim:
1. Molecular effect: does the drug engage the target with sufficient potency and selectivity?
2. Human exposure: is achievable human exposure (Cmax, AUC) sufficient to reach active concentrations in the target tissue?
3. Target engagement: is target engagement demonstrated in humans or human disease tissue?
4. Biological response: does target engagement trigger the intended biological/pathway response in patients?
5. Patient benefit: does the biological response translate into clinically meaningful patient benefit?

FOR EACH LINK:
- If supported by snippets: produce an "evidence" claim (with evidence_id and exact_snippet) or a well-reasoned "inference".
- If data is lacking or inconclusive: produce an "inference" claim starting with "MISSING LINK: [Link Name] - ..." stating the gap and specifying what experimental assay or human trial data is needed (e.g., PET target occupancy, tissue biopsy PK/PD, patient biomarker stratification).

STRICT SCHEMA RULES:
For type "evidence":
- "claim": factual statement
- "type": "evidence"
- "evidence_id": source ID (e.g. "S1")
- "exact_snippet": VERBATIM text copied from the snippet.
For type "inference":
- "claim": analytical conclusion or "MISSING LINK: ..."
- "type": "inference"
- "reasoning": max 2 sentences
- "confidence": "low", "medium", or "high" (use "low" for missing links or unsupported extrapolations)
- "based_on": array of source IDs used (e.g. ["S1"] or [])

CONSTRAINTS:
- Maximum 15 claims.
- Never cite facts not present in the snippets.
- Return ONLY the JSON array, nothing else.
"""


def collect_scientific_sources(
    indication: str, mechanism: str, cutoff_date: Optional[str] = None
) -> dict[str, Any]:
    """Collect parameterized, multi-angle scientific sources for the scientific agent.

    Queries PubMed across 8 distinct research groups and Open Targets Platform.
    Deduplicates articles by PMID and tags each with its research group.

    Returns:
        {
            "scientific_thesis": list[dict],
            "human_translation": list[dict],
            "known_drugs": list[str],
            "ids": dict,
            "notes": list[str]
        }
    """
    notes = []
    target_ctx = get_target_context(indication, mechanism)
    symbol = target_ctx.get("symbol") or mechanism
    synonyms = target_ctx.get("synonyms") or []

    # Build target query using symbol and top 2 clean synonyms
    clean_syns = [s for s in synonyms if s.lower() != mechanism.lower() and len(s) >= 3][:2]
    target_terms = [f'"{mechanism}"']
    if symbol.lower() != mechanism.lower():
        target_terms.append(f'"{symbol}"')
    for s in clean_syns:
        target_terms.append(f'"{s}"')
    target_query = " OR ".join(dict.fromkeys(target_terms))

    # Parameterized query templates (generic, no hardcoded diseases or targets)
    query_groups = {
        "genetics": f'({target_query}) AND ("{indication}") AND (GWAS OR "loss of function" OR "gain of function" OR "Mendelian randomization" OR "genetic variant" OR polymorphism OR mutation)',
        "tissue": f'({target_query}) AND ("{indication}") AND ("tissue expression" OR overexpression OR "patient tissue" OR biopsy OR immunohistochemistry OR "tumor expression" OR upregulation)',
        "perturbation": f'({target_query}) AND (knockout OR knockdown OR CRISPR OR siRNA OR shRNA OR inhibition OR degrader OR PROTAC)',
        "animal": f'({target_query}) AND ("{indication}") AND ("animal model" OR mouse OR murine OR xenograft OR "in vivo" OR rat)',
        "pathway": f'({target_query}) AND ("signaling pathway" OR downstream OR phosphorylation OR activation OR "pathway biology")',
        "clinical": f'({target_query}) AND ("{indication}") AND ("clinical trial" OR "phase 1" OR "phase 2" OR "phase 3" OR "patient response" OR "therapeutic efficacy")',
        "contradictory": f'({target_query}) AND ("{indication}") AND ("negative result" OR "no effect" OR "failed" OR "lack of efficacy" OR toxicity OR adverse OR "off-target" OR resistance OR futile)',
        "translation": f'({target_query}) AND (pharmacokinetics OR "PK/PD" OR "target engagement" OR "tissue penetration" OR biomarker OR "dose response" OR "therapeutic window" OR biodistribution)',
    }

    all_pubmed_records: list[dict] = []
    seen_pmids: set[str] = set()
    total_budget = 40

    for group_name, query_str in query_groups.items():
        if len(all_pubmed_records) >= total_budget:
            break
        records = fetch_pubmed(query_str, max_results=8, cutoff_date=cutoff_date)
        if not records:
            notes.append(f"No PubMed articles found for group '{group_name}'.")
            continue

        added_in_group = 0
        for r in records:
            pmid = r.get("pmid")
            if pmid and pmid in seen_pmids:
                continue
            r_copy = dict(r)
            r_copy["group"] = group_name
            # Enrich raw snippet with group tag for model visibility
            r_copy["raw_snippet"] = f"[{group_name}] {r_copy.get('raw_snippet', '')}"
            all_pubmed_records.append(r_copy)
            if pmid:
                seen_pmids.add(pmid)
            added_in_group += 1
            if len(all_pubmed_records) >= total_budget:
                break

        if added_in_group == 0:
            notes.append(f"All articles for group '{group_name}' were duplicate PMIDs.")

    # Open Targets data
    ot_records = fetch_opentargets(mechanism, indication, cutoff_date=cutoff_date) or []
    if cutoff_date and any(r.get("cutoff_excluded") for r in ot_records):
        notes.append(f"Open Targets evidence excluded due to cutoff_date={cutoff_date}.")

    for r in ot_records:
        r["group"] = "opentargets"

    # Split into sections
    sci_groups = {"genetics", "tissue", "perturbation", "animal", "pathway", "clinical", "contradictory", "opentargets"}
    trans_groups = {"translation", "clinical", "opentargets"}

    scientific_sources = [r for r in (all_pubmed_records + ot_records) if r.get("group") in sci_groups]
    translation_sources = [r for r in (all_pubmed_records + ot_records) if r.get("group") in trans_groups]

    return {
        "scientific_thesis": scientific_sources,
        "human_translation": translation_sources,
        "known_drugs": target_ctx.get("known_drugs", []),
        "ids": {
            "ensembl_id": target_ctx.get("ensembl_id"),
            "efo_id": target_ctx.get("efo_id"),
            "symbol": target_ctx.get("symbol"),
        },
        "notes": notes,
    }


def scientific_agent(
    indication: str,
    mechanism: str,
    sources: Optional[list[dict]] = None,
    extra_context: str = "",
    cutoff_date: Optional[str] = None,
    valid_sources: Optional[set] = None,
) -> list[Claim]:
    """Execute scientific agent producing 'scientific_thesis' and 'human_translation_thesis' claims.

    Args:
        indication: Disease or indication name.
        mechanism: Biological mechanism or target name.
        sources: Optional list of pre-fetched sources. If None, collect_scientific_sources is called.
        extra_context: Optional additional scientific context.
        cutoff_date: Optional publication cutoff date (YYYY-MM-DD).
        valid_sources: Optional set of allowed source names for grounding validation.

    Returns:
        Combined list of validated Claim objects for scientific_thesis and human_translation_thesis.
    """
    # Guard against positional argument inversion (e.g. if caller passes valid_sources as 5th arg)
    if isinstance(cutoff_date, (set, frozenset)) and valid_sources is None:
        valid_sources = cutoff_date
        cutoff_date = None

    if sources is None:
        bundle = collect_scientific_sources(indication, mechanism, cutoff_date=cutoff_date)
        sci_sources = bundle["scientific_thesis"]
        trans_sources = bundle["human_translation"]
        notes = bundle["notes"]
    else:
        # Caller provided a flat sources list (e.g. pipeline.py): partition if groups exist
        sci_groups = {"genetics", "tissue", "perturbation", "animal", "pathway", "clinical", "contradictory", "opentargets"}
        trans_groups = {"translation", "clinical", "opentargets"}

        has_groups = any("group" in s for s in sources)
        if has_groups:
            sci_sources = [s for s in sources if s.get("group") in sci_groups]
            trans_sources = [s for s in sources if s.get("group") in trans_groups]
        else:
            sci_sources = sources
            trans_sources = sources
        notes = []

    # 1. Run scientific_thesis agent
    sci_map, sci_prompt_block = label_sources(sci_sources)
    notes_block = f"\nCollection notes: {'; '.join(notes)}\n" if notes else ""
    user_prompt_sci = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + (f"Cutoff date: {cutoff_date} (ignore any events after this date)\n" if cutoff_date else "")
        + notes_block
        + f"\nAvailable scientific evidence sources:\n{sci_prompt_block}\n"
    )

    claims_sci = run_agent_llm_with_retry(
        system_prompt=SYSTEM_PROMPT_SCIENTIFIC,
        user_prompt=user_prompt_sci,
        source_map=sci_map,
        section="scientific_thesis",
        agent_name="ScientificAgent[ScientificThesis]",
        max_claims=15,
        valid_sources=valid_sources,
    )

    # 2. Run human_translation_thesis agent
    trans_map, trans_prompt_block = label_sources(trans_sources)
    user_prompt_trans = (
        f"Indication: {indication}\n"
        f"Mechanism/target: {mechanism}\n"
        + (f"Additional context: {extra_context}\n" if extra_context else "")
        + (f"Cutoff date: {cutoff_date}\n" if cutoff_date else "")
        + f"\nPreceding scientific thesis claims:\n{[c.to_dict() for c in claims_sci]}\n\n"
        f"Available translation evidence sources:\n{trans_prompt_block}\n"
    )

    claims_trans = run_agent_llm_with_retry(
        system_prompt=SYSTEM_PROMPT_TRANSLATION,
        user_prompt=user_prompt_trans,
        source_map=trans_map,
        section="human_translation_thesis",
        agent_name="ScientificAgent[HumanTranslation]",
        max_claims=15,
        valid_sources=valid_sources,
    )

    return claims_sci + claims_trans


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test Scientific Agent standalone.")
    parser.add_argument("indication", nargs="?", default="NSCLC", help="Indication/disease")
    parser.add_argument("mechanism", nargs="?", default="KRAS G12C", help="Target/mechanism")
    parser.add_argument("--cutoff-date", default=None, help="Cutoff date (YYYY-MM-DD)")
    args = parser.parse_args()

    print(f"=== TESTING SCIENTIFIC AGENT: [{args.indication} | {args.mechanism}] ===")
    if args.cutoff_date:
        print(f"Cutoff date applied: {args.cutoff_date}")

    reset_usage_log()
    claims = scientific_agent(args.indication, args.mechanism, cutoff_date=args.cutoff_date)

    print(f"\n=== GENERATED CLAIMS ({len(claims)}) ===")
    by_section: dict[str, list[Claim]] = {}
    for c in claims:
        by_section.setdefault(c.section, []).append(c)

    for section, sec_claims in by_section.items():
        print(f"\n--- Section: {section} ({len(sec_claims)} claims) ---")
        for c in sec_claims:
            badge = f"[{c.type.upper()}]"
            if c.type == "evidence":
                print(f"  {badge} {c.claim}")
                print(f"       source: {c.source} | quote: {c.exact_snippet[:80]}...")
            else:
                conf = f"conf: {c.confidence}"
                based = f"based_on: {c.based_on}" if hasattr(c, "based_on") and c.based_on else ""
                print(f"  {badge} {c.claim} ({conf} {based})")
                print(f"       reasoning: {c.reasoning}")

    usage = get_usage_summary()
    print("\n=== LLM USAGE & COST ===")
    print(f"Calls: {usage.get('calls')}, Model: {usage.get('model')}")
    print(f"Tokens: {usage.get('input_tokens')} in / {usage.get('output_tokens')} out")
    print(f"Estimated Cost: ${usage.get('approx_cost_usd', 0.0)}")
