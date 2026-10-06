# Professional Services Performance Report Agent

AI agent for generating professional services performance reports, built with Agentic Star.

> **Category**: Cat 2 (domain-specific document-generation pipeline)
> **Industry**: Services
> **Template ID**: SVC-C2-007

## Overview

Turns a period's engagement figures into a partner-ready performance report. You submit the
utilisation rate, revenue, client-satisfaction score, headcount and pipeline for one reporting
period; the agent derives the KPIs a partner actually asks about — utilisation against target,
revenue per consultant, pipeline coverage — writes the executive summary and trend commentary
around them, recommends the charts that carry the story, and returns the whole thing as Markdown.

Composition is **deterministic**: every sentence and every table cell is assembled from a figure
the caller supplied or a KPI derived from one. That is the point of the design. A performance
report is circulated to people who will make staffing and pricing decisions from it, so a number
in it has to be traceable to a number that went in, and the same request has to produce the same
report twice.

Everything the caller sends is treated as hostile until it is proven bounded. Figures must be
finite and inside a declared range — a `NaN` utilisation rate is refused rather than rendered as
`nan%` — and the two labels that appear in the report text are locked to a plain
`[a-z0-9_]` identifier, because free text there would be caller-controlled output injection.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | 3.11 or later |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode: the agent imports the framework at start-up, and without it start-up fails rather than the
agent coming up in a partially working state. That is intentional — a half-running agent is worse
than one that refuses to start.

There is no stub or offline answer path either. A performance report that looks real and
describes no actual engagement is exactly the failure a reader cannot detect.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Sending a request

`POST /invoke` takes the figures on `input_context`. The same object may be sent as a JSON
document in `input` instead, and goes through the identical validation.

```json
{
  "session_id": "q1-review",
  "input_context": {
    "firm_name": "acme_consulting_jp",
    "report_period": "q1_2026",
    "engagement_metrics": {
      "utilization_rate": 0.82,
      "revenue_usd": 1250000,
      "client_satisfaction_score": 4.2,
      "active_engagements": 15,
      "consultant_count": 12,
      "partner_count": 3
    },
    "kpis": {
      "target_utilization": 0.85,
      "billable_hours": 3840,
      "pipeline_value": 2500000
    }
  }
}
```

Every field is optional except at least one engagement metric. Unrecognised fields are refused
rather than ignored, and a refusal names the field and its bounds, never the value that failed.
`docs/02_design.md` documents the full contract.

In a standalone deployment nothing upstream establishes caller trust, so set
`INVOKE_AUTH_TOKEN` on the server and send it as `Authorization: Bearer <token>`. Behind a
gateway that already authenticates the caller, that variable is left unset and the established
trust is used as is.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent manifest and runtime parameters
docs/         design and operational documentation
```

`docs/02_design.md` covers the architecture and the caller contract;
`docs/03_test_spec.md` covers what is tested and how.

## Customising

1. Adjust `config/config.yaml` for your own reporting conventions — the utilisation target used
   when a request omits one, the pipeline-coverage ratio that counts as healthy, and how many
   visualisation recommendations the report carries.
2. Extend the metric contract in `src/nodes/pre_process_node.py` with the figures your practice
   tracks, each with its own bounds.
3. Adapt the narrative and table composition in `src/nodes/` to your report format.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
