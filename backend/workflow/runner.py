"""
ADK Runner + SSE translator.

Replaces the old LangGraph ``graph.py``:

- ``build_runner`` constructs the ADK ``Runner`` once, backed by Aerospike for
  sessions/history, long-term memory, and report artifacts (via ``adk-aerospike``,
  reusing the app's existing Aerospike client connection).
- ``run_investigation`` runs one investigation and translates the ADK ``Event``
  stream into the SSE event dicts the frontend already understands
  (node_start / evidence / node_complete / agent_iteration / agent_thinking /
  tool_call / assessment / metrics / complete / error). The 4-step UI is
  preserved by mapping the deterministic pre-steps and the two agent stages onto
  the existing node names.
"""

import logging
import traceback
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, Optional

from google.adk.runners import Runner
from google.genai import types
from adk_aerospike import (
    AerospikeSessionService,
    AerospikeMemoryService,
    AerospikeArtifactService,
)

from workflow.agent import build_investigation_agent, APP_NAME, INVESTIGATOR_NAME, REPORT_WRITER_NAME
from workflow.plugins import MetricsPlugin
from workflow.assessment import deterministic_assessment
from workflow.nodes.report_generation import generate_fallback_report
from workflow.nodes.alert_validation import alert_validation_node
from workflow.nodes.data_collection import data_collection_node
from workflow.metrics import get_collector, remove_collector

logger = logging.getLogger('investigation.runner')

# Map ADK sub-agent names → the step ids the frontend renders.
_AGENT_TO_STEP = {
    INVESTIGATOR_NAME: "llm_agent",
    REPORT_WRITER_NAME: "report_generation",
}


class InvestigationRunner:
    """Holds the ADK Runner and Aerospike-backed services (built once)."""

    def __init__(self, aerospike_service: Any, graph_service: Any, model: str = None):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service

        client = aerospike_service.client
        namespace = aerospike_service.namespace

        # Reuse the live Aerospike client — no second connection.
        self.session_service = AerospikeSessionService(client, namespace)
        self.memory_service = AerospikeMemoryService(client, namespace)
        self.artifact_service = AerospikeArtifactService(client, namespace)

        self.agent = build_investigation_agent(model)
        self.runner = Runner(
            app_name=APP_NAME,
            agent=self.agent,
            session_service=self.session_service,
            memory_service=self.memory_service,
            artifact_service=self.artifact_service,
            plugins=[MetricsPlugin()],
        )
        logger.info("InvestigationRunner ready (Aerospike sessions/memory/artifacts)")

    def close(self):
        for svc in (self.session_service, self.memory_service, self.artifact_service):
            try:
                svc.close()
            except Exception:
                pass


def build_runner(aerospike_service: Any, graph_service: Any, model: str = None) -> InvestigationRunner:
    return InvestigationRunner(aerospike_service, graph_service, model)


def get_workflow_steps() -> list:
    """Step list for the investigation UI (unchanged 4-step contract)."""
    return [
        {
            "id": "alert_validation",
            "name": "Alert Validation",
            "description": "Extract alert trigger context from flagged account",
            "phase": "context",
        },
        {
            "id": "data_collection",
            "name": "Data Collection",
            "description": "Gather baseline profile, accounts, devices, transactions",
            "phase": "evidence",
        },
        {
            "id": "llm_agent",
            "name": "AI Investigation Agent",
            "description": "ADK agent uses tools to gather evidence and make an assessment",
            "phase": "reasoning",
        },
        {
            "id": "report_generation",
            "name": "Report Generation",
            "description": "Generate detailed investigation report",
            "phase": "report",
        },
    ]


def _trace(node: str, type_: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "trace",
        "event": {
            "type": type_,
            "node": node,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        },
    }


