"""
LangGraph investigation workflow (ADK feature parity).

Pipeline:
  alert_validation → data_collection → memory_recall → evidence_collection
  (parallel specialists) → investigator → report_generation → action (HITL)
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from langgraph.checkpoint.aerospike import AerospikeSaver
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig

from workflow.action_core import ALL_DECISIONS, DESTRUCTIVE_DECISIONS, execute_action
from workflow.specialists import (
    NETWORK_ANALYST_NAME,
    DEVICE_ANALYST_NAME,
    VELOCITY_ANALYST_NAME,
    _SPECIALIST_SPECS,
    _SPECIALIST_SYSTEM,
    _SPECIALIST_TOOLS,
)
from workflow.assessment import build_evidence_summary
from workflow.case_memory import recall_cases, store_case
from workflow.llm import LLMConfig
from workflow.nodes.langgraph_agent import call_llm
from workflow.memory_service import get_memory_service
from workflow.metrics import get_collector, remove_collector
from workflow.nodes.alert_validation import alert_validation_node
from workflow.nodes.data_collection import data_collection_node
from workflow.nodes.langgraph_agent import (
    format_prior_cases,
    format_specialist_findings,
    run_tool_loop,
)
from workflow.nodes.report_generation import (
    build_report_instruction,
    finalize_report,
    generate_fallback_report,
)
from workflow.state import InvestigationState, create_initial_state
from workflow.tools.investigation_tools import InvestigationTools

logger = logging.getLogger("investigation.graph")

APP_NAME = "fraud_investigation"

_SPECIALIST_TOOL_NAMES = _SPECIALIST_TOOLS

_TOOL_CALL_KEYS = {
    NETWORK_ANALYST_NAME: "specialist_tool_calls_network_analyst",
    DEVICE_ANALYST_NAME: "specialist_tool_calls_device_analyst",
    VELOCITY_ANALYST_NAME: "specialist_tool_calls_velocity_analyst",
}

_FINDING_KEYS = {
    NETWORK_ANALYST_NAME: "network_findings",
    DEVICE_ANALYST_NAME: "device_findings",
    VELOCITY_ANALYST_NAME: "velocity_findings",
}


class InstrumentedAerospikeSaver(AerospikeSaver):
    """Wrap AerospikeSaver to record checkpoint metrics."""

    def _inv_id(self, config: Optional[RunnableConfig]) -> Optional[str]:
        try:
            return (config or {}).get("configurable", {}).get("thread_id")
        except Exception:
            return None

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        import time

        inv_id = self._inv_id(config)
        start = time.time()
        try:
            return super().put(config, checkpoint, metadata, new_versions)
        finally:
            if inv_id:
                try:
                    collector = get_collector(inv_id)
                    duration_ms = (time.time() - start) * 1000
                    collector.track_checkpoint("put", duration_ms)
                    collector.track_db_call("checkpoint_put", "KV", duration_ms)
                except Exception:
                    pass

    def put_writes(
        self,
        config: RunnableConfig,
        writes,
        task_id: str,
        task_path: str = "",
    ) -> None:
        import time

        inv_id = self._inv_id(config)
        start = time.time()
        try:
            super().put_writes(config, writes, task_id, task_path)
        finally:
            if inv_id:
                try:
                    collector = get_collector(inv_id)
                    duration_ms = (time.time() - start) * 1000
                    collector.track_checkpoint("put_writes", duration_ms)
                    collector.track_db_call("checkpoint_put_writes", "KV", duration_ms)
                except Exception:
                    pass

    def get_tuple(self, config: RunnableConfig):
        import time

        inv_id = self._inv_id(config)
        start = time.time()
        try:
            return super().get_tuple(config)
        finally:
            if inv_id:
                try:
                    collector = get_collector(inv_id)
                    duration_ms = (time.time() - start) * 1000
                    collector.track_checkpoint("get_tuple", duration_ms)
                    collector.track_db_call("checkpoint_get", "KV", duration_ms)
                except Exception:
                    pass


def _evidence_entities(user_id: str, initial: Optional[Dict[str, Any]]) -> List[str]:
    initial = initial or {}
    ids = [user_id, *(initial.get("accounts") or {}).keys(), *(initial.get("devices") or {}).keys()]
    return [i for i in ids if i]


def _holder_name(initial: Optional[Dict[str, Any]]) -> str:
    profile = (initial or {}).get("profile") or {}
    return (
        profile.get("name")
        or profile.get("account_holder")
        or f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    )


def _counterparties(tool_calls: list) -> list:
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


def create_investigation_workflow(
    aerospike_service: Any,
    graph_service: Any,
    llm_config: Optional[LLMConfig] = None,
) -> Any:
    """Build the compiled LangGraph workflow."""
    llm_config = llm_config or LLMConfig.from_env()
    memory_service = get_memory_service(aerospike_service)

    async def _memory_recall(state: InvestigationState) -> Dict[str, Any]:
        prior = await recall_cases(
            memory_service,
            APP_NAME,
            _evidence_entities(state["user_id"], state.get("initial_evidence")),
            exclude_investigation_id=state["investigation_id"],
            exclude_user_id=state["user_id"],
        )
        trace = []
        if prior:
            trace.append(
                {
                    "type": "memory_recall",
                    "node": "data_collection",
                    "timestamp": datetime.now().isoformat(),
                    "data": {"prior_cases": prior},
                }
            )
        return {"prior_cases": prior, "trace_events": trace}

    def _run_specialist(name: str, state: InvestigationState) -> Dict[str, Any]:
        spec = _SPECIALIST_SPECS[name]
        initial = state.get("initial_evidence") or {}
        alert = state.get("alert_evidence") or {}
        evidence = build_evidence_summary(initial, alert)
        system = _SPECIALIST_SYSTEM.format(
            role=spec["role"],
            focus=spec["focus"],
            max_calls=spec["max_calls"],
            evidence=evidence,
        )
        metrics = get_collector(state["investigation_id"])
        engine = InvestigationTools(
            aerospike_service, graph_service, state["user_id"], metrics
        )
        result = run_tool_loop(
            node_name=name,
            system_prompt=system,
            evidence=evidence,
            allowed_tools=_SPECIALIST_TOOL_NAMES[name],
            tools_engine=engine,
            metrics=metrics,
            llm_config=llm_config,
            max_iterations=spec["max_calls"] + 1,
            max_tool_calls=spec["max_calls"],
        )
        findings = result.get("findings_text", "")
        traces = list(result.get("trace_events", []))
        if findings:
            traces.append(
                {
                    "type": "specialist_finding",
                    "node": "llm_agent",
                    "timestamp": datetime.now().isoformat(),
                    "data": {"agent": name, "finding": findings[:1500]},
                }
            )
        return {
            _FINDING_KEYS[name]: findings,
            _TOOL_CALL_KEYS[name]: result.get("tool_calls", []),
            "trace_events": traces,
        }

    async def _evidence_collection(state: InvestigationState) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, _run_specialist, name, state)
            for name in (NETWORK_ANALYST_NAME, DEVICE_ANALYST_NAME, VELOCITY_ANALYST_NAME)
        ]
        results = await asyncio.gather(*tasks)
        merged: Dict[str, Any] = {"trace_events": []}
        for partial in results:
            for key, value in partial.items():
                if key == "trace_events":
                    merged["trace_events"].extend(value)
                else:
                    merged[key] = value
        merged["current_phase"] = "llm_reasoning"
        return merged

    def _investigator(state: InvestigationState) -> Dict[str, Any]:
        initial = state.get("initial_evidence") or {}
        alert = state.get("alert_evidence") or {}
        evidence = build_evidence_summary(initial, alert) + format_prior_cases(
            state.get("prior_cases") or []
        )
        findings = format_specialist_findings(state)
        system = f"""You are a SENIOR FRAUD ANALYST synthesizing parallel specialist reports.

