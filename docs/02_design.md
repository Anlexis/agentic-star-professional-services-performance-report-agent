# Design Specification — SVC-C2-007

## Identity

- **Template ID**: SVC-C2-007
- **Name**: Professional Services Performance Report Generator
| L1 Base (framework base class) | AgentBaseGraph — direct framework inheritance |
- **Category**: Cat 2 (nested two-layer architecture)
- **Industry**: SVC (professional services)
- **Pattern**: document generation
- **Generation mode**: deterministic

## What it does

A caller submits one reporting period's engagement figures. The agent normalises them,
derives the KPIs a partner reads a performance report for — utilisation against target,
revenue per consultant, pipeline coverage — composes an executive summary and trend
commentary around those figures, recommends the visualisations that carry the story, and
assembles the whole thing as a Markdown report.

Composition is **deterministic**: the same figures always produce the same report. That is
a deliberate choice for this domain. A performance report is circulated to people making
staffing and pricing decisions, so every number in it must trace back to a number that went
in. There is no generative step and no external model call; the template declares
`generation_mode: deterministic` in its manifest for that reason.

## Architecture

### Two-layer nested structure

```
Outer backbone (AgentBaseGraph — fixed; add_edges is never overridden):
  START
    -> initialize          [InitializeNode — framework default]
    -> pre_process         [InputValidateNode — the caller-contract gate]
    -> main                [ReportGenerationGraphNode — GraphNode subclass]
         |
         +- Inner graph (DomainWorkflowGraph — BaseGraph):
              START
                -> structure_data      [StructureDataNode]
                -> generate_narrative  [GenerateNarrativeNode]
                -> format_report       [FormatReportNode]
              END
         |
    -> post_process        [OutputFormatNode — the output gate]
    -> finalize            [FinalizeNode — framework default]
  END
```

### Layer responsibilities

| Slot | Class | File | Trust level | Responsibility |
|---|---|---|---|---|
| pre_process | `InputValidateNode` | `src/nodes/pre_process_node.py` | VERIFIED_EXTERNAL | the caller-data contract and the template's single trust boundary |
| main | `ReportGenerationGraphNode` | `src/graph/graph.py` | ANONYMOUS | delegates to the inner workflow; contains an inner failure |
| — structure_data | `StructureDataNode` | `src/nodes/structure_data_node.py` | ANONYMOUS | normalise the request, derive the KPIs |
| — generate_narrative | `GenerateNarrativeNode` | `src/nodes/generate_narrative_node.py` | ANONYMOUS | compose the narrative and the visualisation recommendations |
| — format_report | `FormatReportNode` | `src/nodes/format_report_node.py` | ANONYMOUS | assemble the Markdown report |
| post_process | `OutputFormatNode` | `src/nodes/post_process_node.py` | ANONYMOUS | screen the assembled report and decide what the caller receives |

**Trust levels.** The external requirement sits on `InputValidateNode`, so an
unauthenticated caller is refused before any domain node runs. Every other node declares
`ANONYMOUS`: the subgraph node passes the caller's own invocation context through unchanged,
so a node requiring more than the caller holds would deny a request the trust boundary has
already admitted — and the caller can never hold more than the boundary grants.

## Caller-data contract

`POST /invoke` accepts the figures on `input_context`. The same object may be sent as a JSON
document in `input`; both go through the identical validation, and `input_context` wins when
both carry data.

| Field | Type | Bounds | Rendered |
|---|---|---|---|
| `firm_name` | string | `[a-z0-9_]`, 1–32 characters | yes |
| `report_period` | string | `[a-z0-9_]`, 1–32 characters | yes |
| `engagement_metrics.utilization_rate` | number | 0 – 1 | yes |
| `engagement_metrics.revenue_usd` | number | 0 – 1e12 | yes |
| `engagement_metrics.client_satisfaction_score` | number | 0 – 5 | yes |
| `engagement_metrics.active_engagements` | whole number | 0 – 100000 | yes |
| `engagement_metrics.consultant_count` | whole number | 1 – 100000 | yes |
| `engagement_metrics.partner_count` | whole number | 0 – 100000 | yes |
| `kpis.target_utilization` | number | 0 – 1 | via the utilisation gap |
| `kpis.billable_hours` | number | 0 – 1e7 | yes |
| `kpis.pipeline_value` | number | 0 – 1e12 | via the coverage ratio |

