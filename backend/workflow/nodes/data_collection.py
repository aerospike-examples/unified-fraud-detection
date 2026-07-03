"""
Data Collection Node

Gathers initial evidence from Aerospike KV store before the LLM agent takes over.
Collects user profile, accounts, devices, and pre-computed risk features.
The LLM agent decides what additional data to pull via tools.

Data Source: Aerospike KV. In remote mode, the KV working set for the user is
lazily materialized from the Graph on a cache miss (see _ensure_remote_hydration)
before the KV reads run.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional
import logging
import time

from workflow.state import InvestigationState, InitialEvidence, TraceEvent
from workflow.metrics import get_collector

logger = logging.getLogger('investigation.data_collection')


def data_collection_node(
    state: InvestigationState,
    aerospike_service: Any,
    graph_service: Any  # used in remote mode for lazy KV hydration on cache miss
) -> Dict[str, Any]:
    """
    Collect initial evidence for LLM reasoning agent from KV store.
    
    Gathers baseline data (all from Aerospike KV):
    - User profile
    - Accounts map (with balances, is_fraud flags)
    - Devices map (with is_fraud flags)
    - Pre-computed account risk features (15 features per account)
    - Pre-computed device risk features (5 features per device)
    
    The LLM agent will request transaction data and network analysis via tools.
    
    Args:
        state: Current investigation state
        aerospike_service: Aerospike KV service instance
        graph_service: Aerospike Graph service instance (not used in this node)
        
    Returns:
        Updated state with initial evidence
    """
    user_id = state["user_id"]
    investigation_id = state["investigation_id"]
    node_name = "data_collection"
    
    # Get metrics collector for this investigation
    metrics = get_collector(investigation_id)
    
    logger.info(f"[{node_name}] Collecting initial data for user {user_id} (KV-only)")
    
    trace_events = []
    
    # Emit start event
    trace_events.append(TraceEvent(
        type="node_start",
        node=node_name,
        timestamp=datetime.now().isoformat(),
        data={"user_id": user_id}
    ))
    
    try:
        # 0. Remote mode: the KV working set is populated lazily. If this user's
        # record hasn't been materialized yet, build it from the Graph now so the
        # KV reads below hit real data (and the cache is warm next time).
        start = time.time()
        hydrated = _ensure_remote_hydration(aerospike_service, graph_service, user_id)
        if hydrated:
            metrics.track_db_call("hydrate_from_graph", "Graph", (time.time() - start) * 1000)

        # 1. User Profile from KV
        start = time.time()
        user_profile = _get_user_profile(aerospike_service, user_id)
        metrics.track_db_call("get_user_profile", "KV", (time.time() - start) * 1000)
        
        # 2. Accounts map from KV (nested in user record)
        start = time.time()
        accounts = _get_user_accounts(aerospike_service, user_id)
        metrics.track_db_call("get_user_accounts", "KV", (time.time() - start) * 1000)
        
        # 3. Devices map from KV (nested in user record)
        start = time.time()
        devices = _get_user_devices(aerospike_service, user_id)
        metrics.track_db_call("get_user_devices", "KV", (time.time() - start) * 1000)
        
        # 4. Pre-computed account risk features from KV (account_fact set)
        account_ids = list(accounts.keys())
        start = time.time()
        account_facts = _get_account_facts(aerospike_service, account_ids)
        metrics.track_db_call("batch_get_account_facts", "KV", (time.time() - start) * 1000)
        
        # 5. Pre-computed device risk features from KV (device_fact set)
        device_ids = list(devices.keys())
        start = time.time()
        device_facts = _get_device_facts(aerospike_service, device_ids)
        metrics.track_db_call("batch_get_device_facts", "KV", (time.time() - start) * 1000)
        
        # 6. Calculate basic metrics from KV data
        account_metrics = _calculate_account_metrics(user_profile, accounts, devices, account_facts, device_facts)
        
        # Build initial evidence structure
        initial_evidence = InitialEvidence(
            user_id=user_id,
            profile=user_profile,
            accounts=accounts,
            devices=devices,
            account_facts=account_facts,
            device_facts=device_facts,
            account_metrics=account_metrics,
            alert_evidence=state.get("alert_evidence", {})
        )
        
        # Emit evidence event
        trace_events.append(TraceEvent(
            type="evidence",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={
                "profile_found": bool(user_profile.get("name")),
                "account_count": len(accounts),
                "device_count": len(devices),
                "account_facts_loaded": sum(1 for v in account_facts.values() if v),
                "device_facts_loaded": sum(1 for v in device_facts.values() if v),
                "account_age_days": account_metrics.get("account_age_days", 0),
                "total_balance": account_metrics.get("total_balance", 0),
                "has_flagged_account": account_metrics.get("has_flagged_account", False),
                "has_flagged_device": account_metrics.get("has_flagged_device", False)
            }
        ))
        
        # Emit complete event
        trace_events.append(TraceEvent(
            type="node_complete",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={"status": "success"}
        ))
        
        logger.info(
            f"[{node_name}] Data collection complete (KV-only) - "
            f"{len(accounts)} accounts, {len(devices)} devices, "
            f"{sum(1 for v in account_facts.values() if v)} account facts, "
            f"{sum(1 for v in device_facts.values() if v)} device facts"
        )
        
        return {
            "initial_evidence": initial_evidence,
            "current_node": "llm_agent",
            "current_phase": "llm_reasoning",
            "trace_events": trace_events
        }
        
    except Exception as e:
        logger.error(f"[{node_name}] Error during data collection: {e}")
        
        trace_events.append(TraceEvent(
            type="error",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={"error": str(e)}
        ))
        
        # Return minimal evidence on error
        return {
            "initial_evidence": InitialEvidence(
                user_id=user_id,
                profile={},
                accounts={},
                devices={},
                account_facts={},
                device_facts={},
                account_metrics={},
                alert_evidence=state.get("alert_evidence", {})
            ),
            "current_node": "llm_agent",
            "current_phase": "llm_reasoning",
            "error_message": str(e),
            "trace_events": trace_events
        }


def _ensure_remote_hydration(aerospike_service: Any, graph_service: Any, user_id: str) -> bool:
    """
    In remote mode, lazily materialize this user's KV working set from the Graph
    when it's missing. No-op in local mode or when a KV record already exists.

    Returns True only when a hydration pass actually built the record, so callers
    can attribute the Graph latency. Never raises: hydration is best-effort and the
    node continues (with whatever KV data exists) on failure.
    """
    try:
        import settings
        if not settings.is_remote_mode():
            return False
        from services.hydration_service import HydrationService
        # Cheap short-circuit if already present (avoids constructing the service).
        if aerospike_service and aerospike_service.get_user(user_id):
            return False
        return HydrationService(graph_service, aerospike_service).ensure_user_hydrated(user_id)
    except Exception as e:
        logger.warning(f"Remote hydration skipped for user {user_id}: {e}")
        return False


def _get_user_profile(aerospike_service: Any, user_id: str) -> Dict[str, Any]:
    """Get user profile from Aerospike KV."""
    try:
        user_data = aerospike_service.get_user(user_id)
        if user_data:
            return {
                "name": user_data.get("name", ""),
                "email": user_data.get("email", ""),
                "phone": user_data.get("phone"),
                "location": user_data.get("location", "Unknown"),
                "occupation": user_data.get("occupation", "Unknown"),
                "age": user_data.get("age", 0),
                "signup_date": user_data.get("signup_date", ""),
                "workflow_status": user_data.get("workflow_status", "pending_review"),
                "current_risk_score": user_data.get("risk_score", 0)
            }
        return {}
    except Exception as e:
        logger.warning(f"Error getting user profile: {e}")
        return {}


def _get_user_accounts(aerospike_service: Any, user_id: str) -> Dict[str, Dict[str, Any]]:
    """Get user's accounts map from KV (nested in user record)."""
    try:
        accounts = aerospike_service.get_user_accounts(user_id)
        # accounts is already {account_id: {type, balance, status, is_fraud, ...}}
        return accounts or {}
    except Exception as e:
        logger.warning(f"Error getting accounts from KV: {e}")
        return {}


