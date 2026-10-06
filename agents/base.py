"""Base agent infrastructure — source attribution, snippet verification, retry and logging.

Enforces structural integrity on claim objects produced by LLM agents:
1. Every evidence claim must reference a valid source ID (e.g. 'S1') and provide an exact,
   verbatim quote (exact_snippet) found in that source's raw_snippet.
2. Metadata (source, url, date_accessed) is stamped by code directly from the source,
   never trusted from the LLM.
3. Every inference claim must provide non-empty reasoning, confidence ('low'|'medium'|'high'),
   and based_on (list of source IDs, or empty list).
4. Section assignment is enforced by code, not the LLM.
5. Invalid claims are discarded with explicit logging of reasons.
6. Automatic retry on empty or malformed LLM responses.
7. Graceful degradation when sources list is empty (only low-confidence inferences accepted).
"""
import datetime
import re
from typing import Any, Optional

from llm import ask_claude_json
from schema import Claim, Confidence, Section


def normalize_text(text: Optional[str]) -> str:
    """Normalize whitespace, unicode dashes/quotes, and lowercase for tolerant verbatim snippet matching."""
    if not text:
        return ""
    t = text.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("—", "-").replace("–", "-").replace("−", "-")
    t = t.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", t).strip().lower()


def label_sources(sources: list[dict]) -> tuple[dict[str, dict], str]:
    """Assign stable IDs (S1, S2, ...) to sources and format them for the LLM prompt.

    Returns:
        source_map: mapping from 'S1' -> source dict with guaranteed keys
        prompt_block: formatted text describing each source for LLM consumption
    """
    source_map: dict[str, dict] = {}
    formatted_blocks: list[str] = []

    valid_items = [s for s in (sources or []) if isinstance(s, dict)]
    for i, s in enumerate(valid_items, 1):
        s_id = f"S{i}"
        entry = {
            "id": s_id,
            "source": str(s.get("source") or f"Source {s_id}"),
            "url": str(s.get("url") or ""),
            "date_accessed": str(s.get("date_accessed") or datetime.date.today().isoformat()),
            "raw_snippet": str(s.get("raw_snippet") or ""),
        }
        source_map[s_id] = entry
        formatted_blocks.append(
            f"[{s_id}] Source: {entry['source']}\n"
            f"URL: {entry['url']}\n"
            f"Snippet: {entry['raw_snippet']}"
        )

    if not formatted_blocks:
        prompt_block = "No clinical trial or registry sources available."
    else:
        prompt_block = "\n\n".join(formatted_blocks)

    return source_map, prompt_block


def resolve_evidence_id(raw_id: Any, source_map: dict[str, dict]) -> Optional[str]:
    """Resolve raw evidence_id to a canonical ID in source_map ('S1', 's1', 1 -> 'S1')."""
    if raw_id is None:
        return None
    s = str(raw_id).strip()
    if s in source_map:
        return s
    upper = s.upper()
    if upper in source_map:
        return upper
    with_s = f"S{s}"
    if with_s in source_map:
        return with_s
    return None


def verify_exact_snippet(exact_snippet: str, raw_snippet: str) -> bool:
    """Check if exact_snippet is contained verbatim in raw_snippet, ignoring case, whitespace, and formatting."""
    clean_exact = exact_snippet.strip(" \t\n\r\"'«»“”….[]() ")
    if not clean_exact or len(clean_exact) < 3:
        return False

    norm_exact = normalize_text(clean_exact)
    norm_raw = normalize_text(raw_snippet)

    if norm_exact in norm_raw:
        return True

    # If the model used internal ellipsis ('...' or '…') to bridge sentences in a long quote
    if "..." in norm_exact or "…" in norm_exact:
        parts = [p.strip() for p in re.split(r"\.\.\.+|…", norm_exact) if len(p.strip()) >= 3]
        if parts:
            pos = 0
            for p in parts:
                idx = norm_raw.find(p, pos)
                if idx == -1:
                    return False
                pos = idx + len(p)
            return True

    return False


