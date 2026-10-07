"""Deterministic market sizing and patient funnel model.

Pure Python calculation engine — absolutely NO LLM.
Validates input assumptions, computes patient funnel attrition stages
(total -> diagnosed -> eligible -> treated), and estimates peak annual revenue
across low, base, and high scenarios with explicit basis tracking (evidence vs inference).
"""
from typing import Any, Optional

REQUIRED_PARAMETERS = [
    "prevalence_or_incidence",
    "diagnosed_fraction",
    "drug_eligible_fraction",
    "treated_penetration",
    "annual_price",
    "compliance_rate",
]

FRACTION_PARAMETERS = {
    "diagnosed_fraction",
    "drug_eligible_fraction",
    "treated_penetration",
    "compliance_rate",
}


def _extract_scenario_val(param_data: Any, scenario: str) -> Optional[float]:
    """Safely extract numeric value for a given scenario ('low', 'base', 'high')."""
    if isinstance(param_data, dict):
        val = param_data.get(scenario)
        if val is None and scenario == "base":
            val = param_data.get("value")
    elif isinstance(param_data, (int, float)):
        val = param_data
    elif isinstance(param_data, str):
        try:
            val = float(param_data.replace(",", "").replace("$", "").strip())
        except ValueError:
            return None
    else:
        return None

    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def estimate_market(assumptions: dict[str, Any]) -> dict[str, Any]:
    """Calculate deterministic patient funnel and peak sales across low/base/high scenarios.

    Args:
        assumptions: Mapping of parameter names to dicts containing:
            {"low": float, "base": float, "high": float, "basis": str, "note": str}
            Required parameters:
            - prevalence_or_incidence: Total addressable annual patient pool
            - diagnosed_fraction: Proportion of patients correctly diagnosed (0..1)
            - drug_eligible_fraction: Proportion clinically eligible for this drug class (0..1)
            - treated_penetration: Market share / adoption rate among eligible patients (0..1)
            - annual_price: Net annual treatment cost in USD
            - compliance_rate: Adherence / compliance / persistence rate (0..1)

    Returns:
        dict containing:
            funnel: Dict of patient numbers at each stage for low, base, high
            peak_sales: Dict of peak annual revenue in USD for low, base, high
            peak_sales_millions: Peak revenue in USD millions for low, base, high
            inference_parameters: List of parameters relying on inference
            evidence_parameters: List of parameters grounded in evidence
            notes: List of explanatory notes, bounds warnings, or missing parameters
            status: 'complete' if all 3 scenarios calculated, 'partial', or 'incomplete'
    """
    notes: list[str] = []
    scenarios = ["low", "base", "high"]

    # 1. Track parameter basis (evidence vs inference)
    inference_parameters: list[str] = []
    evidence_parameters: list[str] = []

    for param in REQUIRED_PARAMETERS:
        p_obj = assumptions.get(param)
        if isinstance(p_obj, dict):
            basis = str(p_obj.get("basis", "")).strip().lower()
            note = str(p_obj.get("note", "")).strip()
            if basis.startswith("evidence:"):
                evidence_parameters.append(param)
            else:
                inference_parameters.append(param)
            if note:
                notes.append(f"Assumption '{param}' ({basis}): {note}")
        elif p_obj is not None:
            inference_parameters.append(param)
        else:
            notes.append(f"Missing required parameter '{param}' in assumptions.")

    funnel: dict[str, dict[str, Optional[float]]] = {
        "total": {"low": None, "base": None, "high": None},
        "diagnosed": {"low": None, "base": None, "high": None},
        "eligible": {"low": None, "base": None, "high": None},
        "treated": {"low": None, "base": None, "high": None},
    }
    peak_sales: dict[str, Optional[float]] = {
        "low": None,
        "base": None,
        "high": None,
        "currency": "USD",  # type: ignore[dict-item]
    }
    peak_sales_millions: dict[str, Optional[float]] = {
        "low": None,
        "base": None,
        "high": None,
        "currency": "USD_millions",  # type: ignore[dict-item]
    }

    completed_scenarios = 0

    # 2. Compute funnel for each scenario independently
    for s in scenarios:
        missing_for_scenario: list[str] = []
        vals: dict[str, float] = {}

        for p in REQUIRED_PARAMETERS:
            v = _extract_scenario_val(assumptions.get(p), s)
            if v is None:
                missing_for_scenario.append(p)
            else:
                vals[p] = v

        if missing_for_scenario:
            notes.append(
                f"Scenario '{s}' cannot be calculated: missing values for {missing_for_scenario}. "
                "Returning None for this scenario without guessing."
            )
            continue

        # Range and sanity checks
        bounds_ok = True
        for frac_param in FRACTION_PARAMETERS:
            frac_val = vals[frac_param]
            if frac_val < 0.0 or frac_val > 1.0:
                notes.append(
                    f"Warning: parameter '{frac_param}.{s}' is outside valid fraction range [0.0, 1.0]: {frac_val}"
                )
                if frac_val < 0.0:
                    bounds_ok = False

        if vals["prevalence_or_incidence"] < 0 or vals["annual_price"] < 0:
            notes.append(f"Scenario '{s}' rejected: negative population or price values.")
            bounds_ok = False

        if not bounds_ok:
            continue

        # Step-by-step funnel computation
        tot = vals["prevalence_or_incidence"]
        diag = tot * vals["diagnosed_fraction"]
        elig = diag * vals["drug_eligible_fraction"]
        treated = elig * vals["treated_penetration"]
        revenue = treated * vals["annual_price"] * vals["compliance_rate"]

        funnel["total"][s] = round(tot, 1)
        funnel["diagnosed"][s] = round(diag, 1)
        funnel["eligible"][s] = round(elig, 1)
        funnel["treated"][s] = round(treated, 1)

        peak_sales[s] = round(revenue, 2)
        peak_sales_millions[s] = round(revenue / 1e6, 2)
        completed_scenarios += 1

    if completed_scenarios == 3:
        status = "complete"
    elif completed_scenarios > 0:
        status = "partial"
    else:
        status = "incomplete"

    return {
        "status": status,
        "funnel": funnel,
        "peak_sales": peak_sales,
        "peak_sales_millions": peak_sales_millions,
        "inference_parameters": inference_parameters,
        "evidence_parameters": evidence_parameters,
        "notes": notes,
    }

