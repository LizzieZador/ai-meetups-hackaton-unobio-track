"""Evidence for the program's biomarkers (the optional "Biomarkers" input).

The free-text field ("KRAS G12C mutation; PD-L1 TPS ≥1%; co-mutation
STK11/KEAP1") is split into searchable markers, and each one is looked up for
the indication in:
  - ClinicalTrials.gov — trials that select or stratify on that marker
    (who else enrolls the same biomarker-defined population);
  - PubMed — literature on the marker in the indication, together with the
    mechanism when that narrows it (predictive / prognostic evidence).
Both feed the cards, and the literature also feeds the scientific agent as
evidence it may cite.
"""
import re
from typing import Optional

from sources.fetch_clinicaltrials import fetch_clinicaltrials
from sources.fetch_pubmed import fetch_pubmed

MAX_MARKERS = 3
TRIALS_PER_MARKER = 5
ARTICLES_PER_MARKER = 4

# Words that describe a marker's state or its cut-off rather than the marker
# itself — "PD-L1 TPS ≥50% positive" is searched as "PD-L1".
_QUALIFIERS = re.compile(
    r"\b(?:co-?mutations?|mutations?|mutant|mutated|positive|negative|expression|expressing|high|low|"
    r"status|level|levels|tps|cps|score|wild[- ]type|wt|carriers?|variants?|fusion-?positive)\b",
    re.I,
)
# Assay read-outs ("IHC 3+", "FISH+") qualify a marker, they aren't one.
_ASSAY = re.compile(r"\b(?:IHC|FISH|ISH|NGS|PCR)\b\s*\d?\+?", re.I)
_THRESHOLD = re.compile(r"[≥≤<>=]+\s*\d+(?:\.\d+)?\s*%?|\b\d+(?:\.\d+)?\s*%")
_MECHANISM_WORDS = re.compile(
    r"\b(?:inhibitors?|antagonists?|agonists?|blockers?|modulators?|degraders?|antibod(?:y|ies)|"
    r"targeting|directed|therapy|vaccine)\b",
    re.I,
)


def biomarker_terms(text: Optional[str]) -> list[str]:
    """The distinct markers in the free-text field, at most MAX_MARKERS."""
    terms: list[str] = []
    for part in re.split(r"[;,\n]|\band\b|\bplus\b", text or "", flags=re.I):
        part = _THRESHOLD.sub(" ", part)
        part = _ASSAY.sub(" ", part)
        # "HER2-positive" -> "HER2": a hyphenated state word goes with its hyphen
        part = re.sub(r"-(?=(?:positive|negative|high|low|mutant|mutated|amplified)\b)", " ", part, flags=re.I)
        part = _QUALIFIERS.sub(" ", part)
        part = re.sub(r"[()\[\]]", " ", part)
        part = " ".join(part.split()).strip(" -:")
        if 2 <= len(part) <= 40 and part.lower() not in (t.lower() for t in terms):
            terms.append(part)
    return terms[:MAX_MARKERS]


def _marker_query(marker: str) -> str:
    """'STK11/KEAP1' means either gene — searched as an OR group; 'BRCA1/2'
    is shorthand for BRCA1 or BRCA2."""
    alternatives: list[str] = []
    for alt in (a.strip() for a in marker.split("/")):
        if alt.isdigit() and alternatives and re.search(r"\d+$", alternatives[-1]):
            alt = re.sub(r"\d+$", "", alternatives[-1]) + alt
        if alt:
            alternatives.append(alt)
    return "(" + " OR ".join(f'"{a}"' for a in alternatives) + ")"


def fetch_biomarker_evidence(
    indication: str, mechanism: str, biomarkers: Optional[str], cutoff_date: Optional[str] = None
) -> dict[str, list[dict]]:
    """{"biomarker_trials": [...], "biomarker_literature": [...]} — empty lists
    when no biomarker was given. Trials are a present-day registry view, so a
    backtest (cutoff_date) skips them; literature honours the cutoff."""
    trials: list[dict] = []
    literature: list[dict] = []
    seen_trials: set[str] = set()
    seen_articles: set[str] = set()
    mechanism_core = " ".join(_MECHANISM_WORDS.sub(" ", mechanism or "").split())

    for marker in biomarker_terms(biomarkers):
        if not cutoff_date:
            for t in fetch_clinicaltrials(indication, "", max_results=TRIALS_PER_MARKER, term=marker) or []:
                if t.get("nctId") and t["nctId"] not in seen_trials:
                    seen_trials.add(t["nctId"])
                    trials.append({**t, "matched_biomarker": marker})

        marker_q = _marker_query(marker)
        queries = [f'{marker_q} AND ("{indication}")']
        if mechanism_core and mechanism_core.lower() not in marker.lower():
            queries.insert(0, f'{marker_q} AND ("{indication}") AND ("{mechanism_core}")')
        for query in queries:  # narrowest first; broaden only if it finds nothing
            records = fetch_pubmed(query, max_results=ARTICLES_PER_MARKER, cutoff_date=cutoff_date) or []
            fresh = [r for r in records if r.get("source") and r["source"] not in seen_articles]
            if fresh:
                for r in fresh:
                    seen_articles.add(r["source"])
                    literature.append({**r, "group": "biomarker", "matched_biomarker": marker})
                break

    return {"biomarker_trials": trials, "biomarker_literature": literature}
