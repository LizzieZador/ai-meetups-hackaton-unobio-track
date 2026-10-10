"""Unit test for deterministic financial modeling engine (financials.py) and Chair Agent integration."""
from unittest.mock import patch
from schema import Claim
from financials import (
    calculate_financials,
    extract_enrollment_from_aact,
    extract_ptrs_parameters,
    extract_peak_sales,
    build_deterministic_financial_claims,
    format_financial_brief_for_chair,
)
from agents.chair_agent import chair_agent


def test_financials_suite():
    # 1. Test AACT median extraction
    mock_sources = {
        "aact": [
            {
                "source": "AACT flat-file dump",
                "raw_snippet": (
                    "AACT analysis... Median enrollment across all matched studies in the indication: "
                    "Phase 1: median 40 (n=2 trials); Phase 2: median 150 (n=3 trials); Phase 3: median 480 (n=1 trials)."
                ),
                "median_enrollment": {"Phase 1": 40, "Phase 2": 150, "Phase 3": 480},
            }
        ]
    }
    enrollment = extract_enrollment_from_aact(mock_sources, "non-small cell lung cancer")
    assert enrollment["Phase 1"] == 40
    assert enrollment["Phase 2"] == 150
    assert enrollment["Phase 3"] == 480
    print("Test 1 Passed: extract_enrollment_from_aact correctly pulls AACT medians!")

    # 2. Test PTRS extraction with biomarker multiplier
    mock_clinical_claims = [
        Claim(
            claim="PTRS Benchmarks: Phase 1 = 65%, Phase 2 = 35%, Phase 3 = 60%, Cumulative PTRS = 13.6%",
            type="inference",
            section="clinical_development_plan",
            reasoning="Biomarker-selected patient population with KRAS G12C mutation acts as a 1.4x success multiplier.",
            confidence="high",
        )
    ]
    ptrs_data, is_bmk = extract_ptrs_parameters(mock_clinical_claims, "non-small cell lung cancer", "KRAS G12C")
    assert is_bmk is True
    assert ptrs_data["Phase 1"]["base"] == 0.65
    assert ptrs_data["Phase 2"]["base"] == 0.35
    assert ptrs_data["Phase 3"]["base"] == 0.60
    print("Test 2 Passed: extract_ptrs_parameters identifies biomarker enrichment and structured PTRS!")

    # 3. Test Peak Sales extraction
    mock_commercial_claims = [
        Claim(
            claim="Deterministic patient funnel and peak sales estimate: Base peak sales projected at $4800.0M (Range: $2400.0M - $9600.0M).",
            type="inference",
            section="commercial_opportunity",
            reasoning="Calculated deterministically by market_model.estimate_market from model assumptions.",
            confidence="high",
        )
    ]
    peak_sales = extract_peak_sales(mock_commercial_claims)
    assert peak_sales["base"] == 4800.0
    assert peak_sales["low"] == 2400.0
    assert peak_sales["high"] == 9600.0
    print("Test 3 Passed: extract_peak_sales accurately extracts Low, Base, and High values!")

    # 4. Test calculate_financials DCF and Capital Model
    res = calculate_financials(
        peak_sales_millions=peak_sales,
        enrollment_by_phase=enrollment,
        ptrs_per_phase=ptrs_data,
        indication="non-small cell lung cancer",
        mechanism="KRAS G12C inhibitor",
    )

    # Capital checks
    assert res.capital_to_next_milestone_millions["base"] > 0
    assert res.total_capital_to_launch_millions["base"] > res.capital_to_next_milestone_millions["base"]
    assert res.timeline_to_launch_years == 7.5

    # rNPV checks (time-value DCF)
    assert res.rnpv_millions["low"] < res.rnpv_millions["base"] < res.rnpv_millions["high"]
    assert res.discount_rate == 0.105
    assert len(res.value_inflection_points) == 4
    assert res.licensing_terms["upfront_cash_millions"] > 0
    print(f"Test 4 Passed: calculate_financials computes DCF rNPV (Base = ${res.rnpv_millions['base']}M) and milestone capital!")

    # 5. Test brief formatting and claim objects
    brief = format_financial_brief_for_chair(res)
    assert "DETERMINISTIC FINANCIAL & VALUATION MODEL" in brief
    expected_base_str = f"Base Scenario: rNPV = ${res.rnpv_millions['base']:.1f}M"
    assert expected_base_str in brief

    claims = build_deterministic_financial_claims(res)
    assert len(claims) == 4
    assert any(c.section == "recommendation" and "rNPV Scenarios:" in c.claim for c in claims)
    assert any(c.section == "capital_required" and "Capital required to reach next major milestone" in c.claim for c in claims)
    print("Test 5 Passed: format_financial_brief_for_chair and build_deterministic_financial_claims generate valid artifacts!")

    # 6. Test Chair Agent enforcement of deterministic numbers
    mock_chair_llm = [
        {"claim": "Executive Summary: Solid pipeline candidate.", "type": "inference", "section": "recommendation", "reasoning": "Holistic synthesis.", "confidence": "high"},
        {"claim": "rNPV Scenarios: Low = $50M, Base = $200M, High = $600M", "type": "inference", "section": "recommendation", "reasoning": "Chair qualitative thoughts on market.", "confidence": "medium"},
        {"claim": "Invest", "type": "inference", "section": "recommendation", "reasoning": "High commercial value.", "confidence": "high"},
        {"claim": "Key Risk 1: Competition in second-line.", "type": "inference", "section": "key_risks", "reasoning": "15% probability.", "confidence": "medium"},
        {"claim": "Capital Required: Needs around $15M for Phase 1.", "type": "inference", "section": "capital_required", "reasoning": "Standard trial spend.", "confidence": "medium"},
        {"claim": "Critical Unknown: Long-term cardiotoxicity.", "type": "inference", "section": "critical_unknowns", "reasoning": "Requires biopsy assays.", "confidence": "medium"},
        {"claim": "Question 1: Human target engagement?", "type": "inference", "section": "diligence_questions", "reasoning": "Phase 1 biopsy.", "confidence": "medium"},
        {"claim": "Question 2: Dosing schedule?", "type": "inference", "section": "diligence_questions", "reasoning": "Phase 1 dosing.", "confidence": "medium"},
        {"claim": "Question 3: FDA regulatory pathway?", "type": "inference", "section": "diligence_questions", "reasoning": "FDA meeting minutes.", "confidence": "medium"},
        {"claim": "Question 4: CMC yield stability?", "type": "inference", "section": "diligence_questions", "reasoning": "CMC analytical batch.", "confidence": "medium"},
        {"claim": "Question 5: Patent freedom to operate?", "type": "inference", "section": "diligence_questions", "reasoning": "FTO legal opinion.", "confidence": "medium"},
    ]

    with patch("agents.chair_agent.ask_claude_json", return_value=mock_chair_llm):
        final_claims = chair_agent(
            indication="non-small cell lung cancer",
            mechanism="KRAS G12C inhibitor",
            scientific_claims=[],
            clinical_claims=mock_clinical_claims,
            commercial_claims=mock_commercial_claims,
            financial_result=res,
        )

        rnpv_final = next(c for c in final_claims if c.section == "recommendation" and "rnpv" in c.claim.lower())
        expected_rnpv_claim = f"rNPV Scenarios: Low = ${res.rnpv_millions['low']:.1f}M, Base = ${res.rnpv_millions['base']:.1f}M, High = ${res.rnpv_millions['high']:.1f}M"
        assert rnpv_final.claim == expected_rnpv_claim, f"Expected '{expected_rnpv_claim}', got '{rnpv_final.claim}'"
        assert "Chair qualitative thoughts on market" in rnpv_final.reasoning
        print("Test 6 Passed: Chair agent enforces exact deterministic rNPV numbers while preserving LLM explanation!")

    # 7. Test Chair Agent backfill when LLM omits rNPV and capital_required claims
    mock_chair_omitted = [
        {"claim": "Executive Summary: Solid pipeline candidate.", "type": "inference", "section": "recommendation", "reasoning": "Holistic synthesis.", "confidence": "high"},
        {"claim": "Invest", "type": "inference", "section": "recommendation", "reasoning": "High commercial value.", "confidence": "high"},
        {"claim": "Key Risk 1: Competition in second-line.", "type": "inference", "section": "key_risks", "reasoning": "15% probability.", "confidence": "medium"},
        {"claim": "Critical Unknown: Long-term cardiotoxicity.", "type": "inference", "section": "critical_unknowns", "reasoning": "Requires biopsy assays.", "confidence": "medium"},
        {"claim": "Question 1: Human target engagement?", "type": "inference", "section": "diligence_questions", "reasoning": "Phase 1 biopsy.", "confidence": "medium"},
        {"claim": "Question 2: Dosing schedule?", "type": "inference", "section": "diligence_questions", "reasoning": "Phase 1 dosing.", "confidence": "medium"},
        {"claim": "Question 3: FDA regulatory pathway?", "type": "inference", "section": "diligence_questions", "reasoning": "FDA meeting minutes.", "confidence": "medium"},
        {"claim": "Question 4: CMC yield stability?", "type": "inference", "section": "diligence_questions", "reasoning": "CMC analytical batch.", "confidence": "medium"},
        {"claim": "Question 5: Patent freedom to operate?", "type": "inference", "section": "diligence_questions", "reasoning": "FTO legal opinion.", "confidence": "medium"},
    ]

    with patch("agents.chair_agent.ask_claude_json", return_value=mock_chair_omitted):
        final_claims_2 = chair_agent(
            indication="non-small cell lung cancer",
            mechanism="KRAS G12C inhibitor",
            scientific_claims=[],
            clinical_claims=mock_clinical_claims,
            commercial_claims=mock_commercial_claims,
            financial_result=res,
        )

        rnpv_final_2 = next((c for c in final_claims_2 if c.section == "recommendation" and "rnpv" in c.claim.lower()), None)
        assert rnpv_final_2 is not None, "rNPV claim should be backfilled"
        expected_rnpv_claim = f"rNPV Scenarios: Low = ${res.rnpv_millions['low']:.1f}M, Base = ${res.rnpv_millions['base']:.1f}M, High = ${res.rnpv_millions['high']:.1f}M"
        assert rnpv_final_2.claim == expected_rnpv_claim
        cap_claims_2 = [c for c in final_claims_2 if c.section == "capital_required"]
        assert len(cap_claims_2) >= 3, f"Expected at least 3 capital claims backfilled, got {len(cap_claims_2)}"
        print("Test 7 Passed: Chair agent backfills deterministic rNPV and capital_required if LLM omits them!")


if __name__ == "__main__":
    test_financials_suite()
    print("\n ALL FINANCIAL TESTS PASSED SUCCESSFULLY! ")
