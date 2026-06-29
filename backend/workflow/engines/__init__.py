"""
Investigation engine factory.

Select backend via ``INVESTIGATION_ENGINE`` environment variable:

- ``adk`` (default) — Google ADK SequentialAgent
- ``langgraph`` — LangGraph StateGraph with feature parity
- ``mock`` — deterministic engine with no LLM calls (tests and demos)
"""

import os
from typing import Any, Optional, Tuple

from workflow.engines.base import BaseInvestigationEngine
from workflow.llm import LLMConfig

SUPPORTED_ENGINES: Tuple[str, ...] = ("adk", "langgraph", "mock")


def get_engine(
    name: Optional[str],
    aerospike_service: Any,
    graph_service: Any,
    llm_config: Optional[LLMConfig] = None,
) -> BaseInvestigationEngine:
    """Return the configured investigation engine instance."""
    engine_name = (name or os.environ.get("INVESTIGATION_ENGINE") or "adk").strip().lower()
    llm_config = llm_config or LLMConfig.from_env()

    if engine_name == "langgraph":
        from workflow.engines.langgraph_engine import LangGraphEngine

        return LangGraphEngine(aerospike_service, graph_service, llm_config)
    if engine_name == "adk":
        from workflow.engines.adk_engine import AdkEngine

        return AdkEngine(aerospike_service, graph_service, llm_config)
    if engine_name == "mock":
        from workflow.engines.mock_engine import MockEngine

        return MockEngine(aerospike_service, graph_service, llm_config)

    raise ValueError(
        f"Unknown INVESTIGATION_ENGINE '{engine_name}'. Supported: {', '.join(SUPPORTED_ENGINES)}"
    )


def __getattr__(name: str):
    """Lazy exports for concrete engine classes."""
    if name == "AdkEngine":
        from workflow.engines.adk_engine import AdkEngine

        return AdkEngine
    if name == "LangGraphEngine":
        from workflow.engines.langgraph_engine import LangGraphEngine

        return LangGraphEngine
    if name == "MockEngine":
        from workflow.engines.mock_engine import MockEngine

        return MockEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseInvestigationEngine",
    "AdkEngine",
    "LangGraphEngine",
    "MockEngine",
    "get_engine",
    "SUPPORTED_ENGINES",
]
