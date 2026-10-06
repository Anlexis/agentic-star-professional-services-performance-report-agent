"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never the full state)
#  - Return AgentStatus enum values — never bare strings
#  - Read input_context via state.get("input_context", {}) — read-only
#
# SVC-C2-007 — InputValidateNode.
#
# The pre_process backbone slot. This node owns the caller-data contract and is
# the single trust boundary of the template: it declares
# required_trust_level = VERIFIED_EXTERNAL, so an unauthenticated caller is
# refused here, before any domain node runs.
#
# Caller-data contract (input_context, preferred channel; every field optional):
#   firm_name           label rendered into the report; inert [a-z0-9_]{1,32}
#   report_period       label rendered into the report; inert [a-z0-9_]{1,32}
#   engagement_metrics  mapping of recognised metric names to finite, bounded numbers
#   kpis                mapping of recognised KPI names to finite, bounded numbers
#
# The same object may be supplied as a JSON document in `user_input`, which is
# what a plain HTTP client without a structured channel sends; both go through
# the identical validation. input_context wins when both carry data.
#
# Two properties are deliberate:
#   - Unknown fields are REFUSED, not ignored. Ignoring is not stripping: an
#     unrecognised key stays on input_context, is returned verbatim by the
#     platform's first node, and is then scanned by the platform's output gate —
#     so an unrecognised key carrying a credential shape fails the run at node 1
#     with an error the caller cannot act on.
#   - Every caller string that reaches the rendered report is locked to an inert
#     identifier alphabet. Free text on that channel is caller-controlled output
#     injection, and it is also what the platform's PII mask rewrites, which
#     would silently corrupt the label the report is about.

import json
import re
import unicodedata
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG
from src.services.progress import emit_progress

from src.schemas.state import to_json
from src.services.runtime_settings import finite_in_range

# ---------------------------------------------------------------------------
# Caller-data contract
# ---------------------------------------------------------------------------

_ALLOWED_CONTEXT_FIELDS = frozenset({"firm_name", "report_period", "engagement_metrics", "kpis"})

# Fields the hosting runtime puts on the context channel itself. They are not
# part of the caller contract and this node reads none of them, but treating
# them as unrecognised caller fields refuses every invocation served that way —
# and the caller cannot correct it, because it never added the field. They are
# dropped from the context channel before anything reads it, so the request is
# judged on what the CALLER sent. That is sound exactly because no constraint is
# attached to them: nothing is promised about a field this node does not read,
# so there is nothing for the caller to be misled about — which is the whole
# reason an unrecognised CALLER field is refused rather than ignored.
#
# Dropped, not merely tolerated: a bare runtime field would otherwise make the
# context channel non-empty and win the channel selection below, so a request
# whose data is in `user_input` would be read from an empty context instead.
_RUNTIME_CONTEXT_FIELDS = frozenset({"conversation_history"})

# name -> (low bound, high bound, whole numbers only)
_METRIC_BOUNDS: Dict[str, Tuple[float, float, bool]] = {
    "utilization_rate": (0.0, 1.0, False),
    "revenue_usd": (0.0, 1e12, False),
    "client_satisfaction_score": (0.0, 5.0, False),
    "active_engagements": (0, 100_000, True),
    "consultant_count": (1, 100_000, True),
    "partner_count": (0, 100_000, True),
}

_KPI_BOUNDS: Dict[str, Tuple[float, float, bool]] = {
    "target_utilization": (0.0, 1.0, False),
    "billable_hours": (0.0, 10_000_000.0, False),
    "pipeline_value": (0.0, 1e12, False),
}

# Structural cap on each mapping, checked before any value is read: a payload can
# be oversized because of how MANY entries it carries, not only how large one is.
_MAX_MAPPING_ENTRIES = 32

# Cap on the JSON document accepted through user_input.
_MAX_DOCUMENT_CHARS = 65_536

# Caller strings that render into the report are locked to this inert alphabet.
_INERT_IDENTIFIER_RE = re.compile(r"\A[a-z0-9_]{1,32}\Z")

# Field names are caller data too. A name that does not match this is reported
# positionally rather than echoed back.
_SAFE_FIELD_NAME_RE = re.compile(r"\A[A-Za-z0-9_.\-]{1,40}\Z")

