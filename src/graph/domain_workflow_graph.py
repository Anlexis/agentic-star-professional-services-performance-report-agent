"""AgentCore Platform v1.0"""

# SVC-C2-007 — DomainWorkflowGraph (inner BaseGraph).
#
# The inner graph of the two-layer nested architecture. It encapsulates the
# professional-services report workflow:
#
#   START -> structure_data -> generate_narrative -> format_report -> END
#
# Reached through ReportGenerationGraphNode.get_subgraph(); get_output() shapes
# the sub_result dict that merge_output() consumes on the outer side.
#
# Runtime settings arrive through the constructor config, forwarded by the outer
# graph, and are republished into inner state by _extra_initial_state() — the
# domain nodes take no constructor arguments and read their parameters from
# state, which is the only channel the framework gives them.
#
# Rules enforced:
#   Inherits BaseGraph (fully custom topology — no forced backbone)
#   register_nodes() does NOT call super() (abstract in BaseGraph)
#   initialize / finalize are outer backbone concerns and are not registered
#   All inner nodes declare TrustLevel.ANONYMOUS: GraphNode.execute() passes the
#     outer InvocationContext through unchanged, so a gate above the caller's own
#     level would deny a request the outer trust boundary already admitted

import json
from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.format_report_node import FormatReportNode
from src.nodes.generate_narrative_node import GenerateNarrativeNode
from src.nodes.structure_data_node import StructureDataNode
from src.schemas.state import State
from src.services.runtime_settings import BUILTIN_SETTINGS, declared_settings


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for SVC-C2-007.

    Pipeline (linear):
        START
          -> structure_data     (StructureDataNode)     normalise + derive KPIs
          -> generate_narrative (GenerateNarrativeNode) summary + visualisations
          -> format_report      (FormatReportNode)      assemble the report
          -> END
    """

    # -- Identity --------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "svc_c2_007_report_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # -- Config validation -----------------------------------------------

    def _validate_config(self) -> None:
        """Reject a forwarded settings block that is not a mapping.

        Individual values are validated by declared_settings(), which keeps the
        built-in floor for anything out of contract; a wholesale wrong shape is
        a wiring mistake and fails loudly instead.
        """
        settings = self.config.get("settings", {})
        if not isinstance(settings, dict):
            raise ValueError(f"[{type(self).__name__}] 'settings' must be a mapping, got: {type(settings).__name__}")

    # -- Initial state ---------------------------------------------------

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Publish the forwarded runtime settings into inner state.

        This is what makes the declaration in config/config.yaml observable
        inside the pipeline: the domain nodes read `runtime_settings` and have no
        defaults of their own beyond the shared floor.
        """
        forwarded: Any = self.config.get("settings", {})
        settings = dict(BUILTIN_SETTINGS)
        if isinstance(forwarded, dict):
            settings.update({key: forwarded[key] for key in BUILTIN_SETTINGS if key in forwarded})
        else:  # pragma: no cover - _validate_config already refuses this shape
            settings = declared_settings({})
        return {"runtime_settings": json.dumps(settings, sort_keys=True)}

    # -- Node registration -----------------------------------------------

    def register_nodes(self) -> None:
        """Register the three domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract. Every key
        registered here MUST be referenced in add_edges(). No constructor
        arguments: the node contract has none, and parameters travel in state.
        """
        self._nodes["structure_data"] = StructureDataNode()
        self._nodes["generate_narrative"] = GenerateNarrativeNode()
        self._nodes["format_report"] = FormatReportNode()

    # -- Edge wiring -----------------------------------------------------

    def add_edges(self) -> None:
        """Wire the linear report-generation topology."""
        self._sg.add_edge(START, "structure_data")
        self._sg.add_edge("structure_data", "generate_narrative")
        self._sg.add_edge("generate_narrative", "format_report")
        self._sg.add_edge("format_report", END)

    # -- Routing ---------------------------------------------------------

    def route(self, state: State) -> str:
        """Required by the BaseGraph contract; unused by this linear topology.

        Annotated with this graph's own State: the graph runtime reads a path
        callable's annotation as its input schema and projects away fields the
        annotation does not declare, so a broader annotation would hide the very
        fields the decision is made on.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "format_report"

    # -- Output shape ----------------------------------------------------

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the sub_result dict returned to ReportGenerationGraphNode.

        Coupling (designed together with merge_output() in graph.py):
            here             emits -> formatted_report, report_metadata, status
            merge_output()   reads -> the same three keys

        The report is surfaced only on the success path. On any other status the
        report keys are None, so a partial or refused run cannot hand the outer
        graph text to publish.
        """
        succeeded = state.get("status") == AgentStatus.SUCCESS.value
        return {
            "formatted_report": state.get("formatted_report") if succeeded else None,
            "report_metadata": state.get("report_metadata") if succeeded else None,
            "status": state.get("status"),
            "node_history": state.get("node_history", []),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
        }
