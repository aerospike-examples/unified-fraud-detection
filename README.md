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
                     │   │  parallel evidence  │   │     services persisted to Aerospike ────┘
                     │   │   (3 specialists)   │   │            (adk-aerospike)
                     │   │  ─▶ investigator    │   │
                     │   │  ─▶ report_writer   │   │
                     │   │  ─▶ action_taker    │   │
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

## 🤖 Agentic Layer — ADK or LangGraph

The investigation backend exposes **two interchangeable engines** behind a common interface (`backend/workflow/engines/`). Both emit the same SSE event contract, so the frontend works unchanged.

| Setting | Values | Default |
|---------|--------|---------|
| `INVESTIGATION_ENGINE` | `adk`, `langgraph` | `adk` |
| `LLM_PROVIDER` | `gemini`, `ollama` | `gemini` |

- **`adk`** — Google ADK `SequentialAgent` + `Runner` (`backend/workflow/runner.py`, `agent.py`). Uses Aerospike for ADK session/memory/artifact services.
- **`langgraph`** — LangGraph `StateGraph` with the same pipeline (parallel specialists → investigator → report → gated action) and feature parity: cross-case memory, HITL approval, disposition overrides, specialist findings, and enacted actions (`backend/workflow/graph.py`).

Set in `.env` before starting the backend:

```bash
INVESTIGATION_ENGINE=adk          # or langgraph
LLM_PROVIDER=gemini               # or ollama (OLLAMA_BASE_URL, OLLAMA_MODEL)
ADK_MODEL=gemini-3.5-flash
```

## 🤖 Agentic Layer — Google ADK (default engine)

### What is ADK?

