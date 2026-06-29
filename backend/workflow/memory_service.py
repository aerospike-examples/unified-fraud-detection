"""
Shared Aerospike-backed case memory (engine-neutral).

Both investigation engines (ADK and LangGraph) use the same long-term memory
store for cross-case recall (``case_memory.store_case`` / ``case_memory.recall_cases``).

Implemented via ``adk-aerospike``'s ``AerospikeMemoryService`` with a neutral
``case_`` set prefix so the Aerospike set is ``case_memory``, not ``adk_memory``.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger("investigation.memory_service")

# adk-aerospike set_prefix → full set name is f"{prefix}memory"
CASE_MEMORY_SET_PREFIX = "case_"
CASE_MEMORY_SET = f"{CASE_MEMORY_SET_PREFIX}memory"

_memory_service: Optional[Any] = None


def get_memory_service(aerospike_service: Any) -> Any:
    """Return a process-wide AerospikeMemoryService (lazy singleton)."""
    global _memory_service
    if _memory_service is None:
        from adk_aerospike import AerospikeMemoryService

        client = aerospike_service.client
        namespace = aerospike_service.namespace
        _memory_service = AerospikeMemoryService(
            client, namespace, set_prefix=CASE_MEMORY_SET_PREFIX,
        )
        logger.info("Case memory service ready (Aerospike set %s)", CASE_MEMORY_SET)
    return _memory_service


def close_memory_service() -> None:
    """Close the singleton memory service."""
    global _memory_service
    if _memory_service is not None:
        try:
            _memory_service.close()
        except Exception:
            pass
        _memory_service = None
