"""AgentCore Platform v1.0"""

# State is a flat TypedDict, never a validated model object. The checkpointer
# serialises state with msgpack; a model instance does not survive that round
# trip and comes back silently corrupted rather than raising.
#
# For the same reason, dict and list fields are typed Optional[str] and stored
# through to_json() / from_json(). Credentials and secrets never belong here.

import json
from typing import Any, Optional, TypeVar

from framework.schemas.agent_state import AgentState

T = TypeVar("T")


def to_json(value: Any) -> Optional[str]:
    """Serialise a dict or list to a JSON string for State storage.

    Use when writing any dict/list field to State so LangGraph msgpack
    checkpoints serialise it as a primitive string.

    Producer pattern:
        return {"structured_data": to_json(my_dict), ...}
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialise a JSON string from State back to a dict or list.

    Returns `default` when value is None or not valid JSON.

    Consumer pattern:
        data = from_json(state.get("structured_data"), {})
    """
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return default


class State(AgentState):
    """SVC-C2-007 — Professional Services Performance Report Generation Agent state.

    Shared fields (user_input, validated_input, status, session_id, node_history,
    error_log, result, formatted_output, hitl_*, etc.) are inherited from AgentState.

    Domain fields use Optional[str] for dict and list values; read and write
    them through the to_json() / from_json() helpers above.
    """

    # Written by the graph before the first node runs (and republished by the
    # inner graph). JSON-encoded runtime settings validated from
    # config/config.yaml — the channel the declared parameters travel on, since
    # the node contract takes no config argument. Carries no answer text.
    runtime_settings: Optional[str]

    # Written by StructureDataNode (inner domain step 1).
    # JSON-encoded normalised engagement-metrics mapping.
    # Contains: firm_name, report_period, engagement_metrics, kpis, derived.
    structured_data: Optional[str]

    # Written by GenerateNarrativeNode (inner domain step 2).
    # Executive summary and trend commentary (already a plain string).
    narrative: Optional[str]

    # Written by GenerateNarrativeNode (inner domain step 2).
    # JSON-encoded list of visualisation description strings.
    visualization_descriptions: Optional[str]

    # Written by FormatReportNode (inner domain step 3).
    # Final assembled Markdown performance report (plain string).
    formatted_report: Optional[str]

    # Written by FormatReportNode (inner domain step 3).
    # JSON-encoded report metadata mapping
    # {"firm_name", "report_period", "report_length", "sections"}.
    report_metadata: Optional[str]
    # Set when a run COMPLETES without carrying out the request, because the
    # caller sent a value they can correct. A closed set of codes, never caller
    # content. Nodes downstream of the one that set it do no work and pass it on.
    error_code: Optional[str]
