"""
Investigation Service

Manages fraud investigations via a pluggable engine (ADK or LangGraph).
Provides SSE streaming for real-time progress and human-in-the-loop approval.
"""

import os
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, Optional

from workflow.engines import get_engine, BaseInvestigationEngine
from workflow.llm import LLMConfig

logger = logging.getLogger("investigation.service")


class InvestigationService:
    """Service for managing fraud investigations."""

    def __init__(
        self,
        aerospike_service: Any,
        graph_service: Any,
        engine_name: Optional[str] = None,
        llm_config: Optional[LLMConfig] = None,
    ):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service
        self.engine_name = (
            engine_name or os.environ.get("INVESTIGATION_ENGINE") or "adk"
        ).strip().lower()
        self.llm_config = llm_config or LLMConfig.from_env()

        self.engine: Optional[BaseInvestigationEngine] = None

        self._active_investigations: Dict[str, Dict[str, Any]] = {}
        self._investigation_results: Dict[str, Dict[str, Any]] = {}
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}

        logger.info(
            "Investigation service initialized (engine=%s, llm_provider=%s)",
            self.engine_name,
            self.llm_config.provider,
        )

    async def initialize(self):
        """Build the selected investigation engine."""
        try:
            self.engine = get_engine(
                self.engine_name,
                self.aerospike_service,
                self.graph_service,
                self.llm_config,
            )
            await self.engine.initialize()
            logger.info("%s investigation engine initialized", self.engine.engine_name)
        except Exception as e:
            logger.error("Failed to initialize investigation service: %s", e)
            raise

    async def close(self):
        """Clean up resources."""
        if self.engine:
            await self.engine.close()
        logger.info("Investigation service closed")

    def get_workflow_steps(self) -> list[Dict[str, str]]:
        """Get list of workflow steps for UI."""
        if self.engine:
            return self.engine.get_workflow_steps()
        return get_engine(
            self.engine_name,
            self.aerospike_service,
            self.graph_service,
            self.llm_config,
        ).get_workflow_steps()

    async def start_investigation(
        self,
        user_id: str,
        triggered_by: str = "manual",
    ) -> str:
        """Start a new investigation for a user."""
        investigation_id = f"inv_{uuid.uuid4().hex[:12]}"

        self._active_investigations[investigation_id] = {
            "user_id": user_id,
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "triggered_by": triggered_by,
            "current_step": "alert_validation",
        }

        logger.info("Started investigation %s for user %s", investigation_id, user_id)
        return investigation_id

    async def _consume(
        self,
        investigation_id: str,
        user_id: str,
        event_agen: AsyncGenerator[Dict[str, Any], None],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Translate runner events into SSE events, handle HITL pauses, persist result."""
        final_state = None
        paused = False

        async for event in event_agen:
            event_type = event.get("type", "unknown")

            if event_type == "trace":
                trace = event.get("event", {})
                yield {"event": "trace", "data": trace}
                if investigation_id in self._active_investigations:
                    self._active_investigations[investigation_id]["current_step"] = trace.get(
                        "node", ""
                    )

            elif event_type == "action_confirmation_required":
                yield {"event": "action_confirmation_required", "data": event.get("data", {})}

            elif event_type == "_paused":
                data = event.get("data", {})
                self._pending_confirmations[investigation_id] = {**data, "user_id": user_id}
                if investigation_id in self._active_investigations:
                    self._active_investigations[investigation_id]["status"] = "awaiting_confirmation"
                paused = True

            elif event_type == "state_update":
                data = event.get("data", {})
                yield {
                    "event": "progress",
                    "data": {
                        "node": event.get("node", ""),
                        "phase": data.get("current_phase", ""),
                        **{k: v for k, v in data.items() if k != "trace_events"},
                    },
                }
                if not final_state:
                    final_state = {}
                final_state.update(data)

            elif event_type == "metrics":
                yield {
                    "event": "metrics",
                    "data": {
                        "investigation_id": investigation_id,
                        "data": event.get("data", {}),
                    },
                }

            elif event_type == "complete":
                yield {
                    "event": "complete",
                    "data": {"investigation_id": investigation_id, "user_id": user_id},
                }

            elif event_type == "error":
                yield {
                    "event": "error",
                    "data": {
                        "error": event.get("error", "Unknown error"),
                        "investigation_id": investigation_id,
                    },
                }

        if paused:
            return

        if final_state:
            completed_at = datetime.now().isoformat()
            self._investigation_results[investigation_id] = {
                "user_id": user_id,
                "completed_at": completed_at,
                "state": final_state,
            }
            if self.aerospike_service and self.aerospike_service.is_connected():
                try:
                    kv_data = {
                        "investigation_id": investigation_id,
                        "user_id": user_id,
                        "completed_at": completed_at,
                        "status": "completed",
                        "initial_evidence": final_state.get("initial_evidence", {}),
                        "final_assessment": final_state.get("final_assessment", {}),
                        "tool_calls": final_state.get("tool_calls", []),
                        "spec_findings": final_state.get("specialist_findings", {}),
                        "prior_cases": final_state.get("prior_cases", []),
                        "enacted_actions": final_state.get("enacted_actions", []),
                        "agent_iterations": final_state.get("agent_iterations", 0),
                        "report_markdown": final_state.get("report_markdown", ""),
                        "completed_steps": [
                            "alert_validation",
                            "data_collection",
                            "llm_agent",
                            "report_generation",
                        ],
                    }
                    self.aerospike_service.put_investigation(investigation_id, kv_data)
                    logger.info("Investigation %s persisted to KV store", investigation_id)
                except Exception as e:
                    logger.warning("Failed to persist investigation to KV: %s", e)

        self._pending_confirmations.pop(investigation_id, None)
        if investigation_id in self._active_investigations:
            self._active_investigations[investigation_id]["status"] = "completed"

    async def stream_investigation(
        self,
        user_id: str,
        investigation_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream investigation progress as SSE events (may pause for HITL approval)."""
        if not investigation_id:
            investigation_id = await self.start_investigation(user_id)
        if not self.engine:
            await self.initialize()

        yield {
            "event": "start",
            "data": {
                "investigation_id": investigation_id,
                "user_id": user_id,
                "steps": self.get_workflow_steps(),
                "engine": self.engine.engine_name if self.engine else self.engine_name,
            },
        }

        try:
            async for ev in self._consume(
                investigation_id,
                user_id,
                self.engine.run_investigation(user_id, investigation_id),
            ):
                yield ev
        except Exception as e:
            logger.error("Investigation error: %s", e)
            yield {
                "event": "error",
                "data": {"error": str(e), "investigation_id": investigation_id},
            }
            if investigation_id in self._active_investigations:
                self._active_investigations[investigation_id]["status"] = "error"
                self._active_investigations[investigation_id]["error"] = str(e)

    def has_pending_action(self, investigation_id: str) -> bool:
        """Whether the investigation is paused awaiting analyst approval."""
        return investigation_id in self._pending_confirmations

    async def resume_investigation_action(
        self,
        investigation_id: str,
        approved: bool,
        override: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Resume a paused investigation after analyst approves/rejects the action."""
        pending = self._pending_confirmations.get(investigation_id)
        if not pending:
            yield {
                "event": "error",
                "data": {
                    "error": "No pending action to confirm",
                    "investigation_id": investigation_id,
                },
            }
            return

        user_id = pending["user_id"]
        if not self.engine:
            await self.initialize()

        self._pending_confirmations.pop(investigation_id, None)
        if investigation_id in self._active_investigations:
            self._active_investigations[investigation_id]["status"] = "running"

        yield {
            "event": "start",
            "data": {
                "investigation_id": investigation_id,
                "user_id": user_id,
                "steps": self.get_workflow_steps(),
                "resumed": True,
                "engine": self.engine.engine_name if self.engine else self.engine_name,
            },
        }

        try:
            agen = self.engine.resume_investigation(
                user_id,
                investigation_id,
                fc_id=pending.get("fc_id", "langgraph_interrupt"),
                approved=approved,
                hint=pending.get("hint", ""),
                payload={
                    "decision": pending.get("decision"),
                    "account_id": pending.get("account_id"),
                    "reason": pending.get("reason"),
                },
                override=override,
            )
            async for ev in self._consume(investigation_id, user_id, agen):
                yield ev
        except Exception as e:
            logger.error("Resume investigation error: %s", e)
            yield {
                "event": "error",
                "data": {"error": str(e), "investigation_id": investigation_id},
            }
            if investigation_id in self._active_investigations:
                self._active_investigations[investigation_id]["status"] = "error"

    def get_investigation_status(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an investigation."""
        if investigation_id in self._active_investigations:
            return self._active_investigations[investigation_id]
        return None

    def get_investigation_result(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get result of a completed investigation."""
        if investigation_id in self._investigation_results:
            return self._investigation_results[investigation_id]

        if self.aerospike_service and self.aerospike_service.is_connected():
            kv_result = self.aerospike_service.get_investigation(investigation_id)
            if kv_result:
                self._investigation_results[investigation_id] = {
                    "user_id": kv_result.get("user_id"),
                    "completed_at": kv_result.get("completed_at"),
                    "state": kv_result,
                }
                return self._investigation_results[investigation_id]

        return None

    @staticmethod
    def _restore_bin_names(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Map short Aerospike bin names back to full API field names."""
        if (
            isinstance(record, dict)
            and "spec_findings" in record
            and "specialist_findings" not in record
        ):
            record["specialist_findings"] = record.pop("spec_findings")
        return record

    def get_user_latest_investigation(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the most recent completed investigation for a user."""
        if self.aerospike_service and self.aerospike_service.is_connected():
            return self._restore_bin_names(
                self.aerospike_service.get_user_latest_investigation(user_id)
            )

        user_investigations = [
            {"investigation_id": inv_id, **data}
            for inv_id, data in self._investigation_results.items()
            if data.get("user_id") == user_id
        ]

        if not user_investigations:
            return None

        user_investigations.sort(key=lambda x: x.get("completed_at", ""), reverse=True)
        return user_investigations[0]

    def get_user_investigation_history(self, user_id: str) -> list[Dict[str, Any]]:
        """Get investigation history for a user."""
        if self.aerospike_service and self.aerospike_service.is_connected():
            return self.aerospike_service.get_user_investigation_history(user_id)

        history = []
        for inv_id, data in self._investigation_results.items():
            if data.get("user_id") == user_id:
                history.append(
                    {
                        "investigation_id": inv_id,
                        "completed_at": data.get("completed_at"),
                        "risk_level": data.get("state", {})
                        .get("risk_assessment", {})
                        .get("risk_level"),
                        "recommendation": data.get("state", {})
                        .get("decision", {})
                        .get("recommended_action"),
                    }
                )

        return sorted(history, key=lambda x: x.get("completed_at", ""), reverse=True)

    async def get_investigation_report(self, investigation_id: str) -> Optional[str]:
        """Get the markdown report for an investigation."""
        result = self._investigation_results.get(investigation_id)
        if result:
            return result.get("state", {}).get("report_markdown")
        return None


investigation_service: Optional[InvestigationService] = None
