"""Commercial source — market/epidemiology figures from the local data/market_data.csv.

Owner: Комерційний аналітик. No allowed API covers market size or pricing, so this
is a curated static table (SEER / GLOBOCAN / WHO figures); the commercial agent
treats anything derived from it as low-confidence unless the CSV names a source.
"""
import csv
import datetime
from pathlib import Path
from typing import Optional

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "market_data.csv"


def get_epidemiology_from_csv(indication_query: str) -> Optional[dict]:
    """Return the CSV row whose Indication matches the query (substring, either way)."""
    if not CSV_PATH.exists():
        return None

    query = indication_query.lower()
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = row["Indication"].lower()
            if name in query or query in name:
                return row
    return None


def fetch_commercial(indication: str) -> Optional[list[dict]]:
    """Pipeline-shaped source: a list of snippet dicts, or None if no row matches."""
    row = get_epidemiology_from_csv(indication)
    if row is None:
        return None
    return [{
        "source": row["Primary_Epidemiology_Source"],
        "url": str(CSV_PATH.name),
        "date_accessed": datetime.date.today().isoformat(),
        "raw_snippet": (
            f"{row['Indication']}: prevalence US+EU {row['Prevalence_US_EU']}, "
            f"annual incidence US+EU {row['Incidence_Annual_US_EU']}, "
            f"average annual treatment cost USD {row['Avg_Annual_Treatment_Cost_USD']}"
        ),
    }]


def run_commercial_analysis(indication: str, target: str) -> Optional[dict]:
    """Epidemiology evidence claims + naive peak-sales scenarios for the local test run."""
    row = get_epidemiology_from_csv(indication)
    if row is None:
        return None

    today = datetime.date.today().isoformat()
    incidence = int(row["Incidence_Annual_US_EU"])
    cost = int(row["Avg_Annual_Treatment_Cost_USD"])
    # Peak sales = incidence x treated share x annual cost, in USD millions.
    scenarios = {
        "peak_sales_low": 0.05,
        "peak_sales_base": 0.10,
        "peak_sales_high": 0.20,
    }
    return {
        "claims": [{
            "claim": (
                f"{row['Indication']}: prevalence {row['Prevalence_US_EU']} and annual "
                f"incidence {row['Incidence_Annual_US_EU']} in US+EU."
            ),
            "type": "evidence",
            "source": row["Primary_Epidemiology_Source"],
            "date_accessed": today,
        }],
        "financial_metrics": {
            **{k: round(incidence * share * cost / 1e6) for k, share in scenarios.items()},
            "currency": "USD_millions",
        },
    }