Four properties of the contract are load-bearing:

1. **Unknown fields are refused, not ignored.** Ignoring is not stripping. An unrecognised
   key stays on `input_context`, the framework's first node returns that mapping verbatim
   inside its own result, and the framework's output gate scans every value of every result —
   so an unrecognised key carrying a credential shape fails the run at the first node, before
   any template code runs, with an error the caller cannot act on. The entry point screens the
   whole channel for credential shapes for the same reason and refuses with `400`, naming the
   field.
2. **Every number is finite and bounded.** `NaN` and `±Infinity` parse cleanly through
   `float()` and arrive verbatim through JSON, and every comparison against `NaN` is False —
   so an unchecked non-finite value passes every later bound and is rendered (`nan%`,
   `$inf`). The parser rejects them, rejects booleans, and rejects out-of-range magnitudes,
   failing closed with a message that names the field and its bounds.
3. **Rendered strings are inert.** `firm_name` and `report_period` appear in the report text,
   so free text there would be caller-controlled output injection — a caller could inject
   Markdown structure into a document someone else reads. They are locked to `[a-z0-9_]`.
4. **A refusal never echoes the value.** The audit record carries the field path and the bound;
   what the caller reads carries neither. A quoted value could itself be credential-shaped, and
   the framework's gate on the refusing node's own result would then raise and discard the
   refusal delta, clearing included.

### Injection screening

The gate screens two families over the whole parsed payload, depth first, keys included:

- **directive phrases** — instruction override, persona override, system-prompt probes,
  script markup — matched with both ends anchored so ordinary engagement wording is not
  refused;
- **chat-template control tokens** — `<|…|>`, `[INST]`, `<<SYS>>` — matched structurally,
  because an attack need not use a phrase at all.

Each string is screened **raw and again markup-stripped**: control tokens exist only in the
raw form, and stripping markup can splice a directive back together out of fragments a raw
scan would not match. Screening runs after JSON decoding, so a `\u`-escaped payload is
screened in its decoded form.

### How a refusal ends

A refusal never carries out the request and never produces a report. What differs is how the
run reports it, and that is settled by one question: can the caller act on the finding?

**A value the caller can correct ends the run by completing.** An empty request, an oversized
request, a label outside the inert alphabet, an unsupported field, a non-finite or out-of-range
figure — each returns a success status carrying an `error_code` from a closed set
(`EMPTY_INPUT`, `QUESTION_TOO_LONG`, `INVALID_REQUEST`) and one fixed sentence from
`src/services/failure_message.py`. Terminating instead would end the calling surface's turn and
surface only an exception type, leaving the reason reachable solely from the audit trail;
completing lets the caller fix the value and send the request again on the same conversation.
The sentence names *what to correct* and nothing else — it never echoes the rejected value,
names an internal field path or quotes a gate message. Those stay in `error_log`.

**A refusal the caller cannot reword their way past still terminates**, with an error status and
no reason code. That covers the injection screen — spliced instructions and chat-template
control tokens are not a formatting mistake to correct — the credential and staff-identifier
layers at the output boundary, and the upstream invariants the inner workflow guards: a
validated request that cannot be read, a missing structured model, a missing narrative, a figure
the derivation cannot read. Completing those would make a refusal read like an ordinary declined
request, and the containment paths below depend on the error status to stop the run rather than
dress it up.

`error_code` is a state field, not an envelope field: it is deliberately not surfaced in what
the framework returns to the caller. It exists to carry the decision from the node that settled
it to the nodes after it, and the reason reaches the caller through the sentence alone.

