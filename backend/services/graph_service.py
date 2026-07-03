from typing import List, Dict, Any, Optional
import logging
import os
import re
import time

from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.driver.aiohttp.transport import AiohttpTransport
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.graph_traversal import __
from gremlin_python.process.traversal import T, Order

# Get logger for graph service
logger = logging.getLogger('fraud_detection.graph')

class GraphService:
    def __init__(self, host: str = os.environ.get('GRAPH_HOST_ADDRESS') or 'localhost', port: int = 8182):
        self.host = host
        self.port = port
        self.client = None
        self.connection = None
        # Cached graph summary (used for billion-scale stats in remote mode)
        self._summary_cache: Optional[Dict[str, Any]] = None
        self._summary_cache_ts: float = 0.0
    

    # ----------------------------------------------------------------------------------------------------------
    # Connection maintenance
    # ----------------------------------------------------------------------------------------------------------


    def connect(self):
        """Synchronous connection to Aerospike Graph (to be called outside async context)"""
        try:
            url = f'ws://{self.host}:{self.port}/gremlin'
            logger.info(f"🔄 Connecting to Aerospike Graph: {url}")
            
            # Use the same approach as the working sample
            self.connection = DriverRemoteConnection(url, "g", transport_factory=lambda:AiohttpTransport(call_from_event_loop=True))
            self.client = traversal().with_remote(self.connection)
            
            # Test connection using the same method as the sample
            test_result = self.client.inject(0).next()
            if test_result != 0:
                raise Exception("Failed to connect to graph instance")
            
            logger.info("✅ Connected to Aerospike Graph Service")
            return True
                
        except Exception as e:
            logger.error(f"❌ Could not connect to Aerospike Graph: {e}")
            logger.error("Graph database connection is required. Please ensure Aerospike Graph is running on port 8182")
            self.client = None
            self.connection = None
            raise Exception(f"Failed to connect to Aerospike Graph: {e}")

    def close(self):
        """Synchronous close of graph connection"""
        if self.connection:
            try:
                self.connection.close()
                logger.info("✅ Disconnected from Aerospike Graph")
            except Exception as e:
                logger.warning(f"⚠️  Error closing connection: {e}")


    # ----------------------------------------------------------------------------------------------------------
    # Helper functions
    # ----------------------------------------------------------------------------------------------------------


    def get_property_value(self, vertex, key, default=None):
        """Helper function to get property value from vertex"""
        for prop in vertex.properties:
            if prop.key == key:
                return prop.value
        return default
    
    # ----------------------------------------------------------------------------------------------------------
    # Dashboard functions
    # ----------------------------------------------------------------------------------------------------------


    def get_graph_summary(self) -> Dict[str, Any]:
        """Get graph summary using Aerospike Graph admin API - reusable method"""
        try:
            if not self.client:
                logger.warning("No graph client available for summary")
                return {}
                
            logger.info("Getting graph summary using Aerospike Graph admin API")
            summary_result = self.client.call("aerospike.graph.admin.metadata.summary").next()
            logger.debug(f"Raw graph summary result: {summary_result}")
            
            # Parse and structure the summary data
            parsed_summary = {
                'total_vertex_count': summary_result.get('Total vertex count', 0),
                'total_edge_count': summary_result.get('Total edge count', 0),
                'total_supernode_count': summary_result.get('Total supernode count', 0),
                'vertex_counts': summary_result.get('Vertex count by label', {}),
                'edge_counts': summary_result.get('Edge count by label', {}),
                'supernode_counts': summary_result.get('Supernode count by label', {}),
                'vertex_properties': summary_result.get('Vertex properties by label', {}),
                'edge_properties': summary_result.get('Edge properties by label', {}),
                'raw_summary': summary_result  # Include raw data for advanced use cases
            }
            
            logger.info(f"Parsed graph summary - Vertices: {parsed_summary['total_vertex_count']}, Edges: {parsed_summary['total_edge_count']}")
            return parsed_summary
            
        except Exception as e:
            logger.error(f"Error getting graph summary: {e}")
            return {}

    def get_graph_summary_cached(self, ttl_seconds: int = 30) -> Dict[str, Any]:
        """
        Cached wrapper around get_graph_summary().

        At billion scale the summary API is cheap (it reads maintained metadata),
        but dashboard polling can be frequent, so we memoize the result for a
        short TTL to avoid hammering AGS.
        """
        now = time.time()
        if self._summary_cache is not None and (now - self._summary_cache_ts) < ttl_seconds:
            return self._summary_cache

        summary = self.get_graph_summary()
        if summary:
            self._summary_cache = summary
            self._summary_cache_ts = now
            return summary

        # On failure, fall back to the last good cache if we have one
        return self._summary_cache or {}

    def get_dashboard_stats_from_summary(self, flagged_count: int = 0,
                                         amount: Optional[float] = None,
                                         fraud_rate: Optional[float] = None) -> Dict[str, Any]:
        """
        Build dashboard stats from the Graph summary API for remote mode.

        Vertex/edge counts come from graph metadata (instant even at 1B+).
        `flagged_count` is supplied from the small KV flagged_accounts set.
        `amount`/`fraud_rate` are not derivable cheaply from the summary; they
        come from an external aggregate record when available, else remain None.
        """
        summary = self.get_graph_summary_cached()
        if not summary:
            return {
                "users": 0, "accounts": 0, "devices": 0, "txns": 0,
                "flagged": flagged_count,
                "amount": amount, "fraud_rate": fraud_rate,
                "health": "disconnected" if not self.client else "error",
                "source": "graph_summary",
            }

        vertices = summary.get("vertex_counts", {}) or {}
        edges = summary.get("edge_counts", {}) or {}

        return {
            "users": vertices.get("user", 0),
            "accounts": vertices.get("account", 0),
            "devices": vertices.get("device", 0),
            # One TRANSACTS edge per transaction (no in/out double-count like KV)
            "txns": edges.get("TRANSACTS", 0),
            "flagged": flagged_count,
            "amount": amount,
            "fraud_rate": fraud_rate,
            "health": "connected",
            "source": "graph_summary",
        }

    # ----------------------------------------------------------------------------------------------------------
    # Graph-backed browse (remote mode: KV users/transactions are not populated)
    # ----------------------------------------------------------------------------------------------------------

    def _summary_user_count(self) -> int:
        summary = self.get_graph_summary_cached()
        return (summary.get("vertex_counts", {}) or {}).get("user", 0)

    def get_users_paginated_from_graph(self, page: int = 1, page_size: int = 20,
                                       query: Optional[str] = None) -> Dict[str, Any]:
        """
        Paginated user list backed by the Graph (remote mode).

        Uses range() for bounded paging (total from the summary). For search we
        do a bounded exact id/name lookup to avoid full-graph text scans.
        """
        empty = {'result': [], 'total': 0, 'total_pages': 0, 'page': page, 'page_size': page_size}
        if not self.client:
            return empty

        def _project(trav):
            return (trav
                .project("id", "name", "email", "risk_score", "location", "occupation", "age", "signup_date")
                .by(__.id_())
                .by(__.coalesce(__.values("name"), __.constant("")))
                .by(__.coalesce(__.values("email"), __.constant("")))
                .by(__.coalesce(__.values("risk_score"), __.constant(0)))
                .by(__.coalesce(__.values("location"), __.constant("")))
                .by(__.coalesce(__.values("occupation"), __.constant("")))
                .by(__.coalesce(__.values("age"), __.constant(0)))
                .by(__.coalesce(__.values("signup_date"), __.constant(""))))

        try:
            if query:
                q = query.strip()
                seen = {}
                # Exact vertex id (empty if not found)
                try:
                    for v in _project(self.client.V(q).hasLabel("user")).to_list():
                        seen[v["id"]] = v
                except Exception:
                    pass
                # Exact name match, bounded
                try:
                    for v in _project(self.client.V().hasLabel("user").has("name", q).limit(page_size * 5)).to_list():
                        seen[v["id"]] = v
                except Exception:
                    pass
                results = list(seen.values())
                total = len(results)
                start = (page - 1) * page_size
                paged = results[start:start + page_size]
            else:
                total = self._summary_user_count()
                start = (page - 1) * page_size
                end = start + page_size
                paged = _project(self.client.V().hasLabel("user").range(start, end)).to_list()

            for v in paged:
                v["user_id"] = v["id"]

            total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
            return {
                'result': paged,
                'total': total,
                'total_pages': total_pages,
                'page': page,
                'page_size': page_size,
                'source': 'graph',
            }
        except Exception as e:
            logger.error(f"Error paginating users from graph: {e}")
            return empty

    def get_user_profile(self, user_id: str, txn_limit: int = 50) -> Optional[Dict[str, Any]]:
        """
        Build a user profile (accounts, devices, recent transactions) from the
        Graph for remote mode. Returns None if the user vertex does not exist.
        """
        if not self.client:
            return None
        try:
            props = self.client.V(user_id).hasLabel("user").value_map().to_list()
            if not props:
                return None

            def _flat(vm):
                return {k: (v[0] if isinstance(v, list) and v else v) for k, v in vm.items()}

            p = _flat(props[0])
            user = {
                "id": user_id,
                "name": p.get("name", ""),
                "email": p.get("email", ""),
                "phone": p.get("phone", ""),
                "age": p.get("age", 0),
                "location": p.get("location", ""),
                "occupation": p.get("occupation", ""),
                "signup_date": p.get("signup_date", ""),
                "risk_score": p.get("risk_score", 0) or 0,
                "is_flagged": bool(p.get("is_flagged", False)),
            }

            accounts = (self.client.V(user_id).out("OWNS").hasLabel("account")
                .project("id", "type", "balance", "bank_name", "status", "fraud_flag")
                .by(__.id_())
                .by(__.coalesce(__.values("type"), __.constant("")))
                .by(__.coalesce(__.values("balance"), __.constant(0)))
                .by(__.coalesce(__.values("bank_name"), __.constant("")))
                .by(__.coalesce(__.values("status"), __.constant("")))
                .by(__.coalesce(__.values("fraud_flag"), __.constant(False)))
                .to_list())

            devices = (self.client.V(user_id).out("USES").hasLabel("device")
                .project("id", "type", "os", "browser", "fraud_flag")
                .by(__.id_())
                .by(__.coalesce(__.values("type"), __.constant("")))
                .by(__.coalesce(__.values("os"), __.constant("")))
                .by(__.coalesce(__.values("browser"), __.constant("")))
                .by(__.coalesce(__.values("fraud_flag"), __.constant(False)))
                .to_list())

            # Recent outgoing transactions across all of the user's accounts (bounded)
            txns_list = []
            try:
                raw_txns = (self.client.V(user_id).out("OWNS").outE("TRANSACTS")
                    .order().by("timestamp", Order.desc).limit(txn_limit)
                    .project("txn_id", "amount", "timestamp", "type", "status", "from", "to")
                    .by(__.coalesce(__.values("txn_id"), __.constant("")))
                    .by(__.coalesce(__.values("amount"), __.constant(0)))
                    .by(__.coalesce(__.values("timestamp"), __.constant("")))
                    .by(__.coalesce(__.values("type"), __.constant("transfer")))
                    .by(__.coalesce(__.values("status"), __.constant("completed")))
                    .by(__.outV().id_())
                    .by(__.inV().id_())
                    .to_list())
                for t in raw_txns:
                    txns_list.append({
                        "txn": {
                            "txn_id": t.get("txn_id", ""),
                            "amount": t.get("amount", 0),
                            "timestamp": t.get("timestamp", ""),
                            "type": t.get("type", "transfer"),
                            "fraud_score": 0,
                            "status": "clean",
                        },
                        "other_party": {
                            "id": t.get("to", ""),
                            "name": "Unknown",
                            "risk_score": 0,
                        },
                    })
            except Exception as e:
                logger.warning(f"Could not fetch graph transactions for user {user_id}: {e}")

            return {"user": user, "accounts": accounts, "devices": devices, "txns": txns_list}
        except Exception as e:
            logger.error(f"Error building user profile from graph for {user_id}: {e}")
            return None

    @staticmethod
    def _entropy(items) -> float:
        """Shannon entropy (base 2) of a list of labels; 0 for empty/uniform."""
        from collections import Counter
        import math
        vals = [i for i in items if i is not None]
        if not vals:
            return 0.0
        total = len(vals)
        return -sum((c / total) * math.log2(c / total) for c in Counter(vals).values())

    @staticmethod
    def _age_days(date_str) -> int:
        """Days since an ISO date string; 365 on parse failure."""
        from datetime import datetime
        try:
            d = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
            now = datetime.now(d.tzinfo) if d.tzinfo else datetime.now()
            return max(0, (now - d).days)
        except Exception:
            return 365

    def compute_account_fact_from_graph(self, account_id: str, max_edges: int = 2000) -> Dict[str, Any]:
        """
        Compute a bounded account_fact from the Graph for lazy hydration (remote mode).

        Only the subset of the KV feature set that is cheaply derivable from a
        bounded traversal is populated (counts, amounts, unique recipients,
        recipient entropy, device count, account age). Time-window features
        (24h peak, z-scores, new-recipient ratio, first-txn delay) are left at
        neutral defaults. `max_edges` caps every traversal so supernode accounts
        stay fast and bounded.
        """
        fact = {
            'txn_out_7d': 0, 'txn_24h_peak': 0, 'avg_txn_day': 0, 'max_txn_hr': 0,
            'txn_zscore': 0, 'out_amt_7d': 0, 'avg_out_amt': 0, 'max_out_amt': 0,
            'amt_zscore': 0, 'uniq_recip': 0, 'new_recip_rat': 0, 'recip_entropy': 0,
            'dev_count': 1, 'shared_dev_ct': 0, 'acct_age_days': 365, 'first_txn_dly': 0,
            'source': 'graph_hydrated',
        }
        if not self.client:
            return fact
        g = self.client
        try:
            out_amounts = g.V(account_id).outE("TRANSACTS").limit(max_edges).values("amount").fold().next() or []
            amts = [float(a) for a in out_amounts if a is not None]
            fact['txn_out_7d'] = len(amts)
            if amts:
                fact['out_amt_7d'] = round(sum(amts), 2)
                fact['avg_out_amt'] = round(sum(amts) / len(amts), 2)
                fact['max_out_amt'] = round(max(amts), 2)
        except Exception as e:
            logger.debug(f"hydrate: out amounts for {account_id}: {e}")
        try:
            recips = g.V(account_id).outE("TRANSACTS").limit(max_edges).inV().id_().fold().next() or []
            fact['uniq_recip'] = len(set(recips))
            fact['recip_entropy'] = round(self._entropy(recips), 2)
        except Exception as e:
            logger.debug(f"hydrate: recipients for {account_id}: {e}")
        try:
            devs = g.V(account_id).bothE("TRANSACTS").limit(max_edges).values("device_id").fold().next() or []
            distinct_devs = {d for d in devs if d}
            if distinct_devs:
                fact['dev_count'] = len(distinct_devs)
        except Exception as e:
            logger.debug(f"hydrate: devices for {account_id}: {e}")
        try:
            created = g.V(account_id).values("created_date").fold().next() or []
            if created:
                fact['acct_age_days'] = self._age_days(created[0])
        except Exception as e:
            logger.debug(f"hydrate: age for {account_id}: {e}")
        return fact

    def compute_device_fact_from_graph(self, device_id: str,
                                       account_risk_scores: Optional[List[float]] = None,
                                       max_edges: int = 2000) -> Dict[str, Any]:
        """
        Compute a bounded device_fact from the Graph for lazy hydration (remote mode).

        `shared_acct_ct` comes from a bounded count of users linked to the device;
        risk aggregates are derived from the account risk scores already computed
        for the owning user in this hydration pass (partial but representative).
        """
        fact = {
            'shared_acct_ct': 0, 'flag_acct_ct': 0, 'avg_acct_risk': 0,
            'max_acct_risk': 0, 'new_acct_7d': 0, 'fraud': False, 'watchlist': False,
            'source': 'graph_hydrated',
        }
        if not self.client:
            return fact
        g = self.client
        try:
            shared = g.V(device_id).in_("USES").limit(max_edges).count().next()
            fact['shared_acct_ct'] = int(shared)
        except Exception as e:
            logger.debug(f"hydrate: shared accts for device {device_id}: {e}")
        scores = [float(s) for s in (account_risk_scores or []) if s is not None]
        if scores:
            fact['avg_acct_risk'] = round(sum(scores) / len(scores), 2)
            fact['max_acct_risk'] = round(max(scores), 2)
        return fact

    def get_account_transactions_from_graph(self, account_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Read an account's transaction ledger from the Graph (remote mode).

        Returns up to `limit` TRANSACTS edges (both directions) shaped like the KV
        transaction records the investigation tools expect, including a resolved
        `counterparty_user_id`. Bounded by `limit` so supernode accounts stay fast.
        """
        if not self.client:
            return []
        g = self.client
        try:
            raw = (g.V(account_id).bothE("TRANSACTS").limit(limit)
                .project("txn_id", "amount", "timestamp", "type", "status", "is_fraud", "fraud_score", "out_acct", "in_acct")
                .by(__.coalesce(__.values("txn_id"), __.constant("")))
                .by(__.coalesce(__.values("amount"), __.constant(0)))
                .by(__.coalesce(__.values("timestamp"), __.constant("")))
                .by(__.coalesce(__.values("type"), __.constant("transfer")))
                .by(__.coalesce(__.values("status"), __.constant("completed")))
                .by(__.coalesce(__.values("is_fraud"), __.constant(False)))
                .by(__.coalesce(__.values("fraud_score"), __.constant(0)))
                .by(__.outV().id_())
                .by(__.inV().id_())
                .to_list())
        except Exception as e:
            logger.warning(f"graph txns for account {account_id}: {e}")
            return []

        # Resolve owner user_id for each distinct counterparty account in one hop.
        counterparties = set()
        for t in raw:
            other = t.get("in_acct") if t.get("out_acct") == account_id else t.get("out_acct")
            if other:
                counterparties.add(other)
        owner_map: Dict[str, str] = {}
        if counterparties:
            try:
                pairs = (g.V(*list(counterparties)).as_("a").in_("OWNS").as_("u")
                    .select("a", "u").by(__.id_()).by(__.id_()).to_list())
                for p in pairs:
                    owner_map[p.get("a")] = p.get("u")
            except Exception as e:
                logger.debug(f"graph owner resolve for {account_id}: {e}")

        txns: List[Dict[str, Any]] = []
        for t in raw:
            out_acct = t.get("out_acct")
            in_acct = t.get("in_acct")
            direction = "out" if out_acct == account_id else "in"
            other = in_acct if direction == "out" else out_acct
            txns.append({
                "txn_id": t.get("txn_id", ""),
                "amount": t.get("amount", 0),
                "timestamp": t.get("timestamp", ""),
                "type": t.get("type", "transfer"),
                "status": t.get("status", "completed"),
                "direction": direction,
                "account_id": account_id,
                "counterparty": other or "",
                "counterparty_user_id": owner_map.get(other, ""),
                "is_fraud": bool(t.get("is_fraud", False)),
                "fraud_score": t.get("fraud_score", 0),
            })
        return txns

    def get_recent_transactions_from_graph(self, page: int = 1, page_size: int = 12) -> Dict[str, Any]:
        """
        Paginated transaction list backed by the Graph (remote mode).

        Uses an unordered range() over TRANSACTS edges for bounded, fast paging
        at billion scale (a global order-by-timestamp over billions of edges is
        not feasible). Total comes from the summary edge count.
        """
        empty = {'result': [], 'total': 0, 'total_pages': 0, 'page': page, 'page_size': page_size}
        if not self.client:
            return empty
        try:
            start = (page - 1) * page_size
            end = start + page_size
            raw = (self.client.E().hasLabel("TRANSACTS").range(start, end)
                .project("txn_id", "amount", "timestamp", "type", "method", "location", "status", "is_fraud", "fraud_score", "from", "to")
                .by(__.coalesce(__.values("txn_id"), __.constant("")))
                .by(__.coalesce(__.values("amount"), __.constant(0)))
                .by(__.coalesce(__.values("timestamp"), __.constant("")))
                .by(__.coalesce(__.values("type"), __.constant("transfer")))
                .by(__.coalesce(__.values("method"), __.constant("")))
                .by(__.coalesce(__.values("location"), __.constant("")))
                .by(__.coalesce(__.values("status"), __.constant("completed")))
                .by(__.coalesce(__.values("is_fraud"), __.constant(False)))
                .by(__.coalesce(__.values("fraud_score"), __.constant(0)))
                .by(__.outV().id_())
                .by(__.inV().id_())
                .to_list())

            result = []
            for t in raw:
                is_fraud = bool(t.get("is_fraud", False))
                fraud_score = t.get("fraud_score", 0) or 0
                result.append({
                    "id": t.get("txn_id", ""),
                    "txn_id": t.get("txn_id", ""),
                    "sender": t.get("from", ""),
                    "receiver": t.get("to", ""),
                    "amount": t.get("amount", 0),
                    "fraud_score": fraud_score,
                    "timestamp": t.get("timestamp", ""),
                    "location": t.get("location", ""),
                    "fraud_status": "fraud" if is_fraud else "clean",
                    "type": t.get("type", "transfer"),
                    "method": t.get("method", ""),
                    "status": t.get("status", "completed"),
                    "is_fraud": is_fraud,
                })

            summary = self.get_graph_summary_cached()
            total = (summary.get("edge_counts", {}) or {}).get("TRANSACTS", 0)
            total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
            return {
                'result': result,
                'total': total,
                'total_pages': total_pages,
                'page': page,
                'page_size': page_size,
                'source': 'graph',
            }
        except Exception as e:
            logger.error(f"Error paginating transactions from graph: {e}")
            return empty

    # ----------------------------------------------------------------------------------------------------------
    # User functions
    # ----------------------------------------------------------------------------------------------------------


    def update_user_risk_score(self, user_id: str, risk_score: float) -> bool:
        """Update the risk_score property on a user vertex in the graph."""
        try:
            if not self.client:
                logger.warning("Graph client not available — cannot update risk score")
                return False
            
            self.client.V(user_id).property("risk_score", risk_score).iterate()
            return True
        except Exception as e:
            logger.error(f"Error updating risk score for user {user_id} in graph: {e}")
            return False

    def get_user_connected_devices(self, user_id: str) -> List[Dict[str, Any]]:
        """Get users who share devices with the specified user"""
        try:
            if not self.client:
                return []
            
            connected_users = (self.client.V(user_id)
                .out("USES")
                .in_("USES")
                .where(__.not_(__.hasId(user_id)))
                .dedup()
                .project("user_id", "name", "risk_score", "shared_device_count")
                .by(__.id_())
                .by(__.coalesce(__.values("name"), __.constant("Unknown")))
                .by(__.coalesce(__.values("risk_score"), __.constant(0)))
                .by(__.out("USES").where(__.in_("USES").hasId(user_id)).count())
                .to_list()
            )
            return connected_users
        except Exception as e:
            logger.error(f"Error finding connected device users: {e}")
            return []
    
    # ----------------------------------------------------------------------------------------------------------
    # Transaction functions
    # ----------------------------------------------------------------------------------------------------------


    def get_transaction_summary(self, txn_id_or_edge_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed transaction information by txn_id or edge ID"""
        try:
            if self.client:
                # First try to find by txn_id property (for KV-sourced transaction links)
                edges = self.client.E().has("txn_id", txn_id_or_edge_id).toList()
                
                if not edges:
                    # Not found by txn_id - transaction may not exist in Graph
                    logger.warning(f"Transaction with txn_id '{txn_id_or_edge_id}' not found in Graph DB")
                    return None
                
                # Found by txn_id property - use the edge ID
                edge_id = edges[0].id
                
                txn_detail = (self.client.E(edge_id)
                    .project("txn", "src", "dest")
                    .by(__.elementMap())
                    .by(__.outV()
                        .project("account", "user")
                        .by(__.elementMap())
                        .by(__.in_("OWNS").elementMap()))
                    .by(__.inV()
                        .project("account", "user")
                        .by(__.elementMap())
                        .by(__.in_("OWNS").elementMap()))
                    .next())

                return {
                    "txn": txn_detail.get("txn"),
                    "src": txn_detail.get("src"),
                    "dest": txn_detail.get("dest")
                }

            else:
                # No graph client available
                raise Exception("Graph client not available. Cannot get transaction detail without graph database connection.")
                
        except Exception as e:
            logger.error(f"Error getting transaction detail for {txn_id_or_edge_id}: {e}")
            return None


    def drop_all_transactions(self):
        if self.client:
            try:
                self.client.with_('evaluationTimeout', 0).E().has_label("TRANSACTS").drop().iterate()
                
                edges = 1
                while edges > 0:
                    edges = self.client.E().has_label("TRANSACTS").count()
                    time.sleep(.5)
                
                return True
            
            except Exception as e:
                logger.error(f"An error occured while dropping all transactions: {e}")
                return False
        logger.error("No graph client available. Cannot drop all transactions without graph database connection.")
        return False


    # ----------------------------------------------------------------------------------------------------------
    # Account functions
    # ----------------------------------------------------------------------------------------------------------


    def get_all_accounts(self) -> List[Dict[str, Any]]:
        """Get all accounts with their associated user information"""
        try:
            if self.client:
                accounts = self.client.V().has_label("account").project("account_id", "account_type").by(T.id).by("type").to_list()
                logger.info(f"Found {len(accounts)} account vertices")
                return accounts
            else:
                return []
                
        except Exception as e:
            logger.error(f"Error getting all accounts: {e}")
            return []
        
    def get_fraud_details_by_txn_id(self, txn_id: str) -> Optional[Dict[str, Any]]:
        """
        Get fraud detection details from Graph DB by transaction ID.
        
        Returns fraud details including RT1, RT2, RT3 rule results if the transaction was flagged.
        Details are returned as JSON strings (as stored in Graph) for frontend to parse.
        """
        try:
            if not self.client:
                return None
            
            # Find the TRANSACTS edge by txn_id property
            edges = self.client.E().has_label('TRANSACTS').has('txn_id', txn_id).valueMap(True).toList()
            
            if not edges:
                return None
            
            edge = edges[0]
            
            # Check if this transaction has fraud details
            is_fraud = edge.get('is_fraud', [False])
            is_fraud = is_fraud[0] if isinstance(is_fraud, list) else is_fraud
            
            if not is_fraud:
                return None
            
            # Get the details array (contains JSON strings for each RT rule)
            # Return as-is since frontend expects JSON strings to parse
            details_raw = edge.get('details', [])
            
            # Details is already a list of JSON strings from Graph
            # No need to unwrap - just ensure it's a list
            if not isinstance(details_raw, list):
                details_raw = [details_raw] if details_raw else []
            
            # Get other fraud properties
            fraud_score = edge.get('fraud_score', [0])
            fraud_score = fraud_score[0] if isinstance(fraud_score, list) else fraud_score
            
            fraud_status = edge.get('fraud_status', [''])
            fraud_status = fraud_status[0] if isinstance(fraud_status, list) else fraud_status
            
            eval_timestamp = edge.get('eval_timestamp', [''])
            eval_timestamp = eval_timestamp[0] if isinstance(eval_timestamp, list) else eval_timestamp
            
            return {
                'is_fraud': True,
                'fraud_score': fraud_score,
                'fraud_status': fraud_status,
                'eval_timestamp': eval_timestamp,
                'details': details_raw  # Return as JSON strings for frontend to parse
            }
            
        except Exception as e:
            logger.error(f"Error getting fraud details for txn_id {txn_id}: {e}")
            return None
        

    # ----------------------------------------------------------------------------------------------------------
    # Utility functions
    # ----------------------------------------------------------------------------------------------------------

    def bulk_load_csv_data(self, vertices_path: str = None, edges_path: str = None) -> Dict[str, Any]:
        """Bulk load data from CSV files using Aerospike Graph bulk loader.
        
        Note: Aerospike Graph bulk loader REQUIRES vertices path - it's not optional.
        
        Args:
            vertices_path: Path to vertices CSV directory (required by bulk loader)
            edges_path: Path to edges CSV directory
        """
        try:
            if not self.client:
                raise Exception("Graph client not available. Cannot bulk load data without graph database connection.")
            
            # Default paths if not provided
            if not vertices_path:
                vertices_path = "/data/graph_csv/vertices"
            if not edges_path:
                edges_path = "/data/graph_csv/edges"
            
            logger.info(f"🚀 bulk_load_csv_data called:")
            logger.info(f"   vertices_path: {vertices_path}")
            logger.info(f"   edges_path: {edges_path}")
            
            # Check what files exist in the edges path (os imported at module level)
            if edges_path and os.path.exists(edges_path):
                logger.info(f"📂 Checking edges directory: {edges_path}")
                if os.path.isdir(edges_path):
                    for root, dirs, files in os.walk(edges_path):
                        for f in files:
                            file_path = os.path.join(root, f)
                            file_size = os.path.getsize(file_path)
                            logger.info(f"   Found: {file_path} ({file_size} bytes)")
                            # Read first 2 lines of CSV files
                            if f.endswith('.csv'):
                                try:
                                    with open(file_path, 'r') as csvf:
                                        lines = csvf.readlines()[:3]
                                        logger.info(f"      Header: {lines[0].strip() if lines else 'EMPTY'}")
                                        if len(lines) > 1:
                                            logger.info(f"      Row 1: {lines[1].strip()}")
                                        logger.info(f"      Total lines: {len(open(file_path).readlines())}")
                                except Exception as read_e:
                                    logger.warning(f"      Could not read file: {read_e}")
                else:
                    logger.info(f"   Path is a file: {edges_path}")
            else:
                logger.warning(f"⚠️ Edges path does not exist: {edges_path}")
            
            bulk_load_result = {}
            try:
                # Execute bulk load using Aerospike Graph loader
                # Note: Both vertices and edges paths are REQUIRED by Aerospike Graph bulk loader
                logger.info("   Executing bulk load Gremlin call...")
                
                bulk_load_result["result"] = (self.client
                    .with_("evaluationTimeout", 2000000)
                    .call("aerospike.graphloader.admin.bulk-load.load")
                    .with_("aerospike.graphloader.vertices", vertices_path)
                    .with_("aerospike.graphloader.edges", edges_path)
                    .next())
                
                logger.info(f"   Bulk load Gremlin call returned: {bulk_load_result['result']}")
                bulk_load_result["success"] = True
                
                # Poll for bulk load status (time imported at module level)
                max_polls = 30
                for i in range(max_polls):
                    try:
                        status = self.client.call("aerospike.graphloader.admin.bulk-load.status").next()
                        logger.info(f"   Bulk load status (poll {i+1}): {status}")
                        status_str = str(status)
                        if "complete=true" in status_str or "step=done" in status_str:
                            logger.info(f"✅ Bulk load completed!")
                            if "bad-edges=" in status_str:
                                # Extract bad-edges count (re imported at module level)
                                match = re.search(r'bad-edges=(\d+)', status_str)
                                if match:
                                    bad_edges = int(match.group(1))
                                    if bad_edges > 0:
                                        logger.warning(f"⚠️ Bulk load had {bad_edges} bad edges!")
                            break
                        time.sleep(1)
                    except Exception as status_e:
                        logger.warning(f"   Could not get status: {status_e}")
                        break

            except Exception as e:
                logger.error(f"❌ Bulk load Gremlin call failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
                bulk_load_result["success"] = False 
                bulk_load_result["error"] = str(e)
                      
            if bulk_load_result["success"]:
                logger.info("Bulk load completed successfully")
                
                # Get statistics about loaded data
                stats = self._get_bulk_load_statistics()
                
                return {
                    "success": True,
                    "message": "Data bulk loaded successfully from CSV files",
                    "vertices_path": vertices_path,
                    "edges_path": edges_path,
                    "statistics": stats,
                    "bulk_load_result": bulk_load_result["result"]
                }
            else:
                return {
                    "success": False,
                    "error": bulk_load_result["error"],
                    "vertices_path": vertices_path,
                    "edges_path": edges_path
                }
                
        except Exception as e:
            logger.error(f"Error during bulk load: {e}")
            return {
                "success": False,
                "error": str(e),
                "vertices_path": vertices_path if vertices_path else "default",
                "edges_path": edges_path if edges_path else "default"
            }

    def _get_bulk_load_statistics(self) -> Dict[str, Any]:
        """Get statistics about the loaded data after bulk load using the reusable get_graph_summary method"""
        try:
            # Reuse the existing get_graph_summary() which correctly parses the dict-based API response
            summary = self.get_graph_summary()
            
            if not summary:
                logger.warning("Could not retrieve graph summary after bulk load")
                return {
                    "total_vertices": 0, "total_edges": 0,
                    "users": 0, "accounts": 0, "devices": 0,
                    "owns_edges": 0, "uses_edges": 0,
                    "vertex_counts_by_label": {}, "edge_counts_by_label": {},
                    "supernode_count": 0, "supernode_counts_by_label": {}
                }
            
            vertices = summary.get('vertex_counts', {})
            edges = summary.get('edge_counts', {})
            
            stats = {
                "total_vertices": summary.get('total_vertex_count', 0),
                "total_edges": summary.get('total_edge_count', 0),
                "users": vertices.get('user', 0),
                "accounts": vertices.get('account', 0),
                "devices": vertices.get('device', 0),
                "owns_edges": edges.get('OWNS', 0),
                "uses_edges": edges.get('USES', 0),
                "vertex_counts_by_label": vertices,
                "edge_counts_by_label": edges,
                "supernode_count": summary.get('total_supernode_count', 0),
                "supernode_counts_by_label": summary.get('supernode_counts', {})
            }
            
            logger.info(f"Graph summary retrieved: {stats['total_vertices']} vertices, {stats['total_edges']} edges, "
                        f"{stats['users']} users, {stats['accounts']} accounts, {stats['devices']} devices")
            return stats
            
        except Exception as e:
            logger.error(f"Error getting graph summary: {e}")
            return {
                "total_vertices": 0,
                "total_edges": 0,
                "users": 0,
                "accounts": 0,
                "devices": 0,
                "owns_edges": 0,
                "uses_edges": 0,
                "error": str(e)
            }

    def get_bulk_load_status(self) -> Dict[str, Any]:
        """Get the status of the current bulk load operation using Aerospike Graph Status API"""
        try:
            if not self.client:
                raise Exception("Graph client not available. Cannot check bulk load status without graph database connection.")
            logger.info("Checking bulk load status using Aerospike Graph Status API...")

            # Use Aerospike Graph Status API to check bulk load progress
            status_result = self.client.call("aerospike.graphloader.admin.bulk-load.status").next()
            
            logger.info(f"Raw bulk load status result: {status_result}")
            
            # Parse the status result
            status_info = {
                "step": status_result.get("step", "unknown"),
                "complete": status_result.get("complete", False),
                "status": status_result.get("status", "unknown"),
                "elements_written": status_result.get("elements-written"),
                "complete_partitions_percentage": status_result.get("complete-partitions-percentage"),
                "duplicate_vertex_ids": status_result.get("duplicate-vertex-ids"),
                "bad_entries": status_result.get("bad-entries"),
                "bad_edges": status_result.get("bad-edges"),
                "message": status_result.get("message"),
                "stacktrace": status_result.get("stacktrace")
            }
            
            # Clean up None values
            status_info = {k: v for k, v in status_info.items() if v is not None}
            
            logger.info(f"Bulk load status: {status_info.get('status', 'unknown')} - {status_info.get('step', 'unknown')}")
            
            # Determine if bulk load is complete or still running
            is_complete = status_info.get("complete", False)
            current_status = status_info.get("status", "unknown")
            
            return {
                "success": True,
                "message": f"Bulk load {current_status}" if current_status != "unknown" else "Status retrieved",
                "status": current_status,
                "step": status_info.get("step", "unknown"),
                "complete": is_complete,
                "elements_written": status_info.get("elements_written"),
                "progress_percentage": status_info.get("complete_partitions_percentage"),
                "details": status_info
            }
                
        except Exception as e:
            logger.error(f"Error getting bulk load status: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Error occurred while checking bulk load status",
                "status": "error"
            }