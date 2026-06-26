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

from workflow.agent import (
    build_investigation_agent, APP_NAME, INVESTIGATOR_NAME, REPORT_WRITER_NAME,
    SPECIALIST_NAMES, SPECIALIST_OUTPUT_KEYS,
)
from workflow.case_memory import recall_cases, store_case
from workflow.plugins import MetricsPlugin
from workflow.assessment import deterministic_assessment
from workflow.nodes.report_generation import generate_fallback_report
from workflow.nodes.alert_validation import alert_validation_node
from workflow.nodes.data_collection import data_collection_node
from workflow.metrics import get_collector, remove_collector

logger = logging.getLogger('investigation.runner')

# Map ADK sub-agent names → the step ids the frontend renders. The parallel
# evidence specialists and the investigator both fall under the "llm_agent" step
# (the AI Investigation phase), so the 4-step UI contract is unchanged.
_AGENT_TO_STEP = {
    INVESTIGATOR_NAME: "llm_agent",
    REPORT_WRITER_NAME: "report_generation",
    **{name: "llm_agent" for name in SPECIALIST_NAMES},
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


def _evidence_entities(user_id: str, ie: Optional[Dict[str, Any]]) -> list:
    """The suspect's own entity ids — user, accounts, devices — for memory recall."""
    ie = ie or {}
    ids = [user_id, *(ie.get("accounts") or {}).keys(), *(ie.get("devices") or {}).keys()]
    return [i for i in ids if i]


def _holder_name(ie: Optional[Dict[str, Any]]) -> str:
    p = (ie or {}).get("profile") or {}
    return (p.get("name") or p.get("account_holder")
            or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip())


def _counterparties(tool_calls: list) -> list:
    """All counterparty user_ids this investigation touched, so future
    investigations of any of those accounts recall this case. Pulled from the
    full transaction results (not just the 2-3 the agent drilled into), so recall
    is complete regardless of which counterparties the agent chose."""
    out = set()
    for tc in tool_calls or []:
        tool = tc.get("tool")
        if tool in ("get_counterparty_profile", "get_counterparty_transactions"):
            uid = (tc.get("params") or {}).get("user_id")
            if uid:
                out.add(uid)
        elif tool == "get_account_transactions":
            for txn in ((tc.get("result") or {}).get("transactions") or []):
                cp = txn.get("counterparty_user_id")
                if cp:
                    out.add(cp)
    return list(out)


async def _read_state(inv_runner: InvestigationRunner, user_id: str, investigation_id: str) -> Dict[str, Any]:
    session = await inv_runner.session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=investigation_id,
    )
    if session is None:
        return {}
    return session.state.to_dict() if hasattr(session.state, "to_dict") else dict(session.state)


