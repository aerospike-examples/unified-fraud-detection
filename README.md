# Fraud Detection Application

A real-time, graph-based fraud detection system — FastAPI backend, Next.js frontend, and **Aerospike Graph** for the data layer — with an **agentic investigation layer built on [Google ADK](https://google.github.io/adk-docs/)** that autonomously investigates flagged accounts and can take gated remediation actions.

This project is a reference for two things:

1. **Aerospike Graph** for real-time fraud signals (flagged-account / flagged-device traversals, fraud-ring detection).
2. **Google ADK as the agentic layer**, with **Aerospike as the ADK backing store** — showing how an AI agent reasons over graph + KV data, remembers past cases, persists its work, and keeps a human in control of consequential actions.

## 🚀 Quick Start

1. Copy the sample env file and add your Gemini API key:
   ```bash
   cp .env.sample .env
   ```
   Then edit `.env`:
   ```
   GOOGLE_API_KEY=your-gemini-api-key-here   # from https://aistudio.google.com/apikey
   GOOGLE_GENAI_USE_VERTEXAI=FALSE           # use the Gemini Developer API
   ADK_MODEL=gemini-3.5-flash                # model the ADK agent uses
   ```

2. Start all services:
   ```bash
   docker compose up -d
   ```

3. **Open the dashboard:** http://localhost:8081 — pick a flagged account and run an investigation to watch the ADK agent work.

## 🏗️ Architecture

```
┌──────────────┐     ┌─────────────────────────────┐     ┌────────────────────┐     ┌───────────────┐
│   Frontend   │────▶│          Backend            │────▶│  Aerospike Graph   │────▶│  Aerospike DB │
│   :8081      │ SSE │  FastAPI :4000              │     │  Service :8182     │     │  :3000        │
└──────────────┘     │                             │     │  (Gremlin)         │     │  (KV + Graph) │
                     │   ┌─────────────────────┐   │     └────────────────────┘     └───────▲───────┘
                     │   │  ADK Agentic Layer  │   │                                         │
                     │   │  (Google ADK)       │   │     ADK Session / Memory / Artifact     │
                     │   │  investigator ─▶    │   │     services persisted to Aerospike ────┘
                     │   │  report_writer      │   │            (adk-aerospike)
                     │   └──────────┬──────────┘   │
                     └──────────────┼──────────────┘
                                    │
                              ┌─────▼─────┐          ┌────────────┐
                              │  Gemini   │          │   Zipkin   │
                              │  API      │          │   :9411    │
                              └───────────┘          └────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Aerospike   │────▶│  Prometheus  │────▶│   Grafana    │
│  Exporter    │     │  :9091       │     │   :3030      │
└──────────────┘     └──────────────┘     └──────────────┘
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| **Frontend** | 8081 | Next.js dashboard for exploring users, transactions, fraud patterns, and live agent investigations |
| **Backend** | 4000 | FastAPI server — fraud detection, Gremlin queries, and the ADK agentic layer |
| **Generator** | 4001 | Synthetic data generator for seeding the graph |
| **Aerospike DB** | 3000 | Key-value and graph data store (also the ADK backing store) |
| **Aerospike Graph Service** | 8182 | Gremlin-compatible graph query engine on top of Aerospike |
| **Zipkin** | 9411 | Distributed tracing for graph query performance |
| **Aerospike Exporter** | 9145 | Prometheus metrics exporter for Aerospike |
| **Prometheus** | 9091 | Metrics collection and storage |
| **Grafana** | 3030 | Monitoring dashboards (default login `admin`/`admin`) |

## 🤖 Agentic Layer — Google ADK

### What is ADK?

The **[Agent Development Kit (ADK)](https://google.github.io/adk-docs/)** is Google's open-source framework for building production LLM agents. It provides the building blocks an agent needs beyond a raw model call: a **Runner** that drives the reason→act loop and streams **Events**, **tools** the model can call, **multi-agent composition** (sequential / parallel / hierarchical), pluggable **Session / Memory / Artifact** services for state and persistence, **plugins & callbacks** for cross-cutting concerns (metrics, guardrails), and first-class **human-in-the-loop** primitives. ADK is model-flexible; this demo runs it on **Gemini** (`gemini-3.5-flash`).

### How it's wired into this demo

When a flagged account is investigated, the request streams (SSE) from the frontend into the backend, which runs an ADK agent over the account's graph + KV data and translates ADK's `Event` stream back into the UI's progress contract.

The agent is a two-stage **`SequentialAgent`** (`backend/workflow/agent.py`):

```
SequentialAgent  "fraud_investigation"
  ├─ investigator   — tool-using LlmAgent (ReAct): gathers evidence, decides, enacts
  └─ report_writer  — LlmAgent: drafts the markdown investigation report
```

Two deterministic pre-steps (`alert_validation`, `data_collection`) seed the ADK session state from fast KV reads before the LLM agent starts, so the model begins with baseline context instead of spending tool calls on it. The `Runner` and services are built once in `InvestigationRunner` (`backend/workflow/runner.py`).

### Aerospike ⇄ ADK integration

The standout integration: **Aerospike is the ADK backing store.** Via the [`adk-aerospike`](https://pypi.org/project/adk-aerospike/) package, ADK's three persistence interfaces are implemented on Aerospike and **reuse the application's existing Aerospike client** (no second connection):

```python
# backend/workflow/runner.py
client    = aerospike_service.client
namespace = aerospike_service.namespace

self.session_service  = AerospikeSessionService(client, namespace)   # conversation + state
self.memory_service   = AerospikeMemoryService(client, namespace)    # long-term, searchable
self.artifact_service = AerospikeArtifactService(client, namespace)  # files (reports)

self.runner = Runner(
    app_name="fraud_investigation",
    agent=build_investigation_agent(model),
    session_service=self.session_service,
    memory_service=self.memory_service,
    artifact_service=self.artifact_service,
    plugins=[MetricsPlugin()],
)
```

| ADK service | Aerospike role | Used in the demo for |
|-------------|----------------|----------------------|
| **SessionService** | Sessions + per-app/user/session state | The live investigation's evidence, tool log, assessment, and enacted actions |
| **MemoryService** | Durable, searchable memory | Recalling similar past investigations across cases (`recall_similar_investigations`) |
| **ArtifactService** | Binary/text artifacts | Persisting the final markdown report (`investigation_report.md`) |

This means the agent's entire footprint — its working state, its long-term memory, and its output artifacts — lives in the **same Aerospike cluster** that powers the fraud graph. One datastore, one client, one operational surface.

> Dependencies: `google-adk>=1.35,<2.0`, `adk-aerospike>=0.0.2` (`backend/requirements.txt`).

### ADK capabilities showcased

| ADK capability | How the demo uses it | Where |
|----------------|----------------------|-------|
| **Tool-using ReAct agent** | The `investigator` LlmAgent calls evidence tools one step at a time and reasons over each result | `agent.py`, `tools/investigation_tools_adk.py` |
| **Multi-agent pipeline** | `SequentialAgent` chains `investigator → report_writer` | `agent.py` |
| **Session + state** | Deterministic pre-steps seed state; tools and the agent read/write it | `runner.py`, `nodes/` |
| **Long-term memory** | `recall_similar_investigations` calls `tool_context.search_memory(...)` to surface prior cases | `tools/investigation_tools_adk.py` |
| **Artifacts** | The report is saved with `artifact_service.save_artifact(...)` and the session is added to memory on completion | `runner.py` |
| **Plugins & callbacks** | `MetricsPlugin` (a `BasePlugin`) collects timing/DB/LLM/token metrics via callbacks and enforces a per-run tool-call budget in `before_tool_callback` | `plugins.py` |
| **Human-in-the-loop tool confirmation** | Destructive remediation actions pause the agent for analyst approval via ADK's native `request_confirmation` — see below | `action_tools.py`, `runner.py` |
| **Event-stream → SSE** | The runner translates ADK's `Event` stream (function calls, partials, completions) into the frontend's existing SSE progress contract | `runner.py` |

The investigator's tool belt (`INVESTIGATION_TOOLS`) wraps the same Gremlin/KV engine the rest of the app uses:
`get_account_transactions`, `get_counterparty_profile`, `get_counterparty_transactions`, `get_account_risk_features`, `get_device_risk_features`, `detect_fraud_ring`, `get_transaction_network`, `recall_similar_investigations`, and the exit tool `submit_assessment`.

### Feature spotlight: Human-in-the-loop remediation actions

The agent doesn't just *recommend* a decision — it can **take action** on the flagged account, with destructive actions gated behind a human. This uses ADK's native tool-confirmation primitive (`tool_context.request_confirmation` / the `adk_request_confirmation` flow).

After `submit_assessment`, the agent calls **`enact_decision(decision, account_id, reason)`**:

- **Non-destructive** (`allow_monitor`, `step_up_auth`) → executes immediately.
- **Destructive** (`temporary_freeze`, `full_block`, `escalate_compliance`) → the tool calls `request_confirmation(...)` and the run **pauses**. The backend emits an `action_confirmation_required` event; the analyst sees an inline approve/reject card.
  - **Approve** → the run resumes (`GET /investigation/{id}/resume?approved=true`), the agent's confirmation is answered, and the action is enforced through the existing `flagged_account_service.resolve_account` path — the account is marked fraudulent and its devices flagged. The enacted action is recorded in session state and shown in the UI.
  - **Reject** → no enforcement; the investigation still completes with a full report.

```
investigator ─▶ submit_assessment ─▶ enact_decision
                                          │
                          destructive? ───┴─── non-destructive
                              │                      │
                     request_confirmation       execute now
                       (run PAUSES)                  │
                              │                      │
                   analyst approves / rejects        │
                              │                      │
                       enforce / skip ───────────────┴─▶ report_writer ─▶ done
```

This keeps the agent useful (it closes the loop on its own findings) while ensuring a human authorizes anything with real consequences.

## 🧭 ADK Roadmap / Ideas to Showcase

This demo is intended to grow into a showcase of what ADK can do as a fraud-investigation agentic layer. Candidate additions (not yet implemented):

- **Parallel evidence gathering** — a `ParallelAgent` fan-out for independent evidence pulls (network, device, velocity) before synthesis.
- **Guardrail callbacks** — input/output guardrails via plugin callbacks (PII redaction, action-policy enforcement beyond the budget).
- **Streaming partial reasoning** — surface the agent's intermediate thoughts/tokens live in the UI.
- **Evaluation harness** — ADK eval sets to score investigation quality/consistency across known cases.
- **Richer artifacts** — attach evidence graphs/exports alongside the markdown report.
- **Escalation sub-agents** — specialist agents (e.g. AML, sanctions) the investigator can transfer to.

> Adding a capability? Wire it in `backend/workflow/`, then add a row to the **ADK capabilities showcased** table above (and a spotlight section if it's user-facing).

## 🕵️ Fraud Detection System

The system implements real-time fraud detection using graph-based analysis:

### RT1 - Flagged Account Detection
- **Purpose**: Detects transactions involving previously flagged accounts
- **Method**: 1-hop graph lookup for immediate threat detection
- **Risk Level**: High
- **Use Cases**: Known fraudster connections, blacklisted accounts

### RT2 - Flagged Device Connection
- **Purpose**: Detects transactions involving accounts connected to flagged devices
- **Method**: Network analysis through transaction history
- **Risk Level**: High
- **Use Cases**: Device-based fraud networks, shared device abuse

### RT3 - Supernode Detection (Future)
- **Purpose**: Identifies accounts with unusually high connectivity
- **Method**: Graph centrality analysis
- **Risk Level**: Medium-High
- **Use Cases**: Money laundering hubs, distribution networks

## 📚 Documentation

- **[Data Model](./docs/datamodel.md)** - Detailed data structure documentation

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
