"""
Investigation engine interface.

Both ADK and LangGraph engines implement :class:`BaseInvestigationEngine` and
emit the same SSE event-dict contract consumed by
:class:`services.investigation_service.InvestigationService`.

SSE event contract
------------------
Each engine yields async dicts with a top-level ``type`` key:

- ``trace`` — ``{"type": "trace", "event": {type, node, timestamp, data}}``
  Trace ``event.type`` values: ``node_start``, ``node_complete``, ``evidence``,
  ``tool_call``, ``agent_iteration``, ``agent_thinking``, ``specialist_finding``,
  ``assessment``, ``action_proposed``, ``action_confirmation_required``,
  ``action_decision``, ``memory_recall``, ``error``.
- ``state_update`` — partial investigation state for the UI
  (``final_assessment``, ``report_markdown``, ``specialist_findings``, etc.)
- ``action_confirmation_required`` — HITL pause payload for the analyst
- ``_paused`` — internal marker; service stashes pending confirmation
- ``metrics`` — per-investigation performance metrics
- ``complete`` — investigation finished successfully
- ``error`` — fatal error
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional


class BaseInvestigationEngine(ABC):
    """Polymorphic investigation workflow backend (ADK or LangGraph)."""

    @abstractmethod
    async def initialize(self) -> None:
        """Build runners, graphs, or other one-time resources."""

    @abstractmethod
    async def close(self) -> None:
        """Release connections held by the engine."""

    @abstractmethod
    def get_workflow_steps(self) -> List[Dict[str, str]]:
        """Four-step UI contract (alert_validation → data_collection → llm_agent → report_generation)."""

    @abstractmethod
    async def run_investigation(
        self,
        user_id: str,
        investigation_id: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run one investigation; may pause for human-in-the-loop approval."""

    @abstractmethod
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
        """Resume after analyst approves/rejects a destructive action."""

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Engine identifier (``adk`` or ``langgraph``)."""