# ---------------------------------------------------------------------------
# Injection screening
# ---------------------------------------------------------------------------
#
# Two families, both screened. Directive phrases are matched with both ends
# anchored so ordinary engagement commentary ("the team will act as a single
# delivery unit") does not trip them. Chat-template control tokens are matched
# structurally, because an attack does not have to use a phrase at all:
# "<|im_start|>system ..." carries no directive wording a phrase list would see.

_INJECTION_PHRASES: List[Tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+)?"
            r"(?:the\s+)?(?:previous|prior|above|earlier|preceding)\s+"
            r"(?:instruction|instructions|prompt|prompts|rule|rules)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "persona_override",
        re.compile(
            r"\byou\s+are\s+now\s+(?:a\s+|an\s+)?(?:dan|jailbreak|unrestricted|developer\s+mode)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(
            r"\b(?:reveal|print|show|repeat|output)\s+(?:me\s+)?(?:your|the)\s+"
            r"(?:system\s+prompt|initial\s+instructions|hidden\s+instructions)\b",
            re.IGNORECASE,
        ),
    ),
    ("script_markup", re.compile(r"<\s*/?\s*script\b", re.IGNORECASE)),
]

_CONTROL_TOKENS: List[Tuple[str, re.Pattern[str]]] = [
    ("chat_template_token", re.compile(r"<\|[^|>]{0,64}\|>")),
    ("instruction_tag", re.compile(r"\[/?INST\]", re.IGNORECASE)),
    ("system_tag", re.compile(r"<</?SYS>>", re.IGNORECASE)),
]

# Characters removed before the second screening pass. Stripping markup can
# splice a directive back together out of fragments a raw scan would not match
# ("ig<b>nore all previous instructions"), so every string is screened BOTH raw —
# which catches control tokens before they are removed — and again afterwards.
_MARKUP_RE = re.compile(r"</?[A-Za-z][^>]{0,120}>")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = ("​", "‌", "‍", "﻿")


def _strip_markup(text: str) -> str:
    """Remove HTML-ish markup and zero-width characters, then collapse spacing."""
    stripped = _MARKUP_RE.sub("", text)
    for char in _ZERO_WIDTH:
        stripped = stripped.replace(char, "")
    return re.sub(r"[ \t]{2,}", " ", stripped)


def _screen_text(text: str) -> Optional[str]:
    """Return the name of the first injection family found, or None if clean.

    The raw form and the markup-stripped form are both screened: control tokens
    only exist in the raw form, spliced directives only in the stripped one.
    """
    for candidate in (text, _strip_markup(text)):
        for name, pattern in _CONTROL_TOKENS:
            if pattern.search(candidate):
                return name
        for name, pattern in _INJECTION_PHRASES:
            if pattern.search(candidate):
                return name
    return None


def _safe_name(name: str) -> str:
    """Render a caller-supplied field name, or mask it when it is not inert."""
    return name if _SAFE_FIELD_NAME_RE.match(name) else "<masked>"


def _screen_structure(value: Any, path: str) -> Optional[Tuple[str, str]]:
    """Depth-first injection screen over a parsed payload, keys included.

    Returns (family, location) for the first finding, or None. Locations are
    field paths only — a matched value is never returned, because it may itself
    carry a credential shape the platform's output gate would then refuse, which
    discards the whole refusal delta including its clearing.

    Scanning after the parse is what defeats an escaped payload: "\\u003cscript"
    is only a script tag once JSON decoding has turned it into one.
    """
    if isinstance(value, str):
        found = _screen_text(value)
        return (found, path) if found else None
    if isinstance(value, dict):
        for position, key in enumerate(value, start=1):
            key_text = key if isinstance(key, str) else str(key)
            found = _screen_text(key_text)
            if found:
                return (found, f"{path}.<key #{position}>")
            nested = _screen_structure(value[key], f"{path}.{_safe_name(key_text)}")
            if nested:
                return nested
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            nested = _screen_structure(item, f"{path}[{index}]")
            if nested:
                return nested
    return None


# Reason code -> the sentence the caller reads. A code with no entry falls back
# to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES: Dict[str, str] = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}


