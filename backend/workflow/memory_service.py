"""
Shared Aerospike-backed ADK MemoryService factory.

Both investigation engines use the same long-term memory store for cross-case
recall (``case_memory.store_case`` / ``case_memory.recall_cases``).
"""

import logging
from typing import Any, Optional

logger = logging.getLogger("investigation.memory_service")

_memory_service: Optional[Any] = None


def get_memory_service(aerospike_service: Any) -> Any:
    """Return a process-wide AerospikeMemoryService (lazy singleton)."""
    global _memory_service
    if _memory_service is None:
        from adk_aerospike import AerospikeMemoryService

        client = aerospike_service.client
        namespace = aerospike_service.namespace
        _memory_service = AerospikeMemoryService(client, namespace)
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
