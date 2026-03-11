"""
Investigation Progress Store

Shared in-memory dictionary that holds live investigation state.
Both the workflow nodes and the poll endpoint read/write to this dict.

Thread-safe in a single-process Python application due to the GIL
and cooperative async multitasking.
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger('investigation.progress')


_store: Dict[str, Dict[str, Any]] = {}


def init_progress(investigation_id: str, user_id: str) -> None:
    """Create an empty progress entry when an investigation starts."""
    _store[investigation_id] = {
        "investigation_id": investigation_id,
        "user_id": user_id,
        "status": "running",
        "currentNode": "",
        "currentPhase": "",
        "completedSteps": [],
        "toolCalls": [],
        "agentIterations": 0,
        "finalAssessment": None,
        "initialEvidence": None,
        "alertEvidence": None,
        "report": None,
        "performanceMetrics": None,
        "error": None,
        "updated_at": datetime.now().isoformat(),
    }
    logger.debug(f"Progress store: init {investigation_id}")


def update_progress(investigation_id: str, updates: Dict[str, Any]) -> None:
    """Merge *updates* into the entry for *investigation_id*."""
    entry = _store.get(investigation_id)
    if entry is None:
        logger.warning(f"Progress store: update called for unknown {investigation_id}")
        return
    entry.update(updates)
    entry["updated_at"] = datetime.now().isoformat()


def get_progress(investigation_id: str) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of the current progress, or None."""
    entry = _store.get(investigation_id)
    if entry is None:
        return None
    return {**entry}


def remove_progress(investigation_id: str) -> None:
    """Remove a completed/errored investigation from the store."""
    _store.pop(investigation_id, None)
    logger.debug(f"Progress store: removed {investigation_id}")