def _reject(message: str, code: str = "INVALID_REQUEST") -> Dict[str, Any]:
    """Build the refusal delta. `message` names fields and bounds, never values.

    Two outcomes, chosen by the caller's ability to act on the finding, and
    distinguished by an explicit argument at the call site rather than by the
    text of `message` — so the distinction survives any later rewording.

    `code` non-empty — a value the caller can correct (empty, too long, out of
    contract). The run COMPLETES carrying the reason, so the calling surface can
    show the sentence and the caller can send a corrected request on the same
    conversation instead of receiving an exception type.

    `code` empty — a refusal the caller cannot reword their way past (spliced
    instructions). This terminates, exactly as before.

    Either way the request is NOT processed and nothing is published: the
    refusal itself is unchanged, only the way it is reported.

    `formatted_output` carries a TRUTHY notice for two reasons. It is what the
    caller actually receives — the platform resolves an agent's output as
    `formatted_output or result`, so a refusal that sets neither returns an empty
    envelope with no reason in it. And because that resolution has no status
    check, a falsy value here would fall through to whatever `result` happens to
    hold, which is exactly the fallback a refusal must not re-arm.
    """
    withheld: Dict[str, Any] = {
        # `message` names field paths and bounds. That belongs in the audit
        # channel; the caller-facing field below carries a fixed sentence.
        "error_log": [f"InputValidateNode: {message}"],
        "validated_input": None,
        "result": None,
    }
    if code:
        emit_progress(INPUT_REJECTED)
        return {
            **withheld,
            "status": AgentStatus.SUCCESS.value,
            "error_code": code,
            "formatted_output": _DEGRADED_MESSAGES.get(code, INPUT_REJECTED),
        }
    return {
        **withheld,
        "status": AgentStatus.ERROR.value,
        "formatted_output": f"The request was not accepted: {message}.",
    }