def make_claim_object(
    claim: str,
    claim_type: str,
    section: Section,
    source: Optional[str] = None,
    url: Optional[str] = None,
    date_accessed: Optional[str] = None,
    exact_snippet: Optional[str] = None,
    reasoning: Optional[str] = None,
    confidence: Optional[Confidence] = None,
    evidence_id: Optional[str] = None,
    based_on: Optional[list[str]] = None,
) -> Claim:
    """Instantiate a Claim object safely.

    Handles optional schema fields (evidence_id, based_on) whether or not
    they are declared as formal dataclass fields in schema.py.
    """
    kwargs: dict[str, Any] = {
        "claim": claim,
        "type": claim_type,
        "section": section,
        "source": source,
        "url": url,
        "date_accessed": date_accessed,
        "exact_snippet": exact_snippet,
        "reasoning": reasoning,
        "confidence": confidence,
    }

    # If schema.py was updated with evidence_id or based_on fields:
    if "evidence_id" in Claim.__dataclass_fields__:
        kwargs["evidence_id"] = evidence_id
    if "based_on" in Claim.__dataclass_fields__:
        kwargs["based_on"] = based_on

    c = Claim(**kwargs)

    # If schema.py does not yet define them, attach dynamically for downstream access:
    if "evidence_id" not in Claim.__dataclass_fields__ and evidence_id is not None:
        c.evidence_id = evidence_id
    if "based_on" not in Claim.__dataclass_fields__ and based_on is not None:
        c.based_on = based_on

    c.validate()
    return c


