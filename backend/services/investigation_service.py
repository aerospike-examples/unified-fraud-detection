"""
Investigation Service

Manages fraud investigations using the Google ADK agent (Aerospike-backed
sessions, memory, and artifacts). Provides SSE streaming for real-time progress.
"""

import os
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, Optional

from workflow.runner import build_runner, run_investigation, resume_investigation, get_workflow_steps

logger = logging.getLogger('investigation.service')


class InvestigationService:
    """
    Service for managing fraud investigations.
    """

    def __init__(
        self,
        aerospike_service: Any,
        graph_service: Any,
        model: Optional[str] = None,
    ):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service
        self.model = model or os.environ.get("ADK_MODEL", "gemini-3.5-flash")

        # ADK runner (built in initialize())
        self.inv_runner = None

        # Active investigations cache
        self._active_investigations: Dict[str, Dict[str, Any]] = {}
        self._investigation_results: Dict[str, Dict[str, Any]] = {}
        # Investigations paused awaiting analyst action approval (HITL)
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}

        logger.info(f"Investigation service initialized (ADK model={self.model})")

    async def initialize(self):
        """Build the ADK runner backed by Aerospike."""
        try:
            self.inv_runner = build_runner(
                self.aerospike_service,
                self.graph_service,
                self.model,
            )
            logger.info("ADK investigation runner initialized")
        except Exception as e:
            logger.error(f"Failed to initialize investigation service: {e}")
            raise

    async def close(self):
        """Clean up resources."""
        if self.inv_runner:
            self.inv_runner.close()
        logger.info("Investigation service closed")
    
    def get_workflow_steps(self) -> list[Dict[str, str]]:
        """Get list of workflow steps for UI."""
        return get_workflow_steps()
    
    async def start_investigation(
        self,
        user_id: str,
        triggered_by: str = "manual"
    ) -> str:
        """
        Start a new investigation for a user.
        
        Args:
            user_id: User ID to investigate
            triggered_by: What triggered the investigation
            
        Returns:
            Investigation ID
        """
        investigation_id = f"inv_{uuid.uuid4().hex[:12]}"
        
        # Track active investigation
        self._active_investigations[investigation_id] = {
            "user_id": user_id,
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "triggered_by": triggered_by,
            "current_step": "alert_validation"
        }
        
        logger.info(f"Started investigation {investigation_id} for user {user_id}")
        
        return investigation_id
    
    async def _consume(
        self,
        investigation_id: str,
        user_id: str,
        event_agen: AsyncGenerator[Dict[str, Any], None],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Translate runner events into SSE events, handle HITL pauses, and persist
        the final result on completion. Shared by the initial run and the resume."""
        final_state = None
        paused = False

        async for event in event_agen:
            event_type = event.get("type", "unknown")

            if event_type == "trace":
                trace = event.get("event", {})
                yield {"event": "trace", "data": trace}
                if investigation_id in self._active_investigations:
                    self._active_investigations[investigation_id]["current_step"] = trace.get("node", "")

            elif event_type == "action_confirmation_required":
                # Surface the proposed action so the analyst can approve/reject.
                yield {"event": "action_confirmation_required", "data": event.get("data", {})}

            elif event_type == "_paused":
                # Internal: stash the paused confirmation so /resume can continue it.
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
                yield {"event": "metrics", "data": {"investigation_id": investigation_id, "data": event.get("data", {})}}

            elif event_type == "complete":
                yield {"event": "complete", "data": {"investigation_id": investigation_id, "user_id": user_id}}

            elif event_type == "error":
                yield {"event": "error", "data": {"error": event.get("error", "Unknown error"), "investigation_id": investigation_id}}

        if paused:
            # Investigation is parked awaiting approval; do not persist yet.
            return

        # Store + persist the completed result.
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
                        "specialist_findings": final_state.get("specialist_findings", {}),
                        "enacted_actions": final_state.get("enacted_actions", []),
                        "agent_iterations": final_state.get("agent_iterations", 0),
                        "report_markdown": final_state.get("report_markdown", ""),
                        "completed_steps": ["alert_validation", "data_collection", "llm_agent", "report_generation"],
                    }
                    self.aerospike_service.put_investigation(investigation_id, kv_data)
                    logger.info(f"Investigation {investigation_id} persisted to KV store")
                except Exception as e:
                    logger.warning(f"Failed to persist investigation to KV: {e}")

        self._pending_confirmations.pop(investigation_id, None)
        if investigation_id in self._active_investigations:
            self._active_investigations[investigation_id]["status"] = "completed"

    async def stream_investigation(
        self,
        user_id: str,
        investigation_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream investigation progress as SSE events (may pause for HITL approval)."""
        if not investigation_id:
            investigation_id = await self.start_investigation(user_id)
        if not self.inv_runner:
            await self.initialize()

        yield {"event": "start", "data": {
            "investigation_id": investigation_id, "user_id": user_id, "steps": self.get_workflow_steps(),
        }}

        try:
            async for ev in self._consume(
                investigation_id, user_id,
                run_investigation(self.inv_runner, user_id, investigation_id),
            ):
                yield ev
        except Exception as e:
            logger.error(f"Investigation error: {e}")
            yield {"event": "error", "data": {"error": str(e), "investigation_id": investigation_id}}
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
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Resume a paused investigation after the analyst approves/rejects the action."""
        pending = self._pending_confirmations.get(investigation_id)
        if not pending:
            yield {"event": "error", "data": {"error": "No pending action to confirm", "investigation_id": investigation_id}}
            return

        user_id = pending["user_id"]
        if not self.inv_runner:
            await self.initialize()

        # Claim it so it can't be double-resumed.
        self._pending_confirmations.pop(investigation_id, None)
        if investigation_id in self._active_investigations:
            self._active_investigations[investigation_id]["status"] = "running"

        yield {"event": "start", "data": {
            "investigation_id": investigation_id, "user_id": user_id,
            "steps": self.get_workflow_steps(), "resumed": True,
        }}

        try:
            agen = resume_investigation(
                self.inv_runner, user_id, investigation_id,
                fc_id=pending["fc_id"], approved=approved,
                hint=pending.get("hint", ""),
                payload={"decision": pending.get("decision"), "account_id": pending.get("account_id"),
                         "reason": pending.get("reason")},
            )
            async for ev in self._consume(investigation_id, user_id, agen):
                yield ev
        except Exception as e:
            logger.error(f"Resume investigation error: {e}")
            yield {"event": "error", "data": {"error": str(e), "investigation_id": investigation_id}}
            if investigation_id in self._active_investigations:
                self._active_investigations[investigation_id]["status"] = "error"
    
    def get_investigation_status(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an investigation."""
        if investigation_id in self._active_investigations:
            return self._active_investigations[investigation_id]
        return None
    
    def get_investigation_result(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get result of a completed investigation."""
        # Check memory cache first
        if investigation_id in self._investigation_results:
            return self._investigation_results[investigation_id]
        
        # Fall back to KV store
        if self.aerospike_service and self.aerospike_service.is_connected():
            kv_result = self.aerospike_service.get_investigation(investigation_id)
            if kv_result:
                # Cache it in memory
                self._investigation_results[investigation_id] = {
                    "user_id": kv_result.get("user_id"),
                    "completed_at": kv_result.get("completed_at"),
                    "state": kv_result  # The KV record contains the state fields directly
                }
                return self._investigation_results[investigation_id]
        
        return None
    
    def get_user_latest_investigation(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get the most recent completed investigation for a user."""
        # Check KV store (has persistence)
        if self.aerospike_service and self.aerospike_service.is_connected():
            return self.aerospike_service.get_user_latest_investigation(user_id)
        
        # Fall back to memory cache
        user_investigations = [
            {"investigation_id": inv_id, **data}
            for inv_id, data in self._investigation_results.items()
            if data.get("user_id") == user_id
        ]
        
        if not user_investigations:
            return None
        
        user_investigations.sort(
            key=lambda x: x.get("completed_at", ""),
            reverse=True
        )
        
        return user_investigations[0]
    
    def get_user_investigation_history(self, user_id: str) -> list[Dict[str, Any]]:
        """Get investigation history for a user."""
        # Check KV store first (has persistence)
        if self.aerospike_service and self.aerospike_service.is_connected():
            return self.aerospike_service.get_user_investigation_history(user_id)
        
        # Fall back to memory cache
        history = []
        
        for inv_id, data in self._investigation_results.items():
            if data.get("user_id") == user_id:
                history.append({
                    "investigation_id": inv_id,
                    "completed_at": data.get("completed_at"),
                    "risk_level": data.get("state", {}).get("risk_assessment", {}).get("risk_level"),
                    "recommendation": data.get("state", {}).get("decision", {}).get("recommended_action")
                })
        
        return sorted(history, key=lambda x: x.get("completed_at", ""), reverse=True)
    
    async def get_investigation_report(self, investigation_id: str) -> Optional[str]:
        """Get the markdown report for an investigation."""
        result = self._investigation_results.get(investigation_id)
        if result:
            return result.get("state", {}).get("report_markdown")
        return None


# Singleton instance (to be initialized in main.py)
investigation_service: Optional[InvestigationService] = None
