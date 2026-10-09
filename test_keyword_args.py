"""Unit tests verifying that all agent and pipeline functions use explicit keyword arguments

without relying on isinstance swaps or positional argument hacks.
"""
import unittest
from unittest.mock import MagicMock, patch

from agents.scientific_agent import scientific_agent
from agents.clinical_agent import clinical_agent
from agents.commercial_agent import commercial_agent
from agents.chair_agent import chair_agent
from pipeline import run_pipeline, collect_sources
from schema import Claim


class TestKeywordArguments(unittest.TestCase):
    def test_scientific_agent_keyword_args(self):
        # Verify scientific_agent cleanly accepts keyword arguments and does not swap valid_sources / cutoff_date
        test_sources = [
            {"source": "PubMed 123", "text": "Target validation", "group": "genetics", "raw_snippet": "Target validation"}
        ]
        valid_src = {"PubMed 123"}

        with patch("agents.base.ask_claude_json") as mock_llm:
            mock_llm.return_value = [
                {
                    "claim": "EGFR is validated in lung cancer",
                    "type": "inference",
                    "section": "scientific_thesis",
                    "reasoning": "Strong biological validation across studies.",
                    "confidence": "high",
                }
            ]

            claims = scientific_agent(
                indication="lung cancer",
                mechanism="EGFR inhibitor",
                sources=test_sources,
                extra_context="Preclinical stage",
                valid_sources=valid_src,
                cutoff_date="2026-01-01",
            )
            self.assertEqual(len(claims), 2)
            self.assertEqual(claims[0].claim, "EGFR is validated in lung cancer")

    def test_commercial_agent_keyword_args(self):
        # Verify commercial_agent cleanly accepts keyword arguments and valid_sources set without isinstance swap
        test_sources = [
            {"source": "Open Targets EGFR", "text": "Market data", "raw_snippet": "Market data"}
        ]
        valid_src = {"Open Targets EGFR"}

        with patch("agents.commercial_agent.ask_claude_json") as mock_llm:
            mock_llm.side_effect = [
                # First call: competitive landscape claims
                [
                    {
                        "claim": "Multiple EGFR inhibitors are approved",
                        "type": "inference",
                        "section": "competitive_landscape",
                        "reasoning": "Standard of care established.",
                        "confidence": "high",
                    }
                ],
                # Second call: commercial opportunity JSON
                {
                    "target_population": {"value": 50000, "basis": "incidence"},
                    "diagnosis_rate": {"value": 0.70, "basis": "guidelines"},
                    "eligibility_rate": {"value": 0.50, "basis": "biomarker"},
                    "share_penetration_peak": {"base": 0.20, "low": 0.10, "high": 0.30, "basis": "competitive"},
                    "price_annual_per_patient": {"value": 150000, "basis": "analogs"},
                    "gross_to_net": {"value": 0.75, "basis": "standard"},
                },
            ]

            claims = commercial_agent(
                indication="lung cancer",
                mechanism="EGFR inhibitor",
                sources=test_sources,
                clinical_claims=[],
                extra_context="Global market",
                valid_sources=valid_src,
                cutoff_date="2026-01-01",
            )
            self.assertGreaterEqual(len(claims), 1)

    def test_clinical_agent_keyword_args(self):
        # Verify clinical_agent accepts standardized keyword arguments including cutoff_date
        test_sources = [
            {"source": "ClinicalTrials.gov NCT123", "text": "Phase 2 trial", "raw_snippet": "Phase 2 trial"}
        ]
        valid_src = {"ClinicalTrials.gov NCT123"}

        with patch("agents.base.ask_claude_json") as mock_llm:
            mock_llm.return_value = [
                {
                    "claim": "Phase 2 trial ongoing in 150 patients",
                    "type": "inference",
                    "section": "clinical_development_plan",
                    "reasoning": "Registry records show active phase 2 study.",
                    "confidence": "high",
                }
            ]

            claims = clinical_agent(
                indication="lung cancer",
                mechanism="EGFR inhibitor",
                sources=test_sources,
                scientific_claims=[],
                extra_context="",
                valid_sources=valid_src,
                cutoff_date="2026-01-01",
            )
            self.assertEqual(len(claims), 1)

    @patch("pipeline.collect_sources")
    @patch("pipeline.scientific_agent")
    @patch("pipeline.clinical_agent")
    @patch("pipeline.commercial_agent")
    @patch("pipeline.chair_agent")
    def test_run_pipeline_keyword_propagation(
        self, mock_chair, mock_comm, mock_clin, mock_sci, mock_collect
    ):
        mock_collect.return_value = {
            "pubmed": [],
            "opentargets": [],
            "clinicaltrials": [],
            "aact": [],
            "commercial": [],
            "regulatory": [],
            "market_news": [],
        }
        mock_sci.return_value = []
        mock_clin.return_value = []
        mock_comm.return_value = []
        mock_chair.return_value = [
            Claim(claim="Invest", type="inference", section="recommendation", reasoning="Strong data", confidence="high")
        ]

        claims, cost, sources = run_pipeline(
            indication="NSCLC",
            mechanism="KRAS G12C",
            extra_context="Phase 1 complete",
            cutoff_date="2025-12-31",
        )

        # Verify collect_sources was called with keyword args
        mock_collect.assert_called_once_with(
            indication="nsclc",
            mechanism="kras g12c",
            cutoff_date="2025-12-31",
        )

        # Verify scientific_agent was called with keyword args
        mock_sci.assert_called_once()
        _, sci_kwargs = mock_sci.call_args
        self.assertEqual(sci_kwargs["indication"], "nsclc")
        self.assertEqual(sci_kwargs["mechanism"], "kras g12c")
        self.assertEqual(sci_kwargs["extra_context"], "Phase 1 complete")
        self.assertEqual(sci_kwargs["cutoff_date"], "2025-12-31")

        # Verify clinical_agent was called with keyword args
        mock_clin.assert_called_once()
        _, clin_kwargs = mock_clin.call_args
        self.assertEqual(clin_kwargs["indication"], "nsclc")
        self.assertEqual(clin_kwargs["mechanism"], "kras g12c")
        self.assertEqual(clin_kwargs["cutoff_date"], "2025-12-31")

        # Verify commercial_agent was called with keyword args
        mock_comm.assert_called_once()
        _, comm_kwargs = mock_comm.call_args
        self.assertEqual(comm_kwargs["indication"], "nsclc")
        self.assertEqual(comm_kwargs["mechanism"], "kras g12c")
        self.assertEqual(comm_kwargs["cutoff_date"], "2025-12-31")

        # Verify chair_agent was called with keyword args
        mock_chair.assert_called_once()
        _, chair_kwargs = mock_chair.call_args
        self.assertEqual(chair_kwargs["indication"], "nsclc")
        self.assertEqual(chair_kwargs["mechanism"], "kras g12c")
        self.assertEqual(chair_kwargs["extra_context"], "Phase 1 complete")


if __name__ == "__main__":
    unittest.main()
