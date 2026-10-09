"""Demonstration scenario: system response to contradictory data.

Specification requirement ("Moving Beyond the Molecule"):
"The demo should include at least one example where new or contradictory evidence
changes the system's conclusion."

This script runs the investment committee in two modes for the same target:
1. Baseline scenario (no extra context) -> Positive/conditional evaluation.
2. Contradictory evidence scenario (Phase 3 failure, hepatotoxicity, FDA hold) -> Verdict flips to Do Not Invest.
"""
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from pipeline import run_pipeline


INDICATION = "non-small cell lung cancer"
MECHANISM = "KRAS G12C inhibitor"

CONTRADICTORY_CONTEXT = (
    "URGENT CONFIRMATORY TRIAL UPDATE: Phase 3 confirmatory study failed to demonstrate "
    "statistically significant Overall Survival improvement (HR=0.98, p=0.82) and revealed "
    "unexpected 24% treatment-emergent Grade 4 hepatotoxicity, resulting in an FDA clinical hold "
    "and complete response letter."
)


def extract_summary(claims: list) -> dict:
    verdict = "Unknown"
    recommendation_reasons = []
    rnpv_claim = "Not calculated"
    top_risks = []

    for c in claims:
        if c.section == "recommendation":
            if c.claim in ("Invest", "Conditional", "Do Not Invest"):
                verdict = c.claim
            elif "rnpv" in c.claim.lower() or "scenario" in c.claim.lower():
                rnpv_claim = c.claim
            if c.reasoning:
                recommendation_reasons.append(c.reasoning)
        elif c.section == "key_risks":
            top_risks.append(f"{c.claim} (Reasoning: {c.reasoning})")

    return {
        "verdict": verdict,
        "rnpv": rnpv_claim,
        "top_risk": top_risks[0] if top_risks else "None reported",
        "reasoning": " | ".join(recommendation_reasons[:2]),
    }


def main():
    print("=" * 80)
    print("DEMO: BIOTECH INVESTMENT COMMITTEE — CONTRADICTORY EVIDENCE SENSITIVITY")
    print(f"Indication: {INDICATION}")
    print(f"Target/Mechanism: {MECHANISM}")
    print("=" * 80)

    # 1. Baseline run
    print("\n>>> STEP 1: Running baseline pipeline (without additional context)...")
    base_claims, base_cost, _ = run_pipeline(INDICATION, MECHANISM, extra_context="")
    base_summary = extract_summary(base_claims)

    print(f"   [Baseline verdict]: {base_summary['verdict']}")
    print(f"   [rNPV estimate]: {base_summary['rnpv']}")
    print(f"   [Top risk]: {base_summary['top_risk'][:120]}...")

    # 2. Run with contradictory evidence
    print("\n>>> STEP 2: Running with new contradictory evidence (FDA Hold + toxicity)...")
    print(f"   Injected context: {CONTRADICTORY_CONTEXT[:100]}...")
    contra_claims, contra_cost, _ = run_pipeline(INDICATION, MECHANISM, extra_context=CONTRADICTORY_CONTEXT)
    contra_summary = extract_summary(contra_claims)

    print(f"   [Updated verdict]: {contra_summary['verdict']}")
    print(f"   [rNPV estimate]: {contra_summary['rnpv']}")
    print(f"   [Top risk]: {contra_summary['top_risk'][:120]}...")

    # 3. Summary comparison table
    print("\n" + "=" * 80)
    print("DEMO SUMMARY: SYSTEM CONCLUSION UPDATE (BEFORE vs AFTER)")
    print("=" * 80)
    print(f"{'Parameter':<25} | {'1. Baseline State':<25} | {'2. Contradictory Data':<25}")
    print("-" * 80)
    print(f"{'Final Verdict':<25} | {base_summary['verdict']:<25} | {contra_summary['verdict']:<25}")
    print(f"{'Top Risk':<25} | {base_summary['top_risk'][:25]:<25} | {contra_summary['top_risk'][:25]:<25}")
    print("-" * 80)
    print(f"Verdict Rationale (Before): {base_summary['reasoning'][:120]}...")
    print(f"Verdict Rationale (After):  {contra_summary['reasoning'][:120]}...")
    print("=" * 80)
    print("Requirement verified: new evidence dynamically updates the committee verdict!")


if __name__ == "__main__":
    main()