async def run_investigation(
    inv_runner: InvestigationRunner,
    user_id: str,
    investigation_id: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run one investigation, yielding SSE event dicts."""
    metrics = get_collector(investigation_id)
    metrics.reset()

    started_at = datetime.now().isoformat()
    logger.info(f"Starting investigation {investigation_id} for user {user_id}")

    current_step: Optional[str] = None

    def _start_step(step: str):
        nonlocal current_step
        if current_step and current_step != step:
            metrics.end_node(current_step)
        if current_step != step:
            metrics.start_node(step)
            current_step = step

    try:
        # ── Deterministic pre-steps (KV reads) → seed session state ──────────
        seed_state = {
            "user_id": user_id,
            "investigation_id": investigation_id,
            "started_at": started_at,
            "tool_calls": [],
            "agent_iterations": 0,
            "tool_calls_count": 0,
            "final_assessment": None,
            "report_markdown": "",
        }

        # 1) Alert validation
        _start_step("alert_validation")
        av = alert_validation_node({"user_id": user_id}, inv_runner.aerospike_service)
        seed_state["alert_evidence"] = av.get("alert_evidence")
        for ev in av.get("trace_events", []):
            yield {"type": "trace", "event": ev}

        # 2) Data collection
        _start_step("data_collection")
        dc = data_collection_node(
            {"user_id": user_id, "investigation_id": investigation_id, "alert_evidence": seed_state["alert_evidence"]},
            inv_runner.aerospike_service,
            inv_runner.graph_service,
        )
        seed_state["initial_evidence"] = dc.get("initial_evidence")
        for ev in dc.get("trace_events", []):
            yield {"type": "trace", "event": ev}

        # Push early evidence so the UI can render it immediately.
        yield {
            "type": "state_update",
            "data": {
                "alert_evidence": seed_state["alert_evidence"],
                "initial_evidence": seed_state["initial_evidence"],
                "current_phase": "llm_reasoning",
            },
        }

        # ── Create the ADK session with seeded state ─────────────────────────
        await inv_runner.session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=investigation_id,
            state=seed_state,
        )

        # ── Drive the agent, translate the event stream ──────────────────────
        new_message = types.Content(
            role="user",
            parts=[types.Part(text=(
                "Investigate this flagged account using the available tools, then "
                "submit your assessment and write the investigation report."
            ))],
        )

        iteration = 0
        assessment_emitted = False

        async for event in inv_runner.runner.run_async(
            user_id=user_id,
            session_id=investigation_id,
            new_message=new_message,
        ):
            step = _AGENT_TO_STEP.get(event.author)
            if step:
                # Step transition: close the previous step, open this one.
                if current_step != step:
                    if current_step:
                        yield _trace(current_step, "node_complete", {"status": "success"})
                    _start_step(step)
                    yield _trace(step, "node_start", {"user_id": user_id})

            # Tool calls → tool_call / assessment trace events
            for fc in event.get_function_calls():
                args = dict(fc.args or {})
                if fc.name == "submit_assessment":
                    assessment_emitted = True
                    yield _trace("llm_agent", "assessment", {
                        "typology": args.get("typology"),
                        "risk_level": args.get("risk_level"),
                        "risk_score": args.get("risk_score"),
                        "decision": args.get("decision"),
                        "reasoning": args.get("reasoning"),
                    })
                else:
                    iteration += 1
                    yield _trace("llm_agent", "agent_iteration", {
                        "iteration": iteration, "tool_calls_so_far": iteration,
                    })
                    yield _trace("llm_agent", "tool_call", {
                        "tool": fc.name,
                        "params": args,
                        "iteration": iteration,
                    })

            # Investigator free-text reasoning → agent_thinking
            if event.author == INVESTIGATOR_NAME and not event.partial and event.content:
                text = "".join(p.text or "" for p in (event.content.parts or []) if getattr(p, "text", None))
                if text.strip():
                    yield _trace("llm_agent", "agent_thinking", {
                        "iteration": iteration,
                        "response_preview": text[:200],
                    })

        # Close the final step.
        if current_step:
            yield _trace(current_step, "node_complete", {"status": "success"})
            metrics.end_node(current_step)
            current_step = None

        # ── Read final session state ─────────────────────────────────────────
        final_session = await inv_runner.session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=investigation_id,
        )
        if final_session is None:
            final_state = {}
        elif hasattr(final_session.state, "to_dict"):
            final_state = final_session.state.to_dict()
        else:
            final_state = dict(final_session.state)

        assessment = final_state.get("final_assessment")
        if not assessment:
            logger.warning(f"[{investigation_id}] No assessment submitted — using deterministic fallback")
            assessment = deterministic_assessment(
                seed_state.get("initial_evidence") or {},
                seed_state.get("alert_evidence") or {},
            )

        report_markdown = final_state.get("report_markdown") or ""
        if not report_markdown:
            report_markdown = generate_fallback_report({**final_state, **seed_state, "final_assessment": assessment})

        tool_calls = final_state.get("tool_calls", [])
        agent_iterations = final_state.get("agent_iterations", iteration)

        # ── Persist report as an Aerospike artifact ──────────────────────────
        try:
            await inv_runner.artifact_service.save_artifact(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=investigation_id,
                filename="investigation_report.md",
                artifact=types.Part(text=report_markdown),
            )
        except Exception as e:
            logger.warning(f"[{investigation_id}] Failed to save report artifact: {e}")

        # ── Add the session to long-term memory for future recall ────────────
        try:
            if final_session:
                await inv_runner.memory_service.add_session_to_memory(final_session)
        except Exception as e:
            logger.warning(f"[{investigation_id}] Failed to add session to memory: {e}")

        # ── Final state_update (also what investigation_service persists to KV)
        yield {
            "type": "state_update",
            "data": {
                "final_assessment": assessment,
                "tool_calls": tool_calls,
                "agent_iterations": agent_iterations,
                "report_markdown": report_markdown,
                "initial_evidence": seed_state.get("initial_evidence"),
                "current_phase": "report",
                "workflow_status": "completed",
            },
        }

        final_metrics = metrics.get_metrics()
        logger.info(
            f"Investigation {investigation_id} completed in {final_metrics['total_duration_ms']:.0f}ms — "
            f"DB calls: {final_metrics['total_db_calls']} (KV {final_metrics['kv_calls']}, Graph {final_metrics['graph_calls']}), "
            f"LLM calls: {final_metrics['llm_calls']}, tools: {final_metrics['tool_calls_count']}"
        )
        yield {"type": "metrics", "investigation_id": investigation_id, "data": final_metrics}
        yield {"type": "complete", "investigation_id": investigation_id, "user_id": user_id}
        remove_collector(investigation_id)

    except Exception as e:
        logger.error(f"Investigation workflow error: {e}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        if current_step:
            try:
                metrics.end_node(current_step)
            except Exception:
                pass
        try:
            yield {"type": "metrics", "investigation_id": investigation_id, "data": metrics.get_metrics()}
        except Exception:
            pass
        yield {"type": "error", "investigation_id": investigation_id, "user_id": user_id, "error": str(e)}
        remove_collector(investigation_id)
