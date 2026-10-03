"""AACT (Aggregate Analysis of ClinicalTrials.gov) — live Postgres DB, with a
static flat-file fallback.

AACT and the ClinicalTrials.gov API are one dataset in two forms: AACT is for
aggregated queries ("all trials on target X"), the API is for one study's detail
(see fetch_clinicaltrials.py).

get_competitors_from_aact() does the actual lookup and returns a raw analysis
dict (active trial count + phase distribution); fetch_aact() wraps it into the
pipeline's {source, url, date_accessed, raw_snippet} list shape — the same split
as fetch_commercial.py's get_epidemiology_from_csv() / fetch_commercial().

Tries a live, read-only connection to the AACT public Postgres database
(aact-db.ctti-clinicaltrials.org) using credentials from the environment
(AACT_DB_HOST/PORT/NAME/USER/PASSWORD — see .env.example) first. If those
aren't set, or the query fails, falls back to local static flat-files in
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

ACTIVE_STATUS_KEYWORDS = ("recruit", "active", "enrolling")
INACTIVE_STATUS_KEYWORDS = ("completed", "terminated", "withdrawn")


def fetch_aact(condition: str, intervention: str) -> Optional[list[dict]]:
    """Pipeline-shaped source: a list with one snippet dict, or None if AACT is
    unreachable (neither the live DB nor the local flat-files are available) —
    caller must not crash the pipeline just because this source is unreachable.
    """
    key = f"{condition}__{intervention}"
    return cached(
        "aact", key, lambda: _to_pipeline_shape(get_competitors_from_aact(condition, intervention))
    )


def _to_pipeline_shape(analysis: Optional[dict]) -> Optional[list[dict]]:
    if analysis is None:
        return None
    return [{
        "source": analysis["source"],
        "url": "https://aact.ctti-clinicaltrials.org",
        "date_accessed": analysis["date_accessed"],
        "raw_snippet": analysis["raw_snippet"],
    }]


def get_competitors_from_aact(condition: str, intervention: str) -> Optional[dict]:
    """Active trial count + phase distribution for a condition/intervention pair.

    Tries the live DB first, then the local flat-file dump. Returns None if
    neither source is available.
    """
    live = _query_live(condition, intervention)
    if live is not None:
        return live
    return _query_flatfile(condition, intervention)


def _query_live(condition: str, intervention: str) -> Optional[dict]:
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
                SELECT DISTINCT s.nct_id, s.overall_status, s.phase
                FROM ctgov.studies s
                JOIN ctgov.conditions c ON c.nct_id = s.nct_id
                WHERE c.name ILIKE %s AND s.brief_title ILIKE %s
                LIMIT 1000
                """,
                (f"%{condition}%", f"%{intervention}%"),
            )
            rows = cur.fetchall()
    except psycopg2.Error:
        return None
    finally:
        conn.close()

    return _summarize(rows, condition, "AACT live database (aact-db.ctti-clinicaltrials.org)")


def _query_flatfile(condition: str, intervention: str) -> Optional[dict]:
    conditions_file = AACT_DIR / "conditions.txt"
    studies_file = AACT_DIR / "studies.txt"
    if not conditions_file.exists() or not studies_file.exists():
        return None

    try:
        target_nct_ids = set()
        with open(conditions_file, mode="r", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                condition_name = row.get("downcase_name", "")
                if condition_name and condition.lower() in condition_name.lower():
                    target_nct_ids.add(row.get("nct_id"))

        if not target_nct_ids:
            return _summarize([], condition, "AACT flat-file dump")

        rows = []
        with open(studies_file, mode="r", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="|"):
                if row.get("nct_id") not in target_nct_ids:
                    continue
                brief_title = (row.get("brief_title") or "").lower()
                if intervention.lower() in brief_title:
                    rows.append((row.get("nct_id"), row.get("overall_status", ""), row.get("phase", "")))

        return _summarize(rows, condition, "AACT flat-file dump")
    except (OSError, KeyError, ValueError):
        return None


def _summarize(rows: list[tuple], condition: str, source: str) -> dict:
    active_trials_count = 0
    phases_distribution: dict[str, int] = {}
    for _nct_id, status, phase in rows:
        status = (status or "").strip().lower()
        is_active = any(k in status for k in ACTIVE_STATUS_KEYWORDS) and not any(
            k in status for k in INACTIVE_STATUS_KEYWORDS
        )
        if is_active:
            active_trials_count += 1
            phase_label = (phase or "Unknown").strip() or "Unknown"
            phases_distribution[phase_label] = phases_distribution.get(phase_label, 0) + 1

    return {
        "source": source,
        "date_accessed": datetime.date.today().isoformat(),
        "raw_snippet": (
            f"Знайдено {active_trials_count} активних клінічних випробувань за "
            f"показанням '{condition}'. Розподіл за фазами: {phases_distribution}"
        ),
        "active_trials_count": active_trials_count,
        "phases_distribution": phases_distribution,
    }
