"""Deterministic financial modeling, capital requirement, and rNPV engine.

Pure Python calculation engine — absolutely NO LLM math.
Computes:
1. Capital model: Phase-by-phase trial costs based on AACT median enrollment
   and industry cost-per-patient benchmarks ($50k-$150k/patient).
2. Development timeline and value inflection points (Phase 1, Phase 2 PoC, Phase 3).
3. Risk-adjusted technical and regulatory success probabilities (PTRS per phase & cumulative).
4. Out-licensing and M&A valuation benchmarks at Phase 2 Proof-of-Concept.
5. Institutional-grade risk-adjusted Net Present Value (rNPV) across Low, Base,
   and High scenarios using discounted cash flows (DCF) with time-value of money.

The chair agent does NOT calculate financial numbers; it receives this deterministic
model output and focuses purely on explaining the underwriting implications.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Optional

from schema import Claim


# ----------------------------------------------------------------------------
# Industry benchmark defaults by therapeutic area / indication
# ----------------------------------------------------------------------------
DEFAULT_DISCOUNT_RATE = 0.105  # 10.5% standard biotech WACC / cost of capital
DEFAULT_COMMERCIAL_MARGIN = 0.75  # 75% net operating margin (COGS ~15%, distribution/SG&A ~10%)
DEFAULT_COMMERCIAL_LIFE_YEARS = 10  # 10 years of patent exclusivity post-launch
DEFAULT_LAUNCH_PREP_COST_MILLIONS = 30.0  # $30M for pre-launch inventory & validation

# Standard industry benchmarks for trial duration (years)
DEFAULT_PHASE_DURATIONS = {
    "Phase 1": 1.5,
    "Phase 2": 2.0,
    "Phase 3": 3.0,
    "Regulatory Review": 1.0,
}

# Standard enrollment benchmarks if AACT data is unavailable
DEFAULT_ENROLLMENT_BENCHMARKS = {
    "oncology": {"Phase 1": 35, "Phase 2": 120, "Phase 3": 400},
    "general": {"Phase 1": 45, "Phase 2": 180, "Phase 3": 750},
    "rare": {"Phase 1": 20, "Phase 2": 45, "Phase 3": 160},
}

# Cost per patient benchmarks ($ per patient)
DEFAULT_COST_PER_PATIENT = {
    "Phase 1": {"low": 45000, "base": 65000, "high": 90000},
    "Phase 2": {"low": 60000, "base": 85000, "high": 120000},
    "Phase 3": {"low": 80000, "base": 110000, "high": 155000},
}

# Cumulative PTRS benchmarks (BIO / Informa clinical development success rates)
DEFAULT_PTRS_BENCHMARKS = {
    "oncology": {
        "Phase 1": {"low": 0.50, "base": 0.62, "high": 0.75},
        "Phase 2": {"low": 0.18, "base": 0.28, "high": 0.42},
        "Phase 3": {"low": 0.42, "base": 0.55, "high": 0.68},
    },
    "non_oncology": {
        "Phase 1": {"low": 0.55, "base": 0.68, "high": 0.78},
        "Phase 2": {"low": 0.25, "base": 0.38, "high": 0.50},
        "Phase 3": {"low": 0.50, "base": 0.64, "high": 0.75},
    },
}

# Annual post-launch revenue ramp (% of peak sales achieved each year of exclusivity)
REVENUE_RAMP_CURVE = [
    0.15,  # Year 1 post-launch
    0.40,  # Year 2
    0.75,  # Year 3
    0.95,  # Year 4
    1.00,  # Year 5 (Peak plateau)
    1.00,  # Year 6
    1.00,  # Year 7
    0.90,  # Year 8
    0.65,  # Year 9 (Generic / biosimilar entry begins)
    0.40,  # Year 10
]


@dataclass
class FinancialResult:
    """Complete structured output of the deterministic financial model."""
    # Peak sales input from market_model ($ Millions)
    peak_sales_millions: dict[str, float]  # {"low": float, "base": float, "high": float}

    # PTRS per phase and cumulative success probability (0..1)
    ptrs_per_phase: dict[str, dict[str, float]]
    cumulative_ptrs: dict[str, float]  # {"low": float, "base": float, "high": float}

    # Clinical trial capital model
    enrollment_by_phase: dict[str, int]
    cost_per_patient: dict[str, dict[str, float]]
    phase_costs_millions: dict[str, dict[str, float]]  # Phase 1, Phase 2, Phase 3 in $M
    capital_to_next_milestone_millions: dict[str, float]  # {"low": float, "base": float, "high": float}
    total_capital_to_launch_millions: dict[str, float]  # {"low": float, "base": float, "high": float}

    # Timelines
    timeline_to_next_milestone_months: int
    timeline_to_launch_years: float
    value_inflection_points: list[dict[str, Any]]

    # rNPV valuation ($ Millions)
    discount_rate: float
    pv_commercial_millions: dict[str, float]
    pv_development_cost_millions: dict[str, float]
    rnpv_millions: dict[str, float]  # {"low": float, "base": float, "high": float}

    # Out-licensing & M&A benchmark outcomes (at Phase 2 PoC)
    licensing_terms: dict[str, Any]

    # Basis and metadata
    is_biomarker_selected: bool
    therapeutic_area: str
    assumptions_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_sales_millions": self.peak_sales_millions,
            "cumulative_ptrs": self.cumulative_ptrs,
            "ptrs_per_phase": self.ptrs_per_phase,
            "enrollment_by_phase": self.enrollment_by_phase,
            "phase_costs_millions": self.phase_costs_millions,
            "capital_to_next_milestone_millions": self.capital_to_next_milestone_millions,
            "total_capital_to_launch_millions": self.total_capital_to_launch_millions,
            "timeline_to_next_milestone_months": self.timeline_to_next_milestone_months,
            "timeline_to_launch_years": self.timeline_to_launch_years,
            "value_inflection_points": self.value_inflection_points,
            "discount_rate": self.discount_rate,
            "pv_commercial_millions": self.pv_commercial_millions,
            "pv_development_cost_millions": self.pv_development_cost_millions,
            "rnpv_millions": self.rnpv_millions,
            "licensing_terms": self.licensing_terms,
            "is_biomarker_selected": self.is_biomarker_selected,
            "therapeutic_area": self.therapeutic_area,
            "assumptions_summary": self.assumptions_summary,
        }


# ----------------------------------------------------------------------------
# Extraction helpers for upstream data
# ----------------------------------------------------------------------------
def extract_peak_sales(
    commercial_claims: list[Claim],
    sources: Optional[dict] = None,
    indication: str = "",
    mechanism: str = "",
) -> dict[str, float]:
    """Extract peak annual sales ($ Millions) from commercial claims or market model.

    Returns dict {"low": float, "base": float, "high": float}.
    """
    # 1. Search in commercial claims for deterministic funnel claim
    for c in commercial_claims or []:
        text = f"{c.claim} {c.reasoning or ''}"
        # Look for pattern: Base peak sales projected at $XX.XM (Range: $YY.YM - $ZZ.ZM)
        m = re.search(r"Base peak sales projected at \$([0-9\.,]+)M\s*\(Range:\s*\$([0-9\.,]+)M\s*-\s*\$([0-9\.,]+)M\)", text, re.I)
        if m:
            try:
                base_val = float(m.group(1).replace(",", ""))
                low_val = float(m.group(2).replace(",", ""))
                high_val = float(m.group(3).replace(",", ""))
                return {"low": round(low_val, 1), "base": round(base_val, 1), "high": round(high_val, 1)}
            except ValueError:
                pass

        # Look for general peak sales mentions: Peak sales ... $XX M
        m2 = re.search(r"peak\s+sales[^$]*\$([0-9\.,]+)\s*M", text, re.I)
        if m2:
            try:
                base_val = float(m2.group(1).replace(",", ""))
                return {"low": round(base_val * 0.5, 1), "base": round(base_val, 1), "high": round(base_val * 2.0, 1)}
            except ValueError:
                pass

    # 2. Check sources["commercial"] for peak sales data
    if sources and "commercial" in sources:
        for r in sources.get("commercial", []):
            snippet = r.get("raw_snippet", "")
            m = re.search(r"peak sales[^$]*\$([0-9\.,]+)\s*(?:M|million)", snippet, re.I)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    return {"low": round(val * 0.5, 1), "base": round(val, 1), "high": round(val * 2.0, 1)}
                except ValueError:
                    pass

    # 3. Default fallback based on indication scale
    ind_lower = indication.lower()
    if any(k in ind_lower for k in ("lung", "breast", "colon", "cancer", "oncology", "alzheimer", "cardio")):
        return {"low": 800.0, "base": 2200.0, "high": 4500.0}
    elif any(k in ind_lower for k in ("rare", "orphan", "genetic")):
        return {"low": 250.0, "base": 650.0, "high": 1400.0}
    return {"low": 500.0, "base": 1500.0, "high": 3000.0}


def extract_enrollment_from_aact(
    sources: Optional[dict] = None,
    indication: str = "",
) -> dict[str, int]:
    """Extract median trial enrollment by phase from AACT sources or use robust benchmarks."""
    enrollment: dict[str, int] = {}

    if sources and "aact" in sources:
        for r in sources["aact"]:
            # Check direct dict field if present
            med_dict = r.get("median_enrollment")
            if isinstance(med_dict, dict):
                for ph, val in med_dict.items():
                    if isinstance(val, (int, float)) and val > 0:
                        enrollment[ph] = int(val)

            # Check raw snippet regex
            snippet = r.get("raw_snippet", "")
            if "Median enrollment" in snippet:
                for ph_num in ("1", "2", "3"):
                    ph_key = f"Phase {ph_num}"
                    m = re.search(rf"Phase\s*{ph_num}:\s*median\s*(\d+)", snippet, re.I)
                    if m and ph_key not in enrollment:
                        enrollment[ph_key] = int(m.group(1))

    # Apply benchmark defaults for missing phases
    ind_lower = indication.lower()
    is_oncology = any(k in ind_lower for k in ("cancer", "carcinoma", "tumor", "oncology", "nsclc", "leukemia", "lymphoma"))
    benchmarks = DEFAULT_ENROLLMENT_BENCHMARKS["oncology"] if is_oncology else DEFAULT_ENROLLMENT_BENCHMARKS["general"]

    for ph in ("Phase 1", "Phase 2", "Phase 3"):
        if ph not in enrollment or enrollment[ph] <= 0:
            enrollment[ph] = benchmarks[ph]

    return enrollment


def extract_ptrs_parameters(
    clinical_claims: list[Claim],
    indication: str = "",
    mechanism: str = "",
) -> tuple[dict[str, dict[str, float]], bool]:
    """Extract PTRS per phase from clinical claims, checking for biomarker stratification.

    Returns (ptrs_dict, is_biomarker_selected).
    """
    is_biomarker_selected = False
    all_text = " ".join(f"{c.claim} {c.reasoning or ''}" for c in clinical_claims or "").lower()

    if any(k in all_text for k in ("biomarker", "companion diagnostic", "stratification", "g12c", "mutation", "selected patient")):
        is_biomarker_selected = True

    ind_lower = indication.lower()
    is_oncology = any(k in ind_lower for k in ("cancer", "carcinoma", "tumor", "oncology", "nsclc", "leukemia", "lymphoma"))
    base_benchmarks = DEFAULT_PTRS_BENCHMARKS["oncology"] if is_oncology else DEFAULT_PTRS_BENCHMARKS["non_oncology"]

    ptrs: dict[str, dict[str, float]] = {
        "Phase 1": dict(base_benchmarks["Phase 1"]),
        "Phase 2": dict(base_benchmarks["Phase 2"]),
        "Phase 3": dict(base_benchmarks["Phase 3"]),
    }

    # If biomarker enrichment is identified, apply the ~1.3x - 1.5x PTRS multiplier (capped at 0.85)
    if is_biomarker_selected:
        for ph in ptrs:
            for s in ("low", "base", "high"):
                mult = 1.25 if ph == "Phase 1" else (1.40 if ph == "Phase 2" else 1.20)
                ptrs[ph][s] = min(0.85, round(ptrs[ph][s] * mult, 3))

    # Also inspect if the clinical agent proposed specific percentages
    for c in clinical_claims or []:
        text = f"{c.claim} {c.reasoning or ''}"
        for ph_num in ("1", "2", "3"):
            ph_key = f"Phase {ph_num}"
            m = re.search(rf"Phase\s*{ph_num}[^%\d]*?(\d{{1,2}})%", text, re.I)
            if m:
                val = float(m.group(1)) / 100.0
                if 0.05 <= val <= 0.95:
                    ptrs[ph_key]["base"] = val
                    ptrs[ph_key]["low"] = max(0.05, round(val * 0.75, 3))
                    ptrs[ph_key]["high"] = min(0.90, round(val * 1.25, 3))

    return ptrs, is_biomarker_selected


# ----------------------------------------------------------------------------
# Core Deterministic Financial Calculation Engine
# ----------------------------------------------------------------------------
def calculate_financials(
    peak_sales_millions: dict[str, float],
    enrollment_by_phase: dict[str, int],
    ptrs_per_phase: dict[str, dict[str, float]],
    indication: str = "",
    mechanism: str = "",
    current_stage: str = "Phase 1",
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    commercial_margin: float = DEFAULT_COMMERCIAL_MARGIN,
    cost_per_patient: Optional[dict[str, dict[str, float]]] = None,
) -> FinancialResult:
    """Calculate deterministic capital requirements, timelines, inflection points, and rNPV.

    Args:
        peak_sales_millions: Dict {"low": float, "base": float, "high": float}
        enrollment_by_phase: Dict {"Phase 1": int, "Phase 2": int, "Phase 3": int}
        ptrs_per_phase: Dict of phase -> {"low": float, "base": float, "high": float}
        indication: Indication name string
        mechanism: Mechanism name string
        current_stage: Current stage of the program ("Preclinical", "Phase 1", "Phase 2")
        discount_rate: Annual discount rate (WACC), e.g. 0.105 (10.5%)
        commercial_margin: Net operating margin on product sales, e.g. 0.75 (75%)
        cost_per_patient: Optional override for cost per patient by phase

    Returns:
        Populated FinancialResult object with low, base, and high scenarios.
    """
    scenarios = ("low", "base", "high")
    cost_patient = cost_per_patient or DEFAULT_COST_PER_PATIENT

    ind_lower = indication.lower()
    is_oncology = any(k in ind_lower for k in ("cancer", "carcinoma", "tumor", "oncology", "nsclc"))
    therapeutic_area = "Oncology" if is_oncology else "General Therapeutics"

    # 1. Cumulative PTRS calculation
    cumulative_ptrs: dict[str, float] = {}
    for s in scenarios:
        p_cum = ptrs_per_phase["Phase 1"][s] * ptrs_per_phase["Phase 2"][s] * ptrs_per_phase["Phase 3"][s]
        cumulative_ptrs[s] = round(p_cum, 4)

    # 2. Phase-by-phase trial cost model ($ Millions)
    # Trial Cost = Enrollment * Cost_per_patient * Overhead_Factor (1.25 for CRO/site setup/CMC supply)
    overhead_factor = 1.25
    phase_costs_millions: dict[str, dict[str, float]] = {
        "Phase 1": {},
        "Phase 2": {},
        "Phase 3": {},
    }

    for ph in ("Phase 1", "Phase 2", "Phase 3"):
        n_pts = enrollment_by_phase.get(ph, 100)
        for s in scenarios:
            c_pt = cost_patient[ph][s]
            raw_cost = n_pts * c_pt * overhead_factor
            phase_costs_millions[ph][s] = round(raw_cost / 1e6, 2)

    # 3. Capital to next milestone and total capital to launch ($ Millions)
    capital_to_next_milestone_millions: dict[str, float] = {}
    total_capital_to_launch_millions: dict[str, float] = {}

    launch_prep = {
        "low": DEFAULT_LAUNCH_PREP_COST_MILLIONS * 0.8,
        "base": DEFAULT_LAUNCH_PREP_COST_MILLIONS,
        "high": DEFAULT_LAUNCH_PREP_COST_MILLIONS * 1.3,
    }

    for s in scenarios:
        # Next major milestone: completion of the next clinical phase
        # If in Phase 1 / preclinical, next milestone is Phase 1 PK/PD & Safety proof-of-mechanism
        c_next = phase_costs_millions["Phase 1"][s]
        capital_to_next_milestone_millions[s] = round(c_next, 1)

        # Total capital required across all phases + launch preparation
        tot = (
            phase_costs_millions["Phase 1"][s]
            + phase_costs_millions["Phase 2"][s]
            + phase_costs_millions["Phase 3"][s]
            + launch_prep[s]
        )
        total_capital_to_launch_millions[s] = round(tot, 1)

    timeline_next_months = int(DEFAULT_PHASE_DURATIONS["Phase 1"] * 12)
    timeline_launch_years = round(
        DEFAULT_PHASE_DURATIONS["Phase 1"]
        + DEFAULT_PHASE_DURATIONS["Phase 2"]
        + DEFAULT_PHASE_DURATIONS["Phase 3"]
        + DEFAULT_PHASE_DURATIONS["Regulatory Review"],
        1,
    )

    # 4. Value Inflection Points
    inflection_points = [
        {
            "milestone": "Phase 1 First-in-Human PK/PD & Safety Readout",
            "time_months": int(DEFAULT_PHASE_DURATIONS["Phase 1"] * 12),
            "capital_required_millions": capital_to_next_milestone_millions["base"],
            "derisking_effect": "Confirms human tolerability, therapeutic exposure, and biological target engagement.",
        },
        {
            "milestone": "Phase 2 Proof-of-Concept (PoC) & Primary Efficacy Readout",
            "time_months": int((DEFAULT_PHASE_DURATIONS["Phase 1"] + DEFAULT_PHASE_DURATIONS["Phase 2"]) * 12),
            "capital_required_millions": phase_costs_millions["Phase 2"]["base"],
            "derisking_effect": "Major equity re-rating inflection point (~3x-5x value step-change); primary window for pharma partnership or M&A.",
        },
        {
            "milestone": "Phase 3 Pivotal Primary Endpoint (OS/PFS) Readout",
            "time_months": int((DEFAULT_PHASE_DURATIONS["Phase 1"] + DEFAULT_PHASE_DURATIONS["Phase 2"] + DEFAULT_PHASE_DURATIONS["Phase 3"]) * 12),
            "capital_required_millions": phase_costs_millions["Phase 3"]["base"],
            "derisking_effect": "Statistical validation for BLA/NDA filing; removes technical development risk.",
        },
        {
            "milestone": "FDA/EMA Approval & Commercial Launch",
            "time_months": int(timeline_launch_years * 12),
            "capital_required_millions": round(launch_prep["base"], 1),
            "derisking_effect": "Regulatory clearance and initial commercial product revenue generation.",
        },
    ]

    # 5. Discounted Cash Flow (DCF) rNPV Calculation with Time-Value of Money
    # Development timeline milestones (years from now t=0)
    t_p1_mid = DEFAULT_PHASE_DURATIONS["Phase 1"] / 2.0
    t_p2_mid = DEFAULT_PHASE_DURATIONS["Phase 1"] + (DEFAULT_PHASE_DURATIONS["Phase 2"] / 2.0)
    t_p3_mid = (
        DEFAULT_PHASE_DURATIONS["Phase 1"]
        + DEFAULT_PHASE_DURATIONS["Phase 2"]
        + (DEFAULT_PHASE_DURATIONS["Phase 3"] / 2.0)
    )
    t_launch = timeline_launch_years

    pv_dev_cost: dict[str, float] = {}
    pv_commercial: dict[str, float] = {}
    rnpv: dict[str, float] = {}

    for s in scenarios:
        # Development cash outflows discounted to t=0 and risk-adjusted by stage-gate success
        # Phase 1 is spent upfront (prob = 1.0)
        pv_c1 = phase_costs_millions["Phase 1"][s] / math.pow(1.0 + discount_rate, t_p1_mid)

        # Phase 2 is spent only if Phase 1 succeeds (prob = PTRS_P1)
        prob_p2 = ptrs_per_phase["Phase 1"][s]
        pv_c2 = (phase_costs_millions["Phase 2"][s] * prob_p2) / math.pow(1.0 + discount_rate, t_p2_mid)

        # Phase 3 is spent only if Phase 1 and 2 succeed (prob = PTRS_P1 * PTRS_P2)
        prob_p3 = ptrs_per_phase["Phase 1"][s] * ptrs_per_phase["Phase 2"][s]
        pv_c3 = (phase_costs_millions["Phase 3"][s] * prob_p3) / math.pow(1.0 + discount_rate, t_p3_mid)

        # Launch prep spent at t_launch - 0.5 (prob = cumulative PTRS)
        prob_launch = cumulative_ptrs[s]
        pv_launch = (launch_prep[s] * prob_launch) / math.pow(1.0 + discount_rate, t_launch - 0.5)

        total_pv_dev = pv_c1 + pv_c2 + pv_c3 + pv_launch
        pv_dev_cost[s] = round(total_pv_dev, 1)

        # Commercial cash inflows discounted to t=0
        # Post-launch revenue ramp across 10 years of exclusivity
        peak_rev = peak_sales_millions[s]
        pv_comm_inflows = 0.0

        for yr_idx, ramp_pct in enumerate(REVENUE_RAMP_CURVE, start=1):
            yr_time = t_launch + yr_idx
            annual_rev = peak_rev * ramp_pct
            annual_net_cash = annual_rev * commercial_margin
            discount_factor = 1.0 / math.pow(1.0 + discount_rate, yr_time)
            pv_comm_inflows += annual_net_cash * discount_factor

        # Risk-adjust commercial inflows by cumulative probability of reaching market
        risk_adjusted_pv_comm = pv_comm_inflows * cumulative_ptrs[s]
        pv_commercial[s] = round(risk_adjusted_pv_comm, 1)

        # Final rNPV = PV(Commercial Inflows) - PV(Development Outflows)
        net_rnpv = risk_adjusted_pv_comm - total_pv_dev
        rnpv[s] = round(net_rnpv, 1)

    # 6. Out-licensing terms benchmark at Phase 2 PoC
    base_rnpv = rnpv["base"]
    licensing_terms = {
        "timing": f"Month {int((DEFAULT_PHASE_DURATIONS['Phase 1'] + DEFAULT_PHASE_DURATIONS['Phase 2']) * 12)} (Post-Phase 2 PoC)",
        "upfront_cash_millions": round(max(20.0, base_rnpv * 0.18), 1),
        "development_milestones_millions": round(max(50.0, base_rnpv * 0.35), 1),
        "commercial_milestones_millions": round(max(100.0, base_rnpv * 0.50), 1),
        "tiered_royalties_range": "12% - 16% on net sales",
        "estimated_total_deal_value_millions": round(
            max(20.0, base_rnpv * 0.18) + max(50.0, base_rnpv * 0.35) + max(100.0, base_rnpv * 0.50), 1
        ),
    }

    assumptions_summary = {
        "discount_rate_wacc": f"{discount_rate * 100:.1f}%",
        "commercial_net_margin": f"{commercial_margin * 100:.0f}%",
        "commercial_exclusivity_years": DEFAULT_COMMERCIAL_LIFE_YEARS,
        "cost_per_patient_base": {ph: f"${cost_patient[ph]['base']:,.0f}" for ph in cost_patient},
        "median_enrollment_applied": enrollment_by_phase,
    }

    return FinancialResult(
        peak_sales_millions=peak_sales_millions,
        ptrs_per_phase=ptrs_per_phase,
        cumulative_ptrs=cumulative_ptrs,
        enrollment_by_phase=enrollment_by_phase,
        cost_per_patient=cost_patient,
        phase_costs_millions=phase_costs_millions,
        capital_to_next_milestone_millions=capital_to_next_milestone_millions,
        total_capital_to_launch_millions=total_capital_to_launch_millions,
        timeline_to_next_milestone_months=timeline_next_months,
        timeline_to_launch_years=timeline_launch_years,
        value_inflection_points=inflection_points,
        discount_rate=discount_rate,
        pv_commercial_millions=pv_commercial,
        pv_development_cost_millions=pv_dev_cost,
        rnpv_millions=rnpv,
        licensing_terms=licensing_terms,
        is_biomarker_selected=any("biomarker" in str(v).lower() for v in ptrs_per_phase.values()),
        therapeutic_area=therapeutic_area,
        assumptions_summary=assumptions_summary,
    )


def run_deterministic_financials(
    commercial_claims: list[Claim],
    clinical_claims: list[Claim],
    sources: Optional[dict] = None,
    indication: str = "",
    mechanism: str = "",
    current_stage: str = "Phase 1",
) -> FinancialResult:
    """End-to-end wrapper: extracts all upstream inputs and executes the financial model."""
    peak_sales = extract_peak_sales(commercial_claims, sources, indication, mechanism)
    enrollment = extract_enrollment_from_aact(sources, indication)
    ptrs_dict, is_bmk = extract_ptrs_parameters(clinical_claims, indication, mechanism)

    return calculate_financials(
        peak_sales_millions=peak_sales,
        enrollment_by_phase=enrollment,
        ptrs_per_phase=ptrs_dict,
        indication=indication,
        mechanism=mechanism,
        current_stage=current_stage,
    )


# ----------------------------------------------------------------------------
# Formatting for Chair Agent & Claim Generation
# ----------------------------------------------------------------------------
def format_financial_brief_for_chair(result: FinancialResult) -> str:
    """Format structured financial output into an unambiguous brief for the Chair Agent."""
    r = result
    p = r.peak_sales_millions
    ptrs = r.cumulative_ptrs
    cap_next = r.capital_to_next_milestone_millions
    cap_tot = r.total_capital_to_launch_millions
    val = r.rnpv_millions
    lic = r.licensing_terms

    lines = [
        "==================================================================",
        "DETERMINISTIC FINANCIAL & VALUATION MODEL (CALCULATED IN CODE):",
        "==================================================================",
        f"1. PEAK ANNUAL SALES: Low = ${p['low']:.1f}M, Base = ${p['base']:.1f}M, High = ${p['high']:.1f}M",
        f"2. CUMULATIVE PTRS (Probability of Success to Approval): Low = {ptrs['low']*100:.1f}%, Base = {ptrs['base']*100:.1f}%, High = {ptrs['high']*100:.1f}%",
        f"   - Phase 1 PTRS: {r.ptrs_per_phase['Phase 1']['base']*100:.0f}% (Phase 1 -> 2)",
        f"   - Phase 2 PTRS: {r.ptrs_per_phase['Phase 2']['base']*100:.0f}% (Phase 2 -> 3)",
        f"   - Phase 3 PTRS: {r.ptrs_per_phase['Phase 3']['base']*100:.0f}% (Phase 3 -> Approval)",
        "3. CAPITAL REQUIREMENTS (from AACT median enrollments & benchmark costs):",
        f"   - Capital to Next Major Milestone ({r.timeline_to_next_milestone_months} months): Low = ${cap_next['low']:.1f}M, Base = ${cap_next['base']:.1f}M, High = ${cap_next['high']:.1f}M",
        f"   - Total Capital to Commercial Launch ({r.timeline_to_launch_years:.1f} years): Low = ${cap_tot['low']:.1f}M, Base = ${cap_tot['base']:.1f}M, High = ${cap_tot['high']:.1f}M",
        f"   - Enrollment Model: Phase 1 = {r.enrollment_by_phase['Phase 1']} pts, Phase 2 = {r.enrollment_by_phase['Phase 2']} pts, Phase 3 = {r.enrollment_by_phase['Phase 3']} pts",
        f"4. REAL rNPV (Discounted Cash Flow at {r.discount_rate*100:.1f}% WACC, 10-year patent exclusivity, 75% margin):",
        "   EXACT FORMULA VALUES:",
        f"   * Low Scenario:  rNPV = ${val['low']:.1f}M (PV Inflows: ${r.pv_commercial_millions['low']:.1f}M - PV Outflows: ${r.pv_development_cost_millions['low']:.1f}M)",
        f"   * Base Scenario: rNPV = ${val['base']:.1f}M (PV Inflows: ${r.pv_commercial_millions['base']:.1f}M - PV Outflows: ${r.pv_development_cost_millions['base']:.1f}M)",
        f"   * High Scenario: rNPV = ${val['high']:.1f}M (PV Inflows: ${r.pv_commercial_millions['high']:.1f}M - PV Outflows: ${r.pv_development_cost_millions['high']:.1f}M)",
        "5. EXIT & LICENSING BENCHMARKS AT PHASE 2 PoC:",
        f"   - Upfront Cash: ${lic['upfront_cash_millions']:.1f}M",
        f"   - Total Deal Value: ${lic['estimated_total_deal_value_millions']:.1f}M (including dev & commercial milestones, {lic['tiered_royalties_range']})",
        "==================================================================",
        "CRITICAL INSTRUCTION FOR CHAIR:",
        "Your rNPV claim MUST report these EXACT numbers: Low = $[Low]M, Base = $[Base]M, High = $[High]M.",
        "Your capital_required claims MUST cite the exact calculated milestone and launch figures.",
        "Your role is to EXPLAIN the economic drivers, sensitivity, and underwriting rationale behind these numbers.",
        "==================================================================",
    ]
    return "\n".join(lines)


def build_deterministic_financial_claims(result: FinancialResult) -> list[Claim]:
    """Build fully deterministic Claim objects representing the financial model."""
    r = result
    p = r.peak_sales_millions
    ptrs = r.cumulative_ptrs
    cap_next = r.capital_to_next_milestone_millions
    cap_tot = r.total_capital_to_launch_millions
    val = r.rnpv_millions
    lic = r.licensing_terms

    claims: list[Claim] = [
        # Claim 1: Structured rNPV valuation claim
        Claim(
            claim=f"rNPV Scenarios: Low = ${val['low']:.1f}M, Base = ${val['base']:.1f}M, High = ${val['high']:.1f}M",
            type="inference",
            section="recommendation",
            reasoning=(
                f"Discounted cash flow rNPV modeled in financials.py at {r.discount_rate*100:.1f}% WACC with 10-year commercial exclusivity. "
                f"Low scenario: {ptrs['low']*100:.1f}% cumulative PTRS on ${p['low']:.1f}M peak sales with ${cap_tot['high']:.1f}M capital. "
                f"Base scenario: {ptrs['base']*100:.1f}% cumulative PTRS on ${p['base']:.1f}M peak sales with ${cap_tot['base']:.1f}M capital. "
                f"High scenario: {ptrs['high']*100:.1f}% cumulative PTRS on ${p['high']:.1f}M peak sales with ${cap_tot['low']:.1f}M capital."
            ),
            confidence="high",
        ),
        # Claim 2: Capital required to next major milestone
        Claim(
            claim=(
                f"Capital required to reach next major milestone: ${cap_next['low']:.1f}M - ${cap_next['high']:.1f}M "
                f"(Base: ${cap_next['base']:.1f}M over {r.timeline_to_next_milestone_months} months)."
            ),
            type="inference",
            section="capital_required",
            reasoning=(
                f"Calculated deterministically from clinical trial modeling: Phase 1 enrollment of {r.enrollment_by_phase['Phase 1']} patients "
                f"at benchmark cost of ${r.cost_per_patient['Phase 1']['base']:,.0f}/patient plus CRO site and CMC formulation overhead."
            ),
            confidence="high",
        ),
        # Claim 3: Total capital to commercial approval & launch
        Claim(
            claim=(
                f"Total cumulative capital to commercial launch: ${cap_tot['low']:.1f}M - ${cap_tot['high']:.1f}M "
                f"(Base: ${cap_tot['base']:.1f}M over {r.timeline_to_launch_years:.1f} years)."
            ),
            type="inference",
            section="capital_required",
            reasoning=(
                f"Encompasses Phase 1 (${r.phase_costs_millions['Phase 1']['base']:.1f}M), Phase 2 (${r.phase_costs_millions['Phase 2']['base']:.1f}M, {r.enrollment_by_phase['Phase 2']} pts), "
                f"Phase 3 (${r.phase_costs_millions['Phase 3']['base']:.1f}M, {r.enrollment_by_phase['Phase 3']} pts), and commercial launch prep (${DEFAULT_LAUNCH_PREP_COST_MILLIONS:.1f}M)."
            ),
            confidence="high",
        ),
        # Claim 4: Value inflection points & M&A / licensing outcomes
        Claim(
            claim=(
                f"Primary value inflection point occurs at Phase 2 Proof-of-Concept readout (Month 42), unlocking estimated "
                f"out-licensing deal value of ${lic['estimated_total_deal_value_millions']:.1f}M with ${lic['upfront_cash_millions']:.1f}M upfront."
            ),
            type="inference",
            section="capital_required",
            reasoning=(
                f"Biopharma partnership benchmarks at Phase 2 PoC support {lic['upfront_cash_millions']:.1f}M upfront cash, "
                f"${lic['development_milestones_millions']:.1f}M development milestones, ${lic['commercial_milestones_millions']:.1f}M commercial milestones, and {lic['tiered_royalties_range']}."
            ),
            confidence="medium",
        ),
    ]
    return claims