async def _drive_agent(
    inv_runner: InvestigationRunner,
    user_id: str,
    investigation_id: str,
    new_message: Any,
    metrics: Any,
    start_step: Optional[str],
    manual_override: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Drive runner.run_async and translate the ADK event stream into SSE dicts.

    If the agent pauses for an action confirmation (adk_request_confirmation),
    emits an ``action_confirmation_required`` event + an internal ``_paused``
    marker and stops WITHOUT finalizing. Otherwise finalizes on completion.
    """
    current_step = start_step
    iteration = 0
    paused: Optional[Dict[str, Any]] = None

    async for event in inv_runner.runner.run_async(
        user_id=user_id, session_id=investigation_id, new_message=new_message,
    ):
        step = _AGENT_TO_STEP.get(event.author)
        if step and current_step != step:
            if current_step:
                yield _trace(current_step, "node_complete", {"status": "success"})
                metrics.end_node(current_step)
            current_step = step
            metrics.start_node(step)
            yield _trace(step, "node_start", {"user_id": user_id})

        for fc in event.get_function_calls():
            args = dict(fc.args or {})

            # Human-in-the-loop: the agent paused for analyst approval.
            if fc.name == "adk_request_confirmation":
                tc = args.get("toolConfirmation") or args.get("tool_confirmation") or {}
                tc = tc if isinstance(tc, dict) else {}
                payload = tc.get("payload") if isinstance(tc.get("payload"), dict) else {}
                paused = {
                    "investigation_id": investigation_id,
                    "user_id": user_id,
                    "fc_id": fc.id,
                    "hint": tc.get("hint", ""),
                    "decision": payload.get("decision"),
                    "account_id": payload.get("account_id"),
                    "reason": payload.get("reason"),
                    "current_step": current_step,
                }
                # The report has ALREADY been written by this point (action_taker
                # runs after report_writer). Close the analysis/report step (so it
                # shows complete) — we're now in the decision phase.
                if current_step:
                    yield _trace(current_step, "node_complete", {"status": "success"})
                    metrics.end_node(current_step)
                    current_step = None
                # Push the report + assessment + findings so the analyst can review
                # the full report BEFORE approving the action.
                snap = await _read_state(inv_runner, user_id, investigation_id)
                spec_calls = []
                for nm in SPECIALIST_NAMES:
                    spec_calls.extend(snap.get(f"specialist_tool_calls_{nm}", []))
                yield {"type": "state_update", "data": {
                    "final_assessment": snap.get("final_assessment"),
                    "report_markdown": snap.get("report_markdown", ""),
                    "specialist_findings": {nm: (snap.get(SPECIALIST_OUTPUT_KEYS[nm]) or "") for nm in SPECIALIST_NAMES},
                    "tool_calls": spec_calls + snap.get("tool_calls", []),
                    "initial_evidence": snap.get("initial_evidence"),
                    "current_phase": "awaiting_decision",
                }}
                yield _trace("llm_agent", "action_confirmation_required", paused)
                yield {"type": "action_confirmation_required", "data": paused}
                break

            if fc.name == "submit_assessment":
                yield _trace("llm_agent", "assessment", {
                    "typology": args.get("typology"),
                    "risk_level": args.get("risk_level"),
                    "risk_score": args.get("risk_score"),
                    "decision": args.get("decision"),
                    "reasoning": args.get("reasoning"),
                })
            elif fc.name == "enact_decision":
                yield _trace("llm_agent", "action_proposed", {
                    "decision": args.get("decision"),
                    "account_id": args.get("account_id"),
                    "reason": args.get("reason"),
                })
            else:
                is_specialist = event.author in SPECIALIST_NAMES
                # Only count investigator (synthesis) tool calls as iterations;
                # the parallel specialists run concurrently and shouldn't inflate it.
                if not is_specialist:
                    iteration += 1
                    yield _trace("llm_agent", "agent_iteration", {
                        "iteration": iteration, "tool_calls_so_far": iteration,
                    })
                yield _trace("llm_agent", "tool_call", {
                    "tool": fc.name, "params": args, "iteration": iteration,
                    "agent": event.author,
                })

        if paused:
            break

        # Parallel specialist finished → surface its findings summary live.
        if event.author in SPECIALIST_NAMES and not event.partial and event.content:
            text = "".join(p.text or "" for p in (event.content.parts or []) if getattr(p, "text", None))
            if text.strip():
                yield _trace("llm_agent", "specialist_finding", {
                    "agent": event.author, "finding": text.strip()[:1500],
                })

        if event.author == INVESTIGATOR_NAME and not event.partial and event.content:
            text = "".join(p.text or "" for p in (event.content.parts or []) if getattr(p, "text", None))
            if text.strip():
                yield _trace("llm_agent", "agent_thinking", {
                    "iteration": iteration, "response_preview": text[:200],
                })

    if paused:
        # Paused for confirmation — let the service stash it and stop (no finalize).
        yield {"type": "_paused", "data": paused}
        return

    # Analyst rejected the agent's action and chose a different disposition — enact
    # it now (same enforcement path the agent uses) and fold it into the result.
    extra_actions = []
    if manual_override:
        from workflow.action_core import execute_action
        try:
            res = execute_action(
                manual_override["decision"], manual_override["account_id"],
                manual_override.get("reason", "Analyst override of the agent's recommendation"),
            )
            extra_actions.append(res)
            yield _trace("llm_agent", "action_proposed", {
                "decision": manual_override["decision"],
                "account_id": manual_override["account_id"],
                "reason": "Analyst override",
            })
        except Exception as e:
            logger.error(f"[{investigation_id}] manual override failed: {e}")

    async for ev in _finalize(inv_runner, user_id, investigation_id, metrics, current_step,
                              extra_actions=extra_actions):
        yield ev


async def _finalize(
    inv_runner: InvestigationRunner,
    user_id: str,
    investigation_id: str,
    metrics: Any,
    current_step: Optional[str],
    extra_actions: Optional[list] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Close the run: read final state, persist report artifact + memory, emit
    final state_update / metrics / complete."""
    if current_step:
        yield _trace(current_step, "node_complete", {"status": "success"})
        metrics.end_node(current_step)

    final_session = await inv_runner.session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=investigation_id,
    )
    final_state = (
        final_session.state.to_dict() if final_session and hasattr(final_session.state, "to_dict")
        else (dict(final_session.state) if final_session else {})
    )

    alert = final_state.get("alert_evidence") or {}
    initial = final_state.get("initial_evidence") or {}
    assessment = final_state.get("final_assessment")
    if not assessment:
        logger.warning(f"[{investigation_id}] No assessment submitted — using deterministic fallback")
        assessment = deterministic_assessment(initial, alert)

    report_markdown = final_state.get("report_markdown") or ""
    if not report_markdown:
        report_markdown = generate_fallback_report({**final_state, "final_assessment": assessment})

    # Merge the parallel specialists' (per-agent) tool calls with the
    # investigator's into one chronological list — specialists ran first.
    specialist_calls = []
    for name in SPECIALIST_NAMES:
        specialist_calls.extend(final_state.get(f"specialist_tool_calls_{name}", []))
    tool_calls = specialist_calls + final_state.get("tool_calls", [])

    specialist_findings = {
        name: (final_state.get(SPECIALIST_OUTPUT_KEYS[name]) or "")
        for name in SPECIALIST_NAMES
    }
    enacted_actions = final_state.get("enacted_actions", []) + (extra_actions or [])
    agent_iterations = final_state.get("agent_iterations", 0)

    try:
        await inv_runner.artifact_service.save_artifact(
            app_name=APP_NAME, user_id=user_id, session_id=investigation_id,
            filename="investigation_report.md", artifact=types.Part(text=report_markdown),
        )
    except Exception as e:
        logger.warning(f"[{investigation_id}] Failed to save report artifact: {e}")

    try:
        if final_session:
            await inv_runner.memory_service.add_session_to_memory(final_session)
    except Exception as e:
        logger.warning(f"[{investigation_id}] Failed to add session to memory: {e}")

    # Store a compact, entity-indexed case record for cross-case recall.
    decision = enacted_actions[0].get("action") if enacted_actions else (assessment or {}).get("decision")
    await store_case(inv_runner.memory_service, APP_NAME, {
        "investigation_id": investigation_id,
        "user_id": user_id,
        "account_id": (assessment or {}).get("account_id") or next(iter((initial.get("accounts") or {})), None),
        "holder": _holder_name(initial),
        "typology": (assessment or {}).get("typology"),
        "decision": decision,
        "entities": list(dict.fromkeys(
            _evidence_entities(user_id, initial) + _counterparties(tool_calls))),
    })

    yield {
        "type": "state_update",
        "data": {
            "final_assessment": assessment,
            "tool_calls": tool_calls,
            "specialist_findings": specialist_findings,
            "enacted_actions": enacted_actions,
            "agent_iterations": agent_iterations,
            "report_markdown": report_markdown,
            "initial_evidence": initial,
            "prior_cases": final_state.get("prior_cases", []),
            "current_phase": "report",
            "workflow_status": "completed",
        },
    }

    final_metrics = metrics.get_metrics()
    logger.info(
        f"Investigation {investigation_id} completed in {final_metrics['total_duration_ms']:.0f}ms — "
        f"DB calls: {final_metrics['total_db_calls']} (KV {final_metrics['kv_calls']}, Graph {final_metrics['graph_calls']}), "
        f"LLM calls: {final_metrics['llm_calls']}, tools: {final_metrics['tool_calls_count']}, "
        f"actions: {len(enacted_actions)}"
    )
    yield {"type": "metrics", "investigation_id": investigation_id, "data": final_metrics}
    yield {"type": "complete", "investigation_id": investigation_id, "user_id": user_id}
    remove_collector(investigation_id)


