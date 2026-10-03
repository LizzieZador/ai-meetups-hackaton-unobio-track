"""AACT (Aggregate Analysis of ClinicalTrials.gov) — live Postgres DB, with a
static flat-file fallback.

AACT and the ClinicalTrials.gov API are one dataset in two forms: AACT is for
aggregated queries ("all trials on target X"), the API is for one study's detail
(see fetch_clinicaltrials.py).

Prefers a live, read-only connection to the AACT public Postgres database
(aact-db.ctti-clinicaltrials.org) using credentials from the environment
(AACT_DB_HOST/PORT/NAME/USER/PASSWORD — see .env.example). If those aren't set,
or the live query fails for any reason, falls back to local static flat-files in
data/aact/ (studies.txt / conditions.txt). Whichever succeeds first is cached by
sources/cache.py, so a flaky live connection still doesn't risk a live demo.

Owner: Клінічний аналітик (trial design) + Комерційний аналітик (competitors via sponsors).
"""
import csv
import datetime
import os
from pathlib import Path
from typing import Optional

from .cache import cached

AACT_DIR = Path(__file__).resolve().parent.parent / "data" / "aact"


def fetch_aact(condition: str, intervention: str) -> Optional[list[dict]]:
    """Count competing trials for a condition/intervention pair from AACT.

    Returns None if neither the live DB nor the local flat-files are available —
    caller must not crash the pipeline just because this source is unreachable.
    """
    key = f"{condition}__{intervention}"
    return cached("aact", key, lambda: _fetch(condition, intervention))


def _fetch(condition: str, intervention: str) -> Optional[list[dict]]:
    live = _fetch_live(condition, intervention)
    if live is not None:
        return live
    return _fetch_flatfile(condition, intervention)


def _fetch_live(condition: str, intervention: str) -> Optional[list[dict]]:
    host = os.environ.get("AACT_DB_HOST")
    if not host:
        return None

    try:
        import psycopg2
    except ImportError:
        return None

    try:
        conn = psycopg2.connect(
            host=host,
            port=os.environ.get("AACT_DB_PORT", "5432"),
            dbname=os.environ.get("AACT_DB_NAME", "aact"),
            user=os.environ["AACT_DB_USER"],
            password=os.environ["AACT_DB_PASSWORD"],
            connect_timeout=10,
        )
    except (psycopg2.Error, KeyError):
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT s.nct_id, s.brief_title, s.overall_status, s.phase
                FROM ctgov.studies s
                JOIN ctgov.conditions c ON c.nct_id = s.nct_id
                WHERE c.name ILIKE %s AND s.brief_title ILIKE %s
                LIMIT 200
                """,
                (f"%{condition}%", f"%{intervention}%"),
            )
            rows = cur.fetchall()
    except psycopg2.Error:
        return None
    finally:
        conn.close()

    today = datetime.date.today().isoformat()
    return [{
        "source": "AACT live database (aact-db.ctti-clinicaltrials.org)",
        "url": "https://aact.ctti-clinicaltrials.org",
        "date_accessed": today,
        "raw_snippet": (
            f"{len(rows)} competing trials found for condition='{condition}', "
            f"intervention='{intervention}'. Examples (nct_id, title, status, phase): "
            f"{rows[:5]}"
        ),
    }]


def _fetch_flatfile(condition: str, intervention: str) -> Optional[list[dict]]:
    """Fallback: read studies.txt / conditions.txt downloaded into data/aact/."""
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


def get_competitors_from_aact(indication: str) -> Optional[dict]:
    """Count active trials (competitors) for an indication from local AACT flat-files.

    Returns None if the flat-files are missing; never raises on absent data.
    """
    conditions_file = AACT_DIR / "conditions.txt"
    studies_file = AACT_DIR / "studies.txt"
    if not conditions_file.exists() or not studies_file.exists():
        return None

    needle = indication.lower()
    with open(conditions_file, encoding="utf-8") as f:
        target_nct_ids = {
            row.get("nct_id")
            for row in csv.DictReader(f, delimiter="|")
            if needle in (row.get("downcase_name") or "").lower()
        }

    active_trials_count = 0
    phases_distribution: dict[str, int] = {}
    if target_nct_ids:
        with open(studies_file, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                if row.get("nct_id") not in target_nct_ids:
                    continue
                status = (row.get("overall_status") or "").strip().lower()
                is_active = any(k in status for k in ("recruit", "active", "enrolling")) and not any(
                    k in status for k in ("completed", "terminated", "withdrawn")
                )
                if is_active:
                    active_trials_count += 1
                    phase = (row.get("phase") or "Unknown").strip()
                    phases_distribution[phase] = phases_distribution.get(phase, 0) + 1

    return {
        "source_type": "aact",
        "indication": indication,
        "raw_snippet": (
            f"{active_trials_count} active clinical trials found for indication "
            f"'{indication}'. Phase distribution: {phases_distribution}"
        ),
        "active_trials_count": active_trials_count,
        "phases_distribution": phases_distribution,
    }