def _validate_numeric_mapping(
    raw: Any, bounds: Dict[str, Tuple[float, float, bool]], path: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate one metric mapping. Returns (values, error message)."""
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        return None, f"{path} must be a mapping"
    if len(raw) > _MAX_MAPPING_ENTRIES:
        return None, f"{path} carries more than {_MAX_MAPPING_ENTRIES} entries"

    validated: Dict[str, Any] = {}
    for position, (name, value) in enumerate(raw.items(), start=1):
        key = name if isinstance(name, str) else str(name)
        if key not in bounds:
            shown = _safe_name(key) if _SAFE_FIELD_NAME_RE.match(key) else f"field #{position}"
            return None, f"{path} carries the unsupported field {shown}"
        low, high, whole = bounds[key]
        parsed = finite_in_range(value, low, high)
        if parsed is None:
            return None, f"{path}.{key} must be a finite number between {low:g} and {high:g}"
        if whole:
            if parsed != int(parsed):
                return None, f"{path}.{key} must be a whole number"
            validated[key] = int(parsed)
        else:
            validated[key] = parsed
    return validated, None


class InputValidateNode(FunctionNode):
    """Caller-contract gate for the performance-report pipeline.

    Validates the caller-supplied engagement metrics, screens the whole parsed
    payload for prompt-injection content, and publishes only validated values.
    This node declares the template's trust requirement, so an unauthenticated
    caller is refused before any domain node runs.

    Input state keys:
        input_context     structured caller-data channel (preferred)
        user_input        the same object as a JSON document (fallback)

    Output state keys (partial dict — changed keys only):
        validated_input   canonical JSON of the validated payload
        enriched_context  JSON-encoded request context
        status / error_log / formatted_output
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        raw_context = state.get("input_context") or {}
        if not isinstance(raw_context, dict):
            return _reject("input_context must be a mapping")

        # What the CALLER put on the context channel: the runtime's own fields
        # are not part of the contract and are not read (see the note above).
        caller_context = {name: value for name, value in raw_context.items() if name not in _RUNTIME_CONTEXT_FIELDS}

        payload, error, code = self._read_payload(state, caller_context)
        if payload is None:
            emit_trace_event("input_validation_failed", {"reason": "payload_unreadable"}, state)
            return _reject(error or "the request could not be read", code=code or "INVALID_REQUEST")

        # The injection screen runs FIRST, over everything the caller sent —
        # before any field is recognised, bounded or discarded. Screening after
        # the field checks would mean a hostile value in a field this node does
        # not recognise is refused for the wrong reason and never screened at
        # all, and it would leave the screen with nothing of its own to catch:
        # the checks below already refuse everything it would have seen.
        finding = _screen_structure(payload, "request")
        if finding:
            family, location = finding
            emit_trace_event(
                "input_validation_failed",
                {"reason": "injection_pattern", "family": family, "location": location},
                state,
            )
            # code="" keeps this one terminal. Spliced instructions are not a
            # value the caller can correct by rewording, and completing the run
            # would make a refusal read like an ordinary declined request.
            return _reject(f"refused: {family} pattern at {location}", code="")

        unknown = [name for name in payload if name not in _ALLOWED_CONTEXT_FIELDS]
        if unknown:
            named = ", ".join(sorted(_safe_name(str(name)) for name in unknown))
            emit_trace_event("input_validation_failed", {"reason": "unsupported_field"}, state)
            return _reject(f"the request carries unsupported field(s): {named}")

        firm_name, error = self._read_label(payload, "firm_name")
        if error is not None:
            emit_trace_event("input_validation_failed", {"reason": "label_not_inert"}, state)
            return _reject(error)
        report_period, error = self._read_label(payload, "report_period")
        if error is not None:
            emit_trace_event("input_validation_failed", {"reason": "label_not_inert"}, state)
            return _reject(error)

        metrics, error = _validate_numeric_mapping(
            payload.get("engagement_metrics"), _METRIC_BOUNDS, "engagement_metrics"
        )
        if metrics is None:
            emit_trace_event("input_validation_failed", {"reason": "metric_out_of_contract"}, state)
            return _reject(error or "engagement_metrics is out of contract")
        kpis, error = _validate_numeric_mapping(payload.get("kpis"), _KPI_BOUNDS, "kpis")
        if kpis is None:
            emit_trace_event("input_validation_failed", {"reason": "kpi_out_of_contract"}, state)
            return _reject(error or "kpis is out of contract")

        if not metrics:
            emit_trace_event("input_validation_failed", {"reason": "no_engagement_metrics"}, state)
            return _reject("engagement_metrics must carry at least one of: " + ", ".join(sorted(_METRIC_BOUNDS)))

        validated = {
            "firm_name": firm_name,
            "report_period": report_period,
            "engagement_metrics": metrics,
            "kpis": kpis,
        }

        emit_trace_event(
            "input_validated",
            {
                "firm_name_supplied": bool(firm_name),
                "report_period_supplied": bool(report_period),
                "metric_names": sorted(metrics),
                "kpi_names": sorted(kpis),
            },
            state,
        )

        return {
            "validated_input": json.dumps(validated, ensure_ascii=False, sort_keys=True),
            "enriched_context": to_json(
                {
                    "source": "SVC-C2-007",
                    "channel": self._channel(caller_context, state),
                    "firm_name": firm_name,
                    "report_period": report_period,
                }
            ),
            "status": AgentStatus.SUCCESS.value,
        }

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _channel(caller_context: Dict[str, Any], state: AgentState) -> str:
        """Name the entry channel for the audit record, never echoing caller text.

        Judged on the caller's own fields: a context channel carrying only the
        runtime's fields is not a channel the caller sent anything on.
        """
        return "input_context" if caller_context else ("user_input" if state.get("user_input") else "none")

    @staticmethod
    def _read_payload(
        state: AgentState, caller_context: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
        """Return the caller's request object from whichever channel carries it.

        The third element is the reason code for the failure. "Nothing was sent"
        and "what was sent is too long to read" are different things to correct,
        and collapsing both into the generic code would tell the caller to check
        a format when the actual fix is to send a question, or to shorten one.
        """
        if caller_context:
            return dict(caller_context), None, ""

        document = state.get("user_input")
        if not isinstance(document, str) or not document.strip():
            return None, "the request carried no engagement metrics on either channel", "EMPTY_INPUT"
        document = _CONTROL_CHAR_RE.sub("", unicodedata.normalize("NFKC", document)).strip()
        if len(document) > _MAX_DOCUMENT_CHARS:
            # The whole request is oversized, not one field of it.
            return None, f"user_input exceeds the {_MAX_DOCUMENT_CHARS}-character maximum", "QUESTION_TOO_LONG"
        try:
            parsed = json.loads(document)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "user_input must be a JSON object describing the reporting period", "INVALID_REQUEST"
        if not isinstance(parsed, dict):
            return None, "user_input must be a JSON object, not another JSON type", "INVALID_REQUEST"
        return parsed, None, ""

    @staticmethod
    def _read_label(payload: Dict[str, Any], field: str) -> Tuple[str, Optional[str]]:
        """Validate one caller label against the inert identifier alphabet."""
        value = payload.get(field, "")
        if value in ("", None):
            return "", None
        if not isinstance(value, str) or not _INERT_IDENTIFIER_RE.match(value):
            return "", f"{field} must match [a-z0-9_] and be 1-32 characters"
        return value, None
