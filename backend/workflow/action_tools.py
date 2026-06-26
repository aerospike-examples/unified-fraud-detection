"""
ADK human-in-the-loop action tool.

Destructive dispositions pause via ADK ``tool_context.request_confirmation``;
enforcement delegates to :mod:`workflow.action_core`.
"""

import logging
from typing import Any, Dict

from google.adk.tools import ToolContext

from workflow.action_core import (
    ALL_DECISIONS,
    DESTRUCTIVE_DECISIONS,
    execute_action,
    init_action_services,
)

logger = logging.getLogger("investigation.action_tools")

_aerospike_service: Any = None


def init_action_tools(flagged_account_service: Any, aerospike_service: Any = None) -> None:
    """Bind services for ADK enact_decision (aerospike kept for API compatibility)."""
    global _aerospike_service
    _aerospike_service = aerospike_service
    init_action_services(flagged_account_service)


def enact_decision(decision: str, account_id: str, reason: str, tool_context: ToolContext) -> dict:
    """Enforce the recommended decision (ADK tool with HITL for destructive actions)."""
    decision = (decision or "").strip()
    if decision not in ALL_DECISIONS:
        return {
            "status": "error",
            "message": f"Unknown decision '{decision}'.",
            "valid_decisions": sorted(ALL_DECISIONS),
        }

    destructive = decision in DESTRUCTIVE_DECISIONS

    if destructive:
        confirmation = tool_context.tool_confirmation
        if confirmation is None:
            logger.info("[enact_decision] requesting approval for %s on %s", decision, account_id)
            tool_context.request_confirmation(
                hint=(
                    f"The AI agent recommends **{decision.replace('_', ' ')}** on account "
                    f"{account_id}. Reason: {reason}. Approve this action?"
                ),
                payload={"decision": decision, "account_id": account_id, "reason": reason},
            )
            return {"status": "pending_confirmation", "decision": decision, "account_id": account_id}
        if not confirmation.confirmed:
            logger.info("[enact_decision] analyst REJECTED %s on %s", decision, account_id)
            return {
                "status": "rejected",
                "executed": False,
                "decision": decision,
                "account_id": account_id,
                "message": "Analyst rejected the action; no change made.",
            }
        logger.info("[enact_decision] analyst APPROVED %s on %s", decision, account_id)

    try:
        result = execute_action(decision, account_id, reason)
        actions = list(tool_context.state.get("enacted_actions", []))
        actions.append(result)
        tool_context.state["enacted_actions"] = actions
        return result
    except Exception as e:
        logger.error("[enact_decision] execution failed: %s", e)
        return {"status": "error", "decision": decision, "account_id": account_id, "message": str(e)}
