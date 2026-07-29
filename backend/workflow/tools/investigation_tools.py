"""
Investigation Tools

Tools that the LLM agent can invoke to gather evidence during fraud investigations.
Designed to mirror a real fraud analyst's workflow:
  - KV store tools for fast profile/transaction lookups (sub-ms reads)
  - Graph DB tools only for network/relationship analysis (fraud rings)

The LLM decides which tools to call and with what parameters.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import logging
import time

import settings

if TYPE_CHECKING:
    from workflow.metrics import MetricsCollector

logger = logging.getLogger('investigation.tools')


class InvestigationTools:
    """
    Tools available to the LLM reasoning agent.
    
    KV-based tools (fast lookups):
      1. get_account_transactions  - Pull transaction ledger for a specific account
      2. get_counterparty_profile  - Investigate who the suspect is transacting with
      3. get_counterparty_transactions - Build behavioral profile of a counterparty
      4. get_account_risk_features - Pre-computed ML risk features for an account
      5. get_device_risk_features  - Pre-computed risk features for a device
    
    Graph-based tools (network analysis):
      6. detect_fraud_ring  - Identify fraud rings via device sharing + transaction overlap
      7. get_transaction_network - Multi-hop money flow visualization
    
    Exit tool:
      8. submit_assessment - Submit final fraud classification
    """
    
    # Tool schemas for LLM
    TOOL_SCHEMAS = [
        {
            "name": "get_account_transactions",
            "description": "Pull the transaction ledger for a specific account. Use this to analyze spending patterns, velocity, amounts, counterparties, and detect unusual behavior. Each transaction includes the counterparty_user_id which you can investigate further.",
            "parameters": {
                "account_id": {"type": "string", "description": "The account ID to pull transactions for (e.g. A527001)", "required": True},
                "days": {"type": "integer", "description": "Days to look back (1-90)", "default": 30}
            }
        },
        {
            "name": "get_counterparty_profile",
            "description": "Get the profile of a user the suspect has been transacting with. Returns their name, location, signup date, risk score, accounts (with balances and fraud flags), and devices. Use this after seeing suspicious transactions to investigate the other party.",
            "parameters": {
                "user_id": {"type": "string", "description": "The counterparty's user_id (from transaction data)", "required": True}
            }
        },
        {
            "name": "get_counterparty_transactions",
            "description": "Get all transactions across all accounts of a counterparty. Use this to build a behavioral profile: Are they receiving money from many sources (mule pattern)? Are they making rapid transfers? What's their transaction volume?",
            "parameters": {
                "user_id": {"type": "string", "description": "The counterparty's user_id", "required": True},
                "days": {"type": "integer", "description": "Days to look back (1-90)", "default": 30}
            }
        },
        {
            "name": "get_account_risk_features",
            "description": "Get pre-computed ML risk features for an account. Returns 15 features: velocity (txn count, peak hour activity), amount patterns (total, average, z-score), counterparty spread (unique recipients, new recipient ratio, entropy), device exposure, and lifecycle metrics (account age, first transaction delay).",
            "parameters": {
                "account_id": {"type": "string", "description": "The account ID to get risk features for", "required": True}
            }
        },
        {
            "name": "get_device_risk_features",
            "description": "Get pre-computed risk features for a device. Returns: shared account count (how many accounts use this device), flagged account count, average and max account risk scores, and new account rate. High shared_account_count or flagged_account_count suggests device is part of a fraud ring.",
            "parameters": {
                "device_id": {"type": "string", "description": "The device ID to get risk features for", "required": True}
            }
        },
        {
            "name": "detect_fraud_ring",
            "description": "Detect if the user is part of a coordinated fraud ring by analyzing the network graph. Checks: (1) users sharing the same devices, (2) users connected through transactions, (3) overlap between device-sharing and transacting (strong ring signal). Use this when you suspect coordinated fraud across multiple accounts.",
            "parameters": {
                "hops": {"type": "integer", "description": "Network traversal depth (1-3)", "default": 2}
            }
        },
        {
            "name": "get_transaction_network",
            "description": "Visualize the money flow network around the user. Multi-hop traversal through transaction edges showing: who sent money to whom, total amounts, transaction counts, and which accounts on the path are flagged. Use this to trace money trails and identify high-risk flow paths.",
            "parameters": {
                "hops": {"type": "integer", "description": "Network traversal depth (1-3)", "default": 2},
                "min_amount": {"type": "number", "description": "Only include edges with total amount above this threshold", "default": 0}
            }
        },
        {
            "name": "submit_assessment",
            "description": "Submit your final fraud assessment. Call this when you have gathered enough evidence to make a decision. You must provide a fraud typology, risk level, risk score, recommended action, and detailed reasoning citing specific evidence.",
            "parameters": {
                "typology": {"type": "string", "description": "Fraud type: account_takeover, money_mule, synthetic_identity, promo_abuse, friendly_fraud, card_testing, fraud_ring, suspicious_activity, legitimate", "required": True},
                "risk_level": {"type": "string", "description": "Risk level: low, medium, high, critical", "required": True},
                "risk_score": {"type": "integer", "description": "Risk score 0-100", "required": True},
                "decision": {"type": "string", "description": "Recommended action: allow_monitor, step_up_auth, temporary_freeze, full_block, escalate_compliance", "required": True},
                "reasoning": {"type": "string", "description": "Detailed reasoning citing specific evidence from your investigation", "required": True}
            }
        }
    ]
    
    def __init__(
        self, 
        aerospike_service: Any, 
        graph_service: Any, 
        user_id: str,
        metrics: Optional["MetricsCollector"] = None
    ):
        self.aerospike = aerospike_service
        self.graph = graph_service
        self.user_id = user_id
        self.metrics = metrics
        self.tool_calls: List[Dict[str, Any]] = []  # Audit trail
    
    def _track_db_call(self, operation: str, target: str, duration_ms: float, success: bool = True):
        """Track a database call if metrics collector is available."""
        if self.metrics:
            self.metrics.track_db_call(operation, target, duration_ms, success)

    def _graph_hops(self, hops: int) -> int:
        """Cap Gremlin depth — remote demo graph is billion-scale."""
        hops = min(3, max(1, int(hops or 1)))
        if settings.is_remote_mode():
            return 1
        return hops

    @staticmethod
    def _fget(facts: Dict[str, Any], *keys: str, default: Any = 0) -> Any:
        """
        Read a fact value accepting multiple key spellings.

        Feature records are written with SHORT names (e.g. `txn_out_7d`), but this
        module historically read LONG names (e.g. `txn_out_count_7d`). Accept both
        so features aren't silently zeroed regardless of which writer produced them.
        """
        for k in keys:
            if k in facts and facts[k] is not None:
                return facts[k]
        return default
    
    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by name with given parameters."""
        call_record = {
            "tool": tool_name,
            "params": params,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            if tool_name == "get_account_transactions":
                result = self.get_account_transactions(**params)
            elif tool_name == "get_counterparty_profile":
                result = self.get_counterparty_profile(**params)
            elif tool_name == "get_counterparty_transactions":
                result = self.get_counterparty_transactions(**params)
            elif tool_name == "get_account_risk_features":
                result = self.get_account_risk_features(**params)
            elif tool_name == "get_device_risk_features":
                result = self.get_device_risk_features(**params)
            elif tool_name == "detect_fraud_ring":
                result = self.detect_fraud_ring(**params)
            elif tool_name == "get_transaction_network":
                result = self.get_transaction_network(**params)
            elif tool_name == "submit_assessment":
                result = self.submit_assessment(**params)
            else:
                result = {"success": False, "error": f"Unknown tool: {tool_name}"}
            
            call_record["success"] = result.get("success", True)
            call_record["result_summary"] = self._summarize_result(tool_name, result)
            
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            result = {"success": False, "error": str(e)}
            call_record["success"] = False
            call_record["error"] = str(e)
        
        self.tool_calls.append(call_record)
        return result
    
    def _summarize_result(self, tool_name: str, result: Dict) -> str:
        """Create a brief summary of tool result for logging."""
        if not result.get("success", True):
            return f"Error: {result.get('error', 'Unknown')}"
        
        if tool_name == "get_account_transactions":
            return f"{result.get('count', 0)} transactions, total ${result.get('total_amount', 0):,.2f}"
        elif tool_name == "get_counterparty_profile":
            return f"Profile: {result.get('name', 'Unknown')}, risk: {result.get('risk_score', 0)}"
        elif tool_name == "get_counterparty_transactions":
            return f"{result.get('total_transaction_count', 0)} transactions across {result.get('account_count', 0)} accounts"
        elif tool_name == "get_account_risk_features":
            return f"Features loaded, velocity_zscore={result.get('features', {}).get('transaction_zscore', 'N/A')}"
        elif tool_name == "get_device_risk_features":
            return f"Shared accounts: {result.get('features', {}).get('shared_account_count_7d', 'N/A')}"
        elif tool_name == "detect_fraud_ring":
            return f"Ring: {result.get('is_fraud_ring', False)}, confidence: {result.get('ring_confidence', 0)}%"
        elif tool_name == "get_transaction_network":
            return f"{result.get('node_count', 0)} nodes, {result.get('edge_count', 0)} edges"
        elif tool_name == "submit_assessment":
            return f"Assessment: {result.get('typology', 'unknown')} - {result.get('risk_level', 'unknown')}"
        return "OK"
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 1: Get Account Transactions (KV Store)
    # ─────────────────────────────────────────────────────────────
    def get_account_transactions(
        self, 
        account_id: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Pull the transaction ledger for a specific account from KV store.
        
        Args:
            account_id: The account ID to pull transactions for
            days: Number of days to look back (default: 30, max: 90)
            
        Returns:
            Transaction list with amounts, counterparties, and summary stats
        """
        logger.info(f"[Tool] get_account_transactions(account_id={account_id}, days={days})")
        
        days = min(90, max(1, days))
        
        try:
            start = time.time()
            if settings.is_remote_mode():
                # No KV transactions in remote mode; read the ledger from the graph.
                transactions = self.graph.get_account_transactions_from_graph(account_id, limit=200)
                self._track_db_call("get_account_transactions", "Graph", (time.time() - start) * 1000)
            else:
                transactions = self.aerospike.get_transactions_for_account(account_id, days=days)
                self._track_db_call("get_transactions_for_account", "KV", (time.time() - start) * 1000)
            
            if not transactions:
                return {
                    "success": True,
                    "count": 0,
                    "transactions": [],
                    "total_amount": 0,
                    "message": f"No transactions found for {account_id} in last {days} days"
                }
            
            # Calculate summary stats
            total_amount = sum(t.get("amount", 0) for t in transactions)
            avg_amount = total_amount / len(transactions) if transactions else 0
            max_amount = max((t.get("amount", 0) for t in transactions), default=0)
            
            # Count outgoing vs incoming
            out_count = sum(1 for t in transactions if t.get("direction") == "out")
            in_count = sum(1 for t in transactions if t.get("direction") == "in")
            
            # Flagged transactions
            flagged_count = sum(1 for t in transactions if t.get("is_fraud"))
            
            # Unique counterparties
            counterparties = set()
            for t in transactions:
                cp = t.get("counterparty_user_id") or t.get("counterparty", "")
                if cp:
                    counterparties.add(cp)
            
            return {
                "success": True,
                "account_id": account_id,
                "count": len(transactions),
                "transactions": transactions[:50],  # Limit response size for LLM context
                "total_amount": round(total_amount, 2),
                "avg_amount": round(avg_amount, 2),
                "max_amount": round(max_amount, 2),
                "outgoing_count": out_count,
                "incoming_count": in_count,
                "flagged_count": flagged_count,
                "unique_counterparties": len(counterparties),
                "query_params": {"account_id": account_id, "days": days}
            }
            
        except Exception as e:
            logger.error(f"get_account_transactions error: {e}")
            return {"success": False, "error": str(e)}
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 2: Get Counterparty Profile (KV Store)
    # ─────────────────────────────────────────────────────────────
    def get_counterparty_profile(
        self, 
        user_id: str
    ) -> Dict[str, Any]:
        """
        Get the profile of a counterparty user from KV store.
        
        Args:
            user_id: The counterparty's user_id
            
        Returns:
            Profile with name, location, accounts, devices, risk score
        """
        logger.info(f"[Tool] get_counterparty_profile(user_id={user_id})")
        
        try:
            # In remote mode counterparties aren't in KV (only the flagged user is
            # hydrated); build the profile from the graph instead.
            if settings.is_remote_mode():
                start = time.time()
                result = self._counterparty_profile_from_graph(user_id)
                self._track_db_call("get_counterparty_profile", "Graph", (time.time() - start) * 1000)
                return result

            # Get user profile
            start = time.time()
            user_data = self.aerospike.get_user(user_id)
            self._track_db_call("get_counterparty_user", "KV", (time.time() - start) * 1000)
            
            if not user_data:
                return {
                    "success": True,
                    "found": False,
                    "message": f"User {user_id} not found in KV store"
                }
            
            # Extract accounts
            accounts = user_data.get("accounts", {})
            account_list = []
            for aid, acc in accounts.items():
                account_list.append({
                    "account_id": aid,
                    "type": acc.get("type", "unknown"),
                    "balance": acc.get("balance", 0),
                    "status": acc.get("status", "active"),
                    "is_fraud": acc.get("is_fraud", False)
                })
            
            # Extract devices
            devices = user_data.get("devices", {})
            device_list = []
            for did, dev in devices.items():
                device_list.append({
                    "device_id": did,
                    "type": dev.get("type", "unknown"),
                    "os": dev.get("os", "unknown"),
                    "is_fraud": dev.get("is_fraud", False)
                })
            
            # Calculate account age
            account_age_days = 0
            signup_date = user_data.get("signup_date", "")
            if signup_date:
                try:
                    signup_dt = datetime.fromisoformat(signup_date.replace("Z", "+00:00"))
                    account_age_days = (datetime.now(signup_dt.tzinfo) - signup_dt).days
                except Exception:
                    pass
            
            total_balance = sum(a.get("balance", 0) for a in accounts.values())
            has_flagged_account = any(a.get("is_fraud") for a in accounts.values())
            
            return {
                "success": True,
                "found": True,
                "user_id": user_id,
                "name": user_data.get("name", "Unknown"),
                "email": user_data.get("email", ""),
                "location": user_data.get("location", "Unknown"),
                "occupation": user_data.get("occupation", "Unknown"),
                "signup_date": signup_date,
                "account_age_days": account_age_days,
                "risk_score": user_data.get("risk_score", 0),
                "accounts": account_list,
                "account_count": len(account_list),
                "total_balance": round(total_balance, 2),
                "has_flagged_account": has_flagged_account,
                "devices": device_list,
                "device_count": len(device_list)
            }
            
        except Exception as e:
            logger.error(f"get_counterparty_profile error: {e}")
            return {"success": False, "error": str(e)}
    
    def _counterparty_profile_from_graph(self, user_id: str) -> Dict[str, Any]:
        """Build a counterparty profile from the graph (remote mode) in the same
        shape get_counterparty_profile returns from KV."""
        profile = self.graph.get_user_profile(user_id)
        if not profile or not profile.get("user"):
            return {
                "success": True,
                "found": False,
                "message": f"User {user_id} not found in graph"
            }
        u = profile["user"]
        account_list = [{
            "account_id": a.get("id", ""),
            "type": a.get("type", "unknown"),
            "balance": a.get("balance", 0),
            "status": a.get("status", "active"),
            "is_fraud": bool(a.get("fraud_flag", False)),
        } for a in profile.get("accounts", [])]
        device_list = [{
            "device_id": d.get("id", ""),
            "type": d.get("type", "unknown"),
            "os": d.get("os", "unknown"),
            "is_fraud": bool(d.get("fraud_flag", False)),
        } for d in profile.get("devices", [])]

        account_age_days = 0
        signup_date = u.get("signup_date", "")
        if signup_date:
            try:
                signup_dt = datetime.fromisoformat(str(signup_date).replace("Z", "+00:00"))
                account_age_days = (datetime.now(signup_dt.tzinfo) - signup_dt).days
            except Exception:
                pass

        total_balance = sum(a.get("balance", 0) for a in account_list)
        has_flagged_account = any(a.get("is_fraud") for a in account_list)

        return {
            "success": True,
            "found": True,
            "user_id": user_id,
            "name": u.get("name", "Unknown"),
            "email": u.get("email", ""),
            "location": u.get("location", "Unknown"),
            "occupation": u.get("occupation", "Unknown"),
            "signup_date": signup_date,
            "account_age_days": account_age_days,
            "risk_score": u.get("risk_score", 0),
            "accounts": account_list,
            "account_count": len(account_list),
            "total_balance": round(total_balance, 2),
            "has_flagged_account": has_flagged_account,
            "devices": device_list,
            "device_count": len(device_list),
        }
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 3: Get Counterparty Transactions (KV Store)
    # ─────────────────────────────────────────────────────────────
    def get_counterparty_transactions(
        self, 
        user_id: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get all transactions for all accounts of a counterparty.
        Builds a behavioral profile of the counterparty.
        
        Args:
            user_id: The counterparty's user_id
            days: Days to look back (default: 30, max: 90)
            
        Returns:
            All transactions with summary stats across all accounts
        """
        logger.info(f"[Tool] get_counterparty_transactions(user_id={user_id}, days={days})")
        
        days = min(90, max(1, days))
        
        try:
            # Get the counterparty's accounts (KV in local mode, graph in remote).
            start = time.time()
            if settings.is_remote_mode():
                profile = self.graph.get_user_profile(user_id)
                account_ids = [a.get("id") for a in (profile.get("accounts") if profile else []) if a.get("id")]
                self._track_db_call("get_counterparty_accounts", "Graph", (time.time() - start) * 1000)
            else:
                accounts = self.aerospike.get_user_accounts(user_id)
                account_ids = list(accounts.keys()) if accounts else []
                self._track_db_call("get_counterparty_accounts", "KV", (time.time() - start) * 1000)
            
            if not account_ids:
                return {
                    "success": True,
                    "found": False,
                    "message": f"No accounts found for user {user_id}"
                }
            
            # Fetch transactions for all accounts (KV batch, or per-account graph reads).
            start = time.time()
            if settings.is_remote_mode():
                all_txns = {
                    aid: self.graph.get_account_transactions_from_graph(aid, limit=100)
                    for aid in account_ids[:25]
                }
                self._track_db_call("get_counterparty_transactions", "Graph", (time.time() - start) * 1000)
            else:
                all_txns = self.aerospike.batch_get_transactions(account_ids, days=days)
                self._track_db_call("batch_get_counterparty_transactions", "KV", (time.time() - start) * 1000)
            
            # Aggregate across all accounts
            all_transactions = []
            per_account_summary = []
            
            for aid in account_ids:
                txns = all_txns.get(aid, [])
                all_transactions.extend(txns)
                
                if txns:
                    acct_total = sum(t.get("amount", 0) for t in txns)
                    acct_out = sum(1 for t in txns if t.get("direction") == "out")
                    acct_in = sum(1 for t in txns if t.get("direction") == "in")
                    per_account_summary.append({
                        "account_id": aid,
                        "transaction_count": len(txns),
                        "total_amount": round(acct_total, 2),
                        "outgoing": acct_out,
                        "incoming": acct_in
                    })
            
            # Overall summary
            total_amount = sum(t.get("amount", 0) for t in all_transactions)
            unique_counterparties = set()
            for t in all_transactions:
                cp = t.get("counterparty_user_id") or t.get("counterparty", "")
                if cp:
                    unique_counterparties.add(cp)
            
            out_count = sum(1 for t in all_transactions if t.get("direction") == "out")
            in_count = sum(1 for t in all_transactions if t.get("direction") == "in")
            flagged_count = sum(1 for t in all_transactions if t.get("is_fraud"))
            
            return {
                "success": True,
                "user_id": user_id,
                "account_count": len(account_ids),
                "total_transaction_count": len(all_transactions),
                "total_amount": round(total_amount, 2),
                "avg_amount": round(total_amount / len(all_transactions), 2) if all_transactions else 0,
                "outgoing_count": out_count,
                "incoming_count": in_count,
                "flagged_count": flagged_count,
                "unique_counterparties": len(unique_counterparties),
                "per_account_summary": per_account_summary,
                "transactions": all_transactions[:30],  # Limit for LLM context
                "query_params": {"user_id": user_id, "days": days}
            }
            
        except Exception as e:
            logger.error(f"get_counterparty_transactions error: {e}")
            return {"success": False, "error": str(e)}
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 4: Get Account Risk Features (KV Store)
    # ─────────────────────────────────────────────────────────────
    def get_account_risk_features(
        self, 
        account_id: str
    ) -> Dict[str, Any]:
        """
        Get pre-computed ML risk features for an account from KV store.
        
        Args:
            account_id: The account ID
            
        Returns:
            15 pre-computed features covering velocity, amount, counterparty, device, lifecycle
        """
        logger.info(f"[Tool] get_account_risk_features(account_id={account_id})")
        
        try:
            start = time.time()
            facts = self.aerospike.get_account_fact(account_id)
            self._track_db_call("get_account_fact", "KV", (time.time() - start) * 1000)
            
            if not facts:
                return {
                    "success": True,
                    "found": False,
                    "message": f"No pre-computed features found for account {account_id}. Features are computed during ML detection runs."
                }
            
            # Organize features by category for LLM readability
            return {
                "success": True,
                "account_id": account_id,
                "found": True,
                "last_computed": facts.get("last_computed", "unknown"),
                "features": {
                    # Velocity features
                    "txn_out_count_7d": self._fget(facts, "txn_out_7d", "txn_out_count_7d"),
                    "txn_out_count_24h_peak": self._fget(facts, "txn_24h_peak", "txn_out_count_24h_peak"),
                    "avg_txn_per_day_7d": self._fget(facts, "avg_txn_day", "avg_txn_per_day_7d"),
                    "max_txn_per_hour_7d": self._fget(facts, "max_txn_hr", "max_txn_per_hour_7d"),
                    "transaction_zscore": self._fget(facts, "txn_zscore", "transaction_zscore"),
                    # Amount features
                    "total_out_amount_7d": self._fget(facts, "out_amt_7d", "total_out_amount_7d"),
                    "avg_out_amount_7d": self._fget(facts, "avg_out_amt", "avg_out_amount_7d"),
                    "max_out_amount_7d": self._fget(facts, "max_out_amt", "max_out_amount_7d"),
                    "amount_zscore_7d": self._fget(facts, "amt_zscore", "amount_zscore_7d"),
                    # Counterparty features
                    "unique_recipients_7d": self._fget(facts, "uniq_recip", "unique_recipients_7d"),
                    "new_recipient_ratio_7d": self._fget(facts, "new_recip_rat", "new_recipient_ratio_7d"),
                    "recipient_entropy_7d": self._fget(facts, "recip_entropy", "recipient_entropy_7d"),
                    # Device features
                    "device_count_7d": self._fget(facts, "dev_count", "device_count_7d"),
                    "shared_device_account_count_7d": self._fget(facts, "shared_dev_ct", "shared_device_account_count_7d"),
                    # Lifecycle features
                    "account_age_days": self._fget(facts, "acct_age_days", "account_age_days"),
                    "first_txn_delay_days": self._fget(facts, "first_txn_dly", "first_txn_delay_days"),
                    "historical_txn_mean": self._fget(facts, "hist_txn_mean", "historical_txn_mean"),
                    "historical_amt_mean": self._fget(facts, "hist_amt_mean", "historical_amt_mean"),
                    "historical_amt_std": self._fget(facts, "hist_amt_std", "historical_amt_std"),
                },
                "interpretation": {
                    "velocity_anomaly": self._fget(facts, "txn_zscore", "transaction_zscore") > 2.0,
                    "amount_anomaly": self._fget(facts, "amt_zscore", "amount_zscore_7d") > 2.0,
                    "high_new_recipient_ratio": self._fget(facts, "new_recip_rat", "new_recipient_ratio_7d") > 0.5,
                    "device_shared": self._fget(facts, "shared_dev_ct", "shared_device_account_count_7d") > 1,
                    "new_account": self._fget(facts, "acct_age_days", "account_age_days", default=365) < 30,
                }
            }
            
        except Exception as e:
            logger.error(f"get_account_risk_features error: {e}")
            return {"success": False, "error": str(e)}
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 5: Get Device Risk Features (KV Store)
    # ─────────────────────────────────────────────────────────────
    def get_device_risk_features(
        self, 
        device_id: str
    ) -> Dict[str, Any]:
        """
        Get pre-computed risk features for a device from KV store.
        
        Args:
            device_id: The device ID
            
        Returns:
            5 pre-computed features for device risk assessment
        """
        logger.info(f"[Tool] get_device_risk_features(device_id={device_id})")
        
        try:
            start = time.time()
            facts = self.aerospike.get_device_fact(device_id)
            self._track_db_call("get_device_fact", "KV", (time.time() - start) * 1000)
            
            if not facts:
                return {
                    "success": True,
                    "found": False,
                    "message": f"No pre-computed features found for device {device_id}. Features are computed during ML detection runs."
                }
            
            return {
                "success": True,
                "device_id": device_id,
                "found": True,
                "last_computed": facts.get("last_computed", "unknown"),
                "features": {
                    "shared_account_count_7d": self._fget(facts, "shared_acct_ct", "shared_account_count_7d"),
                    "flagged_account_count": self._fget(facts, "flag_acct_ct", "flagged_account_count"),
                    "avg_account_risk_score": self._fget(facts, "avg_acct_risk", "avg_account_risk_score"),
                    "max_account_risk_score": self._fget(facts, "max_acct_risk", "max_account_risk_score"),
                    "new_account_rate_7d": self._fget(facts, "new_acct_7d", "new_account_rate_7d"),
                },
                "interpretation": {
                    "shared_device": self._fget(facts, "shared_acct_ct", "shared_account_count_7d") > 2,
                    "has_flagged_accounts": self._fget(facts, "flag_acct_ct", "flagged_account_count") > 0,
                    "high_avg_risk": self._fget(facts, "avg_acct_risk", "avg_account_risk_score") > 50,
                    "high_new_account_rate": self._fget(facts, "new_acct_7d", "new_account_rate_7d") > 0.3,
                }
            }
            
        except Exception as e:
            logger.error(f"get_device_risk_features error: {e}")
            return {"success": False, "error": str(e)}
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 6: Detect Fraud Ring (Graph DB)
    # ─────────────────────────────────────────────────────────────
    def detect_fraud_ring(
        self, 
        hops: int = 2
    ) -> Dict[str, Any]:
        """
        Detect if the user is part of a coordinated fraud ring.
        Uses Graph DB for network traversal.
        
        Checks TWO categories:
        A) Known-bad signals: shared devices, flagged entities, device+transaction overlap
        B) Potential ring patterns: transaction triangles/cycles, reciprocal money flow,
           subgraph density, and abnormal inter-member volume
        
        Args:
            hops: Network traversal depth (1-3, default: 2)
            
        Returns:
            Ring analysis with members, confidence, evidence, and potential ring details
        """
        logger.info(f"[Tool] detect_fraud_ring(hops={hops})")
        
        hops = self._graph_hops(hops)
        g = self.graph.client
        
        try:
            if not g:
                return {"success": False, "error": "Graph service not connected"}
            
            from gremlin_python.process.graph_traversal import __
            from gremlin_python.process.traversal import P
            
            ring_score = 0
            ring_members = []
            evidence = []
            
            # ───────────────────────────────────────────────────
            # PART A: Known-bad signal detection (existing logic)
            # ───────────────────────────────────────────────────
            
            # A1. Find users sharing devices
            start = time.time()
            shared_device_users = (g.V(self.user_id)
                .out("USES")
                .in_("USES")
                .where(__.not_(__.hasId(self.user_id)))
                .dedup()
                .project("user_id", "name", "risk_score", "shared_device_count")
                .by(__.id_())
                .by(__.coalesce(__.values("name"), __.constant("Unknown")))
                .by(__.coalesce(__.values("risk_score"), __.constant(0)))
                .by(__.out("USES").where(__.in_("USES").hasId(self.user_id)).count())
                .to_list()
            )
            self._track_db_call("fraud_ring_shared_devices", "Graph", (time.time() - start) * 1000)
            
            # A2. Find transaction partners (user-level, with info)
            start = time.time()
            txn_partners = (g.V(self.user_id)
                .out("OWNS")
                .bothE("TRANSACTS")
                .bothV()
                .hasLabel("account")
                .in_("OWNS")
                .hasLabel("user")
                .where(__.not_(__.hasId(self.user_id)))
                .dedup()
                .project("user_id", "name", "risk_score")
                .by(__.id_())
                .by(__.coalesce(__.values("name"), __.constant("Unknown")))
                .by(__.coalesce(__.values("risk_score"), __.constant(0)))
                .to_list()
            )
            self._track_db_call("fraud_ring_txn_partners", "Graph", (time.time() - start) * 1000)
            
            partner_ids = {p["user_id"] for p in txn_partners}
            partner_map = {p["user_id"]: p for p in txn_partners}
            
            # A3. Find flagged devices in the network
            start = time.time()
            flagged_devices = (g.V(self.user_id)
                .out("USES")
                .has("fraud_flag", True)
                .project("device_id", "type", "user_count")
                .by(__.id_())
                .by(__.coalesce(__.values("type"), __.constant("unknown")))
                .by(__.in_("USES").count())
                .to_list()
            )
            self._track_db_call("fraud_ring_flagged_devices", "Graph", (time.time() - start) * 1000)
            
            # A4. Score known-bad signals
            shared_ids = {u["user_id"] for u in shared_device_users}
            overlap_ids = shared_ids & partner_ids
            
            for uid in overlap_ids:
                user_info = next((u for u in shared_device_users if u["user_id"] == uid), {})
                ring_members.append({
                    "user_id": uid,
                    "name": user_info.get("name", "Unknown"),
                    "risk_score": user_info.get("risk_score", 0),
                    "connection_type": "device+transaction"
                })
                ring_score += 25
                evidence.append(f"User {uid} shares device AND has transactions with suspect")
            
            high_risk_shared = [u for u in shared_device_users if u.get("risk_score", 0) >= 60 and u["user_id"] not in overlap_ids]
            for u in high_risk_shared:
                ring_members.append({
                    "user_id": u["user_id"],
                    "name": u.get("name", "Unknown"),
                    "risk_score": u.get("risk_score", 0),
                    "connection_type": "device_only"
                })
                ring_score += 15
                evidence.append(f"High-risk user {u['user_id']} (score: {u['risk_score']}) shares device")
            
            if flagged_devices:
                ring_score += 20
                for fd in flagged_devices:
                    evidence.append(f"Flagged device {fd['device_id']} used by {fd.get('user_count', 1)} users")
            
            if len(shared_device_users) >= 3:
                ring_score += 10
                evidence.append(f"Device shared with {len(shared_device_users)} users")
            
            # ───────────────────────────────────────────────────
            # PART B: Potential fraud ring detection (structural)
            # ───────────────────────────────────────────────────
            
            potential_ring = {
                "triangles": [],
                "reciprocal_partners": [],
                "density": 0.0,
                "high_volume_pairs": [],
                "cluster_members": [],
            }
            
            # Only run structural analysis if there are transaction partners
            if len(txn_partners) >= 2:
                
                partner_id_list = list(partner_ids)
                # Cap partners to analyze (top 30 by risk score descending)
                sorted_partners = sorted(txn_partners, key=lambda p: p.get("risk_score", 0), reverse=True)
                analysis_ids = [p["user_id"] for p in sorted_partners[:30]]
                
                # ── B1: Triangle / Cycle Detection ──
                # For each partner, find which of our OTHER partners they also transact with
                inter_partner_edges = {}  # (p1, p2) -> True
                
                start = time.time()
                for pid in analysis_ids:
                    try:
                        mutual = (g.V(pid)
                            .out("OWNS")
                            .bothE("TRANSACTS").bothV()
                            .hasLabel("account")
                            .in_("OWNS").hasLabel("user")
                            .where(__.not_(__.hasId(pid)).not_(__.hasId(self.user_id)))
                            .dedup().id_()
                            .to_list()
                        )
                        # Intersect with our partner set
                        for mid in mutual:
                            if mid in partner_ids and mid != pid:
                                edge_key = tuple(sorted([pid, mid]))
                                inter_partner_edges[edge_key] = True
                    except Exception as e:
                        logger.warning(f"Triangle query failed for {pid}: {e}")
                        continue
                self._track_db_call("fraud_ring_triangle_scan", "Graph", (time.time() - start) * 1000)
                
                # Build triangles: (self, p1, p2) where p1 and p2 also connect
                triangles_found = []
                for (p1, p2) in inter_partner_edges:
                    tri = tuple(sorted([self.user_id, p1, p2]))
                    if tri not in triangles_found:
                        triangles_found.append(tri)
                
                potential_ring["triangles"] = [
                    {"members": list(t)} for t in triangles_found[:20]
                ]
                
                # Triangles are informational — used to identify cluster members.
                # Scoring happens in the density section (B3) below.
                if triangles_found:
                    evidence.append(
                        f"Found {len(triangles_found)} transaction triangle(s) among "
                        f"{len(analysis_ids)} analyzed partners"
                    )
                
                # ── B2: Reciprocal Transaction Detection ──
                # Check if money flows both ways between suspect and partners
                start = time.time()
                my_accounts = g.V(self.user_id).out("OWNS").id_().to_list()
                reciprocal_partners = []
                
                for acct in my_accounts:
                    try:
                        sent_to = set(g.V(acct).outE("TRANSACTS").inV().id_().dedup().to_list())
                        recv_from = set(g.V(acct).inE("TRANSACTS").outV().id_().dedup().to_list())
                        bidirectional_accts = sent_to & recv_from
                        
                        for other_acct in bidirectional_accts:
                            owner_ids = g.V(other_acct).in_("OWNS").id_().to_list()
                            for owner_id in owner_ids:
                                if owner_id != self.user_id and owner_id not in [r["user_id"] for r in reciprocal_partners]:
                                    reciprocal_partners.append({
                                        "user_id": owner_id,
                                        "name": partner_map.get(owner_id, {}).get("name", "Unknown"),
                                        "my_account": acct,
                                        "their_account": other_acct,
                                    })
                    except Exception as e:
                        logger.warning(f"Reciprocal query failed for {acct}: {e}")
                        continue
                
                self._track_db_call("fraud_ring_reciprocal", "Graph", (time.time() - start) * 1000)
                
                potential_ring["reciprocal_partners"] = reciprocal_partners[:15]
                
                # Score: reciprocal partners — require 2+ to be suspicious
                # A single bidirectional partner is normal (e.g., paying rent back and forth)
                if len(reciprocal_partners) >= 3:
                    ring_score += 30
                    evidence.append(
                        f"{len(reciprocal_partners)} reciprocal partners — "
                        f"money flows both directions with multiple counterparties (round-tripping pattern)"
                    )
                elif len(reciprocal_partners) == 2:
                    ring_score += 15
                    evidence.append(
                        f"{len(reciprocal_partners)} reciprocal partners — "
                        f"bidirectional money flow with 2 counterparties"
                    )
                elif len(reciprocal_partners) == 1:
                    evidence.append(
                        f"1 reciprocal partner ({reciprocal_partners[0]['user_id']}) — "
                        f"bidirectional money flow (may be normal)"
                    )
                
                # ── B3: Subgraph Density ──
                # Among partners involved in triangles, how interconnected are they?
                # Density = actual edges / possible edges in the cluster
                triangle_user_set = set()
                for t in triangles_found:
                    triangle_user_set.update(t)
                triangle_user_set.discard(self.user_id)  # exclude self for density calc
                
                cluster_size = len(triangle_user_set) + 1  # +1 for self
                if cluster_size >= 3:
                    # Count edges: self->each member + inter-member edges
                    actual_edges = len(triangle_user_set)  # self connects to all of them
                    actual_edges += len(inter_partner_edges)  # edges between members
                    
                    possible_edges = cluster_size * (cluster_size - 1) / 2
                    density = actual_edges / possible_edges if possible_edges > 0 else 0
                    potential_ring["density"] = round(density, 3)
                    
                    cluster_members_list = [self.user_id] + list(triangle_user_set)
                    potential_ring["cluster_members"] = [
                        {
                            "user_id": uid,
                            "name": partner_map.get(uid, {}).get("name", "Unknown") if uid != self.user_id else "TARGET",
                            "risk_score": partner_map.get(uid, {}).get("risk_score", 0) if uid != self.user_id else 0,
                        }
                        for uid in cluster_members_list
                    ]
                    
                    # Score: high density with 4+ members is very suspicious
                    if density >= 0.7 and cluster_size >= 4:
                        ring_score += 30
                        evidence.append(
                            f"Dense cluster of {cluster_size} users with density {density:.0%} — "
                            f"nearly all members transact with each other (clique pattern)"
                        )
                    elif density >= 0.5 and cluster_size >= 3:
                        ring_score += 20
                        evidence.append(
                            f"Moderately dense cluster of {cluster_size} users (density {density:.0%})"
                        )
                    elif density >= 0.3:
                        ring_score += 10
                        evidence.append(
                            f"Loosely connected cluster of {cluster_size} users (density {density:.0%})"
                        )
                
                # ── B4: High Transaction Volume Between Partners ──
                # Check if inter-member transaction counts are abnormally high
                start = time.time()
                high_volume_pairs = []
                
                # Check volume between suspect and each partner in the cluster
                cluster_pids = list(triangle_user_set)[:10]  # limit for performance
                for pid in cluster_pids:
                    try:
                        # Count transactions between self and this partner
                        txn_count = (g.V(self.user_id)
                            .out("OWNS")
                            .bothE("TRANSACTS")
                            .where(__.bothV().in_("OWNS").hasId(pid))
                            .count().next()
                        )
                        if txn_count >= 50:  # threshold for suspicion
                            high_volume_pairs.append({
                                "user_id": pid,
                                "name": partner_map.get(pid, {}).get("name", "Unknown"),
                                "transaction_count": txn_count,
                            })
                    except Exception as e:
                        logger.warning(f"Volume query failed for {pid}: {e}")
                        continue
                
                self._track_db_call("fraud_ring_volume_check", "Graph", (time.time() - start) * 1000)
                
                potential_ring["high_volume_pairs"] = high_volume_pairs[:10]
                
                # Score: abnormal volume adds 10 pts if 2+ high-volume partners
                if len(high_volume_pairs) >= 2:
                    avg_vol = sum(p["transaction_count"] for p in high_volume_pairs) / len(high_volume_pairs)
                    ring_score += 10
                    evidence.append(
                        f"{len(high_volume_pairs)} partners with abnormally high transaction volume "
                        f"(avg {avg_vol:.0f} transactions each)"
                    )
            
            # ───────────────────────────────────────────────────
            # FINAL: Combine scores and build result
            # ───────────────────────────────────────────────────
            
            ring_score = min(100, ring_score)
            
            # Add potential ring members to the ring_members list if not already there
            existing_member_ids = {m["user_id"] for m in ring_members}
            for cm in potential_ring.get("cluster_members", []):
                if cm["user_id"] not in existing_member_ids and cm["user_id"] != self.user_id:
                    ring_members.append({
                        "user_id": cm["user_id"],
                        "name": cm.get("name", "Unknown"),
                        "risk_score": cm.get("risk_score", 0),
                        "connection_type": "structural_pattern"
                    })
            
            return {
                "success": True,
                "is_fraud_ring": ring_score >= 40,
                "ring_confidence": ring_score,
                "ring_member_count": len(ring_members),
                "ring_members": ring_members[:20],
                # Known-bad signals
                "shared_device_user_count": len(shared_device_users),
                "transaction_partner_count": len(txn_partners),
                "overlap_count": len(overlap_ids),
                "flagged_device_count": len(flagged_devices),
                "flagged_devices": flagged_devices,
                # Potential ring analysis
                "potential_ring": {
                    "triangle_count": len(potential_ring["triangles"]),
                    "triangles": potential_ring["triangles"][:10],
                    "reciprocal_partner_count": len(potential_ring["reciprocal_partners"]),
                    "reciprocal_partners": potential_ring["reciprocal_partners"][:10],
                    "cluster_density": potential_ring["density"],
                    "cluster_size": len(potential_ring["cluster_members"]),
                    "cluster_members": potential_ring["cluster_members"][:15],
                    "high_volume_pair_count": len(potential_ring["high_volume_pairs"]),
                    "high_volume_pairs": potential_ring["high_volume_pairs"][:10],
                },
                "evidence": evidence[:15],
                "hops_used": hops
            }
            
        except Exception as e:
            logger.error(f"detect_fraud_ring error: {e}")
            return {"success": False, "error": str(e)}
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 7: Get Transaction Network (Graph DB)
    # ─────────────────────────────────────────────────────────────
    def get_transaction_ring_network(
        self,
        max_partners: int = 30,
        max_inter_edges: int = 60
    ) -> Dict[str, Any]:
        """
        Build a nodes+edges transaction graph around the user using the SAME
        traversal shape as detect_fraud_ring: the user's direct transaction
        partners, plus a bounded partner-to-partner scan for edges between
        those partners. Returns a flat graph rather than a ring verdict.

        This exists as a separate method from get_transaction_network
        (which does true recursive multi-hop and is capped to hops=1 in
        remote mode) specifically so a "connection graph" visualization can
        show the exact same edges that detect_fraud_ring analyzed — using
        get_transaction_network's 1-hop-only view instead would show a bare
        star with none of the partner-to-partner edges that make something a
        ring, silently disagreeing with the ring panel.

        Args:
            max_partners: Cap on direct transaction partners considered.
            max_inter_edges: Cap on discovered partner-to-partner edges.
        """
        logger.info(f"[Tool] get_transaction_ring_network(max_partners={max_partners})")

        try:
            g = self.graph.client
            if not g:
                return {"success": False, "error": "Graph service not connected"}

            from gremlin_python.process.graph_traversal import __

            start = time.time()
            txn_partners = (g.V(self.user_id)
                .out("OWNS")
                .bothE("TRANSACTS")
                .bothV()
                .hasLabel("account")
                .in_("OWNS")
                .hasLabel("user")
                .where(__.not_(__.hasId(self.user_id)))
                .dedup()
                .limit(max_partners)
                .project("user_id", "name", "risk_score")
                .by(__.id_())
                .by(__.coalesce(__.values("name"), __.constant("Unknown")))
                .by(__.coalesce(__.values("risk_score"), __.constant(0)))
                .to_list()
            )
            self._track_db_call("ring_network_partners", "Graph", (time.time() - start) * 1000)

            partner_ids = {p["user_id"] for p in txn_partners}

            nodes = [{
                "user_id": self.user_id,
                "name": "TARGET",
                "risk_score": 0,
                "is_investigated": True,
            }]
            for p in txn_partners:
                nodes.append({
                    "user_id": p["user_id"],
                    "name": p.get("name", "Unknown"),
                    "risk_score": p.get("risk_score", 0),
                    "is_investigated": False,
                })

            edges = [{"from": self.user_id, "to": p["user_id"]} for p in txn_partners]

            # Bounded partner-to-partner scan — identical shape to
            # detect_fraud_ring's triangle detection, so the same edges that
            # make it a "ring" there show up here too.
            start = time.time()
            seen_pairs = set()
            for pid in list(partner_ids):
                if len(seen_pairs) >= max_inter_edges:
                    break
                try:
                    mutual = (g.V(pid)
                        .out("OWNS")
                        .bothE("TRANSACTS").bothV()
                        .hasLabel("account")
                        .in_("OWNS").hasLabel("user")
                        .where(__.not_(__.hasId(pid)).not_(__.hasId(self.user_id)))
                        .dedup().id_()
                        .to_list()
                    )
                    for mid in mutual:
                        if mid in partner_ids and mid != pid:
                            pair = tuple(sorted([pid, mid]))
                            if pair not in seen_pairs:
                                seen_pairs.add(pair)
                except Exception as e:
                    logger.warning(f"ring network partner scan failed for {pid}: {e}")
                    continue
            self._track_db_call("ring_network_inter_edges", "Graph", (time.time() - start) * 1000)

            for (a, b) in list(seen_pairs)[:max_inter_edges]:
                edges.append({"from": a, "to": b})

            return {
                "success": True,
                "nodes": nodes,
                "edges": edges,
                "node_count": len(nodes),
                "edge_count": len(edges),
            }
        except Exception as e:
            logger.error(f"get_transaction_ring_network error: {e}")
            return {"success": False, "error": str(e)}

    def get_transaction_network(
        self, 
        hops: int = 2,
        min_amount: float = 0
    ) -> Dict[str, Any]:
        """
        Visualize the money flow network around the user.
        Uses Graph DB for multi-hop traversal.
        
        Args:
            hops: Network traversal depth (1-3, default: 2)
            min_amount: Only include edges with total amount above threshold
            
        Returns:
            Network nodes, edges, and high-risk path analysis
        """
        logger.info(f"[Tool] get_transaction_network(hops={hops}, min_amount={min_amount})")
        
        hops = self._graph_hops(hops)
        
        try:
            if not self.graph.client:
                return {"success": False, "error": "Graph service not connected"}
            
            from gremlin_python.process.graph_traversal import __
            from gremlin_python.process.traversal import P
            
            nodes = []
            edges = []
            seen_user_ids = set()
            
            # Get user's accounts
            start = time.time()
            user_accounts = (self.graph.client.V(self.user_id)
                .out("OWNS")
                .id_()
                .to_list()
            )
            self._track_db_call("network_get_accounts", "Graph", (time.time() - start) * 1000)
            
            # Add the investigated user as the root node
            start = time.time()
            root_info = (self.graph.client.V(self.user_id)
                .project("name", "risk_score")
                .by(__.coalesce(__.values("name"), __.constant("Unknown")))
                .by(__.coalesce(__.values("risk_score"), __.constant(0)))
                .next()
            )
            self._track_db_call("network_get_root", "Graph", (time.time() - start) * 1000)
            
            nodes.append({
                "user_id": self.user_id,
                "name": root_info.get("name", "Unknown"),
                "risk_score": root_info.get("risk_score", 0),
                "is_investigated": True,
                "hop": 0
            })
            seen_user_ids.add(self.user_id)
            
            # Traverse transaction edges hop by hop
            current_accounts = set(user_accounts)
            
            for hop in range(1, hops + 1):
                if not current_accounts:
                    break
                
                next_accounts = set()
                
                for acct_id in list(current_accounts)[:20]:  # Limit per hop
                    try:
                        start = time.time()
                        txn_edges = (self.graph.client.V(acct_id)
                            .bothE("TRANSACTS")
                            .project("amount", "other_account", "direction")
                            .by(__.coalesce(__.values("amount"), __.constant(0)))
                            .by(__.bothV().where(__.not_(__.hasId(acct_id))).id_())
                            .by(__.constant("both"))
                            .to_list()
                        )
                        self._track_db_call(f"network_traverse_hop{hop}", "Graph", (time.time() - start) * 1000)
                        
                        for edge in txn_edges:
                            other_acct = edge.get("other_account")
                            amount = edge.get("amount", 0)
                            
                            if not other_acct or (min_amount > 0 and amount < min_amount):
                                continue
                            
                            # Find the owner of the other account
                            start = time.time()
                            owners = (self.graph.client.V(other_acct)
                                .in_("OWNS")
                                .project("user_id", "name", "risk_score")
                                .by(__.id_())
                                .by(__.coalesce(__.values("name"), __.constant("Unknown")))
                                .by(__.coalesce(__.values("risk_score"), __.constant(0)))
                                .to_list()
                            )
                            self._track_db_call(f"network_get_owner_hop{hop}", "Graph", (time.time() - start) * 1000)
                            
                            for owner in owners:
                                owner_id = owner["user_id"]
                                
                                if owner_id not in seen_user_ids:
                                    nodes.append({
                                        "user_id": owner_id,
                                        "name": owner.get("name", "Unknown"),
                                        "risk_score": owner.get("risk_score", 0),
                                        "is_investigated": False,
                                        "hop": hop
                                    })
                                    seen_user_ids.add(owner_id)
                                    next_accounts.add(other_acct)
                                
                                # Add edge (avoid duplicates)
                                edge_key = tuple(sorted([acct_id, other_acct]))
                                if not any(e.get("_key") == edge_key for e in edges):
                                    edges.append({
                                        "from_account": acct_id,
                                        "to_account": other_acct,
                                        "to_user_id": owner_id,
                                        "amount": amount,
                                        "_key": edge_key
                                    })
                    except Exception:
                        continue
                
                current_accounts = next_accounts
            
            # Clean up internal keys
            for e in edges:
                e.pop("_key", None)
            
            # Identify high-risk nodes
            high_risk_nodes = [n for n in nodes if n.get("risk_score", 0) >= 70]
            
            return {
                "success": True,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "nodes": nodes[:30],
                "edges": edges[:50],
                "high_risk_node_count": len(high_risk_nodes),
                "high_risk_nodes": [n["user_id"] for n in high_risk_nodes],
                "hops_used": hops
            }
            
        except Exception as e:
            logger.error(f"get_transaction_network error: {e}")
            return {"success": False, "error": str(e)}
    
    # ─────────────────────────────────────────────────────────────
    # TOOL 8: Submit Assessment (Exit Tool)
    # ─────────────────────────────────────────────────────────────
    def submit_assessment(
        self,
        typology: str,
        risk_level: str,
        risk_score: int,
        decision: str,
        reasoning: str
    ) -> Dict[str, Any]:
        """
        Submit final fraud assessment. This is the exit tool that ends the agent loop.
        
        Args:
            typology: Fraud type classification
            risk_level: Risk level (low, medium, high, critical)
            risk_score: Numeric risk score (0-100)
            decision: Recommended action
            reasoning: Detailed reasoning citing specific evidence
            
        Returns:
            Assessment confirmation
        """
        logger.info(f"[Tool] submit_assessment(typology={typology}, risk_level={risk_level})")
        
        # Validate inputs
        valid_typologies = [
            "account_takeover", "money_mule", "synthetic_identity", 
            "promo_abuse", "friendly_fraud", "card_testing", 
            "fraud_ring", "suspicious_activity", "legitimate", "unknown"
        ]
        valid_risk_levels = ["low", "medium", "high", "critical"]
        valid_decisions = [
            "allow_monitor", "step_up_auth", "temporary_freeze", 
            "full_block", "escalate_compliance"
        ]
        
        if typology not in valid_typologies:
            typology = "unknown"
        if risk_level not in valid_risk_levels:
            risk_level = "medium"
        if decision not in valid_decisions:
            decision = "allow_monitor"
        
        risk_score = min(100, max(0, risk_score))
        
        return {
            "success": True,
            "is_final_assessment": True,
            "typology": typology,
            "risk_level": risk_level,
            "risk_score": risk_score,
            "decision": decision,
            "reasoning": reasoning,
            "tool_calls_made": len(self.tool_calls)
        }
    
    @classmethod
    def get_tool_descriptions(cls) -> str:
        """Get formatted tool descriptions for LLM prompt."""
        descriptions = []
        for tool in cls.TOOL_SCHEMAS:
            params_str = ""
            if tool.get("parameters"):
                params_list = []
                for name, info in tool["parameters"].items():
                    if info.get("required"):
                        params_list.append(f"{name} (required)")
                    else:
                        default = info.get("default", "")
                        params_list.append(f"{name}={default}")
                params_str = f"({', '.join(params_list)})"
            else:
                params_str = "()"
            
            descriptions.append(f"- **{tool['name']}**{params_str}: {tool['description']}")
        
        return "\n\n".join(descriptions)
