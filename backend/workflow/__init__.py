"""
Investigation Workflow Package

Polymorphic fraud-investigation backends:

- ``adk`` — Google ADK SequentialAgent (Aerospike sessions/memory/artifacts)
- ``langgraph`` — LangGraph StateGraph with ADK feature parity

Select via ``INVESTIGATION_ENGINE`` (default ``adk``).
"""

from workflow.engines import get_engine, SUPPORTED_ENGINES, BaseInvestigationEngine
from workflow.state import InvestigationState, create_initial_state

__all__ = [
    "BaseInvestigationEngine",
    "get_engine",
    "SUPPORTED_ENGINES",
    "InvestigationState",
    "create_initial_state",
]
