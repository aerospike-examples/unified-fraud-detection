"""
Mock investigation engine — deterministic, LLM-free investigations for tests and demos.

Runs the real alert-validation and data-collection nodes against Aerospike/Graph,
then synthesizes specialist traces, assessment, report, and optional HITL pause
using :func:`workflow.assessment.deterministic_assessment` and
:func:`workflow.nodes.report_generation.generate_fallback_report`.

Select via ``INVESTIGATION_ENGINE=mock``. Optional env:

- ``MOCK_FORCE_HITL=true`` — always pause for analyst approval (UI testing)
- ``MOCK_INVESTIGATION_DELAY_MS`` — sleep between phases (demo pacing)
"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from workflow.action_core import DESTRUCTIVE_DECISIONS, execute_action
from workflow.assessment import deterministic_assessment
from workflow.case_memory import recall_cases, store_case
from workflow.engines.base import BaseInvestigationEngine
from workflow.memory_service import get_memory_service
from workflow.metrics import get_collector, remove_collector
from workflow.nodes.alert_validation import alert_validation_node
from workflow.nodes.data_collection import data_collection_node
from workflow.nodes.report_generation import generate_fallback_report
from workflow.specialists import SPECIALIST_NAMES, SPECIALIST_OUTPUT_KEYS

logger = logging.getLogger("investigation.engines.mock")

APP_NAME = "fraud_investigation"

_WORKFLOW_STEPS = [
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
        "description": "Mock agent synthesizes evidence without LLM calls",
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


def _evidence_entities(user_id: str, initial: Optional[Dict[str, Any]]) -> List[str]:
    if not initial:
        return [user_id]
    entities = [user_id]
    entities.extend((initial.get("accounts") or {}).keys())
    entities.extend((initial.get("devices") or {}).keys())
    return list(dict.fromkeys(entities))


def _holder_name(initial: Optional[Dict[str, Any]]) -> str:
    if not initial:
        return "Unknown"
    profile = initial.get("profile") or {}
    return profile.get("name") or profile.get("full_name") or "Unknown"


def _primary_account_id(initial: Dict[str, Any]) -> Optional[str]:
    accounts = initial.get("accounts") or {}
    for aid, acc in accounts.items():
        if acc.get("is_fraud"):
            return aid
    return next(iter(accounts), None)


def _mock_delay() -> float:
    raw = os.environ.get("MOCK_INVESTIGATION_DELAY_MS", "0").strip()
    try:
        return max(0.0, int(raw)) / 1000.0
    except ValueError:
        return 0.0


def _force_hitl() -> bool:
    return os.environ.get("MOCK_FORCE_HITL", "").strip().lower() in ("1", "true", "yes")


def _mock_specialist_findings(initial: Dict[str, Any]) -> Dict[str, str]:
    metrics = initial.get("account_metrics") or {}
    max_velocity = metrics.get("max_velocity_zscore", 0)
    max_amount = metrics.get("max_amount_zscore", 0)
    new_recip = metrics.get("max_new_recipient_ratio", 0)
    max_shared = metrics.get("max_shared_accounts_on_device", 0)

    network_lines: List[str] = []
    if metrics.get("has_flagged_account"):
        network_lines.append("- Account linked to previously flagged fraud entity in dataset")
    if max_shared > 2:
        network_lines.append(
            f"- Device shared across {max_shared} accounts (possible fraud-ring linkage)"
        )
    network = "\n".join(network_lines) or "- No significant network anomalies in mock review"

    device_lines: List[str] = []
    if metrics.get("has_flagged_device"):
        device_lines.append("- One or more devices flagged as high risk")
    if max_shared > 1:
        device_lines.append(f"- Shared-device pattern: {max_shared} accounts on same device")
    device = "\n".join(device_lines) or "- Device profile within normal parameters"

    velocity_lines: List[str] = []
    if max_velocity > 2.0:
        velocity_lines.append(f"- Transaction velocity z-score {max_velocity:.1f} exceeds baseline")
    if max_amount > 2.0:
        velocity_lines.append(f"- Outbound amount z-score {max_amount:.1f} is elevated")
    if new_recip > 0.5:
        velocity_lines.append(f"- New-recipient ratio {new_recip:.0%} suggests mule-like behavior")
    velocity = "\n".join(velocity_lines) or "- Velocity and amount patterns within normal range"

    return {
        "network_analyst": network,
        "device_analyst": device,
        "velocity_analyst": velocity,
    }


def _mock_tool_calls(
    initial: Dict[str, Any],
    specialist_findings: Dict[str, str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Synthetic tool-call traces matching the three specialists + investigator."""
    ts = datetime.now().isoformat()
    user_id = initial.get("user_id", "")
    account_id = _primary_account_id(initial) or ""
    devices = list((initial.get("devices") or {}).keys())
    device_id = devices[0] if devices else "D0000000"

    def _call(tool: str, params: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        return {"tool": tool, "params": params, "result": result, "timestamp": ts}

    return {
        "specialist_tool_calls_network_analyst": [
            _call("detect_fraud_ring", {"user_id": user_id}, {"ring_detected": False, "mock": True}),
            _call("get_counterparty_profile", {"user_id": user_id}, {"mock": True}),
        ],
        "specialist_tool_calls_device_analyst": [
            _call("get_device_risk_features", {"device_id": device_id}, {"mock": True}),
        ],
        "specialist_tool_calls_velocity_analyst": [
            _call(
                "get_account_transactions",
                {"account_id": account_id, "limit": 10},
                {"transactions": [], "mock": True},
            ),
        ],
        "tool_calls": [
            _call(
                "submit_assessment",
                {"typology": "mock", "mock": True},
                {"status": "submitted"},
            ),
        ],
    }


class MockEngine(BaseInvestigationEngine):
    """Deterministic investigation backend for integration tests and conference demos."""

    def __init__(self, aerospike_service: Any, graph_service: Any, llm_config: Any = None):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service
        self._memory_service = None
        self._paused: Dict[str, Dict[str, Any]] = {}

    @property
    def engine_name(self) -> str:
        return "mock"

    async def initialize(self) -> None:
        if self.aerospike_service and getattr(self.aerospike_service, "is_connected", lambda: False)():
            self._memory_service = get_memory_service(self.aerospike_service)
        logger.info("MockEngine initialized (no LLM calls)")

    async def close(self) -> None:
        self._paused.clear()

    def get_workflow_steps(self) -> List[Dict[str, str]]:
        return list(_WORKFLOW_STEPS)

    async def _maybe_sleep(self) -> None:
        delay = _mock_delay()
        if delay > 0:
            await asyncio.sleep(delay)

    async def _finalize(
        self,
        investigation_id: str,
        user_id: str,
        state: Dict[str, Any],
        metrics: Any,
        enacted_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        assessment = state.get("final_assessment") or {}
        initial = state.get("initial_evidence") or {}
        report_markdown = state.get("report_markdown") or ""
        specialist_findings = state.get("specialist_findings") or {}
        tool_calls = state.get("merged_tool_calls") or []
        enacted = (enacted_actions or []) + (state.get("enacted_actions") or [])

        if self._memory_service:
            decision = enacted[0].get("action") if enacted else assessment.get("decision")
            await store_case(self._memory_service, APP_NAME, {
                "investigation_id": investigation_id,
                "user_id": user_id,
                "account_id": assessment.get("account_id") or _primary_account_id(initial),
                "holder": _holder_name(initial),
                "typology": assessment.get("typology"),
                "decision": decision,
                "entities": _evidence_entities(user_id, initial),
            })

        yield {
            "type": "state_update",
            "data": {
                "final_assessment": assessment,
                "tool_calls": tool_calls,
                "specialist_findings": specialist_findings,
                "enacted_actions": enacted,
                "agent_iterations": state.get("agent_iterations", 0),
                "report_markdown": report_markdown,
                "initial_evidence": initial,
                "prior_cases": state.get("prior_cases", []),
                "current_phase": "report",
                "workflow_status": "completed",
            },
        }

        final_metrics = metrics.get_metrics()
        logger.info(
            "Mock investigation %s completed in %.0fms (llm_calls=%s)",
            investigation_id,
            final_metrics.get("total_duration_ms", 0),
            final_metrics.get("llm_calls", 0),
        )
        yield {"type": "metrics", "investigation_id": investigation_id, "data": final_metrics}
        yield {"type": "complete", "investigation_id": investigation_id, "user_id": user_id}
        remove_collector(investigation_id)
        self._paused.pop(investigation_id, None)

    async def run_investigation(
        self,
        user_id: str,
        investigation_id: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        metrics = get_collector(investigation_id)
        metrics.reset()
        started_at = datetime.now().isoformat()
        logger.info("Starting mock investigation %s for user %s", investigation_id, user_id)

        try:
            # 1) Alert validation (real KV)
            metrics.start_node("alert_validation")
            yield _trace("alert_validation", "node_start", {"user_id": user_id})
            av = alert_validation_node({"user_id": user_id}, self.aerospike_service)
            alert_evidence = av.get("alert_evidence") or {}
            for ev in av.get("trace_events", []):
                yield {"type": "trace", "event": ev}
            metrics.end_node("alert_validation")
            yield _trace("alert_validation", "node_complete", {"status": "success"})
            await self._maybe_sleep()

            # 2) Data collection (real KV + graph)
            metrics.start_node("data_collection")
            yield _trace("data_collection", "node_start", {"user_id": user_id})
            dc = data_collection_node(
                {
                    "user_id": user_id,
                    "investigation_id": investigation_id,
                    "alert_evidence": alert_evidence,
                },
                self.aerospike_service,
                self.graph_service,
            )
            initial_evidence = dc.get("initial_evidence") or {}
            for ev in dc.get("trace_events", []):
                yield {"type": "trace", "event": ev}
            metrics.end_node("data_collection")
            yield _trace("data_collection", "node_complete", {"status": "success"})
            await self._maybe_sleep()

            prior_cases: List[Dict[str, Any]] = []
            if self._memory_service:
                prior_cases = await recall_cases(
                    self._memory_service,
                    APP_NAME,
                    _evidence_entities(user_id, initial_evidence),
                    exclude_investigation_id=investigation_id,
                    exclude_user_id=user_id,
                )
                if prior_cases:
                    yield _trace("data_collection", "memory_recall", {"prior_cases": prior_cases})

            yield {
                "type": "state_update",
                "data": {
                    "alert_evidence": alert_evidence,
                    "initial_evidence": initial_evidence,
                    "prior_cases": prior_cases,
                    "current_phase": "llm_reasoning",
                },
            }

            # 3) Mock agent phase
            metrics.start_node("llm_agent")
            yield _trace("llm_agent", "node_start", {"user_id": user_id, "engine": "mock"})

            specialist_findings = _mock_specialist_findings(initial_evidence)
            tool_call_groups = _mock_tool_calls(initial_evidence, specialist_findings)
            merged_tool_calls = (
                tool_call_groups["specialist_tool_calls_network_analyst"]
                + tool_call_groups["specialist_tool_calls_device_analyst"]
                + tool_call_groups["specialist_tool_calls_velocity_analyst"]
                + tool_call_groups["tool_calls"]
            )

            for name in SPECIALIST_NAMES:
                yield _trace("llm_agent", "tool_call", {
                    "tool": "mock_specialist",
                    "params": {"agent": name},
                    "iteration": 0,
                    "agent": name,
                })
                finding = specialist_findings.get(name, "")
                if finding:
                    yield _trace("llm_agent", "specialist_finding", {
                        "agent": name,
                        "finding": finding[:1500],
                    })
                await self._maybe_sleep()

            assessment = dict(deterministic_assessment(initial_evidence, alert_evidence))
            account_id = _primary_account_id(initial_evidence)
            if account_id:
                assessment["account_id"] = account_id

            yield _trace("llm_agent", "agent_iteration", {"iteration": 1, "tool_calls_so_far": 1})
            yield _trace("llm_agent", "assessment", {
                "typology": assessment.get("typology"),
                "risk_level": assessment.get("risk_level"),
                "risk_score": assessment.get("risk_score"),
                "decision": assessment.get("decision"),
                "reasoning": assessment.get("reasoning"),
            })
            yield _trace("llm_agent", "action_proposed", {
                "decision": assessment.get("decision"),
                "account_id": account_id,
                "reason": assessment.get("reasoning", "")[:200],
            })
            metrics.end_node("llm_agent")
            yield _trace("llm_agent", "node_complete", {"status": "success"})
            await self._maybe_sleep()

            # 4) Report generation (deterministic)
            metrics.start_node("report_generation")
            yield _trace("report_generation", "node_start", {"user_id": user_id})
            report_state = {
                "user_id": user_id,
                "investigation_id": investigation_id,
                "started_at": started_at,
                "alert_evidence": alert_evidence,
                "initial_evidence": initial_evidence,
                "final_assessment": assessment,
                "tool_calls": merged_tool_calls,
                "agent_iterations": 1,
                **{SPECIALIST_OUTPUT_KEYS[n]: specialist_findings.get(n, "") for n in SPECIALIST_NAMES},
                **tool_call_groups,
            }
            report_markdown = generate_fallback_report(report_state)
            metrics.end_node("report_generation")
            yield _trace("report_generation", "node_complete", {"status": "success"})

            run_state = {
                **report_state,
                "report_markdown": report_markdown,
                "specialist_findings": specialist_findings,
                "merged_tool_calls": merged_tool_calls,
                "prior_cases": prior_cases,
                "enacted_actions": [],
            }

            decision = assessment.get("decision", "allow_monitor")
            needs_hitl = _force_hitl() or decision in DESTRUCTIVE_DECISIONS

            if needs_hitl:
                fc_id = f"mock_fc_{uuid.uuid4().hex[:8]}"
                paused = {
                    "investigation_id": investigation_id,
                    "user_id": user_id,
                    "fc_id": fc_id,
                    "hint": "Mock engine: approve the recommended action?",
                    "decision": decision,
                    "account_id": account_id,
                    "reason": assessment.get("reasoning", ""),
                    "current_step": "llm_agent",
                }
                self._paused[investigation_id] = {**run_state, **paused}

                yield {
                    "type": "state_update",
                    "data": {
                        "final_assessment": assessment,
                        "report_markdown": report_markdown,
                        "specialist_findings": specialist_findings,
                        "tool_calls": merged_tool_calls,
                        "initial_evidence": initial_evidence,
                        "current_phase": "awaiting_decision",
                    },
                }
                yield _trace("llm_agent", "action_confirmation_required", paused)
                yield {"type": "action_confirmation_required", "data": paused}
                yield {"type": "_paused", "data": paused}
                return

            enacted: List[Dict[str, Any]] = []
            if account_id and decision:
                try:
                    enacted.append(execute_action(decision, account_id, assessment.get("reasoning", "")))
                except Exception as e:
                    logger.warning("[%s] mock action failed: %s", investigation_id, e)

            async for ev in self._finalize(
                investigation_id, user_id, run_state, metrics, enacted_actions=enacted,
            ):
                yield ev

        except Exception as e:
            logger.error("Mock investigation error: %s", e)
            logger.error("Full traceback:\n%s", traceback.format_exc())
            try:
                yield {
                    "type": "metrics",
                    "investigation_id": investigation_id,
                    "data": metrics.get_metrics(),
                }
            except Exception:
                pass
            yield {
                "type": "error",
                "investigation_id": investigation_id,
                "user_id": user_id,
                "error": str(e),
            }
            remove_collector(investigation_id)

    async def resume_investigation(
        self,
        user_id: str,
        investigation_id: str,
        fc_id: str,
        approved: bool,
        hint: str = "",
        payload: Optional[Dict[str, Any]] = None,
        override: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        metrics = get_collector(investigation_id)
        paused = self._paused.get(investigation_id)
        if not paused:
            yield {
                "type": "error",
                "investigation_id": investigation_id,
                "user_id": user_id,
                "error": f"No paused mock investigation {investigation_id}",
            }
            return

        payload = payload or {}
        logger.info(
            "Resuming mock investigation %s: approved=%s override=%s fc=%s",
            investigation_id, approved, override, fc_id,
        )

        try:
            yield _trace("llm_agent", "action_decision", {
                "approved": bool(approved),
                "decision": payload.get("decision") or paused.get("decision"),
                "account_id": payload.get("account_id") or paused.get("account_id"),
                "override": override,
            })

            assessment = paused.get("final_assessment") or {}
            account_id = payload.get("account_id") or paused.get("account_id") or assessment.get("account_id")
            enacted: List[Dict[str, Any]] = []

            if approved:
                decision = payload.get("decision") or paused.get("decision")
                if account_id and decision:
                    enacted.append(execute_action(
                        decision, account_id, assessment.get("reasoning", ""),
                    ))
            elif override and account_id:
                enacted.append(execute_action(
                    override,
                    account_id,
                    "Analyst override of the mock agent's recommendation",
                ))

            async for ev in self._finalize(
                investigation_id,
                user_id,
                paused,
                metrics,
                enacted_actions=enacted,
            ):
                yield ev

        except Exception as e:
            logger.error("Mock resume error: %s", e)
            logger.error("Full traceback:\n%s", traceback.format_exc())
            try:
                yield {
                    "type": "metrics",
                    "investigation_id": investigation_id,
                    "data": metrics.get_metrics(),
                }
            except Exception:
                pass
            yield {
                "type": "error",
                "investigation_id": investigation_id,
                "user_id": user_id,
                "error": str(e),
            }
            remove_collector(investigation_id)
