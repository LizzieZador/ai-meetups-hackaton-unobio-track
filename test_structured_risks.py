"""Unit tests for R7 structured risks, numeric ranking, and numeric override guardrails."""
import unittest
from schema import Claim, parse_claims
from agents.chair_agent import _rank_and_score_key_risks, _enforce_risk_override_guardrail


class TestStructuredRisks(unittest.TestCase):
    def test_claim_structured_risk_fields(self):
        c = Claim(
            claim="Phase 2 efficacy failure in all-comers",
            type="inference",
            section="key_risks",
            reasoning="Lack of prospective biomarker selection reduces likelihood of showing superiority.",
            confidence="high",
            probability=0.45,
            impact_pct=0.60,
            impact_musd=90.0,
            risk_score=40.5,
        )
        c.validate()
        d = c.to_dict()
        self.assertEqual(d["probability"], 0.45)
        self.assertEqual(d["impact_pct"], 0.60)
        self.assertEqual(d["impact_musd"], 90.0)
        self.assertEqual(d["risk_score"], 40.5)

    def test_parse_claims_normalizes_percentages_and_numbers(self):
        raw = [
            {
                "claim": "Off-target hepatic toxicity",
                "type": "inference",
                "section": "key_risks",
                "reasoning": "Elevated ALT/AST observed in non-human primate studies.",
                "confidence": "high",
                "probability": "35%",
                "impact_pct": "70%",
                "impact_musd": "$105M",
            },
            {
                "claim": "Competitive displacement by third-generation inhibitor",
                "type": "inference",
                "section": "key_risks",
                "reasoning": "Competitor trial reading out 12 months ahead.",
                "confidence": "medium",
                "probability": 0.50,
                "impact_pct": 0.40,
            },
        ]

        claims = parse_claims(raw)
        self.assertEqual(len(claims), 2)

        c1 = claims[0]
        self.assertEqual(c1.probability, 0.35)
        self.assertEqual(c1.impact_pct, 0.70)
        self.assertEqual(c1.impact_musd, 105.0)
        self.assertEqual(c1.risk_score, 36.75)

        c2 = claims[1]
        self.assertEqual(c2.probability, 0.50)
        self.assertEqual(c2.impact_pct, 0.40)
        self.assertEqual(c2.risk_score, 20.0)

    def test_parse_claims_text_fallback_extraction(self):
        raw = [
            {
                "claim": "CMC yield failure during scale-up",
                "type": "inference",
                "section": "key_risks",
                "reasoning": "Estimated 40% probability of batch failure causing $80M impact on development budget.",
                "confidence": "medium",
            }
        ]

        claims = parse_claims(raw)
        self.assertEqual(len(claims), 1)
        c = claims[0]
        self.assertEqual(c.probability, 0.40)
        self.assertEqual(c.impact_musd, 80.0)
        self.assertEqual(c.risk_score, 32.0)

    def test_rank_and_score_key_risks_ordering(self):
        # 3 risks with different expected losses
        r_low = Claim(
            claim="Minor formulation delay",
            type="inference",
            section="key_risks",
            reasoning="Minor 2 month delay.",
            confidence="low",
            probability=0.20,
            impact_pct=0.10,
        )
        r_high = Claim(
            claim="Major pivotal trial primary endpoint failure",
            type="inference",
            section="key_risks",
            reasoning="Severe efficacy deficit.",
            confidence="high",
            probability=0.50,
            impact_pct=0.80,
        )
        r_med = Claim(
            claim="Patent term extension challenge",
            type="inference",
            section="key_risks",
            reasoning="Moderate IP risk.",
            confidence="medium",
            probability=0.30,
            impact_pct=0.40,
        )

        synthesis = [r_low, r_high, r_med]
        base_rnpv = 150.0

        _rank_and_score_key_risks(synthesis, base_rnpv=base_rnpv)

        # After ranking, index 0 must be r_high, index 1 must be r_med, index 2 must be r_low
        self.assertEqual(synthesis[0].claim, "Major pivotal trial primary endpoint failure")
        self.assertEqual(synthesis[1].claim, "Patent term extension challenge")
        self.assertEqual(synthesis[2].claim, "Minor formulation delay")

        # Check calculated values
        # r_high: impact_musd = 0.80 * 150 = 120.0, risk_score = 0.50 * 120 = 60.0
        self.assertEqual(synthesis[0].impact_musd, 120.0)
        self.assertEqual(synthesis[0].risk_score, 60.0)

    def test_numeric_override_guardrail_triggers_on_high_expected_loss(self):
        upstream = [
            Claim(
                claim="Valid target biology",
                type="evidence",
                section="scientific_thesis",
                source="Test Source",
            )
        ]

        high_risk = Claim(
            claim="Cardiotoxicity causing clinical hold",
            type="inference",
            section="key_risks",
            reasoning="Off-target ion channel inhibition.",
            confidence="high",
            probability=0.50,
            impact_pct=0.70,
            impact_musd=105.0,
            risk_score=52.5,
        )

        verdict = Claim(
            claim="Invest",
            type="inference",
            section="recommendation",
            reasoning="Attractive market size and strong initial preclinical data support full investment.",
            confidence="high",
        )

        synthesis = [high_risk, verdict]
        _enforce_risk_override_guardrail(upstream, synthesis, base_rnpv=150.0)

        # Because high_risk is not mitigated in verdict reasoning, it must downgrade to Conditional
        self.assertEqual(verdict.claim, "Conditional")
        self.assertIn("Rule Enforcement: High Risk Override", verdict.reasoning)
        self.assertIn("50%", verdict.reasoning)
        self.assertIn("-70%", verdict.reasoning)

    def test_numeric_override_guardrail_allows_invest_when_explicitly_mitigated(self):
        upstream = [
            Claim(
                claim="Valid target biology",
                type="evidence",
                section="scientific_thesis",
                source="Test Source",
            )
        ]

        high_risk = Claim(
            claim="Cardiotoxicity causing clinical hold",
            type="inference",
            section="key_risks",
            reasoning="Off-target ion channel inhibition.",
            confidence="high",
            probability=0.50,
            impact_pct=0.70,
            risk_score=52.5,
        )

        verdict = Claim(
            claim="Invest",
            type="inference",
            section="recommendation",
            reasoning="Invest warranted because cardiotoxicity is strictly mitigated by baseline ECG screening and structural lead optimization preventing clinical hold.",
            confidence="high",
        )

        synthesis = [high_risk, verdict]
        _enforce_risk_override_guardrail(upstream, synthesis, base_rnpv=150.0)

        # Mitigation present -> Invest stands
        self.assertEqual(verdict.claim, "Invest")

    def test_numeric_override_guardrail_fatal_risk_forces_do_not_invest(self):
        upstream = [
            Claim(
                claim="Valid target biology",
                type="evidence",
                section="scientific_thesis",
                source="Test Source",
            )
        ]

        fatal_risk = Claim(
            claim="Fatal neurotoxicity observed at sub-therapeutic doses",
            type="inference",
            section="key_risks",
            reasoning="Severe unresolvable central nervous system degeneration in all animal models.",
            confidence="high",
            probability=0.85,
            impact_pct=0.95,
            risk_score=80.75,
        )

        verdict = Claim(
            claim="Conditional",
            type="inference",
            section="recommendation",
            reasoning="Potentially attractive if reformulated.",
            confidence="low",
        )

        synthesis = [fatal_risk, verdict]
        _enforce_risk_override_guardrail(upstream, synthesis, base_rnpv=100.0)

        # Catastrophic risk without mitigation mandates Do Not Invest
        self.assertEqual(verdict.claim, "Do Not Invest")
        self.assertIn("Fatal Risk Override", verdict.reasoning)


if __name__ == "__main__":
    unittest.main()

