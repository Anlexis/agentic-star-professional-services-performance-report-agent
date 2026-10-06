"""AgentCore Platform v1.0"""

# SVC-C2-007 — OutputFormatNode (outer backbone post_process slot).
#
# The output boundary. It is the last place the assembled report can be stopped,
# and it is the only place that decides what the caller receives.
#
# Two independent layers, each with its own audit event:
#
#   1. Credential scan. It calls the PLATFORM's own detector rather than a local
#      pattern list. A local list narrower than the platform's is not a smaller
#      guarantee, it is a bypass: a value this node lets through is then refused
#      by the platform's own gate on this node's result, and the wrapper turns
#      that refusal into a bare error update that discards this node's delta —
#      including its clearing. Delegating makes the two sets identical by
#      construction and removes the drift entirely.
#
#   2. Staff and client identifier scan, the domain layer. Performance data
#      routinely carries employee and consultant references; the report must not.
#
# On a violation the node returns an error status AND CLEARS every field that
# carries answer text. Clearing is the part that matters: the platform resolves
# an agent's output as `formatted_output or result`, with no status check, so a
# gate that only raises — or that returns an error while leaving `result` in
# place — still ships the un-gated report inside the error envelope. A FALSY
# `formatted_output` is not a fix either: it re-arms that same fallback, which is
# why the withheld notice below is a real sentence.
#
# The violation message names the layer and the field path, never the matched
# text. Quoting the match would put the credential back into a returned value,
# where the platform's gate on THIS node's result raises and discards the whole
# refusal delta, clearing included.
#
# Trust level: ANONYMOUS — the external trust boundary is the caller-contract
# gate in the pre_process slot; requiring more here would deny a caller that slot
# has already admitted.

import re
from typing import Any, ClassVar, Dict, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials_in_value
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG

# Staff and client identifiers, the domain layer of the boundary. Independent of
# the credential layer above it and audited separately.
_STAFF_IDENTIFIER_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:EMP|WORKER|EMPL)-?\d{3,8}\b"
    r"|employee[_\s]?(?:id|no|number)[:\s=]+\S+"
    r"|consultant[_\s]?(?:id|no|number)[:\s=]+\S+",
    re.IGNORECASE,
)

# Every state field that can carry report text or a report payload. A violation
# clears all of them, and they are cleared by PRESENCE — the key appears in the
# returned delta with an empty value. Omitting a key is not clearing it: state
# updates are merged, so an omitted key keeps whatever it held before.
_OUTPUT_BEARING_FIELDS: Tuple[str, ...] = (
    "result",
    "formatted_report",
    "narrative",
    "visualization_descriptions",
    "structured_data",
    "report_metadata",
)

# Reason code -> the sentence the caller reads. A code with no entry falls back
# to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES: Dict[str, str] = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}

_WITHHELD_NOTICE = (
    "The performance report was withheld at the output boundary and no partial "
    "report is included. Regenerate the report from engagement metrics that carry "
    "no credential or staff identifier."
)


def screen_output(content: Optional[str]) -> Optional[str]:
    """Return the name of the first violated layer, or None when the text is clean.

    Layer names are fixed reason codes. The matched text is never returned.
    """
    if not content:
        return None
    if detect_credentials_in_value(content):
        return "credential"
    if _STAFF_IDENTIFIER_PATTERN.search(content):
        return "staff_identifier"
    return None


def withheld_delta(layer: str, field: str) -> Dict[str, Any]:
    """Build the refusal delta: an error status, a truthy notice, everything cleared."""
    delta: Dict[str, Any] = {name: None for name in _OUTPUT_BEARING_FIELDS}
    delta.update(
        {
            "formatted_output": _WITHHELD_NOTICE,
            "status": AgentStatus.ERROR.value,
            "error_log": [f"OutputFormatNode: output withheld — {layer} pattern in state.{field}"],
        }
    )
    return delta


class OutputFormatNode(FunctionNode):
    """Screen the assembled report and decide what the caller receives.

    Input state keys:
        result            the assembled report, published by the main slot
        formatted_report  the same text, used when the main slot published only it

    Output state keys (partial dict — changed keys only):
        formatted_output  the report, or the withheld notice
        result            the report, or None on a violation
        every other output-bearing field, cleared on a violation
        status / error_log
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        # A run the caller-contract gate declined produced no report to screen.
        # Without this branch the screen sees "" , finds nothing, and releases a
        # FALSY formatted_output — which re-arms the `formatted_output or result`
        # fallback and hands the caller an empty envelope with no reason in it.
        marker = state.get("error_code")
        if marker:
            emit_trace_event("output_not_produced", {"reason": marker}, state)
            return {
                "formatted_output": _DEGRADED_MESSAGES.get(marker, INPUT_REJECTED),
                "status": AgentStatus.SUCCESS.value,
                "error_code": marker,
            }

        field = "result"
        report = state.get("result")
        if not report:
            field = "formatted_report"
            report = state.get("formatted_report") or ""

        layer = screen_output(report)
        if layer:
            emit_trace_event(
                "output_withheld",
                {"layer": layer, "field": field, "report_chars": len(report)},
                state,
            )
            return withheld_delta(layer, field)

        emit_trace_event(
            "output_released",
            {"field": field, "report_chars": len(report)},
            state,
        )
        return {
            "formatted_output": report,
            "status": AgentStatus.SUCCESS.value,
        }