def _get_user_devices(aerospike_service: Any, user_id: str) -> Dict[str, Dict[str, Any]]:
    """Get user's devices map from KV (nested in user record)."""
    try:
        devices = aerospike_service.get_user_devices(user_id)
        # devices is already {device_id: {type, os, browser, is_fraud, ...}}
        return devices or {}
    except Exception as e:
        logger.warning(f"Error getting devices from KV: {e}")
        return {}


def _get_account_facts(aerospike_service: Any, account_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Get pre-computed account risk features from KV (account_fact set)."""
    if not account_ids:
        return {}
    try:
        facts = aerospike_service.batch_get_account_facts(account_ids)
        # facts is {account_id: {features...} or None}
        # Filter out None values for cleaner data
        return {aid: f for aid, f in facts.items() if f is not None}
    except Exception as e:
        logger.warning(f"Error getting account facts from KV: {e}")
        return {}


def _get_device_facts(aerospike_service: Any, device_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Get pre-computed device risk features from KV (device_fact set)."""
    if not device_ids:
        return {}
    try:
        facts = aerospike_service.batch_get_device_facts(device_ids)
        # facts is {device_id: {features...} or None}
        return {did: f for did, f in facts.items() if f is not None}
    except Exception as e:
        logger.warning(f"Error getting device facts from KV: {e}")
        return {}


def _calculate_account_metrics(
    profile: Dict[str, Any],
    accounts: Dict[str, Dict[str, Any]],
    devices: Dict[str, Dict[str, Any]],
    account_facts: Dict[str, Dict[str, Any]],
    device_facts: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """Calculate basic account metrics from KV data."""
    
    # Account age
    account_age_days = 0
    signup_date = profile.get("signup_date", "")
    if signup_date:
        try:
            signup_dt = datetime.fromisoformat(signup_date.replace("Z", "+00:00"))
            account_age_days = (datetime.now(signup_dt.tzinfo) - signup_dt).days
        except Exception:
            pass
    
    # Total balance (accounts is a dict: {account_id: {balance: ...}})
    total_balance = sum(a.get("balance", 0) for a in accounts.values())
    
    # Flagged accounts
    has_flagged_account = any(a.get("is_fraud") for a in accounts.values())
    flagged_account_count = sum(1 for a in accounts.values() if a.get("is_fraud"))
    
    # Device analysis from KV
    has_flagged_device = any(d.get("is_fraud") for d in devices.values())
    flagged_device_count = sum(1 for d in devices.values() if d.get("is_fraud"))
    
    # Risk features summary (from account_facts)
    max_velocity_zscore = 0
    max_amount_zscore = 0
    max_new_recipient_ratio = 0
    for facts in account_facts.values():
        if facts:
            max_velocity_zscore = max(max_velocity_zscore, facts.get("transaction_zscore", 0))
            max_amount_zscore = max(max_amount_zscore, facts.get("amount_zscore_7d", 0))
            max_new_recipient_ratio = max(max_new_recipient_ratio, facts.get("new_recipient_ratio_7d", 0))
    
    # Device features summary
    max_shared_accounts = 0
    max_flagged_in_device = 0
    for facts in device_facts.values():
        if facts:
            max_shared_accounts = max(max_shared_accounts, facts.get("shared_account_count_7d", 0))
            max_flagged_in_device = max(max_flagged_in_device, facts.get("flagged_account_count", 0))
    
    # KYC completeness
    kyc_completeness = "none"
    if profile.get("email") and profile.get("name"):
        kyc_completeness = "basic"
        if profile.get("phone") and profile.get("location"):
            kyc_completeness = "full"
    
    return {
        "account_age_days": account_age_days,
        "total_balance": round(total_balance, 2),
        "account_count": len(accounts),
        "device_count": len(devices),
        "has_flagged_account": has_flagged_account,
        "flagged_account_count": flagged_account_count,
        "has_flagged_device": has_flagged_device,
        "flagged_device_count": flagged_device_count,
        "max_velocity_zscore": round(max_velocity_zscore, 2),
        "max_amount_zscore": round(max_amount_zscore, 2),
        "max_new_recipient_ratio": round(max_new_recipient_ratio, 2),
        "max_shared_accounts_on_device": max_shared_accounts,
        "max_flagged_accounts_on_device": max_flagged_in_device,
        "kyc_completeness": kyc_completeness,
        "profile_risk_score": profile.get("current_risk_score", 0)
    }
