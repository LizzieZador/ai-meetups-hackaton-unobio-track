"""AACT (Aggregate Analysis of ClinicalTrials.gov) — static flat-file dataset.

AACT and the ClinicalTrials.gov API are one dataset in two forms: AACT is for
aggregated queries ("all trials on target X"), the API is for one study's detail
(see fetch_clinicaltrials.py). Per the plan, start from the weekly static flat-file
dump (no live DB connection) — more reliable for a demo.

Download studies.txt / conditions.txt (pipe-delimited) from
https://aact.ctti-clinicaltrials.org into data/aact/ before this will return data.

Owner: Клінічний аналітик (trial design) + Комерційний аналітик (competitors via sponsors).
"""
import datetime
from pathlib import Path
from typing import Optional

AACT_DIR = Path(__file__).resolve().parent.parent / "data" / "aact"


def fetch_aact(condition: str, intervention: str) -> Optional[list[dict]]:
    """Count competing trials for a condition/intervention from the local AACT flat files.

    Returns None if the flat files haven't been downloaded yet — caller must not
    crash the pipeline just because this optional local dataset is missing.
    """
    try:
        import pandas as pd
    except ImportError:
        return None

    studies_path = AACT_DIR / "studies.txt"
    conditions_path = AACT_DIR / "conditions.txt"
    if not studies_path.exists() or not conditions_path.exists():
        return None

    try:
        studies = pd.read_csv(studies_path, sep="|", low_memory=False)
        conditions = pd.read_csv(conditions_path, sep="|", low_memory=False)

        matching_nct_ids = conditions[
            conditions["name"].str.contains(condition, case=False, na=False)
        ]["nct_id"].unique()
        matched = studies[
            studies["nct_id"].isin(matching_nct_ids)
            & studies["brief_title"].str.contains(intervention, case=False, na=False)
        ]

        today = datetime.date.today().isoformat()
        return [{
            "source": "AACT flat-file dump",
            "url": "https://aact.ctti-clinicaltrials.org",
            "date_accessed": today,
            "raw_snippet": (
                f"{len(matched)} competing trials found for "
                f"condition='{condition}', intervention='{intervention}'"
            ),
        }]
    except (KeyError, ValueError):
        return None
