"""AgentCore Platform v1.0"""

# SVC-C2-007 — GenerateNarrativeNode (inner workflow, step 2).
#
# Composes the performance narrative (executive summary + trend commentary) and
# the visualisation recommendations from the structured model. The composition
# is deterministic: every sentence is assembled from the validated figures, so
# the same request always produces the same report and no figure can appear that
# the caller did not supply.
#
# Trust level: ANONYMOUS — an inner node (see DomainWorkflowGraph).

import re
from typing import Any, ClassVar, Dict, List, cast

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.services.runtime_settings import settings_from_state

# Staff and client identifiers that performance data routinely carries. The
# structured model reaches here through an inert-identifier contract, so this is
# a second, independent pass rather than the only defence.
_STAFF_IDENTIFIER_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:EMP|WORKER|EMPL)-?\d{3,8}\b"
    r"|employee[_\s]?(?:id|no|number)[:\s=]+\S+"
    r"|consultant[_\s]?(?:id|no|number)[:\s=]+\S+",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED]"

# Labels are inert identifiers; these stand in when the caller supplies none.
_DEFAULT_FIRM = "the firm"
_DEFAULT_PERIOD = "the reporting period"


def _pct(value: float) -> str:
    """Format a fraction (0-1) as a percentage string."""
    return f"{value * 100:.1f}%"


def _executive_summary(firm: str, period: str, metrics: Dict[str, Any], derived: Dict[str, Any]) -> str:
    """Produce the report heading and the executive summary paragraph."""
    util_rate = float(metrics.get("utilization_rate", 0.0))
    util_gap = float(derived.get("utilization_gap", 0.0))
    revenue = float(metrics.get("revenue_usd", 0.0))
    csat = float(metrics.get("client_satisfaction_score", 0.0))
    engagements = int(metrics.get("active_engagements", 0))

    direction = "exceeds" if util_gap >= 0 else "falls short of"
    gap_points = abs(util_gap * 100)

    return (
        f"# {firm} — Performance Report: {period}\n\n"
        f"## Executive Summary\n\n"
        f"During {period}, {firm} achieved a utilisation rate of {_pct(util_rate)}, "
        f"which {direction} the target by {gap_points:.1f} percentage points. "
        f"Total revenue reached ${revenue:,.0f} across {engagements} active engagement(s). "
        f"Client satisfaction scored {csat:.1f}/5.0, reflecting the quality of service delivered."
    )


def _trend_commentary(derived: Dict[str, Any], kpis: Dict[str, Any], health_threshold: float) -> str:
    """Produce the KPI trend commentary.

    `health_threshold` is the declared coverage ratio at or above which the
    pipeline reads as healthy; it comes from config/config.yaml, so changing the
    declaration changes this sentence.
    """
    rev_per_head = float(derived.get("revenue_per_head_usd", 0.0))
    coverage = float(derived.get("pipeline_coverage_ratio", 0.0))
    billable = float(kpis.get("billable_hours", 0.0))
    health = "healthy" if coverage >= health_threshold else "moderate"

    return (
        f"## Key Performance Trends\n\n"
        f"Revenue per consultant stands at ${rev_per_head:,.0f}, providing a benchmark "
        f"for workforce efficiency. The pipeline coverage ratio of {coverage:.2f}x "
        f"indicates {health} future revenue visibility, measured against a {health_threshold:.2f}x "
        f"coverage benchmark. Total billable hours recorded this period: {billable:,.0f}."
    )


def _visualization_descriptions(
    metrics: Dict[str, Any], derived: Dict[str, Any], target_utilization: float, cap: int
) -> List[str]:
    """Describe the recommended report visualisations, capped by declaration.

    `cap` is the declared maximum from config/config.yaml, so lowering the
    declaration visibly shortens the section the report renders.
    """
    util_rate = float(metrics.get("utilization_rate", 0.0))
    revenue = float(metrics.get("revenue_usd", 0.0))
    coverage = float(derived.get("pipeline_coverage_ratio", 0.0))
    engagements = int(metrics.get("active_engagements", 0))
    csat = float(metrics.get("client_satisfaction_score", 0.0))
    consultants = int(metrics.get("consultant_count", 0))

    described = [
        (
            f"Bar chart — Utilisation Rate vs Target: "
            f"Actual {_pct(util_rate)} vs Target {_pct(target_utilization)}. "
            "Highlight the gap when the actual rate is below target."
        ),
        (
            f"KPI card grid — Key Metrics: "
            f"Revenue ${revenue:,.0f} | "
            f"Active Engagements {engagements} | "
            f"CSAT {csat:.1f}/5.0 | "
            f"Consultants {consultants}."
        ),
        (
            f"Pipeline funnel chart — Revenue Pipeline Coverage: "
            f"Current revenue ${revenue:,.0f}. "
            f"Coverage ratio {coverage:.2f}x."
        ),
    ]
    return described[: max(cap, 0)]


class GenerateNarrativeNode(FunctionNode):
    """Compose the performance narrative and the visualisation recommendations.

    Input state keys:
        structured_data   JSON-encoded normalised model
        runtime_settings  JSON-encoded settings published by the inner graph

    Output state keys (partial dict — changed keys only):
        narrative                   executive summary + trend commentary
        visualization_descriptions  JSON-encoded list of descriptions
        status / error_log
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        structured: Dict[str, Any] = from_json(state.get("structured_data"), {})
        settings = settings_from_state(state)

        if not structured:
            emit_trace_event("narrative_generation_failed", {"reason": "no_structured_model"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GenerateNarrativeNode: the structured model is missing"],
            }

        metrics: Dict[str, Any] = structured.get("engagement_metrics", {})
        if not metrics:
            emit_trace_event("narrative_generation_failed", {"reason": "no_engagement_metrics"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GenerateNarrativeNode: the structured model carries no engagement metrics"],
            }

        kpis: Dict[str, Any] = structured.get("kpis", {})
        derived: Dict[str, Any] = structured.get("derived", {})
        firm: str = structured.get("firm_name") or _DEFAULT_FIRM
        period: str = structured.get("report_period") or _DEFAULT_PERIOD
        target = float(structured.get("target_utilization", settings["target_utilization"]))

        narrative = (
            f"{_executive_summary(firm, period, metrics, derived)}\n\n"
            f"{_trend_commentary(derived, kpis, cast(float, settings['pipeline_health_threshold']))}"
        )
        redactions = len(_STAFF_IDENTIFIER_PATTERN.findall(narrative))
        if redactions:
            narrative = _STAFF_IDENTIFIER_PATTERN.sub(_REDACTED, narrative)

        descriptions = _visualization_descriptions(metrics, derived, target, cast(int, settings["max_visualizations"]))

        emit_trace_event(
            "narrative_generated",
            {
                # Content is deliberately absent: the narrative carries the
                # client's confidential performance figures.
                "narrative_chars": len(narrative),
                "visualization_count": len(descriptions),
                "staff_identifier_redactions": redactions,
                "pipeline_health_threshold": settings["pipeline_health_threshold"],
            },
            state,
        )

        return {
            "narrative": narrative,
            "visualization_descriptions": to_json(descriptions),
            "status": AgentStatus.SUCCESS.value,
        }
