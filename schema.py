"""Evidence/inference claim schema shared by every agent in the pipeline.

Per the plan: the evidence/inference split is structural, not stylistic.
Every statement the system makes is one of these objects, never a line of free text.

`section` ties every claim to one line item of the "Required Output" list in the
task brief ("Moving Beyond the Molecule") — the report is assembled by grouping
claims under these exact headings, not by an LLM freely writing a report.
"""
from dataclasses import dataclass
from typing import Literal, Optional

ClaimType = Literal["evidence", "inference"]
Confidence = Literal["low", "medium", "high"]

Section = Literal[
    "recommendation",
    "scientific_thesis",
    "human_translation_thesis",
    "clinical_development_plan",
    "competitive_landscape",
    "commercial_opportunity",
    "capital_required",
    "key_risks",
    "critical_unknowns",
    "diligence_questions",
]

# Order here is the render order, and matches the "Required Output" list verbatim.
# "Sources supporting major claims" is deliberately not a key here — it isn't a
# section any agent writes into, it's assembled by report.py from evidence claims.
SECTION_TITLES: dict[str, str] = {
    "recommendation": "Recommendation",
    "scientific_thesis": "Scientific Thesis",
    "human_translation_thesis": "Human Translation Thesis",
    "clinical_development_plan": "Clinical Development Plan",
    "competitive_landscape": "Competitive Landscape",
    "commercial_opportunity": "Commercial Opportunity",
    "capital_required": "Capital Required to Reach the Next Major Milestone",
    "key_risks": "Key Risks and Failure Modes",
    "critical_unknowns": "Critical Unknowns",
    "diligence_questions": "Highest-Value Diligence Questions",
}


@dataclass
class Claim:
    claim: str
    type: ClaimType
    section: Section
    # evidence fields — required when type == "evidence"
    source: Optional[str] = None
    url: Optional[str] = None
    date_accessed: Optional[str] = None
    exact_snippet: Optional[str] = None
    # inference fields — required when type == "inference"
    reasoning: Optional[str] = None
    confidence: Optional[Confidence] = None
    # optional metadata fields
    evidence_id: Optional[str] = None
    based_on: Optional[list[str]] = None

    def __post_init__(self) -> None:
        if isinstance(self.section, str):
            clean_sec = self.section.strip().lower()
            self.section = SECTION_ALIASES.get(clean_sec, clean_sec)  # type: ignore[assignment]

    def validate(self) -> None:
        if self.section not in SECTION_TITLES:
            raise ValueError(f"unknown section {self.section!r} for claim: {self.claim!r}")
        if self.type == "evidence" and not isinstance(self.source, str):
            raise ValueError(f"evidence claim without a string source: {self.claim!r}")
        if self.type == "inference" and not (self.reasoning and self.confidence):
            raise ValueError(f"inference claim without reasoning/confidence: {self.claim!r}")

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


SECTION_ALIASES: dict[str, str] = {
    # recommendation
    "verdict": "recommendation",
    "recommendations": "recommendation",
    "investment_recommendation": "recommendation",
    "investment_verdict": "recommendation",
    "executive_summary": "recommendation",
    "valuation": "recommendation",
    "rnpv": "recommendation",
    "rnpv_scenarios": "recommendation",
    # scientific_thesis
    "scientific": "scientific_thesis",
    "science": "scientific_thesis",
    "scientific_validation": "scientific_thesis",
    "biological_validation": "scientific_thesis",
    "biology": "scientific_thesis",
    # human_translation_thesis
    "human_translation": "human_translation_thesis",
    "translation": "human_translation_thesis",
    "translation_thesis": "human_translation_thesis",
    # clinical_development_plan
    "clinical": "clinical_development_plan",
    "clinical_development": "clinical_development_plan",
    "clinical_plan": "clinical_development_plan",
    "clinical_trials": "clinical_development_plan",
    "trial_design": "clinical_development_plan",
    # competitive_landscape
    "competition": "competitive_landscape",
    "competitors": "competitive_landscape",
    "competitive": "competitive_landscape",
    # commercial_opportunity
    "commercial": "commercial_opportunity",
    "market": "commercial_opportunity",
    "market_opportunity": "commercial_opportunity",
    # capital_required
    "capital": "capital_required",
    "capital_requirement": "capital_required",
    "capital_needed": "capital_required",
    "capital_requirements": "capital_required",
    "financing": "capital_required",
    # key_risks
    "key_risk": "key_risks",
    "risk": "key_risks",
    "risks": "key_risks",
    "failure_modes": "key_risks",
    "key_risks_and_failure_modes": "key_risks",
    "risks_and_failure_modes": "key_risks",
    "risk_analysis": "key_risks",
    # critical_unknowns
    "unknown": "critical_unknowns",
    "unknowns": "critical_unknowns",
    "critical_unknown": "critical_unknowns",
    "key_unknowns": "critical_unknowns",
    # diligence_questions
    "diligence_question": "diligence_questions",
    "diligence": "diligence_questions",
    "questions": "diligence_questions",
    "highest_value_diligence_questions": "diligence_questions",
    "due_diligence": "diligence_questions",
    "due_diligence_questions": "diligence_questions",
}

