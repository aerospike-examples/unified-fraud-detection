"""
Lazy KV hydration for remote (externally bulk-loaded) mode.

In remote mode the billion-scale dataset lives entirely in the Aerospike Graph.
The only KV set the external pipeline is required to write is `flagged_accounts`
(the review queue + risk score). Everything the investigation workflow needs from
KV -- the user record (profile + nested account/device maps), and the per-account
`account_fact` / per-device `device_fact` feature records -- is filled in lazily,
on demand, the first time an analyst actually opens/investigates a flagged user.

This is a read-through cache: build from the Graph, persist to KV, and subsequent
reads hit KV directly. Traversals are bounded (`max_edges`, `max_accounts`) so a
supernode account can never blow up a single hydration pass. Because the bulk-loaded
graph is static, staleness is a non-issue.

All writes happen server-side here; nothing about this runs in the browser.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.ml_service import ml_model_service
from services.aerospike_service import SET_USERS, SET_ACCOUNT_FACT, SET_DEVICE_FACT

logger = logging.getLogger('fraud_detection.hydration')


class HydrationService:
    """Builds a small KV working set for a single flagged user from the Graph."""

    def __init__(self, graph_service: Any, aerospike_service: Any,
                 max_accounts: int = 25, max_edges_per_account: int = 2000):
        self._graph = graph_service
        self._aero = aerospike_service
        self.max_accounts = max_accounts
        self.max_edges_per_account = max_edges_per_account

    def ensure_user_hydrated(self, user_id: str) -> bool:
        """
        Guarantee a KV `users` record (plus account/device facts) exists for
        `user_id`, materializing it from the Graph on a KV miss.

        Returns True if a KV user record is present after the call, False if the
        user could not be hydrated (no graph vertex / services unavailable).
        Idempotent and safe to call on every investigation: an existing record
        short-circuits immediately.
        """
        if self._aero is None or not self._aero.is_connected():
            return False

        # Already hydrated (either by a prior pass or a local-mode load) -> done.
        try:
            if self._aero.get_user(user_id):
                return True
        except Exception as e:
            logger.debug(f"hydrate: KV get_user failed for {user_id}: {e}")

        if self._graph is None or not self._graph.client:
            return False

        profile = self._graph.get_user_profile(user_id)
        if not profile or not profile.get("user"):
            logger.info(f"hydrate: no graph vertex for user {user_id}; nothing to hydrate")
            return False

        now = datetime.now().isoformat()
        accounts_list = (profile.get("accounts") or [])[: self.max_accounts]
        devices_list = profile.get("devices") or []

        account_records: List[tuple] = []
        account_risk_scores: List[float] = []
        accounts_map: Dict[str, Dict[str, Any]] = {}
        for acc in accounts_list:
            acc_id = acc.get("id")
            if not acc_id:
                continue
            fact = self._graph.compute_account_fact_from_graph(
                acc_id, max_edges=self.max_edges_per_account
            )
            try:
                risk = ml_model_service.predict_account_risk(fact).get("risk_score", 0)
            except Exception as e:
                logger.debug(f"hydrate: scoring account {acc_id} failed: {e}")
                risk = 0
            fact["risk_score"] = risk
            fact["account_id"] = acc_id
            fact["last_computed"] = now
            account_risk_scores.append(risk)
            account_records.append((acc_id, fact))
            accounts_map[acc_id] = {
                "account_id": acc_id,
                "type": acc.get("type", ""),
                "balance": acc.get("balance", 0),
                "bank_name": acc.get("bank_name", ""),
                "status": acc.get("status", ""),
                "is_fraud": bool(acc.get("fraud_flag", False)),
                "risk_score": risk,
            }

        device_records: List[tuple] = []
        devices_map: Dict[str, Dict[str, Any]] = {}
        for dev in devices_list:
            dev_id = dev.get("id")
            if not dev_id:
                continue
            dfact = self._graph.compute_device_fact_from_graph(
                dev_id, account_risk_scores=account_risk_scores,
                max_edges=self.max_edges_per_account
            )
            dfact["device_id"] = dev_id
            dfact["last_computed"] = now
            device_records.append((dev_id, dfact))
            devices_map[dev_id] = {
                "device_id": dev_id,
                "type": dev.get("type", ""),
                "os": dev.get("os", ""),
                "browser": dev.get("browser", ""),
                "is_fraud": bool(dev.get("fraud_flag", False)),
            }

        u = profile["user"]
        user_record = {
            "user_id": user_id,
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "phone": u.get("phone", ""),
            "age": u.get("age", 0),
            "location": u.get("location", ""),
            "occupation": u.get("occupation", ""),
            "risk_score": u.get("risk_score", 0) or 0,
            "signup_date": u.get("signup_date", ""),
            "created_at": now,
            "accounts": accounts_map,
            "devices": devices_map,
            "last_eval": None,
            "eval_count": 0,
            "wf_status": None,
            "flagged_date": None,
            "analyst": None,
            "resolution": None,
            "resol_date": None,
            "resol_notes": None,
            # Provenance so it's clear this record was materialized from the graph
            "hydrated_from": "graph",
            "hydrated_at": now,
        }

        try:
            if account_records:
                self._aero.batch_put(SET_ACCOUNT_FACT, account_records)
            if device_records:
                self._aero.batch_put(SET_DEVICE_FACT, device_records)
            self._aero.put(SET_USERS, user_id, user_record)
        except Exception as e:
            logger.error(f"hydrate: failed persisting KV working set for {user_id}: {e}")
            return False

        logger.info(
            f"hydrate: materialized user {user_id} from graph "
            f"({len(accounts_map)} accounts, {len(devices_map)} devices)"
        )
        return True
