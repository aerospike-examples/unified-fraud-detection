"""
Engine-neutral fraud-mitigation action enforcement.

Maps agent/analyst dispositions to ``flagged_account_service`` calls. Used by
both ADK (``enact_decision`` tool) and LangGraph (action node + interrupt resume).
"""

import logging
from typing import Any, Dict

logger = logging.getLogger("investigation.action_core")

DESTRUCTIVE_DECISIONS = {"temporary_freeze", "full_block", "escalate_compliance"}
NONDESTRUCTIVE_DECISIONS = {"allow_monitor", "step_up_auth"}
ALL_DECISIONS = DESTRUCTIVE_DECISIONS | NONDESTRUCTIVE_DECISIONS

_flagged_account_service: Any = None


def init_action_services(flagged_account_service: Any) -> None:
    """Bind the flagged-account service used to enforce actions."""
    global _flagged_account_service
    _flagged_account_service = flagged_account_service
    logger.info("Action enforcement bound to flagged-account service")


def _user_id_for(account_id: str) -> str:
    """Derive owning user_id from account_id (A000396803 -> U0003968)."""
    return f"U{account_id[1:-2]}" if account_id.startswith("A") else account_id


def execute_action(decision: str, account_id: str, reason: str) -> Dict[str, Any]:
    """Enforce a disposition on the flagged account."""
    if _flagged_account_service is None:
        raise RuntimeError("Action services not initialized — call init_action_services()")

    note = f"[AI agent action: {decision}] {reason}"[:480]

    if decision == "clear":
        result = _flagged_account_service.resolve_account(account_id, "cleared", note)
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {
            "status": "executed",
            "action": decision,
            "account_id": account_id,
            "effect": "alert cleared — account marked safe (not fraud)",
            "ok": ok,
        }

    if decision == "temporary_freeze":
        result = _flagged_account_service.freeze_account(account_id, note, frozen=True)
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {
            "status": "executed",
            "action": decision,
            "account_id": account_id,
            "effect": "account temporarily frozen pending review (reversible) — not marked fraudulent",
            "ok": ok,
        }

    if decision == "full_block":
        result = _flagged_account_service.resolve_account(account_id, "confirmed_fraud", note)
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {
            "status": "executed",
            "action": decision,
            "account_id": account_id,
            "effect": "account blocked: marked fraudulent and devices flagged",
            "ok": ok,
        }

    if decision == "escalate_compliance":
        result = _flagged_account_service.resolve_flagged_account(
            _user_id_for(account_id), "under_investigation", note
        )
        ok = result is not None
        return {
            "status": "executed",
            "action": decision,
            "account_id": account_id,
            "effect": "case escalated to compliance (under_investigation)",
            "ok": ok,
        }

    if decision == "step_up_auth":
        result = _flagged_account_service.mark_monitoring(
            account_id, f"{note} (step-up authentication required on next login)"
        )
        ok = bool(result.get("success", True)) if isinstance(result, dict) else True
        return {
            "status": "executed",
            "action": decision,
            "account_id": account_id,
            "effect": "step-up authentication required; account allowed under active monitoring",
            "ok": ok,
        }

    result = _flagged_account_service.mark_monitoring(account_id, note)
    ok = bool(result.get("success", True)) if isinstance(result, dict) else True
    return {
        "status": "executed",
        "action": decision,
        "account_id": account_id,
        "effect": "account allowed and moved to active monitoring (not fraud)",
        "ok": ok,
    }