Once the code is set, the nodes after it do no work and pass it on. The subgraph node skips the
inner workflow, because running it would only reach the first domain node, fail that node's own
precondition and terminate the run — replacing a specific, actionable reason with a vaguer one.
The output boundary then has no report to screen, so it re-publishes the sentence instead;
screening an absent report would find nothing and release a **falsy** `formatted_output`, which
is precisely the fallback the output boundary exists to keep shut.

## Runtime configuration

`config/agent.yaml` is the static manifest, read at root level. `config/config.yaml` holds
the runtime parameters; the registry loads it and passes it to the graph constructor, and the
standalone entry point reads it the same way, so both deployments run on one declaration.

| Key | Bounds | Effect |
|---|---|---|
| `max_retry` | non-negative integer | read by the framework backbone |
| `timeout_s` | seconds | framework-level; this pipeline makes no blocking call |
| `analysis.target_utilization` | 0 – 1 | the target used when a request omits `kpis.target_utilization`; changes the "exceeds / falls short of the target by N points" sentence |
| `report.pipeline_health_threshold` | 0 – 100 | coverage ratio at or above which the commentary reads "healthy" rather than "moderate" |
| `report.max_visualizations` | 1 – 10 | cap on rendered visualisation recommendations |

Each value is validated once in `src/services/runtime_settings.py` — type, finiteness and
range — and an out-of-contract value keeps the built-in floor rather than reaching a consumer
that could not use it. Validated settings are forwarded to the inner graph through the
subgraph node's parent config, and the inner graph republishes them into inner state, which
is the only channel the node contract offers. `tests/integration/test_invoke_e2e.py` proves
each declared value reaches the rendered report.

## Data flow

```
input_context / input
  -> InputValidateNode
       validates every field, screens the payload, writes:
         validated_input   canonical JSON of the validated request
         enriched_context  request context for the audit record
       on a correctable refusal instead: error_code + the caller-facing sentence
  -> ReportGenerationGraphNode
       execute         skips the inner workflow when error_code is set
       extract_input   forwards validated_input across the graph boundary
       inner graph:
         StructureDataNode      -> structured_data
         GenerateNarrativeNode  -> narrative, visualization_descriptions
         FormatReportNode       -> formatted_report, result, report_metadata
       merge_output    publishes the report ONLY on a successful inner status
  -> OutputFormatNode
       re-publishes the sentence when error_code is set; otherwise screens the
       report and releases it as formatted_output, or withholds it
  -> FinalizeNode
```

## State

`src/schemas/state.py` extends the framework state with flat, serialisable fields. Dict and
list values are stored as JSON strings through `to_json()` / `from_json()`, because the
checkpointer serialises state with msgpack and structured objects do not survive it.

| Field | Written by | Carries report text |
|---|---|---|
| `runtime_settings` | the graph, before the first node | no |
| `structured_data` | `StructureDataNode` | yes |
| `narrative` | `GenerateNarrativeNode` | yes |
| `visualization_descriptions` | `GenerateNarrativeNode` | yes |
| `formatted_report` | `FormatReportNode` | yes |
| `report_metadata` | `FormatReportNode` | yes |
| `error_code` | `InputValidateNode`, passed on downstream | no |

`error_code` is the one domain field that carries no caller content — a closed set of reason
codes and nothing else. That is why it must **not** join the inventory the output boundary
clears on a violation: clearing it would erase the reason the run completed on.

## Output boundary

The framework resolves an agent's output as `formatted_output or result`, **with no status
check**. Three consequences shape this design:

- a gate that returns an error while leaving `result` in place still ships the un-gated report
  inside the error envelope;
- a gate that merely raises is worse: the node wrapper turns the exception into a bare error
  update that clears nothing and carries a traceback;
- a **falsy** `formatted_output` (`""`, `{}`, an absent key) re-arms the fallback, so
  "withheld" cannot be expressed as an empty string.

