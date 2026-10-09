import json
import sys
from pathlib import Path

# Allows running both directly via "python agents/test_local_run.py" and
# via "python -m agents.test_local_run" from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

# Ensure AACT_DB_* from .env are loaded so get_competitors_from_aact()
# connects to database rather than falling back to flat files.
load_dotenv()

# Import modules
from sources import run_commercial_analysis
from sources import get_competitors_from_aact

def generate_commercial_report(indication: str, target: str) -> dict:
    print(f"=== RUNNING COMMERCIAL AGENT FOR [{indication} | {target}] ===")

    # 1. Collect market and epidemiology data
    commercial_base = run_commercial_analysis(indication, target)

    # 2. Collect competitor data from AACT (indication + target/mechanism)
    aact_base = get_competitors_from_aact(indication, target)

    all_claims = []

    # Combine evidence from epidemiology source
    if commercial_base and "claims" in commercial_base:
        all_claims.extend(commercial_base["claims"])

    # get_competitors_from_aact() returns raw analysis (count + phase distribution);
    # transform into an evidence claim here following schema.py.
    if aact_base:
        all_claims.append({
            "claim": aact_base["raw_snippet"],
            "type": "evidence",
            "section": "competitive_landscape",
            "source": aact_base["source"],
            "date_accessed": aact_base["date_accessed"],
        })

    # 3. Add inference based on actual count of active trials.
    if aact_base and aact_base["active_trials_count"] > 0:
        all_claims.append({
            "claim": (
                f"High competitive pressure in the '{indication}' indication. "
                f"{aact_base['active_trials_count']} active trials hinder "
                f"patient recruitment for new clinical studies."
            ),
            "type": "inference",
            "section": "competitive_landscape",
            "reasoning": "Estimated based on aggregated AACT data on active trial counts.",
            "confidence": "medium",
        })

    financial_metrics = commercial_base.get("financial_metrics", {}) if commercial_base else {}

    return {
        "agent": "commercial_agent",
        "indication": indication,
        "target": target,
        "claims": all_claims,
        "financial_metrics": financial_metrics
    }

if __name__ == "__main__":
    report = generate_commercial_report("Non-Small Cell Lung Cancer", "KRAS G12C")
    
    # Save summary result to file for Chair Agent
    with open("commercial_agent_output.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    print("\n Commercial Agent report successfully generated and saved to 'commercial_agent_output.json'!")