Three specialists have ALREADY investigated in parallel. Use their findings as primary evidence.
Call tools ONLY to resolve gaps or conflicts. Reach a decision in 0-3 tool calls when possible.

## SPECIALIST FINDINGS
{findings}

When confident, call submit_assessment exactly once with typology, risk_level, risk_score,
decision, account_id (primary flagged account), and reasoning. Then STOP.

Valid decisions: allow_monitor, step_up_auth, temporary_freeze, full_block, escalate_compliance.
"""
        metrics = get_collector(state["investigation_id"])
        engine = InvestigationTools(
            aerospike_service, graph_service, state["user_id"], metrics
        )
        result = run_tool_loop(
            node_name="llm_agent",
            system_prompt=system,
            evidence=evidence,
            allowed_tools=[
                "get_account_transactions",
                "get_counterparty_profile",
                "get_counterparty_transactions",
                "get_account_risk_features",
                "get_device_risk_features",
                "detect_fraud_ring",
                "get_transaction_network",
            ],
            tools_engine=engine,
            metrics=metrics,
            llm_config=llm_config,
            max_iterations=15,
            max_tool_calls=10,
            include_submit=True,
            initial_evidence=initial,
            alert_evidence=alert,
        )
        assessment = result.get("final_assessment")
        if assessment and not assessment.get("account_id"):
            accounts = (initial.get("accounts") or {})
            if accounts:
                assessment = dict(assessment)
                assessment["account_id"] = next(iter(accounts))
        return {
            "final_assessment": assessment,
            "tool_calls": result.get("tool_calls", []),
            "agent_iterations": result.get("agent_iterations", 0),
            "tool_calls_count": len(result.get("tool_calls", [])),
            "agent_messages": result.get("agent_messages", []),
            "current_phase": "report",
            "trace_events": result.get("trace_events", []),
        }

    def _report_generation(state: InvestigationState) -> Dict[str, Any]:
        instruction = build_report_instruction(dict(state))
        raw = ""
        try:
            raw = call_llm(
                instruction + "\n\nWrite the full markdown report now.",
                llm_config,
            )
        except Exception as exc:
            logger.warning("Report LLM failed, using fallback: %s", exc)
        report = finalize_report(raw, dict(state)) if raw.strip() else generate_fallback_report(dict(state))
        return {
            "report_markdown": report,
            "current_phase": "awaiting_decision",
            "trace_events": [
                {
                    "type": "node_complete",
                    "node": "report_generation",
                    "timestamp": datetime.now().isoformat(),
                    "data": {"status": "success"},
                }
            ],
        }

    def _action(state: InvestigationState) -> Dict[str, Any]:
        assessment = state.get("final_assessment") or {}
        decision = (assessment.get("decision") or "allow_monitor").strip()
        account_id = assessment.get("account_id") or ""
        if not account_id:
            accounts = (state.get("initial_evidence") or {}).get("accounts") or {}
            account_id = next(iter(accounts), "")
        reason = (assessment.get("reasoning") or "")[:300]

        trace_events = [
            {
                "type": "action_proposed",
                "node": "llm_agent",
                "timestamp": datetime.now().isoformat(),
                "data": {"decision": decision, "account_id": account_id, "reason": reason},
            }
        ]

        enacted: List[Dict[str, Any]] = list(state.get("enacted_actions") or [])
        pending = state.get("pending_confirmation")

        if pending is not None:
            approved = bool(pending.get("approved"))
            override = pending.get("override")
            if not approved and override:
                enacted.append(
                    execute_action(
                        override,
                        account_id,
                        "Analyst override of the agent's recommendation",
                    )
                )
                trace_events.append(
                    {
                        "type": "action_proposed",
                        "node": "llm_agent",
                        "timestamp": datetime.now().isoformat(),
                        "data": {
                            "decision": override,
                            "account_id": account_id,
                            "reason": "Analyst override",
                        },
                    }
                )
            elif approved and decision in ALL_DECISIONS:
                enacted.append(execute_action(decision, account_id, reason))
            return {
                "enacted_actions": enacted,
                "pending_confirmation": None,
                "workflow_status": "completed",
                "current_phase": "report",
                "trace_events": trace_events,
            }

        if decision in DESTRUCTIVE_DECISIONS:
            payload = {
                "investigation_id": state["investigation_id"],
                "user_id": state["user_id"],
                "decision": decision,
                "account_id": account_id,
                "reason": reason,
                "hint": (
                    f"The AI agent recommends **{decision.replace('_', ' ')}** on account "
                    f"{account_id}. Reason: {reason}. Approve this action?"
                ),
            }
            response = interrupt(payload)
            approved = bool((response or {}).get("approved"))
            override = (response or {}).get("override")
            if not approved and override:
                enacted.append(
                    execute_action(
                        override,
                        account_id,
                        "Analyst override of the agent's recommendation",
                    )
                )
            elif approved:
                enacted.append(execute_action(decision, account_id, reason))
            return {
                "enacted_actions": enacted,
                "workflow_status": "completed",
                "current_phase": "report",
                "trace_events": trace_events,
            }

        if decision in ALL_DECISIONS:
            enacted.append(execute_action(decision, account_id, reason))
        return {
            "enacted_actions": enacted,
            "workflow_status": "completed",
            "current_phase": "report",
            "trace_events": trace_events,
        }

    workflow = StateGraph(InvestigationState)

    workflow.add_node(
        "alert_validation",
        lambda s: alert_validation_node(s, aerospike_service),
    )
    workflow.add_node(
        "data_collection",
        lambda s: data_collection_node(s, aerospike_service, graph_service),
    )
    workflow.add_node("memory_recall", _memory_recall)
    workflow.add_node("evidence_collection", _evidence_collection)
    workflow.add_node("investigator", _investigator)
    workflow.add_node("report_generation", _report_generation)
    workflow.add_node("action", _action)

    workflow.set_entry_point("alert_validation")
    workflow.add_edge("alert_validation", "data_collection")
    workflow.add_edge("data_collection", "memory_recall")
    workflow.add_edge("memory_recall", "evidence_collection")
    workflow.add_edge("evidence_collection", "investigator")
    workflow.add_edge("investigator", "report_generation")
    workflow.add_edge("report_generation", "action")
    workflow.add_edge("action", END)

    if aerospike_service is not None and aerospike_service.is_connected():
        try:
            saver = InstrumentedAerospikeSaver(
                client=aerospike_service.client,
                namespace=aerospike_service.namespace,
            )
            compiled = workflow.compile(checkpointer=saver)
            logger.info("LangGraph workflow compiled with Aerospike checkpointer")
            return compiled
        except Exception as exc:
            logger.error("Failed to create AerospikeSaver: %s", exc)

    compiled = workflow.compile()
    logger.info("LangGraph workflow compiled without checkpointer")
    return compiled


def get_workflow_steps() -> List[Dict[str, str]]:
    """Four-step UI contract (same ids as ADK runner)."""
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
            "description": "LangGraph agent gathers evidence and makes an assessment",
            "phase": "reasoning",
        },
        {
            "id": "report_generation",
            "name": "Report Generation",
            "description": "Generate detailed investigation report",
            "phase": "report",
        },
    ]


async def persist_case_memory(
    aerospike_service: Any,
    state: Dict[str, Any],
    investigation_id: str,
    user_id: str,
) -> None:
    """Store compact case record for cross-case recall."""
    memory_service = get_memory_service(aerospike_service)
    initial = state.get("initial_evidence") or {}
    assessment = state.get("final_assessment") or {}
    specialist_calls = []
    for key in _TOOL_CALL_KEYS.values():
        specialist_calls.extend(state.get(key) or [])
    tool_calls = specialist_calls + (state.get("tool_calls") or [])
    enacted = state.get("enacted_actions") or []
    decision = enacted[0].get("action") if enacted else assessment.get("decision")
    await store_case(
        memory_service,
        APP_NAME,
        {
            "investigation_id": investigation_id,
            "user_id": user_id,
            "account_id": assessment.get("account_id")
            or next(iter((initial.get("accounts") or {})), None),
            "holder": _holder_name(initial),
            "typology": assessment.get("typology"),
            "decision": decision,
            "entities": list(
                dict.fromkeys(_evidence_entities(user_id, initial) + _counterparties(tool_calls))
            ),
        },
    )
