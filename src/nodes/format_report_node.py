"""AgentCore Platform v1.0"""

# SVC-C2-007 — FormatReportNode (inner workflow, step 3).
#
# Assembles the final Markdown performance report from the narrative, the
# visualisation recommendations and the structured metrics.
#
# Output invariant: every figure the report renders is a validated caller figure
# or a KPI derived from one, rendered at its natural precision. This template
# reports a firm's own engagement figures back to it, so the rounding grid other
# templates apply to derived monetary aggregates is deliberately NOT applied —
# rounding a stated revenue would make the deliverable wrong. The invariant that
# IS enforced is inertness: no caller free text reaches the report, because the
# caller-contract gate admits labels only over [a-z0-9_].
#
# Trust level: ANONYMOUS — an inner node (see DomainWorkflowGraph).

from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

_DEFAULT_FIRM = "the firm"


def _metrics_table(metrics: Dict[str, Any], derived: Dict[str, Any], kpis: Dict[str, Any]) -> str:
    """Render the performance metrics as a Markdown table."""
    rows = [
        ("Utilisation Rate", f"{float(metrics.get('utilization_rate', 0.0)) * 100:.1f}%"),
        ("Total Revenue (USD)", f"${float(metrics.get('revenue_usd', 0.0)):,.0f}"),
        ("Client Satisfaction", f"{float(metrics.get('client_satisfaction_score', 0.0)):.1f}/5.0"),
        ("Active Engagements", f"{int(metrics.get('active_engagements', 0))}"),
        ("Consultants", f"{int(metrics.get('consultant_count', 0))}"),
        ("Partners", f"{int(metrics.get('partner_count', 0))}"),
        ("Billable Hours", f"{float(kpis.get('billable_hours', 0.0)):,.0f}"),
        ("Revenue per Consultant", f"${float(derived.get('revenue_per_head_usd', 0.0)):,.0f}"),
        ("Pipeline Coverage", f"{float(derived.get('pipeline_coverage_ratio', 0.0)):.2f}x"),
    ]
    body = "".join(f"| {label} | {value} |\n" for label, value in rows)
    return "## Performance Metrics\n\n| Metric | Value |\n|--------|-------|\n" + body


def _visualization_section(descriptions: List[str]) -> str:
    """Render the visualisation recommendations section."""
    if not descriptions:
        return ""
    items = "\n".join(f"- {description}" for description in descriptions)
    return f"\n## Recommended Visualisations\n\n{items}\n"


def _assemble(
    narrative: str,
    descriptions: List[str],
    metrics: Dict[str, Any],
    derived: Dict[str, Any],
    kpis: Dict[str, Any],
) -> str:
    """Assemble the final Markdown performance report."""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"{narrative}\n\n"
        f"---\n\n"
        f"{_metrics_table(metrics, derived, kpis)}"
        f"{_visualization_section(descriptions)}"
        f"\n---\n\n"
        f"*Generated: {generated} | Professional Services Performance Report*\n"
    )


class FormatReportNode(FunctionNode):
    """Assemble the final Markdown performance report.

    Input state keys:
        narrative                   composed narrative text
        visualization_descriptions  JSON-encoded description list
        structured_data             JSON-encoded normalised model

    Output state keys (partial dict — changed keys only):
        formatted_report  the assembled Markdown report
        result            the same text, read by the output gate downstream
        report_metadata   JSON-encoded metadata
        status / error_log
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        narrative: str = state.get("narrative") or ""
        descriptions: List[str] = from_json(state.get("visualization_descriptions"), [])
        structured: Dict[str, Any] = from_json(state.get("structured_data"), {})

        if not narrative:
            emit_trace_event("format_report_failed", {"reason": "no_narrative"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["FormatReportNode: the narrative is missing"],
            }

        metrics: Dict[str, Any] = structured.get("engagement_metrics", {})
        kpis: Dict[str, Any] = structured.get("kpis", {})
        derived: Dict[str, Any] = structured.get("derived", {})

        report = _assemble(narrative, descriptions, metrics, derived, kpis)
        metadata: Dict[str, Any] = {
            "firm_name": structured.get("firm_name") or _DEFAULT_FIRM,
            "report_period": structured.get("report_period") or "",
            "report_chars": len(report),
            "visualization_count": len(descriptions),
            "sections": ["executive_summary", "trend_commentary", "metrics_table", "visualizations"],
        }

        emit_trace_event(
            "report_formatted",
            {
                # Content is deliberately absent: the report carries the client's
                # confidential performance figures.
                "report_chars": len(report),
                "visualization_count": len(descriptions),
            },
            state,
        )

        return {
            "formatted_report": report,
            "result": report,
            "report_metadata": to_json(metadata),
            "status": AgentStatus.SUCCESS.value,
        }