The **[Agent Development Kit (ADK)](https://google.github.io/adk-docs/)** is Google's open-source framework for building production LLM agents. It provides the building blocks an agent needs beyond a raw model call: a **Runner** that drives the reason→act loop and streams **Events**, **tools** the model can call, **multi-agent composition** (sequential / parallel / hierarchical), pluggable **Session / Memory / Artifact** services for state and persistence, **plugins & callbacks** for cross-cutting concerns (metrics, guardrails), and first-class **human-in-the-loop** primitives. ADK is model-flexible; this demo runs it on **Gemini** (`gemini-3.5-flash`).

### How it's wired into this demo

When a flagged account is investigated, the request streams (SSE) from the frontend into the backend, which runs an ADK agent over the account's graph + KV data and translates ADK's `Event` stream back into the UI's progress contract.

The agent is a four-stage **`SequentialAgent`** whose first stage is itself a **`ParallelAgent`** (`backend/workflow/agent.py`):

```
SequentialAgent  "fraud_investigation"
  ├─ evidence_collection  (ParallelAgent) — three specialists run CONCURRENTLY:
  │     ├─ network_analyst    → fraud rings, counterparties, mule chains
  │     ├─ device_analyst     → device sharing, spoofing, infra risk
  │     └─ velocity_analyst   → velocity, bursts, amount anomalies
  ├─ investigator   — tool-using LlmAgent (ReAct): SYNTHESIZES the specialist
  │     findings, drills into gaps, and submits the assessment (does NOT enact)
  ├─ report_writer  — LlmAgent: drafts the markdown investigation report
  └─ action_taker   — LlmAgent: enacts the decision; destructive actions PAUSE
        here for analyst approval (so the report is already written and on screen
        before the analyst approves) — see the spotlight below
```

Each specialist writes a findings summary to session state via its `output_key`; the investigator reads all three and reaches a decision in 0–3 tool calls instead of gathering everything itself. The **report is written before the decision is enacted** so the analyst reviews the full report before approving. Deterministic pre-steps (`alert_validation`, `data_collection`, then a **memory recall** of related prior cases) seed the ADK session state from fast KV reads before the agent starts, so the model begins with baseline context + relevant history instead of spending tool calls on it. The `Runner` and services are built once in `InvestigationRunner` (`backend/workflow/runner.py`).

### Aerospike ⇄ ADK integration

The standout integration: **Aerospike is the ADK backing store.** Via the [`adk-aerospike`](https://pypi.org/project/adk-aerospike/) package, ADK's three persistence interfaces are implemented on Aerospike and **reuse the application's existing Aerospike client** (no second connection):

```python
# backend/workflow/runner.py
client    = aerospike_service.client
namespace = aerospike_service.namespace

self.session_service  = AerospikeSessionService(client, namespace)   # conversation + state
self.memory_service   = get_memory_service(aerospike_service)        # case_memory set (both engines)
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
| **SessionService** | Sessions + per-app/user/session state | The live investigation's evidence, parallel specialist findings, tool log, assessment, and enacted actions |
| **MemoryService** | Durable, searchable memory (`case_memory` set) | Cross-case recall — every investigation is stored and related prior cases are recalled by entity (see the spotlight); shared by ADK and LangGraph |
| **ArtifactService** | Binary/text artifacts | Persisting the final markdown report (`investigation_report.md`) |

This means the agent's entire footprint — its working state, its long-term memory, and its output artifacts — lives in the **same Aerospike cluster** that powers the fraud graph. One datastore, one client, one operational surface.

> Dependencies: `google-adk>=1.35,<2.0`, `adk-aerospike>=0.0.2` (`backend/requirements.txt`).

### ADK capabilities showcased

| ADK capability | How the demo uses it | Where |
|----------------|----------------------|-------|
| **Tool-using ReAct agent** | The `investigator` LlmAgent calls evidence tools one step at a time and reasons over each result | `agent.py`, `tools/investigation_tools_adk.py` |
| **Sequential pipeline** | `SequentialAgent` chains `evidence_collection → investigator → report_writer → action_taker` (assess → report → enact, in that order) | `agent.py` |
| **Parallel multi-agent** | `ParallelAgent` runs three evidence specialists (network / device / velocity) concurrently, each writing findings via `output_key` — see below | `agent.py`, `runner.py` |
| **Session + state** | Deterministic pre-steps seed state; tools and agents read/write it (specialists write per-agent keys to stay race-free under fan-out) | `runner.py`, `plugins.py`, `nodes/` |
| **Long-term memory** | Every completed investigation is stored to ADK memory; a new one recalls **related prior cases by entity** (account / device / counterparty) before the agent runs — see below | `case_memory.py`, `runner.py` |
| **Artifacts** | The report is saved with `artifact_service.save_artifact(...)` and the session is added to memory on completion | `runner.py` |
| **Plugins & callbacks** | `MetricsPlugin` (a `BasePlugin`) collects timing/DB/LLM/token metrics via callbacks and enforces a per-run tool-call budget in `before_tool_callback` | `plugins.py` |
| **Human-in-the-loop tool confirmation** | The `action_taker` stage pauses for analyst approval on destructive actions via ADK's native `request_confirmation`; the analyst can approve or override with a different disposition — see below | `action_tools.py`, `runner.py` |
| **Event-stream → SSE** | The runner translates ADK's `Event` stream (function calls, partials, completions) into the frontend's existing SSE progress contract | `runner.py` |

The investigator's tool belt (`INVESTIGATION_TOOLS`) wraps the same Gremlin/KV engine the rest of the app uses:
`get_account_transactions`, `get_counterparty_profile`, `get_counterparty_transactions`, `get_account_risk_features`, `get_device_risk_features`, `detect_fraud_ring`, `get_transaction_network`, `recall_similar_investigations`, and the exit tool `submit_assessment` (which records the typology, risk, decision, and the primary `account_id`). Enacting the decision is a separate tool, `enact_decision`, owned by the `action_taker` stage.

### Feature spotlight: Parallel evidence collection

The investigation opens with an ADK **`ParallelAgent`** stage: three specialist agents investigate the flagged account **concurrently**, each scoped to one domain and one slice of the tool belt:

| Specialist | Looks at | Tools |
|------------|----------|-------|
| `network_analyst` | counterparties, fan-out/fan-in, fraud rings, mule chains | `detect_fraud_ring`, `get_transaction_network`, `get_counterparty_*` |
| `device_analyst` | device sharing, spoofing, infrastructure risk | `get_device_risk_features`, `get_account_risk_features` |
| `velocity_analyst` | velocity, bursts, amount anomalies, new-recipient ratio | `get_account_transactions`, `get_account_risk_features` |

Each writes a findings summary to session state via its `output_key`; the `investigator` then **synthesizes** all three rather than re-gathering, typically deciding in 0–3 tool calls. The UI shows the three lanes lighting up live (Investigation tab → *Parallel Evidence Collection*).

Two correctness details worth noting, since fan-out shares one session:
- The tool-call **budget is enforced only on the investigator** — specialists are bounded by their own prompts; a shared counter would race.
- Specialist tool calls accumulate into **per-agent state keys** (merged at finalize), because ADK merges state deltas per key and concurrent appends to one shared list would drop entries.

```
                    ┌──────────────────────────────────────┐
 evidence_collection│  network_analyst  ─┐                  │
 (ParallelAgent,    │  device_analyst   ─┼─▶ findings ─▶    │ investigator ─▶ report_writer ─▶ action_taker
  concurrent)       │  velocity_analyst ─┘   (state)        │ (synthesis)      (report)        (enact)
                    └──────────────────────────────────────┘
```

### Feature spotlight: Cross-case memory

Cross-case memory (`AerospikeMemoryService` in the **`case_memory`** Aerospike set) is a **fraud-intelligence layer shared by both investigation engines**. Every completed investigation is written to memory as a compact, entity-indexed case record (the account, its devices, the counterparties it touched, the typology, and the decision). When a new account is investigated, a **memory-recall pre-step** (before the agent runs) surfaces prior cases that referenced any of the suspect's entities — most usefully, cases where **this account appeared as a counterparty**:

> *"John Garcia was a counterparty in 2 prior confirmed-fraud investigations (Timothy Jones, Christopher Martinez — fraud_ring)."*

The recalled cases are shown in the **Related Prior Cases** panel and injected into the investigator's prompt, so the agent reasons with relevant history. Case memory lives in Aerospike (`case_memory` set) and accumulates across investigations until a full **Delete all data** reset.

One implementation detail worth calling out: the `adk-aerospike` memory index tokenizes on `[A-Za-z]+` (it drops digits), so raw ids like `U0007387` collapse to `u` and match everything. We encode each id's digits to letters so every entity becomes a **unique alphabetic token** that survives the tokenizer and matches precisely — and pool all cases under one shared memory scope (memory is keyed by `app_name + user_id`) so recall works across accounts.

```
investigate account B ──▶ memory recall (entities of B)
                                 │  search case_memory (AerospikeMemoryService)
                                 ▼
                    prior cases referencing B's account/devices,
                    or where B was a counterparty  ──▶ Related Prior Cases panel
                                                       + investigator prompt
```

### Feature spotlight: Human-in-the-loop remediation actions

The agent doesn't just *recommend* a decision — it can **take action** on the flagged account, with destructive actions gated behind a human. This uses ADK's native tool-confirmation primitive (`tool_context.request_confirmation` / the `adk_request_confirmation` flow).

The decision is enacted by the **`action_taker`** stage, which runs **after** `report_writer` — so the full report exists before anyone is asked to approve. `action_taker` calls **`enact_decision(decision, account_id, reason)`**:

- **Non-destructive** (`allow_monitor`, `step_up_auth`) → enforced immediately (the account moves to `monitoring`, leaving the pending queue).
- **Destructive** (`temporary_freeze`, `full_block`, `escalate_compliance`) → the tool calls `request_confirmation(...)` and the run **pauses**. The backend pushes the finished report + assessment, then emits `action_confirmation_required`. The UI opens a **"Review report & decide" modal** (the full report, scrollable, with the controls pinned at the bottom):
  - **Approve & Enact** → the run resumes (`GET /investigation/{id}/resume?approved=true`) and the agent's recommended action is enforced.
  - **Set a different disposition** (override) → resumes with `?approved=false&override=<disposition>`; `action_taker` declines its own recommendation and the analyst's chosen disposition is enacted instead. So a rejected alert is never left dead-ended — the analyst always resolves it.

The enacted action is recorded in session state and shown in the UI ("Actions Taken"), and the flagged account's status updates accordingly.

**Dispositions** (`backend/workflow/action_tools.py`). Note **Full Block *is* "confirm fraud"** — one outcome, not two:

| Disposition | Status | Effect | Who can choose it |
|-------------|--------|--------|-------------------|
| `allow_monitor` / `step_up_auth` | `monitoring` | allowed, kept under watch (not fraud) — `mark_monitoring` | agent or analyst |
| `temporary_freeze` | `temporarily_frozen` | reversible hold — `frozen` flag, **not** fraud, no devices flagged — `freeze_account` | agent or analyst |
| `full_block` (= confirm fraud) | `confirmed_fraud` | `fraud_flag` set + devices flagged — `resolve_account` | agent or analyst |
| `escalate_compliance` | `under_investigation` | case moved to compliance review | agent or analyst |
| `clear` | `cleared` | alert dismissed — account marked safe (not fraud) | **analyst only** (the agent never clears) |

```
investigator ─▶ submit_assessment ──▶ report_writer ──▶ action_taker ─▶ enact_decision
 (assess only)   (report written first)                                       │
                                                       destructive? ──────────┴────── non-destructive
                                                            │                              │
                                                  request_confirmation                enforce now
                                                    (run PAUSES,                    (→ monitoring)
                                                 report on screen)
                                                            │
                                            ┌───── analyst decides ──────┐
                                            │                            │
                                   Approve & Enact          Set a different disposition
                                   (agent's action)         (override: clear / monitor /
                                            │                 freeze / escalate)
                                            └────────────┬───────────────┘
                                                         ▼
                                                  enforce + resolve
```

This keeps the agent useful (it closes the loop on its own findings) while ensuring a human authorizes anything with real consequences — and always reaches a resolution.

## 🧭 ADK Roadmap / Ideas to Showcase

This demo is intended to grow into a showcase of what ADK can do as a fraud-investigation agentic layer. Already shipped: **parallel multi-agent evidence collection**, **cross-case long-term memory**, and **human-in-the-loop tool confirmation** (see the spotlights above). Candidate additions (not yet implemented):

- **Escalation sub-agents** — specialist agents (e.g. AML, sanctions) the investigator can *transfer to* dynamically, showcasing ADK's hierarchical/agent-transfer routing (vs the current static sequence).
- **Guardrail callbacks** — input/output guardrails via plugin callbacks (PII redaction, action-policy enforcement beyond the budget).
- **Streaming partial reasoning** — surface the agent's intermediate thoughts/tokens live in the UI.
- **Evaluation harness** — ADK eval sets to score investigation quality/consistency across known cases.
- **Richer artifacts** — attach evidence graphs/exports alongside the markdown report.

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