`OutputFormatNode` therefore screens inside `execute()` and, on a violation, returns an error
status, a **truthy** withheld notice, and every report-bearing field cleared **by presence** —
the key appears in the returned delta with an empty value. Omitting a key is not clearing it:
state updates are merged, so an omitted key keeps whatever it held before. A withheld output is
a refusal the caller cannot reword their way past, so it terminates: the error status is what
stops the run instead of shipping a run that reads as an ordinary declined request.

Before it screens anything, the node checks `error_code`. A run the caller-contract gate
declined has no report to screen, and screening an empty one finds nothing and releases a falsy
`formatted_output` — the third consequence above. On that path the node re-publishes the
caller-facing sentence and completes.

Two independent layers, each with its own audit event:

1. **Credential scan**, delegated to the framework's own detector rather than a local pattern
   list. A local list narrower than the framework's is not a smaller guarantee, it is a
   bypass: a value this node lets through is refused by the framework's gate on this node's
   result, and the wrapper then discards this node's delta — including its clearing.
2. **Staff and client identifier scan**, the domain layer. Performance data routinely carries
   employee and consultant references; the report must not.

`ReportGenerationGraphNode` contains the other half. Its `merge_output` publishes the report
only on a successful inner status, and `on_subgraph_error` turns an inner failure into a
contained refusal — report fields cleared, a fixed sentence in place of the inner error text,
which can carry a traceback and absolute source paths.

### Output invariant

Every figure the report renders is a validated caller figure or a KPI derived from one,
rendered at its natural precision. The rounding grid other document templates apply to
derived monetary aggregates is deliberately **not** applied here: this template reports a
firm's own stated revenue back to it, and rounding a stated figure would make the deliverable
wrong rather than safer. The invariant that *is* enforced is inertness — no caller free text
reaches the report, because the contract admits labels only over `[a-z0-9_]`.

## Framework facilities used

- `AgentBaseGraph` — the outer backbone; `add_edges()` is never overridden.
- `BaseGraph` — the inner workflow's custom topology, with `_extra_initial_state()`
  publishing the validated settings.
- `GraphNode` — the subgraph boundary, with `error_strategy = "handle"` so an inner failure
  is contained rather than re-raised.
- `FunctionNode` — every domain node; the `@final` input and output gates are never
  overridden, and domain checks live in `execute()`.
- `detect_credentials_in_value` — the framework's credential detector, called by both the
  entry point and the output boundary so neither can drift from it.
- `emit_trace_event` — a domain audit event on every node boundary; payloads carry counts,
  field names and reason codes, never report content or caller values.

## Design decisions

| Decision | Alternative | Why |
|---|---|---|
| Labels locked to `[a-z0-9_]` | free-text firm and period names | free text in a rendered document is caller-controlled output injection; it is also what the framework's input mask rewrites, which would silently corrupt the label the report is about |
| Deterministic composition | a generative narrative step | a figure in a performance report must trace to a figure that went in, and the same request must produce the same report twice |
| Settings republished into inner state | reading config inside each node | the node contract takes no config argument; state is the only channel, and one validated copy avoids a second set of defaults on disk |
| `error_strategy = "handle"` | the framework default, `"propagate"` | re-raising loses containment — the wrapper produces a bare error update that clears nothing and carries a traceback |
| A correctable refusal completes, carrying a reason code | terminating on every refusal alike | an exception type ends the calling surface's turn and leaves the reason reachable only from the audit trail; completing lets the caller correct one value and resend on the same conversation |
| An uncorrectable refusal still terminates | completing there too, for one uniform shape | spliced instructions, a withheld output and a broken upstream invariant are not declined requests, and the containment paths depend on the error status |
| Bearer boundary in the entry point | relying on upstream middleware | nothing sets caller trust in a standalone deployment, so without it every request arrives anonymous and the trust gate refuses it |
| Framework detector in the output gate | a local credential pattern list | a narrower local set is a containment bypass, not a smaller guarantee |
