"""Evidence/inference claim schema shared by every agent in the pipeline.

Per the plan: the evidence/inference split is structural, not stylistic.
Every statement the system makes is one of these objects, never a line of free text.
"""
from dataclasses import dataclass
from typing import Literal, Optional

ClaimType = Literal["evidence", "inference"]
Confidence = Literal["low", "medium", "high"]


@dataclass
class Claim:
    claim: str
    type: ClaimType
    # evidence fields — required when type == "evidence"
    source: Optional[str] = None
    url: Optional[str] = None
    date_accessed: Optional[str] = None
    exact_snippet: Optional[str] = None
    # inference fields — required when type == "inference"
    reasoning: Optional[str] = None
    confidence: Optional[Confidence] = None

    def validate(self) -> None:
        if self.type == "evidence" and not self.source:
            raise ValueError(f"evidence claim without a source: {self.claim!r}")
        if self.type == "inference" and not (self.reasoning and self.confidence):
            raise ValueError(f"inference claim without reasoning/confidence: {self.claim!r}")

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def parse_claims(raw: list[dict]) -> list[Claim]:
    """Parse + validate claim dicts coming back from an LLM call.

    An LLM is never trusted to self-police its own evidence/inference labeling —
    anything that fails validation (e.g. type=evidence with no source) is dropped
    here, in code, rather than rendered into the report.
    """
    claims = []
    for item in raw:
        try:
            c = Claim(**item)
            c.validate()
        except (TypeError, ValueError):
            continue
        claims.append(c)
    return claims
