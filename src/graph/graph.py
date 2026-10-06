"""AgentCore Platform v1.0"""

# SVC-C2-007 — outer graph (AgentBaseGraph; two-layer nested architecture).
#
# Outer backbone (fixed — add_edges() is NOT overridden):
#     START -> initialize -> pre_process -> main -> post_process -> finalize -> END
#
# The `main` slot is a GraphNode subclass (ReportGenerationGraphNode) that
# delegates the whole domain workflow to DomainWorkflowGraph (inner BaseGraph),
# so domain complexity never reaches the backbone.
#
# Runtime configuration: the registry loads config/config.yaml and passes it as
# the graph's config; the standalone entry point does the same through
# runtime_config(). Every declared value is validated once in
# declared_settings() and then travels to its consumer — the backbone reads
# max_retry itself, and the domain parameters are forwarded to the inner graph,
# which republishes them into inner state where the domain nodes read them.
# There is no second copy of the defaults anywhere else on disk.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step topology)

import logging
from typing import Any, ClassVar, Dict, Optional, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import OutputFormatNode
from src.nodes.pre_process_node import InputValidateNode
from src.schemas.state import State
from src.services.runtime_settings import BUILTIN_SETTINGS, declared_settings

logger = logging.getLogger(__name__)

__all__ = ["ReportGenerationGraphNode", "SvcC2007Agent", "Graph"]


class ReportGenerationGraphNode(GraphNode):
    """GraphNode wrapper for the inner DomainWorkflowGraph.

    Assigned to the `main` backbone slot. Delegates the multi-step domain
    workflow (structure -> narrate -> format) to the inner graph and forwards the
    validated runtime settings to it.
    """

    # "handle": an inner failure is turned into a contained refusal by
    # on_subgraph_error() below, rather than re-raised. Re-raising is what the
    # framework default does, and it loses containment: the node wrapper turns
    # the exception into a bare error update that clears nothing and carries a
    # traceback, and the caller receives an empty envelope with no reason in it.
    error_strategy: ClassVar[str] = "handle"

    # Human-in-the-loop interrupts are handled inside the inner graph only.
    propagate_hitl: ClassVar[bool] = False

    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        """Bind the validated runtime settings forwarded by the outer graph."""
        super().__init__()
        self._settings: Dict[str, Any] = dict(settings or BUILTIN_SETTINGS)

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the validated runtime settings to the inner graph.

        The values come from the outer graph's own config — config/config.yaml
        as loaded by the registry, or by the standalone entry point through
        runtime_config() — validated once in declared_settings(). The inner
        graph republishes them into inner state, so the domain nodes read a live
        declaration rather than a dead one.
        """
        return {"settings": dict(self._settings)}

    def get_subgraph(self) -> Any:
        """Instantiate the inner domain workflow graph.

        The inner graph receives the validated settings through the BaseGraph
        constructor; its domain NODES still take no constructor arguments and
        read their parameters from state.

        Imported inside the method to keep module load order independent of the
        inner graph, matching the nested-graph sample under src/examples/.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Hand the inner graph the validated request document.

        GraphNode.extract_input() returns a STRING that the framework writes into
        the inner state under `user_input`; nothing else from the outer state
        crosses the boundary. The caller contract gate has already reduced the
        request to a canonical JSON object of validated numbers and inert
        labels, so that object is what travels.
        """
        return cast(str, state.get("validated_input") or "")

    def execute(self, state: AgentState) -> Dict[str, Any]:
        """Skip the inner graph when the request was already found unacceptable.

        A request the caller-contract gate declined has no validated input to act
        on, so running the workflow would only reach the first domain node, fail
        its own precondition, and terminate the run - replacing the specific,
        actionable reason already settled with a vaguer one.
        """
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        return cast(Dict[str, Any], super().execute(state))

    def on_subgraph_error(self, state: AgentState, error: Exception) -> Dict[str, Any]:
        """Turn an inner-graph failure into a contained, actionable refusal.

        Reached whenever the inner graph ends in an error status or raises —
        which includes the platform refusing one of the inner nodes' own results.

        Two things matter here. The report fields are CLEARED rather than left as
        they were: the platform resolves output as `formatted_output or result`,
        with no status check, so a report left in `result` would be delivered
        inside the error envelope. And the reason is SUMMARISED: the inner error
        text can carry a traceback and absolute source paths, so it is replaced
        by a fixed sentence rather than forwarded.
        """
        logger.warning("ReportGenerationGraphNode: inner workflow failed (%s)", type(error).__name__)
        return {
            "formatted_report": None,
            "narrative": None,
            "visualization_descriptions": None,
            "structured_data": None,
            "report_metadata": None,
            "result": None,
            "formatted_output": (
                "The performance report could not be produced for this request and no "
                "partial report is included. Check the submitted engagement metrics and try again."
            ),
            "status": AgentStatus.ERROR.value,
            "error_log": ["ReportGenerationGraphNode: the report workflow did not complete"],
        }

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner graph's sub_result back into the outer state delta.

        `sub_result` is the dict shaped by DomainWorkflowGraph.get_output().
        Returns ONLY the keys this node changes — never the full state.

        The inner report is published as `result` because the output gate in the
        post_process slot reads that field. On any non-success inner status the
        report is NOT published: un-gated text must not reach a state field the
        framework's output resolution can fall back to.
        """
        if sub_result.get("status") != AgentStatus.SUCCESS.value:
            return {
                "formatted_report": None,
                "result": None,
                "report_metadata": None,
                "status": AgentStatus.ERROR.value,
            }
        return {
            "formatted_report": sub_result.get("formatted_report"),
            "result": sub_result.get("formatted_report"),
            "report_metadata": sub_result.get("report_metadata"),
            "status": AgentStatus.SUCCESS.value,
        }


class SvcC2007Agent(AgentBaseGraph):
    """Outer graph — Professional Services Performance Report Generator.

    Base class: AgentBaseGraph (direct framework inheritance). Domain complexity
    is encapsulated in the inner DomainWorkflowGraph, reached through
    ReportGenerationGraphNode in the `main` slot.

    Backbone slots:
        pre_process   InputValidateNode  — the caller-contract gate and the
                                           single trust boundary of this template
        main          ReportGenerationGraphNode
        post_process  OutputFormatNode   — the output gate

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the platform registry."""
        return "svc_c2_007"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill the backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default initialize and finalize nodes.
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = InputValidateNode()
        self._nodes["main"] = ReportGenerationGraphNode(settings=declared_settings(self.config))
        self._nodes["post_process"] = OutputFormatNode()


# Module-level alias: the standalone entry point and the registry module path
# both resolve the agent class through this name.
Graph = SvcC2007Agent
