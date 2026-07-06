"""
Alert Validation Node

Extracts and validates the initial alert trigger context from flagged accounts.
Data Source: Aerospike KV (flagged_accounts set)
"""

from datetime import datetime
from typing import Dict, Any
import logging

from workflow.state import InvestigationState, AlertEvidence, TraceEvent

logger = logging.getLogger('investigation.alert_validation')


def alert_validation_node(
    state: InvestigationState,
    aerospike_service: Any
) -> Dict[str, Any]:
    """
    Extract alert trigger context for the flagged account.
    
    Args:
        state: Current investigation state
        aerospike_service: Aerospike KV service instance
        
    Returns:
        Updated state with alert evidence
    """
    user_id = state["user_id"]
    node_name = "alert_validation"
    
    logger.info(f"[{node_name}] Starting alert validation for user {user_id}")
    
    trace_events = []
    
    # Emit start event
    trace_events.append(TraceEvent(
        type="node_start",
        node=node_name,
        timestamp=datetime.now().isoformat(),
        data={"user_id": user_id}
    ))
    
    try:
        # Get flagged account data from Aerospike KV
        flagged_account = aerospike_service.get_flagged_account(user_id)
        
        if not flagged_account:
            # User might be flagged but not in flagged_accounts set yet
            # Check the user record for workflow status
            user = aerospike_service.get_user(user_id)
            if user and user.get("workflow_status"):
                flagged_account = {
                    "account_id": user_id,
                    "flag_reason": user.get("flag_reason", "Risk score threshold exceeded"),
                    "flagged_date": user.get("flagged_date", datetime.now().isoformat()),
                    "risk_score": user.get("current_risk_score", 75),
                    "risk_factors": user.get("risk_factors", [])
                }
            else:
                # Create a minimal alert evidence for investigation
                flagged_account = {
                    "account_id": user_id,
                    "flag_reason": "Manual investigation requested",
                    "flagged_date": datetime.now().isoformat(),
                    "risk_score": 50,
                    "risk_factors": []
                }
        
        # Determine alert trigger type based on flag reason
        flag_reason = flagged_account.get("flag_reason", "")
        trigger_rule = _determine_trigger_rule(flag_reason)
        
        # Previous resolved flags are not tracked per-user at scale; avoid scanning flagged_accounts.
        previous_flags_count = int(flagged_account.get("previous_flags_count", 0) or 0)
        
        alert_evidence = AlertEvidence(
            trigger_type=trigger_rule["type"],
            trigger_rule=trigger_rule["name"],
            trigger_timestamp=flagged_account.get("flagged_date", datetime.now().isoformat()),
            flag_reason=flag_reason,
            original_score=float(flagged_account.get("risk_score", 0)),
            previous_flags_count=previous_flags_count
        )
        
        # Emit evidence event
        trace_events.append(TraceEvent(
            type="evidence",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={
                "trigger_type": alert_evidence["trigger_type"],
                "trigger_rule": alert_evidence["trigger_rule"],
                "original_score": alert_evidence["original_score"],
                "previous_flags_count": alert_evidence["previous_flags_count"]
            }
        ))
        
        # Emit complete event
        trace_events.append(TraceEvent(
            type="node_complete",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={"status": "success"}
        ))
        
        logger.info(f"[{node_name}] Alert validation complete - trigger: {alert_evidence['trigger_type']}")
        
        return {
            "alert_evidence": alert_evidence,
            "current_node": "data_collection",
            "current_phase": "evidence",
            "trace_events": trace_events
        }
        
    except Exception as e:
        logger.error(f"[{node_name}] Error during alert validation: {e}")
        
        trace_events.append(TraceEvent(
            type="error",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={"error": str(e)}
        ))
        
        return {
            "alert_evidence": None,
            "current_node": "data_collection",
            "current_phase": "evidence",
            "error_message": str(e),
            "trace_events": trace_events
        }


def _determine_trigger_rule(flag_reason: str) -> Dict[str, str]:
    """Determine the alert trigger rule based on flag reason."""
    flag_reason_lower = flag_reason.lower()
    
    if any(kw in flag_reason_lower for kw in ["device", "fingerprint", "browser"]):
        return {"type": "RT2", "name": "Flagged Device Detection"}
    elif any(kw in flag_reason_lower for kw in ["supernode", "sender", "connection", "network"]):
        return {"type": "RT3", "name": "Supernode Detection"}
    elif any(kw in flag_reason_lower for kw in ["velocity", "rapid", "succession"]):
        return {"type": "RT1", "name": "Velocity Spike Detection"}
    elif any(kw in flag_reason_lower for kw in ["amount", "high-value", "threshold"]):
        return {"type": "RT1", "name": "Transaction Amount Threshold"}
    elif any(kw in flag_reason_lower for kw in ["pattern", "anomaly", "unusual"]):
        return {"type": "ML", "name": "Pattern Anomaly Detection"}
    else:
        return {"type": "MANUAL", "name": "Manual Review Request"}
