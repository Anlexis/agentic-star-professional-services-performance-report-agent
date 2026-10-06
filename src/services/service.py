"""AgentCore Platform v1.0"""

# Domain service for SVC-C2-007: the professional-services performance
# arithmetic. Pure functions over already-validated numbers — no I/O, no
# routing, no credentials, and no caller strings.
#
# The nodes own the pipeline; this module owns the derivations, so the same KPI
# definitions are used by the narrative, the metrics table and the tests.
#
# Range and finiteness are NOT re-checked here: the caller-contract gate owns
# that decision and names the offending field to the caller. What is checked is
# that a value is a number at all, so a hand-constructed state raises a named
# ValueError the calling node can turn into a refusal rather than a traceback.

from typing import Any, Dict, Mapping

# Guard against a division that a validated payload can still make degenerate.
_MIN_DIVISOR = 1e-9


def number(values: Mapping[str, Any], name: str, default: float = 0.0) -> float:
    """Read one numeric field, or raise ValueError naming the field."""
    if name not in values:
        return default
    value = values[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name)
    return float(value)


def utilization_gap(utilization_rate: float, target_utilization: float) -> float:
    """Signed distance between the achieved and the targeted utilisation rate."""
    return round(utilization_rate - target_utilization, 4)


def revenue_per_head(revenue_usd: float, consultant_count: float) -> float:
    """Revenue attributable to one consultant over the reporting period."""
    if consultant_count <= 0:
        return 0.0
    return round(revenue_usd / consultant_count, 2)


def pipeline_coverage(pipeline_value: float, revenue_usd: float) -> float:
    """Weighted pipeline expressed as a multiple of the period's revenue."""
    if revenue_usd < _MIN_DIVISOR:
        return 0.0
    return round(pipeline_value / revenue_usd, 2)


def derive(metrics: Mapping[str, Any], kpis: Mapping[str, Any], target_utilization: float) -> Dict[str, float]:
    """Compute every derived KPI the report renders.

    `metrics` and `kpis` are the validated mappings produced by the caller
    contract gate: every value present is a finite number inside its declared
    bound, so the arithmetic below cannot produce a non-finite result.

    Raises ValueError, naming the field, when a value is not a number.
    """
    revenue = number(metrics, "revenue_usd")
    consultants = number(metrics, "consultant_count")
    return {
        "utilization_gap": utilization_gap(
            number(metrics, "utilization_rate"),
            number(kpis, "target_utilization", target_utilization),
        ),
        "revenue_per_head_usd": revenue_per_head(revenue, consultants),
        "pipeline_coverage_ratio": pipeline_coverage(number(kpis, "pipeline_value"), revenue),
    }