async def run_investigation(
    inv_runner: InvestigationRunner,
    user_id: str,
    investigation_id: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run one investigation, yielding SSE event dicts (may pause for HITL)."""
    metrics = get_collector(investigation_id)
    metrics.reset()
    started_at = datetime.now().isoformat()
    logger.info(f"Starting investigation {investigation_id} for user {user_id}")

    try:
        seed_state = {
            "user_id": user_id,
            "investigation_id": investigation_id,
            "started_at": started_at,
            "tool_calls": [],
            "enacted_actions": [],
            "agent_iterations": 0,
            "tool_calls_count": 0,
            "final_assessment": None,
            "report_markdown": "",
        }

        # 1) Alert validation
        metrics.start_node("alert_validation")
        av = alert_validation_node({"user_id": user_id}, inv_runner.aerospike_service)
        seed_state["alert_evidence"] = av.get("alert_evidence")
        for ev in av.get("trace_events", []):
            yield {"type": "trace", "event": ev}
        metrics.end_node("alert_validation")

        # 2) Data collection
        metrics.start_node("data_collection")
        dc = data_collection_node(
            {"user_id": user_id, "investigation_id": investigation_id, "alert_evidence": seed_state["alert_evidence"]},
            inv_runner.aerospike_service,
            inv_runner.graph_service,
        )
        seed_state["initial_evidence"] = dc.get("initial_evidence")
        for ev in dc.get("trace_events", []):
            yield {"type": "trace", "event": ev}
        metrics.end_node("data_collection")

        # ── Cross-case memory recall (ADK MemoryService) ─────────────────────
        # Search long-term memory for PRIOR investigations that referenced this
        # account, its devices, or where it appeared as a counterparty.
        prior_cases = await recall_cases(
            inv_runner.memory_service, APP_NAME,
            _evidence_entities(user_id, seed_state["initial_evidence"]),
            exclude_investigation_id=investigation_id, exclude_user_id=user_id,
        )
        seed_state["prior_cases"] = prior_cases
        if prior_cases:
            yield _trace("data_collection", "memory_recall", {"prior_cases": prior_cases})
            logger.info(f"[{investigation_id}] memory recall: {len(prior_cases)} related prior case(s)")

        yield {
            "type": "state_update",
            "data": {
                "alert_evidence": seed_state["alert_evidence"],
                "initial_evidence": seed_state["initial_evidence"],
                "prior_cases": prior_cases,
                "current_phase": "llm_reasoning",
            },
        }

        await inv_runner.session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=investigation_id, state=seed_state,
        )

        new_message = types.Content(role="user", parts=[types.Part(text=(
            "Investigate this flagged account using the available tools, then submit your "
            "assessment, enact your recommended decision, and write the investigation report."
        ))])

        async for ev in _drive_agent(inv_runner, user_id, investigation_id, new_message, metrics, start_step=None):
            yield ev

    except Exception as e:
        logger.error(f"Investigation workflow error: {e}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        try:
            yield {"type": "metrics", "investigation_id": investigation_id, "data": metrics.get_metrics()}
        except Exception:
            pass
        yield {"type": "error", "investigation_id": investigation_id, "user_id": user_id, "error": str(e)}
        remove_collector(investigation_id)


async def resume_investigation(
    inv_runner: InvestigationRunner,
    user_id: str,
    investigation_id: str,
    fc_id: str,
    approved: bool,
    hint: str = "",
    payload: Optional[Dict[str, Any]] = None,
    override: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Resume a paused investigation after the analyst approves the action, or
    rejects it and (optionally) picks a different disposition via ``override``."""
    metrics = get_collector(investigation_id)  # continue the same run's metrics
    logger.info(f"Resuming investigation {investigation_id}: approved={approved} override={override} fc={fc_id}")

    try:
        yield _trace("llm_agent", "action_decision", {
            "approved": bool(approved),
            "decision": (payload or {}).get("decision"),
            "account_id": (payload or {}).get("account_id"),
            "override": override,
        })

        # Reply to the adk_request_confirmation call with the analyst's decision.
        confirmation_response = {"confirmed": bool(approved), "hint": hint or "", "payload": payload or {}}
        new_message = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id=fc_id, name="adk_request_confirmation", response=confirmation_response,
        ))])

        # On reject + chosen disposition, enact the analyst's override after the
        # agent declines its own recommendation.
        manual_override = None
        if not approved and override:
            manual_override = {
                "decision": override,
                "account_id": (payload or {}).get("account_id"),
                "reason": "Analyst override of the agent's recommendation",
            }

        async for ev in _drive_agent(inv_runner, user_id, investigation_id, new_message, metrics,
                                     start_step="llm_agent", manual_override=manual_override):
            yield ev

    except Exception as e:
        logger.error(f"Resume investigation error: {e}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        try:
            yield {"type": "metrics", "investigation_id": investigation_id, "data": metrics.get_metrics()}
        except Exception:
            pass
        yield {"type": "error", "investigation_id": investigation_id, "user_id": user_id, "error": str(e)}
        remove_collector(investigation_id)
