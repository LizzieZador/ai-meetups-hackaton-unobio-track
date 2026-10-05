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

    def validate(self) -> None:
        if self.section not in SECTION_TITLES:
            raise ValueError(f"unknown section {self.section!r} for claim: {self.claim!r}")
        if self.type == "evidence" and not isinstance(self.source, str):
            raise ValueError(f"evidence claim without a string source: {self.claim!r}")
        if self.type == "inference" and not (self.reasoning and self.confidence):
            raise ValueError(f"inference claim without reasoning/confidence: {self.claim!r}")

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def parse_claims(raw: list[dict], valid_sources: Optional[set] = None) -> list[Claim]:
    """Parse + validate claim dicts coming back from an LLM call.

    An LLM is never trusted to self-police its own evidence/inference labeling —
    anything that fails validation (e.g. type=evidence with no source) is dropped
    here, in code, rather than rendered into the report.

    When `valid_sources` is given (the exact `source` strings of every item we
    actually fetched this run — see pipeline.collect_valid_sources()), every
    evidence claim's `source` must match one of them (substring match, since the
    LLM sometimes echoes a source slightly reformatted). An evidence claim citing
    a source we never fetched is a hallucinated citation and is dropped — the
    model is instructed to copy sources verbatim, but that instruction is never
    trusted on its own.
    """
    claims = []
    for item in raw:
        try:
            c = Claim(**item)
            c.validate()
        except (TypeError, ValueError):
            continue
        if (
            valid_sources is not None
            and c.type == "evidence"
            and not _source_is_grounded(c.source, valid_sources)
        ):
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
