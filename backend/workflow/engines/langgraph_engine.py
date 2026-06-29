"""
LangGraph investigation engine — SSE translator for the StateGraph workflow.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from langgraph.types import Command

from workflow.assessment import deterministic_assessment
from workflow.engines.base import BaseInvestigationEngine
from workflow.graph import (
    create_investigation_workflow,
    get_workflow_steps,
    persist_case_memory,
)
from workflow.llm import LLMConfig
from workflow.memory_service import close_memory_service
from workflow.metrics import get_collector, remove_collector
from workflow.nodes.report_generation import generate_fallback_report

logger = logging.getLogger("investigation.engines.langgraph")

_NODE_TO_STEP = {
    "alert_validation": "alert_validation",
    "data_collection": "data_collection",
    "memory_recall": "data_collection",
    "evidence_collection": "llm_agent",
    "investigator": "llm_agent",
    "report_generation": "report_generation",
    "action": "llm_agent",
}


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


from workflow.sse_helpers import merged_tool_calls, specialist_findings


class LangGraphEngine(BaseInvestigationEngine):
    """LangGraph StateGraph backend with ADK-parity SSE events."""

    def __init__(
        self,
        aerospike_service: Any,
        graph_service: Any,
        llm_config: Optional[LLMConfig] = None,
    ):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service
        self.llm_config = llm_config or LLMConfig.from_env()
        self._workflow = None

    @property
    def engine_name(self) -> str:
        return "langgraph"

    async def initialize(self) -> None:
        self._workflow = create_investigation_workflow(
            self.aerospike_service,
            self.graph_service,
            self.llm_config,
        )
        logger.info("LangGraphEngine initialized (provider=%s)", self.llm_config.provider)

    async def close(self) -> None:
        close_memory_service()

    def get_workflow_steps(self) -> List[Dict[str, str]]:
        return get_workflow_steps()

    def _config(self, investigation_id: str) -> dict:
        return {
            "configurable": {"thread_id": investigation_id, "checkpoint_ns": "investigation"},
            "recursion_limit": 80,
        }

    async def _stream_graph(
        self,
        user_id: str,
        investigation_id: str,
        input_value: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        metrics = get_collector(investigation_id)
        config = self._config(investigation_id)
        current_step: Optional[str] = None
        latest_state: Dict[str, Any] = {}
        paused_payload: Optional[Dict[str, Any]] = None

        try:
            async for event in self._workflow.astream(input_value, config, stream_mode="updates"):
                if "__interrupt__" in event:
                    interrupts = event["__interrupt__"]
                    if interrupts:
                        intr = interrupts[0]
                        payload = intr.value if hasattr(intr, "value") else intr
                        if isinstance(payload, dict):
                            paused_payload = {
                                **payload,
                                "fc_id": "langgraph_interrupt",
                                "current_step": current_step,
                            }
                            yield _trace("llm_agent", "action_confirmation_required", paused_payload)
                            yield {"type": "action_confirmation_required", "data": paused_payload}
                            yield {
                                "type": "state_update",
                                "data": {
                                    "final_assessment": latest_state.get("final_assessment"),
                                    "report_markdown": latest_state.get("report_markdown", ""),
                                    "specialist_findings": specialist_findings(latest_state),
                                    "tool_calls": merged_tool_calls(latest_state),
                                    "initial_evidence": latest_state.get("initial_evidence"),
                                    "prior_cases": latest_state.get("prior_cases", []),
                                    "current_phase": "awaiting_decision",
                                },
                            }
                            yield {"type": "_paused", "data": paused_payload}
                    continue

                for node_name, state_update in event.items():
                    step = _NODE_TO_STEP.get(node_name, node_name)
                    if current_step != step:
                        if current_step:
                            yield _trace(current_step, "node_complete", {"status": "success"})
                            metrics.end_node(current_step)
                        current_step = step
                        metrics.start_node(step)
                        yield _trace(step, "node_start", {"user_id": user_id})

                    latest_state.update(state_update)

                    for trace_event in state_update.get("trace_events") or []:
                        trace_step = _NODE_TO_STEP.get(trace_event.get("node", ""), step)
                        yield {
                            "type": "trace",
                            "event": {**trace_event, "node": trace_step},
                        }

            if paused_payload:
                return

            if current_step:
                yield _trace(current_step, "node_complete", {"status": "success"})
                metrics.end_node(current_step)

            assessment = latest_state.get("final_assessment")
            if not assessment:
                assessment = deterministic_assessment(
                    latest_state.get("initial_evidence") or {},
                    latest_state.get("alert_evidence") or {},
                )

            report = latest_state.get("report_markdown") or ""
            if not report:
                report = generate_fallback_report({**latest_state, "final_assessment": assessment})

            try:
                await persist_case_memory(
                    self.aerospike_service, latest_state, investigation_id, user_id
                )
            except Exception as exc:
                logger.warning("[%s] case memory store failed: %s", investigation_id, exc)

            yield {
                "type": "state_update",
                "data": {
                    "final_assessment": assessment,
                    "tool_calls": merged_tool_calls(latest_state),
                    "specialist_findings": specialist_findings(latest_state),
                    "enacted_actions": latest_state.get("enacted_actions", []),
                    "agent_iterations": latest_state.get("agent_iterations", 0),
                    "report_markdown": report,
                    "initial_evidence": latest_state.get("initial_evidence"),
                    "prior_cases": latest_state.get("prior_cases", []),
                    "current_phase": "report",
                    "workflow_status": "completed",
                },
            }

            final_metrics = metrics.get_metrics()
            yield {"type": "metrics", "investigation_id": investigation_id, "data": final_metrics}
            yield {"type": "complete", "investigation_id": investigation_id, "user_id": user_id}
            remove_collector(investigation_id)

        except Exception as exc:
            logger.error("LangGraph workflow error: %s", exc)
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
                "error": str(exc),
            }
            remove_collector(investigation_id)

    async def run_investigation(
        self,
        user_id: str,
        investigation_id: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not self._workflow:
            await self.initialize()

        from workflow.state import create_initial_state

        metrics = get_collector(investigation_id)
        metrics.reset()
        logger.info("Starting LangGraph investigation %s for user %s", investigation_id, user_id)

        initial = create_initial_state(investigation_id, user_id)
        async for event in self._stream_graph(user_id, investigation_id, initial):
            yield event

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
        if not self._workflow:
            await self.initialize()

        logger.info(
            "Resuming LangGraph investigation %s: approved=%s override=%s",
            investigation_id,
            approved,
            override,
        )

        yield _trace(
            "llm_agent",
            "action_decision",
            {
                "approved": bool(approved),
                "decision": (payload or {}).get("decision"),
                "account_id": (payload or {}).get("account_id"),
                "override": override,
            },
        )

        resume_value = {"approved": bool(approved), "override": override, "hint": hint or ""}
        async for event in self._stream_graph(
            user_id,
            investigation_id,
            Command(resume=resume_value),
        ):
            yield event
