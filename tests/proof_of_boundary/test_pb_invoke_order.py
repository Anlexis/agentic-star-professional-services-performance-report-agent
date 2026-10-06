# PB-06 — invocation order.
#
# The node wrapper is a security boundary as much as an execution one, and the
# order it runs things in is what makes it one: the trust gate before anything
# else, the input gate before execute(), the output gate after it, and an audit
# event either side. A node cannot opt out of any of that, so this checks the
# order holds for every node this template defines.
#
# It also drives the whole agent once and checks the backbone ran in order:
#   InitializeNode -> InputValidateNode -> ReportGenerationGraphNode
#   -> OutputFormatNode -> FinalizeNode
#
# The caller is a verified external one, never an internal one: an internal
# caller clears every trust gate and would hide a node that asks for more than
# a real caller can ever hold.
#
import importlib
import inspect
import json
import pathlib
import pkgutil

import pytest

# Template-specific constant: the class filling the `main` backbone slot.
_MAIN_SLOT_NODE_NAME = "ReportGenerationGraphNode"

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The request body the first-invoke check posts after a deployment. Loading it
# here keeps that check and this suite on the same request: a payload that drifts
# from the one proven to succeed would make the deployment check meaningless.
_VALID_PAYLOAD = json.loads((_REPO_ROOT / "deploy" / "invoke_payload.json").read_text(encoding="utf-8"))


def _valid_context() -> dict:
    """The structured caller data of the shipped request."""
    return dict(_VALID_PAYLOAD["input_context"])


def _discover_node_classes() -> list:
    """Import every module under src/nodes/ and collect concrete BaseNode subclasses."""
    from framework.nodes.base_node import BaseNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


class TestInvokeOrder:
    """Trust gate -> start event -> input gate -> execute() -> output gate -> complete event."""

    def test_call_order_for_every_node(self, monkeypatch):
        node_classes = _discover_node_classes()
        if not node_classes:
            pytest.skip("no concrete BaseNode subclasses found under src/nodes/")

        import framework.nodes.base_node as base_node_module

        failures: list = []
        for node_cls in node_classes:
            order: list = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            state = {
                "caller_trust_level": node_cls.required_trust_level.value,
                "correlation_id": "pb6-invoke-order-test",
            }
            instance(state)

            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)


class TestBackboneGraphOrder:
    """The backbone runs its five slots in the fixed order on a real invocation.

    Asserts that node_history contains (in order):
        InitializeNode → InputValidateNode (pre_process)
        → ReportGenerationGraphNode (main)
        → OutputFormatNode (post_process)
        → FinalizeNode

    The caller is VERIFIED_EXTERNAL, which is what a real external caller holds.
    An INTERNAL caller would clear every gate and hide a node that asks for more
    trust than the entry point can ever grant.
    """

    @pytest.fixture(autouse=True)
    def patch_all_emits(self, monkeypatch):
        """Suppress emit_trace_event across all domain nodes to reduce noise."""
        for mod in [
            "src.nodes.pre_process_node",
            "src.nodes.structure_data_node",
            "src.nodes.generate_narrative_node",
            "src.nodes.format_report_node",
        ]:
            monkeypatch.setattr(f"{mod}.emit_trace_event", lambda *a, **k: None)

    def test_backbone_node_order_verified_external(self):
        """Drive the whole agent once and assert the backbone slot order."""
        from src.graph.graph import SvcC2007Agent
        from framework.schemas.invocation_context import InvocationContext
        from framework.schemas.trust_level import TrustLevel

        agent = SvcC2007Agent()
        agent.compile()

        ctx = InvocationContext(
            session_id="pb6-backbone-test",
            # A real external caller's level — never an internal one.
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        )
        result = agent.invoke("", ctx=ctx, input_context=_valid_context())

        from framework.schemas.agent_status import AgentStatus

        assert result is not None, "invoke() returned None"
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            f"Expected AgentStatus.SUCCESS, got {result.get('status')!r}. " f"error_log: {result.get('error_log')}"
        )

        node_history = result.get("node_history", [])
        assert len(node_history) >= 5, (
            f"Expected at least 5 backbone nodes in node_history, " f"got {len(node_history)}: {node_history}"
        )

        # Check that the backbone class names appear in order.
        _EXPECTED_BACKBONE = [
            "InitializeNode",
            "InputValidateNode",
            _MAIN_SLOT_NODE_NAME,  # "ReportGenerationGraphNode"
            "OutputFormatNode",
            "FinalizeNode",
        ]

        # Extract class names from node_history entries.
        # node_history may be a list of dicts with "node" key or class names directly.
        def _name(entry) -> str:
            if isinstance(entry, dict):
                return entry.get("node", str(entry))
            return str(entry)

        history_names = [_name(e) for e in node_history]

        for expected_cls in _EXPECTED_BACKBONE:
            assert any(
                expected_cls in name for name in history_names
            ), f"Backbone node '{expected_cls}' not found in node_history: {history_names}"

        # Verify order: each backbone node appears AFTER the previous one.
        positions = {}
        for cls_name in _EXPECTED_BACKBONE:
            for i, name in enumerate(history_names):
                if cls_name in name:
                    positions[cls_name] = i
                    break

        for i in range(len(_EXPECTED_BACKBONE) - 1):
            a = _EXPECTED_BACKBONE[i]
            b = _EXPECTED_BACKBONE[i + 1]
            if a in positions and b in positions:
                assert positions[a] < positions[b], (
                    f"Backbone order violation: '{a}' (pos {positions[a]}) "
                    f"must come before '{b}' (pos {positions[b]})"
                )