_dropped_claims_log: list[dict] = []


def log_dropped_claim(stage: str, claim_data: object, reason: str) -> None:
    """Record a dropped or rejected claim with its reason for trace auditing."""
    if isinstance(claim_data, dict):
        claim_repr = claim_data
    elif hasattr(claim_data, "to_dict"):
        claim_repr = claim_data.to_dict()  # type: ignore[union-attr]
    elif hasattr(claim_data, "__dict__"):
        claim_repr = {k: v for k, v in claim_data.__dict__.items() if not k.startswith("_")}
    else:
        claim_repr = str(claim_data)

    _dropped_claims_log.append({
        "stage": stage,
        "claim": claim_repr,
        "reason": reason,
    })


def reset_dropped_claims_log() -> None:
    """Reset the dropped claims log at the start of a pipeline run."""
    _dropped_claims_log.clear()


def get_dropped_claims_log() -> list[dict]:
    """Retrieve all dropped claims recorded during the current run."""
    return list(_dropped_claims_log)


def parse_claims(
    raw: list[dict],
    valid_sources: Optional[set] = None,
    stage: str = "parse_claims",
) -> list[Claim]:
    """Parse + validate claim dicts coming back from an LLM call.

    Tolerates extra keys from the LLM or upstream collectors by filtering
    to Claim dataclass fields. Normalizes casing and aliases, rescues
    explanation/rationale into reasoning, defaults missing inference confidence
    to 'medium', and logs any dropped claims with their reason.
    """
    claims = []
    known_fields = set(Claim.__dataclass_fields__.keys())

    for idx, item in enumerate(raw or []):
        if not isinstance(item, dict):
            log_dropped_claim(stage, item, f"Claim #{idx + 1}: item is not a dict ({type(item).__name__})")
            continue

        item_copy = dict(item)

        # Extract claim text from common alternate keys if missing
        claim_text = (
            item_copy.get("claim")
            or item_copy.get("statement")
            or item_copy.get("text")
            or item_copy.get("title")
        )
        if claim_text:
            item_copy["claim"] = str(claim_text).strip()

        # Extract reasoning from common alternate keys if missing
        reasoning = (
            item_copy.get("reasoning")
            or item_copy.get("explanation")
            or item_copy.get("rationale")
            or item_copy.get("justification")
        )

        # Infer type if omitted
        raw_type = str(item_copy.get("type") or "").strip().lower()
        if not raw_type:
            raw_type = "evidence" if item_copy.get("source") else "inference"
        item_copy["type"] = raw_type

        # Normalize confidence for inference claims
        raw_conf = str(item_copy.get("confidence") or "").strip().lower()
        if raw_type == "inference" and raw_conf not in ("low", "medium", "high"):
            raw_conf = "medium"
        if raw_conf:
            item_copy["confidence"] = raw_conf

        # Normalize section
        raw_sec = str(item_copy.get("section") or "").strip().lower()
        normalized_sec = SECTION_ALIASES.get(raw_sec, raw_sec)
        item_copy["section"] = normalized_sec

        if reasoning is not None:
            item_copy["reasoning"] = str(reasoning).strip()

        # Filter out extra unexpected keys to tolerate model extensions
        filtered_item = {k: v for k, v in item_copy.items() if k in known_fields}

        try:
            c = Claim(**filtered_item)
            c.validate()
        except (TypeError, ValueError) as e:
            log_dropped_claim(stage, item, f"Claim #{idx + 1}: validation failed ({e})")
            continue

        if (
            valid_sources is not None
            and c.type == "evidence"
            and not _source_is_grounded(c.source, valid_sources)
        ):
            log_dropped_claim(stage, item, f"Claim #{idx + 1}: evidence source '{c.source}' not grounded in valid_sources")
            continue

        claims.append(c)
    return claims


def _source_is_grounded(source: Optional[str], valid_sources: set) -> bool:
    # The LLM occasionally returns a non-string "source" (e.g. a list of
    # sources for one claim) — treat anything that isn't a plain string as
    # ungrounded rather than crashing the whole agent call on it.
    if not isinstance(source, str) or not source:
        return False
    return any(source in v or v in source for v in valid_sources)
