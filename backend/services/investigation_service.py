"""
Investigation Service

Manages fraud investigations using the LangGraph workflow.
Progress is tracked via an in-memory store and consumed by polling.
"""

import asyncio
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, Optional

from workflow.graph import create_investigation_workflow, run_investigation, get_workflow_steps
from workflow.state import InvestigationState
from services.investigation_progress import (
    init_progress,
    update_progress,
    get_progress,
    remove_progress,
)

logger = logging.getLogger('investigation.service')


class InvestigationService:
    """
    Service for managing fraud investigations.
    """
    
    def __init__(
        self,
        aerospike_service: Any,
        graph_service: Any,
    ):
        self.aerospike_service = aerospike_service
        self.graph_service = graph_service
        
        # Workflow instance
        self.workflow = None
        
        # Active investigations cache
        self._active_investigations: Dict[str, Dict[str, Any]] = {}
        self._investigation_results: Dict[str, Dict[str, Any]] = {}
        
        logger.info("Investigation service initialized")
    
    async def initialize(self):
        """Initialize the investigation workflow."""
        try:
            self.workflow = create_investigation_workflow(
                self.aerospike_service,
                self.graph_service,
            )
            logger.info("Investigation workflow initialized")
        except Exception as e:
            logger.error(f"Failed to initialize investigation service: {e}")
            raise
    
    async def close(self):
        """Clean up resources."""
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
    
    async def stream_investigation(
        self,
        user_id: str,
        investigation_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream investigation progress as SSE events.
        
        Args:
            user_id: User ID to investigate
            investigation_id: Optional existing investigation ID
            
        Yields:
            SSE event data
        """
        # Create investigation ID if not provided
        if not investigation_id:
            investigation_id = await self.start_investigation(user_id)
        
        if not self.workflow:
            await self.initialize()
        
        # Initialize shared progress store for polling
        init_progress(investigation_id, user_id)
        
        # Yield initial event
        yield {
            "event": "start",
            "data": {
                "investigation_id": investigation_id,
                "user_id": user_id,
                "steps": self.get_workflow_steps()
            }
        }
        
        try:
            # Run the workflow and stream events
            final_state = None
            
            async for event in run_investigation(self.workflow, user_id, investigation_id):
                event_type = event.get("type", "unknown")
                
                if event_type == "trace":
                    trace = event.get("event", {})
                    yield {
                        "event": "trace",
                        "data": trace
                    }
                    
                    # Update active investigation tracking
                    if investigation_id in self._active_investigations:
                        self._active_investigations[investigation_id]["current_step"] = trace.get("node", "")
                    
                    # Write to progress store for polling
                    progress_updates: Dict[str, Any] = {"currentNode": trace.get("node", "")}
                    if trace.get("type") == "node_complete":
                        current_completed = (get_progress(investigation_id) or {}).get("completedSteps", [])
                        node = trace.get("node", "")
                        if node and node not in current_completed:
                            progress_updates["completedSteps"] = current_completed + [node]
                    update_progress(investigation_id, progress_updates)
                
                elif event_type == "state_update":
                    node = event.get("node", "")
                    data = event.get("data", {})
                    
                    yield {
                        "event": "progress",
                        "data": {
                            "node": node,
                            "phase": data.get("current_phase", ""),
                            **{k: v for k, v in data.items() if k not in ["trace_events"]}
                        }
                    }
                    
                    # Capture final state components
                    if not final_state:
                        final_state = {}
                    final_state.update(data)
                    
                    # Write to progress store for polling
                    state_updates: Dict[str, Any] = {
                        "currentNode": node,
                        "currentPhase": data.get("current_phase", ""),
                    }
                    if data.get("initial_evidence"):
                        state_updates["initialEvidence"] = data["initial_evidence"]
                    if data.get("alert_evidence"):
                        state_updates["alertEvidence"] = data["alert_evidence"]
                    if data.get("final_assessment"):
                        state_updates["finalAssessment"] = data["final_assessment"]
                    if data.get("report_markdown"):
                        state_updates["report"] = data["report_markdown"]
                    update_progress(investigation_id, state_updates)
                
                elif event_type == "metrics":
                    yield {
                        "event": "metrics",
                        "data": {
                            "investigation_id": investigation_id,
                            "data": event.get("data", {})
                        }
                    }
                    update_progress(investigation_id, {
                        "performanceMetrics": event.get("data", {}),
                    })
                
                elif event_type == "complete":
                    yield {
                        "event": "complete",
                        "data": {
                            "investigation_id": investigation_id,
                            "user_id": user_id
                        }
                    }
                    update_progress(investigation_id, {
                        "status": "completed",
                        "completedSteps": ["alert_validation", "data_collection", "llm_agent", "report_generation"],
                    })
                    
                elif event_type == "error":
                    yield {
                        "event": "error",
                        "data": {
                            "error": event.get("error", "Unknown error"),
                            "investigation_id": investigation_id
                        }
                    }
                    update_progress(investigation_id, {
                        "status": "error",
                        "error": event.get("error", "Unknown error"),
                    })
            
            # Store final result
            if final_state:
                completed_at = datetime.now().isoformat()
                
                # Store in memory cache
                self._investigation_results[investigation_id] = {
                    "user_id": user_id,
                    "completed_at": completed_at,
                    "state": final_state
                }
                
                # Persist to Aerospike KV for durability
                if self.aerospike_service and self.aerospike_service.is_connected():
                    try:
                        kv_data = {
                            "investigation_id": investigation_id,
                            "user_id": user_id,
                            "completed_at": completed_at,
                            "status": "completed",
                            # Evidence collected
                            "initial_evidence": final_state.get("initial_evidence", {}),
                            # AI assessment
                            "final_assessment": final_state.get("final_assessment", {}),
                            # Tool calls made
                            "tool_calls": final_state.get("tool_calls", []),
                            # Agent iterations
                            "agent_iterations": final_state.get("agent_iterations", 0),
                            # Generated report
                            "report_markdown": final_state.get("report_markdown", ""),
                            # Completed steps - if investigation completed successfully, all steps are done
                            "completed_steps": ["alert_validation", "data_collection", "llm_agent", "report_generation"],
                        }
                        self.aerospike_service.put_investigation(investigation_id, kv_data)
                        logger.info(f"Investigation {investigation_id} persisted to KV store")
                    except Exception as e:
                        logger.warning(f"Failed to persist investigation to KV: {e}")
            
            # Update status
            if investigation_id in self._active_investigations:
                self._active_investigations[investigation_id]["status"] = "completed"
                
        except Exception as e:
            logger.error(f"Investigation error: {e}")
            
            yield {
                "event": "error",
                "data": {
                    "error": str(e),
                    "investigation_id": investigation_id
                }
            }
            
            update_progress(investigation_id, {
                "status": "error",
                "error": str(e),
            })
            
            if investigation_id in self._active_investigations:
                self._active_investigations[investigation_id]["status"] = "error"
                self._active_investigations[investigation_id]["error"] = str(e)
    
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
