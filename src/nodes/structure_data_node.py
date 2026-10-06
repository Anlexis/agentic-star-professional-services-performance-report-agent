"""AgentCore Platform v1.0"""

# SVC-C2-007 — StructureDataNode (inner workflow, step 1).
#
# Normalises the validated request into the structured model the rest of the
# pipeline reads, and computes the derived KPIs.
#
# Every number this node handles has already been checked for type, finiteness
# and range by the caller-contract gate; the parse below re-checks the shape it
# received rather than trusting the channel, because the inner graph is also
# reachable from a unit test that constructs state by hand.
#
# Trust level: ANONYMOUS — an inner node. GraphNode.execute() passes the outer
# InvocationContext through unchanged, so a gate above the caller's own level
# would deny a request the outer trust boundary already admitted.

import json
from typing import Any, ClassVar, Dict, cast

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import to_json
from src.services import service
from src.services.runtime_settings import settings_from_state


class StructureDataNode(FunctionNode):
    """Normalise the validated engagement metrics and derive the report KPIs.

    Input state keys:
        user_input        canonical JSON request written by the caller-contract
                          gate and forwarded across the graph boundary
        validated_input   the same object, when the node runs on outer state
        runtime_settings  JSON-encoded settings published by the inner graph

    Output state keys (partial dict — changed keys only):
        structured_data   JSON-encoded normalised model
        status / error_log
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input") or ""
        settings = settings_from_state(state)

        try:
            payload: Any = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None

        if not isinstance(payload, dict):
            emit_trace_event("structure_data_failed", {"reason": "unreadable_request"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["StructureDataNode: the validated request could not be read"],
            }

        metrics = payload.get("engagement_metrics")
        kpis = payload.get("kpis")
        if not isinstance(metrics, dict) or not metrics:
            emit_trace_event("structure_data_failed", {"reason": "no_engagement_metrics"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["StructureDataNode: the request carries no engagement metrics"],
            }
        kpis = kpis if isinstance(kpis, dict) else {}

        try:
            derived = service.derive(metrics, kpis, cast(float, settings["target_utilization"]))
        except ValueError as exc:
            emit_trace_event("structure_data_failed", {"reason": "non_numeric_metric", "field": str(exc)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"StructureDataNode: {exc} is not a number"],
            }

        structured: Dict[str, Any] = {
            "firm_name": payload.get("firm_name") or "",
            "report_period": payload.get("report_period") or "",
            "engagement_metrics": metrics,
            "kpis": kpis,
            "derived": derived,
            "target_utilization": kpis.get("target_utilization", settings["target_utilization"]),
        }

        emit_trace_event(
            "data_structured",
            {
                "metric_names": sorted(metrics),
                "kpi_names": sorted(kpis),
                "derived_names": sorted(derived),
                "target_utilization": structured["target_utilization"],
            },
            state,
        )

        return {
            "structured_data": to_json(structured),
            "status": AgentStatus.SUCCESS.value,
        }