def validate_and_build_claims(
    raw_claims: list[dict],
    source_map: dict[str, dict],
    section: Section,
    agent_name: str = "Agent",
    max_claims: int = 12,
    valid_sources: Optional[set] = None,
) -> list[Claim]:
    """Validate raw model claim dictionaries against code-enforced rules.

    Rules enforced:
    - section is overwritten with the code-provided section.
    - evidence claims:
      * sources must not be empty.
      * evidence_id must exist in source_map.
      * exact_snippet must be verbatim present in source's raw_snippet.
      * source, url, date_accessed are injected by code from source_map.
      * if valid_sources is provided, source name must be grounded in valid_sources.
    - inference claims:
      * reasoning must be non-empty string.
      * confidence must be 'low', 'medium', or 'high'.
      * based_on must only contain valid source IDs from source_map (or empty).
      * when source_map is empty, confidence is enforced as 'low' and based_on as [].
    - rejected claims are logged with specific reasons.
    - accepted claims are truncated to max_claims.
    """
    total_received = len(raw_claims)
    accepted: list[Claim] = []
    rejected: list[str] = []

    has_sources = bool(source_map)

    for idx, item in enumerate(raw_claims, 1):
        if not isinstance(item, dict):
            rejected.append(f"Claim #{idx}: item is not a dict ({type(item).__name__})")
            continue

        claim_text = str(item.get("claim") or "").strip()
        if not claim_text:
            rejected.append(f"Claim #{idx}: missing or empty 'claim' text")
            continue

        raw_type = str(item.get("type") or "").strip().lower()

        # ---------------------------------------------------------
        # Case A: EVIDENCE claim
        # ---------------------------------------------------------
        if raw_type == "evidence":
            if not has_sources:
                rejected.append(
                    f"Claim #{idx} [evidence]: sources list is empty, evidence claims are forbidden"
                )
                continue

            raw_eid = item.get("evidence_id")
            resolved_eid = resolve_evidence_id(raw_eid, source_map)
            if not resolved_eid:
                rejected.append(
                    f"Claim #{idx} [evidence]: evidence_id '{raw_eid}' not found in provided sources"
                )
                continue

            raw_snippet = item.get("exact_snippet") or ""
            clean_snippet = str(raw_snippet).strip(" \t\n\r\"'«»“”")
            if not clean_snippet:
                rejected.append(f"Claim #{idx} [evidence]: missing or empty 'exact_snippet'")
                continue

            target_source = source_map[resolved_eid]
            if valid_sources is not None and not any(
                target_source["source"] in v or v in target_source["source"] for v in valid_sources
            ):
                rejected.append(
                    f"Claim #{idx} [evidence]: source '{target_source['source']}' is not in valid_sources"
                )
                continue

            if not verify_exact_snippet(clean_snippet, target_source["raw_snippet"]):
                snippet_preview = clean_snippet[:50] + "..." if len(clean_snippet) > 50 else clean_snippet
                rejected.append(
                    f"Claim #{idx} [evidence]: exact_snippet {snippet_preview!r} not found verbatim in [{resolved_eid}]"
                )
                continue

            try:
                c = make_claim_object(
                    claim=claim_text,
                    claim_type="evidence",
                    section=section,
                    source=target_source["source"],
                    url=target_source["url"],
                    date_accessed=target_source["date_accessed"],
                    exact_snippet=clean_snippet,
                    evidence_id=resolved_eid,
                )
                accepted.append(c)
            except Exception as e:
                rejected.append(f"Claim #{idx} [evidence]: validation error ({e})")
                continue

        # ---------------------------------------------------------
        # Case B: INFERENCE claim
        # ---------------------------------------------------------
        elif raw_type == "inference":
            reasoning = str(item.get("reasoning") or "").strip()
            if not reasoning:
                rejected.append(f"Claim #{idx} [inference]: missing or empty 'reasoning'")
                continue

            raw_conf = str(item.get("confidence") or "").strip().lower()
            if raw_conf not in ("low", "medium", "high"):
                rejected.append(
                    f"Claim #{idx} [inference]: invalid confidence '{raw_conf}' (must be low|medium|high)"
                )
                continue

            # When no sources were provided, only low-confidence inferences are permitted
            if not has_sources:
                confidence: Confidence = "low"
                clean_based_on: list[str] = []
            else:
                confidence = raw_conf  # type: ignore[assignment]
                raw_based = item.get("based_on") or []
                if isinstance(raw_based, str):
                    raw_based = [raw_based]
                elif not isinstance(raw_based, list):
                    raw_based = []

                clean_based_on = []
                invalid_ids = []
                for bid in raw_based:
                    rbid = resolve_evidence_id(bid, source_map)
                    if rbid:
                        clean_based_on.append(rbid)
                    else:
                        invalid_ids.append(str(bid))

                if invalid_ids:
                    rejected.append(
                        f"Claim #{idx} [inference]: based_on contains unknown source IDs {invalid_ids}"
                    )
                    continue

            try:
                c = make_claim_object(
                    claim=claim_text,
                    claim_type="inference",
                    section=section,
                    reasoning=reasoning,
                    confidence=confidence,
                    based_on=clean_based_on,
                )
                accepted.append(c)
            except Exception as e:
                rejected.append(f"Claim #{idx} [inference]: validation error ({e})")
                continue

        else:
            rejected.append(
                f"Claim #{idx}: unknown claim type '{raw_type}' (must be 'evidence' or 'inference')"
            )
            continue

    # ---------------------------------------------------------
    # Logging
    # ---------------------------------------------------------
    print(f"[{agent_name}] Claims received from model: {total_received}")
    if rejected:
        print(f"[{agent_name}] Claims rejected ({len(rejected)}):")
        for r in rejected:
            print(f"  - {r}")
    else:
        print(f"[{agent_name}] Claims rejected: 0")
    print(f"[{agent_name}] Claims accepted: {len(accepted)}")

    if len(accepted) > max_claims:
        print(f"[{agent_name}] Truncating claims from {len(accepted)} to max_claims={max_claims}")
        accepted = accepted[:max_claims]

    return accepted


def run_agent_llm_with_retry(
    system_prompt: str,
    user_prompt: str,
    source_map: dict[str, dict],
    section: Section,
    agent_name: str = "Agent",
    max_claims: int = 12,
    max_tokens: int = 4000,
    valid_sources: Optional[set] = None,
) -> list[Claim]:
    """Execute LLM call with 1 automatic retry on empty/unparseable response, then validate."""
    raw = ask_claude_json(system_prompt, user_prompt, max_tokens=max_tokens)
    if not raw:
        print(f"[{agent_name}] Initial LLM response was empty or unparseable. Retrying once...")
        raw = ask_claude_json(system_prompt, user_prompt, max_tokens=max_tokens)

    if not raw:
        print(f"[{agent_name}] Warning: LLM returned empty output after retry. 0 claims produced.")
        return []

    return validate_and_build_claims(
        raw_claims=raw,
        source_map=source_map,
        section=section,
        agent_name=agent_name,
        max_claims=max_claims,
        valid_sources=valid_sources,
    )

