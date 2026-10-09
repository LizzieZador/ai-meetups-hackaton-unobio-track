import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root folder to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from schema import Claim
from agents.chair_agent import chair_agent
# 1. Create input claims modeling high risk
test_scientific = [
    Claim(
        claim="Target EGFR T790M is strongly implicated in resistance.",
        type="evidence",
        section="scientific_thesis",
        source="PubMed PMID:123456",
        url="https://pubmed.ncbi.nlm.nih.gov/123456/",
        date_accessed="2026-10-04",
    )
]
test_clinical = [
    Claim(
        claim="Phase 2 trial was halted due to 40% grade 4 cardiotoxicity and severe off-target mortality.",
        type="evidence",
        section="clinical_development_plan",
        source="ClinicalTrials.gov NCT0999999",
        url="https://clinicaltrials.gov/study/NCT0999999",
        date_accessed="2026-10-04",
    ),
    Claim(
        claim="PTRS is reduced to 10% due to catastrophic safety profile.",
        type="inference",
        section="clinical_development_plan",
        reasoning="Lethal toxicity profile makes regulatory approval nearly impossible.",
        confidence="high",
    ),
]
test_commercial = [
    Claim(
        claim="Market opportunity is $2B, but multiple safer competitors are already approved.",
        type="inference",
        section="commercial_opportunity",
        reasoning="Commercial viability is low due to superior existing standards of care.",
        confidence="medium",
    )
]
print("=== RUNNING CHAIR AGENT CRITICAL RISK TEST ===")
result_claims = chair_agent(
    indication="Non-Small Cell Lung Cancer",
    mechanism="EGFR mutant inhibitor",
    scientific_claims=test_scientific,
    clinical_claims=test_clinical,
    commercial_claims=test_commercial,
    extra_context="URGENT UPDATE: FDA issued a clinical hold due to patient deaths.",
)
# Verify recommendation/verdict
recommendations = [c for c in result_claims if c.section == "recommendation"]
key_risks = [c for c in result_claims if c.section == "key_risks"]
print("\n--- TOP RISKS ---")
for r in key_risks:
    print(f"[{r.confidence.upper()}] {r.claim} -> {r.reasoning}")
print("\n--- RECOMMENDATION & VERDICT ---")
for rec in recommendations:
    print(f"VERDICT CLAIM: {rec.claim}")
    print(f"REASONING: {rec.reasoning}\n")