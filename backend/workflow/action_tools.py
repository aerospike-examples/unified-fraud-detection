"""
Agent Action Tools (with human-in-the-loop)

Tools that let the investigation agent take real fraud-mitigation ACTIONS on a
flagged account — not just recommend them. Destructive actions
(temporary_freeze, full_block, escalate_compliance) pause the agent and require
an analyst's approval via ADK's native tool-confirmation mechanism
(``tool_context.request_confirmation``); non-destructive ones
(allow_monitor, step_up_auth) execute immediately.

On approval, the action is enforced through the existing
``flagged_account_service.resolve_account`` path (updates Graph fraud_flag +
account-fact + flags devices), so the agent's decision has a real, visible side
effect — gated by a human.
"""

import logging
from typing import Any, Dict

from google.adk.tools import ToolContext

logger = logging.getLogger('investigation.action_tools')

# Decisions that require analyst approval before they take effect.
DESTRUCTIVE_DECISIONS = {"temporary_freeze", "full_block", "escalate_compliance"}
NONDESTRUCTIVE_DECISIONS = {"allow_monitor", "step_up_auth"}
ALL_DECISIONS = DESTRUCTIVE_DECISIONS | NONDESTRUCTIVE_DECISIONS

# Bound at startup.
_flagged_account_service: Any = None
_aerospike_service: Any = None


def init_action_tools(flagged_account_service: Any, aerospike_service: Any) -> None:
    """Bind the services used to enforce actions."""
    global _flagged_account_service, _aerospike_service
    _flagged_account_service = flagged_account_service
    _aerospike_service = aerospike_service
    logger.info("Agent action tools bound to flagged-account + Aerospike services")


def _user_id_for(account_id: str) -> str:
    """Derive the owning user_id from an account_id (A000396803 -> U0003968).
    Flagged-account records are keyed by user_id."""
    return f"U{account_id[1:-2]}" if account_id.startswith("A") else account_id


def _execute_action(decision: str, account_id: str, reason: str) -> Dict[str, Any]:
    """Enforce the decision via the existing resolution path. Each destructive
    decision maps to a DISTINCT outcome:
      - temporary_freeze   -> reversible hold (frozen flag), NOT fraud
      - full_block         -> confirmed fraud + devices flagged (irreversible)
      - escalate_compliance-> case moved to compliance review (under_investigation)
    """
    note = f"[AI agent action: {decision}] {reason}"[:480]

    if decision == "clear":
        # Analyst override only: alert is legitimate — clear the account (not fraud).
        # The agent itself never produces this decision.
        result = _flagged_account_service.resolve_account(account_id, "cleared", note)
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {"status": "executed", "action": decision, "account_id": account_id,
                "effect": "alert cleared — account marked safe (not fraud)", "ok": ok}

    if decision == "temporary_freeze":
        # Reversible hold: set the `frozen` flag; do NOT mark fraud or flag devices.
        result = _flagged_account_service.freeze_account(account_id, note, frozen=True)
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {"status": "executed", "action": decision, "account_id": account_id,
                "effect": "account temporarily frozen pending review (reversible) — not marked fraudulent",
                "ok": ok}

    if decision == "full_block":
        # Irreversible: mark the account fraudulent and flag its devices.
        result = _flagged_account_service.resolve_account(account_id, "confirmed_fraud", note)
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {"status": "executed", "action": decision, "account_id": account_id,
                "effect": "account blocked: marked fraudulent and devices flagged", "ok": ok}

    if decision == "escalate_compliance":
        # Move the flagged case to compliance review (flagged record is keyed by user_id).
        result = _flagged_account_service.resolve_flagged_account(
            _user_id_for(account_id), "under_investigation", note)
        ok = result is not None
        return {"status": "executed", "action": decision, "account_id": account_id,
                "effect": "case escalated to compliance (under_investigation)", "ok": ok}

    if decision == "step_up_auth":
        # Non-destructive: require step-up auth; move the account to monitoring so
        # it leaves the pending queue (reviewed, allowed with extra verification).
        result = _flagged_account_service.mark_monitoring(
            account_id, f"{note} (step-up authentication required on next login)")
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {"status": "executed", "action": decision, "account_id": account_id,
                "effect": "step-up authentication required; account allowed under active monitoring",
                "ok": ok}

    # allow_monitor (or anything else): no fraud action, but record the decision —
    # move the account to monitoring so it leaves the pending-review queue.
    result = _flagged_account_service.mark_monitoring(account_id, note)
    ok = bool(result.get("success", True)) if isinstance(result, dict) else True
    return {"status": "executed", "action": decision, "account_id": account_id,
            "effect": "account allowed and moved to active monitoring (not fraud)", "ok": ok}


def enact_decision(decision: str, account_id: str, reason: str, tool_context: ToolContext) -> dict:
    """Enforce your recommended decision on the flagged account. Call this once,
    AFTER submit_assessment, with the same decision and the primary flagged
    account_id.

    Destructive actions (temporary_freeze, full_block, escalate_compliance) will
    PAUSE and require a human analyst's approval before they take effect.
    Non-destructive actions (allow_monitor, step_up_auth) take effect immediately.

    Args:
        decision: One of allow_monitor, step_up_auth, temporary_freeze,
            full_block, escalate_compliance.
        account_id: The primary flagged account to act on (e.g. A000885901).
        reason: One-sentence justification for the action (shown to the analyst).
    """
    decision = (decision or "").strip()
    if decision not in ALL_DECISIONS:
        return {"status": "error", "message": f"Unknown decision '{decision}'.",
                "valid_decisions": sorted(ALL_DECISIONS)}

    destructive = decision in DESTRUCTIVE_DECISIONS

    if destructive:
        confirmation = tool_context.tool_confirmation
        if confirmation is None:
            # First call → pause and ask the analyst.
            logger.info(f"[enact_decision] requesting approval for {decision} on {account_id}")
            tool_context.request_confirmation(
                hint=(f"The AI agent recommends **{decision.replace('_', ' ')}** on account "
                      f"{account_id}. Reason: {reason}. Approve this action?"),
                payload={"decision": decision, "account_id": account_id, "reason": reason},
            )
            return {"status": "pending_confirmation", "decision": decision, "account_id": account_id}
        if not confirmation.confirmed:
            # Analyst rejected → do not enforce.
            logger.info(f"[enact_decision] analyst REJECTED {decision} on {account_id}")
            return {"status": "rejected", "executed": False, "decision": decision,
                    "account_id": account_id, "message": "Analyst rejected the action; no change made."}
        logger.info(f"[enact_decision] analyst APPROVED {decision} on {account_id}")

    # Non-destructive, or destructive-and-approved → enforce.
    try:
        result = _execute_action(decision, account_id, reason)
        # Record the enacted action in session state for the report + UI.
        actions = list(tool_context.state.get("enacted_actions", []))
        actions.append(result)
        tool_context.state["enacted_actions"] = actions
        return result
    except Exception as e:
        logger.error(f"[enact_decision] execution failed: {e}")
        return {"status": "error", "decision": decision, "account_id": account_id, "message": str(e)}
