"""
Transaction Injector Service

Generates historical transactions for testing and demo purposes.
Writes to both Graph (TRANSACTS edges) AND KV (transactions set).

Features:
- Spreads transactions over configurable period (default 30 days)
- Injects ~15% fraudulent patterns:
  - Fraud rings: Concentrated inter-connected accounts
  - Velocity anomalies: Single accounts with burst activity
  - Amount anomalies: High-value outlier transactions
  - New account fraud: Immediate high activity after creation
"""

import csv
import logging
import os
import random
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, Any, List, Set, Tuple, Optional
from collections import defaultdict

from gremlin_python.process.graph_traversal import __

from services.progress_service import progress_service

logger = logging.getLogger('fraud_detection.transaction_injector')

# Regional transaction locations and currency (aligned with Data Management locale)
REGIONAL_TXN_LOCATIONS = {
    'american': [
        'New York, NY', 'Los Angeles, CA', 'Chicago, IL', 'Houston, TX', 'Phoenix, AZ',
        'Philadelphia, PA', 'San Antonio, TX', 'San Diego, CA', 'Dallas, TX', 'San Jose, CA',
        'Austin, TX', 'Jacksonville, FL', 'Fort Worth, TX', 'Columbus, OH', 'Charlotte, NC',
        'San Francisco, CA', 'Indianapolis, IN', 'Seattle, WA', 'Denver, CO', 'Washington, DC',
    ],
    'indian': [
        'Mumbai', 'Delhi', 'Bengaluru', 'Hyderabad', 'Ahmedabad', 'Chennai', 'Kolkata',
        'Pune', 'Jaipur', 'Lucknow', 'Kanpur', 'Nagpur', 'Indore', 'Thane', 'Bhopal',
        'Visakhapatnam', 'Patna', 'Vadodara', 'Ghaziabad', 'Ludhiana', 'Agra', 'Nashik',
    ],
    'en_GB': [
        'London', 'Manchester', 'Birmingham', 'Edinburgh', 'Glasgow', 'Liverpool', 'Leeds',
        'Bristol', 'Sheffield', 'Newcastle', 'Cardiff', 'Belfast', 'Nottingham', 'Southampton',
        'Brighton', 'Leicester', 'Coventry', 'Hull', 'Bradford', 'Portsmouth',
    ],
    'en_AU': [
        'Sydney', 'Melbourne', 'Brisbane', 'Perth', 'Adelaide', 'Gold Coast', 'Newcastle',
        'Canberra', 'Sunshine Coast', 'Wollongong', 'Hobart', 'Geelong', 'Townsville',
        'Cairns', 'Darwin', 'Toowoomba', 'Ballarat', 'Bendigo', 'Launceston',
    ],
    'zh_CN': [
        'Beijing', 'Shanghai', 'Guangzhou', 'Shenzhen', 'Hangzhou', 'Chengdu', 'Wuhan',
        "Xi'an", 'Tianjin', 'Nanjing', 'Suzhou', 'Zhengzhou', 'Changsha', 'Shenyang',
        'Qingdao', 'Dalian', 'Ningbo', 'Xiamen', 'Kunming', 'Hefei',
    ],
}
REGIONAL_CURRENCY = {
    'american': 'USD',
    'indian': 'INR',
    'en_GB': 'GBP',
    'en_AU': 'AUD',
    'zh_CN': 'CNY',
}


class TransactionInjector:
    """
    Generates historical transactions with fraud patterns.
    Dual-writes to Graph and KV store.
    """
    
    # Class-level operation ID for progress tracking
    OPERATION_ID = "inject_transactions"
    
    def __init__(self, graph_service, aerospike_service):
        self.graph = graph_service
        self.kv = aerospike_service
        self._current_progress = 0
        self._total_items = 0
        
        # Thread safety for parallel transaction processing
        self._balance_locks = {}  # Per-account locks for balance updates
        self._lock_manager = threading.Lock()  # Lock for managing per-account locks
        
        # Transaction locations
        self.locations = [
            'New York, NY', 'Los Angeles, CA', 'Chicago, IL', 'Houston, TX',
            'Phoenix, AZ', 'Philadelphia, PA', 'San Antonio, TX', 'San Diego, CA',
            'Dallas, TX', 'San Jose, CA', 'Austin, TX', 'Jacksonville, FL',
            'Fort Worth, TX', 'Columbus, OH', 'Charlotte, NC', 'San Francisco, CA',
            'Indianapolis, IN', 'Seattle, WA', 'Denver, CO', 'Washington, DC',
        ]
        
        # Transaction types
        self.txn_types = ['transfer', 'payment', 'purchase', 'withdrawal', 'deposit']
        
        # Fraud pattern configuration
        self.fraud_config = {
            'fraud_ring_count': 3,           # Number of fraud rings
            'fraud_ring_size': 5,            # Accounts per ring
            'fraud_ring_txn_count': 50,      # Transactions within each ring
            'velocity_anomaly_count': 10,    # Accounts with burst activity
            'velocity_burst_size': 30,       # Transactions per burst
            'amount_anomaly_count': 20,      # High-value outlier transactions
            'new_account_fraud_count': 5,    # New accounts with immediate activity
            # Fraud bursts land within this many recent days so they fall inside the
            # feature-detection window (feature_service.default_window_days = 7).
            # Normal transactions still spread over the full spread_days for history.
            'fraud_recency_days': 7,
        }
    
    def _get_account_lock(self, account_id: str) -> threading.Lock:
        """
        Get or create a lock for an account (thread-safe).
        Used to prevent race conditions during parallel balance updates.
        """
        with self._lock_manager:
            if account_id not in self._balance_locks:
                self._balance_locks[account_id] = threading.Lock()
            return self._balance_locks[account_id]

    def _get_locations_and_currency(self, locale: Optional[str]) -> Tuple[List[str], str]:
        """Return (locations_list, currency_code) for the given locale. Defaults to American."""
        if locale and locale in REGIONAL_TXN_LOCATIONS and locale in REGIONAL_CURRENCY:
            return (REGIONAL_TXN_LOCATIONS[locale], REGIONAL_CURRENCY[locale])
        return (self.locations, 'USD')

    def _run_locations(self) -> List[str]:
        """Locations for the current inject run (set at start of inject_*)."""
        return getattr(self, '_current_locations', None) or self.locations

    def _run_currency(self) -> str:
        """Currency for the current inject run."""
        return getattr(self, '_current_currency', None) or 'USD'

    def _process_single_transaction(self, txn: dict, account_to_user: dict) -> dict:
        """
        Process a single transaction: create Graph edge + update balances.
        Thread-safe for parallel execution.
        
        Args:
            txn: Transaction data dict with sender_account_id, receiver_account_id, amount, etc.
            account_to_user: Mapping of account_id to user_id
            
        Returns:
            dict with success status and txn_id
        """
        sender = txn['sender_account_id']
        receiver = txn['receiver_account_id']
        amount = txn['amount']
        
        try:
            # 1. Create edge in Graph
            self.graph.client.V(sender) \
                .addE("TRANSACTS") \
                .to(__.V(receiver)) \
                .property("txn_id", txn.get('txn_id', '')) \
                .property("amount", txn.get('amount', 0)) \
                .property("currency", txn.get('currency', self._run_currency())) \
                .property("type", txn.get('type', 'transfer')) \
                .property("method", txn.get('method', 'electronic_transfer')) \
                .property("location", txn.get('location', '')) \
                .property("timestamp", txn.get('timestamp', '')) \
                .property("status", txn.get('status', 'completed')) \
                .property("gen_type", txn.get('gen_type', 'HISTORICAL')) \
                .property("device_id", txn.get('device_id', '')) \
                .iterate()
            
            # 2. Update balances with per-account locks (sorted order to prevent deadlock)
            accounts_sorted = sorted([sender, receiver])
            lock1 = self._get_account_lock(accounts_sorted[0])
            lock2 = self._get_account_lock(accounts_sorted[1])
            
            with lock1:
                with lock2:
                    sender_user = account_to_user.get(sender)
                    receiver_user = account_to_user.get(receiver)
                    
                    if sender_user:
                        try:
                            self.kv.update_account_balance(sender_user, sender, -amount)
                        except ValueError as e:
                            logger.debug(f"Could not update sender balance: {e}")
                    
                    if receiver_user:
                        try:
                            self.kv.update_account_balance(receiver_user, receiver, +amount)
                        except ValueError as e:
                            logger.debug(f"Could not update receiver balance: {e}")
            
            return {"success": True, "txn_id": txn['txn_id']}
            
        except Exception as e:
            return {"success": False, "txn_id": txn.get('txn_id'), "error": str(e)}
    
    # ==================================================================================
    # Bulk Fraud Pattern Generation Methods (for parallel processing)
    # ==================================================================================
    
    def _generate_bulk_fraud_rings(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int,
        account_to_user: Dict[str, str],
        user_to_devices: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Generate fraud ring transactions for bulk processing.
        Returns list of {'graph': {...}, 'kv': [{...}, {...}]} dicts.
        """
        transactions = []
        ring_count = self.fraud_config['fraud_ring_count']
        ring_size = min(self.fraud_config['fraud_ring_size'], len(accounts) // ring_count)
        txns_per_ring = target_count // max(1, ring_count)
        
        for ring_idx in range(ring_count):
            # Select accounts for this ring
            ring_accounts = random.sample(accounts, ring_size)
            
            for _ in range(txns_per_ring):
                # Transactions within the ring (circular pattern)
                sender = random.choice(ring_accounts)
                receiver = random.choice([a for a in ring_accounts if a != sender])
                
                amount = random.uniform(2000, 15000)
                timestamp = self._recent_fraud_timestamp()
                txn_id = str(uuid.uuid4())
                location = random.choice(self._run_locations())
                txn_type = random.choice(self.txn_types)

                sender_user = account_to_user.get(sender, '')
                receiver_user = account_to_user.get(receiver, '')
                device_id = ''
                if sender_user and sender_user in user_to_devices:
                    devices = user_to_devices[sender_user]
                    if devices:
                        device_id = random.choice(devices)

                transactions.append({
                    'graph': {
                        'sender_account_id': sender,
                        'receiver_account_id': receiver,
                        'txn_id': txn_id,
                        'amount': round(amount, 2),
                        'currency': self._run_currency(),
                        'type': txn_type,
                        'method': 'electronic_transfer',
                        'location': location,
                        'timestamp': timestamp,
                        'status': 'completed',
                        'gen_type': 'FRAUD_RING',
                        'device_id': device_id,
                    },
                    'kv': [
                        {
                            'account_id': sender,
                            'direction': 'out',
                            'txn_id': txn_id,
                            'amount': round(amount, 2),
                            'type': txn_type,
                            'method': 'electronic_transfer',
                            'location': location,
                            'timestamp': timestamp,
                            'status': 'completed',
                            'counterparty': receiver,
                            'user_id': sender_user,
                            'counterparty_user_id': receiver_user,
                            'device_id': device_id,
                            'is_fraud': False,
                        },
                        {
                            'account_id': receiver,
                            'direction': 'in',
                            'txn_id': txn_id,
                            'amount': round(amount, 2),
                            'type': txn_type,
                            'method': 'electronic_transfer',
                            'location': location,
                            'timestamp': timestamp,
                            'status': 'completed',
                            'counterparty': sender,
                            'user_id': receiver_user,
                            'counterparty_user_id': sender_user,
                            'device_id': device_id,
                            'is_fraud': False,
                        },
                    ]
                })
        
        logger.info(f"Generated {len(transactions)} fraud ring transactions")
        return transactions
    
    def _generate_bulk_velocity_anomalies(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int,
        account_to_user: Dict[str, str],
        user_to_devices: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Generate velocity anomaly transactions (burst activity) for bulk processing.
        """
        transactions = []
        anomaly_count = self.fraud_config['velocity_anomaly_count']
        burst_size = target_count // max(1, anomaly_count)
        
        anomaly_accounts = random.sample(accounts, min(anomaly_count, len(accounts) // 2))
        
        recency = max(1, self.fraud_config.get('fraud_recency_days', 7))
        for anomaly_account in anomaly_accounts:
            # Burst lands within the recent feature-detection window.
            burst_day = random.randint(1, max(1, min(recency, spread_days) - 1) or 1)
            
            for _ in range(burst_size):
                receiver = random.choice([a for a in accounts if a != anomaly_account])
                amount = random.uniform(100, 2000)  # Small rapid transfers
                
                dt = datetime.now() - timedelta(days=burst_day, hours=random.randint(0, 23), minutes=random.randint(0, 59))
                timestamp = dt.isoformat()
                txn_id = str(uuid.uuid4())
                location = random.choice(self._run_locations())
                txn_type = 'transfer'
                
                sender_user = account_to_user.get(anomaly_account, '')
                receiver_user = account_to_user.get(receiver, '')
                device_id = ''
                if sender_user and sender_user in user_to_devices:
                    devices = user_to_devices[sender_user]
                    if devices:
                        device_id = random.choice(devices)
                
                transactions.append({
                    'graph': {
                        'sender_account_id': anomaly_account,
                        'receiver_account_id': receiver,
                        'txn_id': txn_id,
                        'amount': round(amount, 2),
                        'currency': self._run_currency(),
                        'type': txn_type,
                        'method': 'electronic_transfer',
                        'location': location,
                        'timestamp': timestamp,
                        'status': 'completed',
                        'gen_type': 'VELOCITY_ANOMALY',
                        'device_id': device_id,
                    },
                    'kv': [
                        {
                            'account_id': anomaly_account,
                            'direction': 'out',
                            'txn_id': txn_id,
                            'amount': round(amount, 2),
                            'type': txn_type,
                            'method': 'electronic_transfer',
                            'location': location,
                            'timestamp': timestamp,
                            'status': 'completed',
                            'counterparty': receiver,
                            'user_id': sender_user,
                            'counterparty_user_id': receiver_user,
                            'device_id': device_id,
                            'is_fraud': False,
                        },
                        {
                            'account_id': receiver,
                            'direction': 'in',
                            'txn_id': txn_id,
                            'amount': round(amount, 2),
                            'type': txn_type,
                            'method': 'electronic_transfer',
                            'location': location,
                            'timestamp': timestamp,
                            'status': 'completed',
                            'counterparty': anomaly_account,
                            'user_id': receiver_user,
                            'counterparty_user_id': sender_user,
                            'device_id': device_id,
                            'is_fraud': False,
                        },
                    ]
                })
        
        logger.info(f"Generated {len(transactions)} velocity anomaly transactions")
        return transactions
    
    def _generate_bulk_amount_anomalies(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int,
        account_to_user: Dict[str, str],
        user_to_devices: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Generate amount anomaly transactions (high-value outliers) for bulk processing.
        """
        transactions = []
        
        for _ in range(target_count):
            sender, receiver = random.sample(accounts, 2)
            
            # High-value amounts
            amount = random.choice([
                random.uniform(15000, 50000),
                random.uniform(50000, 100000),
                random.uniform(10000, 15000),
            ])

            timestamp = self._recent_fraud_timestamp()
            txn_id = str(uuid.uuid4())
            location = random.choice(self._run_locations())
            txn_type = 'transfer'
            
            sender_user = account_to_user.get(sender, '')
            receiver_user = account_to_user.get(receiver, '')
            device_id = ''
            if sender_user and sender_user in user_to_devices:
                devices = user_to_devices[sender_user]
                if devices:
                    device_id = random.choice(devices)
            
            transactions.append({
                'graph': {
                    'sender_account_id': sender,
                    'receiver_account_id': receiver,
                    'txn_id': txn_id,
                    'amount': round(amount, 2),
                    'currency': self._run_currency(),
                    'type': txn_type,
                    'method': 'electronic_transfer',
                    'location': location,
                    'timestamp': timestamp,
                    'status': 'completed',
                    'gen_type': 'AMOUNT_ANOMALY',
                    'device_id': device_id,
                },
                'kv': [
                    {
                        'account_id': sender,
                        'direction': 'out',
                        'txn_id': txn_id,
                        'amount': round(amount, 2),
                        'type': txn_type,
                        'method': 'electronic_transfer',
                        'location': location,
                        'timestamp': timestamp,
                        'status': 'completed',
                        'counterparty': receiver,
                        'user_id': sender_user,
                        'counterparty_user_id': receiver_user,
                        'device_id': device_id,
                        'is_fraud': False,
                    },
                    {
                        'account_id': receiver,
                        'direction': 'in',
                        'txn_id': txn_id,
                        'amount': round(amount, 2),
                        'type': txn_type,
                        'method': 'electronic_transfer',
                        'location': location,
                        'timestamp': timestamp,
                        'status': 'completed',
                        'counterparty': sender,
                        'user_id': receiver_user,
                        'counterparty_user_id': sender_user,
                        'device_id': device_id,
                        'is_fraud': False,
                    },
                ]
            })
        
        logger.info(f"Generated {len(transactions)} amount anomaly transactions")
        return transactions
    
    def _generate_bulk_new_account_fraud(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int,
        account_to_user: Dict[str, str],
        user_to_devices: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Generate new account fraud transactions (immediate activity) for bulk processing.
        """
        transactions = []
        
        new_account_candidates = accounts[:min(20, len(accounts))]
        txns_per_account = target_count // max(1, len(new_account_candidates))
        
        for new_account in new_account_candidates[:self.fraud_config['new_account_fraud_count']]:
            for _ in range(txns_per_account):
                receiver = random.choice([a for a in accounts if a != new_account])
                amount = random.uniform(500, 8000)
                
                dt = datetime.now() - timedelta(days=random.randint(0, 2), hours=random.randint(0, 23))
                timestamp = dt.isoformat()
                txn_id = str(uuid.uuid4())
                location = random.choice(self._run_locations())
                txn_type = 'transfer'
                
                sender_user = account_to_user.get(new_account, '')
                receiver_user = account_to_user.get(receiver, '')
                device_id = ''
                if sender_user and sender_user in user_to_devices:
                    devices = user_to_devices[sender_user]
                    if devices:
                        device_id = random.choice(devices)
                
                transactions.append({
                    'graph': {
                        'sender_account_id': new_account,
                        'receiver_account_id': receiver,
                        'txn_id': txn_id,
                        'amount': round(amount, 2),
                        'currency': self._run_currency(),
                        'type': txn_type,
                        'method': 'electronic_transfer',
                        'location': location,
                        'timestamp': timestamp,
                        'status': 'completed',
                        'gen_type': 'NEW_ACCOUNT_FRAUD',
                        'device_id': device_id,
                    },
                    'kv': [
                        {
                            'account_id': new_account,
                            'direction': 'out',
                            'txn_id': txn_id,
                            'amount': round(amount, 2),
                            'type': txn_type,
                            'method': 'electronic_transfer',
                            'location': location,
                            'timestamp': timestamp,
                            'status': 'completed',
                            'counterparty': receiver,
                            'user_id': sender_user,
                            'counterparty_user_id': receiver_user,
                            'device_id': device_id,
                            'is_fraud': False,
                        },
                        {
                            'account_id': receiver,
                            'direction': 'in',
                            'txn_id': txn_id,
                            'amount': round(amount, 2),
                            'type': txn_type,
                            'method': 'electronic_transfer',
                            'location': location,
                            'timestamp': timestamp,
                            'status': 'completed',
                            'counterparty': new_account,
                            'user_id': receiver_user,
                            'counterparty_user_id': sender_user,
                            'device_id': device_id,
                            'is_fraud': False,
                        },
                    ]
                })
        
        logger.info(f"Generated {len(transactions)} new account fraud transactions")
        return transactions
    
    def inject_historical_transactions(
        self,
        transaction_count: int = 10000,
        spread_days: int = 30,
        fraud_percentage: float = 0.15,
        locale: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Inject historical transactions with fraud patterns.
        
        Args:
            transaction_count: Total number of transactions to generate
            spread_days: Days to spread transactions over (should cover cooldown)
            fraud_percentage: Percentage of fraudulent transactions (default 15%)
            locale: Demographics region for locations/currency (american, indian, en_GB, en_AU, zh_CN)
            
        Returns:
            Result dict with counts and details
        """
        start_time = datetime.now()
        self._current_locations, self._current_currency = self._get_locations_and_currency(locale)
        
        # Initialize progress tracking
        self._current_progress = 0
        self._total_items = transaction_count
        progress_service.start_operation(
            self.OPERATION_ID, 
            transaction_count, 
            "Initializing transaction injection..."
        )
        
        result = {
            "job_id": f"inject_{start_time.strftime('%Y%m%d_%H%M%S')}",
            "start_time": start_time.isoformat(),
            "config": {
                "transaction_count": transaction_count,
                "spread_days": spread_days,
                "fraud_percentage": fraud_percentage,
            },
            "normal_transactions": 0,
            "fraud_transactions": 0,
            "fraud_patterns": {
                "fraud_rings": 0,
                "velocity_anomalies": 0,
                "amount_anomalies": 0,
                "new_account_fraud": 0,
            },
            "errors": [],
            "kv_writes": 0,
            "graph_writes": 0,
        }
        
        try:
            # Get all accounts
            progress_service.update_progress(self.OPERATION_ID, 0, "Fetching accounts...")
            accounts = self._get_all_accounts()
            if len(accounts) < 10:
                raise Exception(f"Not enough accounts ({len(accounts)}). Need at least 10.")
            
            logger.info(f"Injecting {transaction_count} transactions over {spread_days} days "
                       f"with {fraud_percentage*100:.0f}% fraud rate")
            
            # Calculate fraud transaction counts
            fraud_txn_count = int(transaction_count * fraud_percentage)
            normal_txn_count = transaction_count - fraud_txn_count
            
            # Generate fraud patterns first (they're more structured)
            progress_service.update_progress(self.OPERATION_ID, 0, "Generating fraud patterns...")
            fraud_result = self._generate_fraud_patterns(accounts, fraud_txn_count, spread_days)
            result["fraud_transactions"] = fraud_result["total"]
            result["fraud_patterns"] = fraud_result["patterns"]
            result["graph_writes"] += fraud_result["graph_writes"]
            result["kv_writes"] += fraud_result["kv_writes"]
            
            # Generate normal transactions
            progress_service.update_progress(
                self.OPERATION_ID, 
                self._current_progress, 
                f"Generating normal transactions... ({fraud_result['total']} fraud done)"
            )
            normal_result = self._generate_normal_transactions(accounts, normal_txn_count, spread_days)
            result["normal_transactions"] = normal_result["count"]
            result["graph_writes"] += normal_result["graph_writes"]
            result["kv_writes"] += normal_result["kv_writes"]
            
            result["status"] = "completed"
            
            # Complete progress tracking
            total = result["normal_transactions"] + result["fraud_transactions"]
            progress_service.complete_operation(
                self.OPERATION_ID,
                f"Completed! {total} transactions injected.",
                extra={
                    "fraud_transactions": result["fraud_transactions"],
                    "normal_transactions": result["normal_transactions"],
                    "graph_writes": result["graph_writes"],
                    "kv_writes": result["kv_writes"],
                }
            )
            
        except Exception as e:
            logger.error(f"Transaction injection failed: {e}")
            result["status"] = "failed"
            result["errors"].append(str(e))
            progress_service.fail_operation(self.OPERATION_ID, str(e), "Injection failed")
        
        result["end_time"] = datetime.now().isoformat()
        result["duration_seconds"] = (datetime.now() - start_time).total_seconds()
        
        total = result["normal_transactions"] + result["fraud_transactions"]
        logger.info(f"Transaction injection complete: {total} transactions "
                   f"({result['fraud_transactions']} fraud, {result['normal_transactions']} normal)")
        
        return result
    
    def inject_transactions_bulk(
        self,
        transaction_count: int = 10000,
        spread_days: int = 30,
        fraud_percentage: float = 0.15,
        locale: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Bulk inject transactions using CSV + native bulk loader for Graph,
        and batch writes for KV store.
        
        This is significantly faster than inject_historical_transactions because:
        1. Pre-fetches all account→user mappings (1 KV scan vs 2 Graph queries per txn)
        2. Generates all transactions in memory first
        3. Writes CSV for Graph native bulk loader (1 bulk load vs N Gremlin queries)
        4. Batch writes to KV (1 batch vs 2N individual writes)
        
        Args:
            transaction_count: Total number of transactions to generate
            spread_days: Days to spread transactions over
            fraud_percentage: Percentage of fraudulent transactions (default 15%)
            locale: Demographics region for locations/currency (american, indian, en_GB, en_AU, zh_CN)
            
        Returns:
            Result dict with counts and details
        """
        start_time = datetime.now()
        self._current_locations, self._current_currency = self._get_locations_and_currency(locale)
        logger.info(
            f"Bulk inject locale={locale!r} -> currency={self._current_currency}, "
            f"locations_sample={self._current_locations[:3] if self._current_locations else []}"
        )
        
        # Initialize progress tracking
        self._current_progress = 0
        self._total_items = transaction_count
        progress_service.start_operation(
            self.OPERATION_ID, 
            5,  # 5 stages: prefetch, generate, write csv, graph bulk, kv batch
            "Initializing bulk transaction injection..."
        )
        
        result = {
            "job_id": f"bulk_inject_{start_time.strftime('%Y%m%d_%H%M%S')}",
            "start_time": start_time.isoformat(),
            "config": {
                "transaction_count": transaction_count,
                "spread_days": spread_days,
                "fraud_percentage": fraud_percentage,
            },
            "normal_transactions": 0,
            "fraud_transactions": 0,
            "graph_bulk_load": None,
            "kv_batch_write": None,
            "csv_path": None,
            "errors": [],
            "status": "running"
        }
        
        try:
            # Stage 1: Pre-fetch all mappings from KV
            progress_service.update_progress(self.OPERATION_ID, 0, "Pre-fetching account→user mappings...")
            account_to_user = self._prefetch_account_user_mappings()
            user_to_devices = self._prefetch_user_devices()
            progress_service.update_progress(self.OPERATION_ID, 1, f"Fetched {len(account_to_user)} mappings")
            
            # Get all accounts from KV store (required for transaction generation)
            accounts = self._get_all_accounts_from_kv()
            if len(accounts) < 10:
                raise Exception(f"Not enough accounts ({len(accounts)}). Need at least 10.")
            
            logger.info(f"Generating {transaction_count} bulk transactions over {spread_days} days "
                       f"with {fraud_percentage*100:.0f}% fraud rate")
            
            # Stage 2: Generate ALL transactions in memory
            progress_service.update_progress(self.OPERATION_ID, 1, "Generating transactions in memory...")
            
            # Calculate counts
            fraud_txn_count = int(transaction_count * fraud_percentage)
            normal_txn_count = transaction_count - fraud_txn_count
            
            # Data structures for bulk operations
            graph_transactions = []  # For CSV/Graph bulk loader
            kv_transactions = []     # For KV batch write
            
            # Generate normal transactions
            for i in range(normal_txn_count):
                sender, receiver = random.sample(accounts, 2)
                amount = random.uniform(50, 5000)
                timestamp = self._generate_timestamp(spread_days)
                txn_id = str(uuid.uuid4())
                location = random.choice(self._run_locations())
                txn_type = random.choice(self.txn_types)
                
                sender_user = account_to_user.get(sender, '')
                receiver_user = account_to_user.get(receiver, '')
                device_id = ''
                if sender_user and sender_user in user_to_devices:
                    devices = user_to_devices[sender_user]
                    if devices:
                        device_id = random.choice(devices)
                
                # For Graph CSV
                graph_transactions.append({
                    'sender_account_id': sender,
                    'receiver_account_id': receiver,
                    'txn_id': txn_id,
                    'amount': round(amount, 2),
                    'currency': self._run_currency(),
                    'type': txn_type,
                    'method': 'electronic_transfer',
                    'location': location,
                    'timestamp': timestamp,
                    'status': 'completed',
                    'gen_type': 'HISTORICAL',
                    'device_id': device_id,
                })
                
                # For KV (sender outgoing)
                kv_transactions.append({
                    'account_id': sender,
                    'direction': 'out',
                    'txn_id': txn_id,
                    'amount': round(amount, 2),
                    'type': txn_type,
                    'method': 'electronic_transfer',
                    'location': location,
                    'timestamp': timestamp,
                    'status': 'completed',
                    'counterparty': receiver,
                    'user_id': sender_user,
                    'counterparty_user_id': receiver_user,
                    'device_id': device_id,
                    'is_fraud': False,
                })
                
                # For KV (receiver incoming)
                kv_transactions.append({
                    'account_id': receiver,
                    'direction': 'in',
                    'txn_id': txn_id,
                    'amount': round(amount, 2),
                    'type': txn_type,
                    'method': 'electronic_transfer',
                    'location': location,
                    'timestamp': timestamp,
                    'status': 'completed',
                    'counterparty': sender,
                    'user_id': receiver_user,
                    'counterparty_user_id': sender_user,
                    'device_id': device_id,
                    'is_fraud': False,
                })
            
            result["normal_transactions"] = normal_txn_count
            
            # Generate fraud transactions with sophisticated patterns
            # Distribution: 30% rings, 25% velocity, 30% amount, 15% new account
            ring_txns = int(fraud_txn_count * 0.30)
            velocity_txns = int(fraud_txn_count * 0.25)
            amount_txns = int(fraud_txn_count * 0.30)
            new_acct_txns = fraud_txn_count - ring_txns - velocity_txns - amount_txns
            
            logger.info(f"Generating sophisticated fraud patterns: "
                       f"{ring_txns} rings, {velocity_txns} velocity, "
                       f"{amount_txns} amount, {new_acct_txns} new account")
            
            # Generate each pattern type
            fraud_ring_txns = self._generate_bulk_fraud_rings(
                accounts, ring_txns, spread_days, account_to_user, user_to_devices
            )
            velocity_anomaly_txns = self._generate_bulk_velocity_anomalies(
                accounts, velocity_txns, spread_days, account_to_user, user_to_devices
            )
            amount_anomaly_txns = self._generate_bulk_amount_anomalies(
                accounts, amount_txns, spread_days, account_to_user, user_to_devices
            )
            new_account_fraud_txns = self._generate_bulk_new_account_fraud(
                accounts, new_acct_txns, spread_days, account_to_user, user_to_devices
            )
            
            # Combine all fraud transactions and add to main lists
            all_fraud_txns = fraud_ring_txns + velocity_anomaly_txns + amount_anomaly_txns + new_account_fraud_txns
            for txn in all_fraud_txns:
                graph_transactions.append(txn['graph'])
                kv_transactions.extend(txn['kv'])
            
            result["fraud_transactions"] = len(all_fraud_txns)
            result["fraud_patterns"] = {
                "fraud_rings": len(fraud_ring_txns),
                "velocity_anomalies": len(velocity_anomaly_txns),
                "amount_anomalies": len(amount_anomaly_txns),
                "new_account_fraud": len(new_account_fraud_txns),
            }
            progress_service.update_progress(
                self.OPERATION_ID, 2, 
                f"Generated {len(graph_transactions)} transactions in memory"
            )
            
            # Stage 3: Write CSV for Graph bulk loader
            progress_service.update_progress(self.OPERATION_ID, 2, "Writing transactions to CSV...")
            csv_path = "/data/graph_csv/edges/transactions/transacts.csv"
            csv_success = self._write_transactions_csv(csv_path, graph_transactions)
            result["csv_path"] = csv_path
            progress_service.update_progress(self.OPERATION_ID, 3, f"CSV written: {csv_path}")
            
            # Stage 4: Call Graph bulk loader (only load transactions directory, not all edges)
            progress_service.update_progress(self.OPERATION_ID, 3, "Loading transactions into Graph...")
            logger.info(f"📊 Starting Graph bulk load for transactions...")
            logger.info(f"   CSV path: {csv_path}")
            logger.info(f"   CSV success: {csv_success}")
            logger.info(f"   Graph service available: {self.graph is not None}")
            
            # Verify some accounts exist in graph before loading
            if self.graph and self.graph.client:
                try:
                    # Check a few sample accounts
                    sample_accounts = accounts[:5] if len(accounts) >= 5 else accounts
                    logger.info(f"🔍 Verifying sample accounts exist in Graph...")
                    for acc_id in sample_accounts:
                        exists = self.graph.client.V(acc_id).hasNext()
                        logger.info(f"   Account {acc_id} exists in Graph: {exists}")
                    
                    # Get current edge count before load
                    edge_count = self.graph.client.E().count().next()
                    logger.info(f"   Current edge count in Graph before transaction load: {edge_count}")
                except Exception as e:
                    logger.warning(f"   Could not verify accounts: {e}")
            
            # Stage 4: Add transactions to Graph using PARALLEL Gremlin
            # Uses ThreadPoolExecutor for concurrent edge creation + balance updates
            if self.graph and self.graph.client:
                try:
                    logger.info(f"   Adding {len(graph_transactions)} transaction edges via parallel Gremlin...")
                    graph_success_count = 0
                    graph_fail_count = 0
                    
                    # Parallel processing with ThreadPoolExecutor
                    max_workers = 20  # Configurable number of parallel workers
                    total_txns = len(graph_transactions)
                    
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        # Submit all tasks
                        futures = {
                            executor.submit(self._process_single_transaction, txn, account_to_user): txn
                            for txn in graph_transactions
                        }
                        
                        completed = 0
                        for future in as_completed(futures):
                            try:
                                result_item = future.result()
                                if result_item["success"]:
                                    graph_success_count += 1
                                else:
                                    graph_fail_count += 1
                                    if graph_fail_count <= 5:  # Only log first 5 errors
                                        logger.warning(f"Transaction failed: {result_item.get('error', 'Unknown')}")
                            except Exception as e:
                                graph_fail_count += 1
                                if graph_fail_count <= 5:
                                    logger.warning(f"Future exception: {e}")
                            
                            completed += 1
                            
                            # Update progress every 500 transactions
                            if completed % 500 == 0:
                                progress_service.update_progress(
                                    self.OPERATION_ID, 3, 
                                    f"Graph: Processed {completed}/{total_txns} transactions ({graph_success_count} success)..."
                                )
                    
                    result["graph_edges_added"] = graph_success_count
                    result["graph_edges_failed"] = graph_fail_count
                    result["graph_writes"] = graph_success_count
                    logger.info(f"✅ Graph edges: {graph_success_count} added, {graph_fail_count} failed (parallel with {max_workers} workers)")
                    
                    # Verify TRANSACTS edges were created
                    transacts_count = self.graph.client.E().hasLabel('TRANSACTS').count().next()
                    logger.info(f"   Total TRANSACTS edges in Graph: {transacts_count}")
                        
                except Exception as e:
                    logger.error(f"❌ Graph edge insertion failed: {e}")
                    result["errors"].append(f"Graph edge insertion failed: {str(e)}")
            else:
                logger.warning(f"⚠️ Graph service not available, skipping edge insertion")
            progress_service.update_progress(self.OPERATION_ID, 4, "Graph bulk load complete")
            
            # Stage 5: Batch write to KV
            progress_service.update_progress(self.OPERATION_ID, 4, "Batch writing to KV store...")
            if self.kv and self.kv.is_connected():
                kv_result = self.kv.batch_store_transactions(kv_transactions)
                result["kv_batch_write"] = kv_result
                result["kv_writes"] = kv_result.get("stored", len(kv_transactions)) if isinstance(kv_result, dict) else len(kv_transactions)
                logger.info(f"KV batch write result: {kv_result}")
            else:
                result["kv_writes"] = 0
                result["errors"].append("KV service not available for batch write")
            
            result["status"] = "completed"
            progress_service.complete_operation(
                self.OPERATION_ID,
                f"Bulk injection complete: {transaction_count} transactions",
                extra={
                    "normal": result["normal_transactions"],
                    "fraud": result["fraud_transactions"],
                }
            )
            
        except Exception as e:
            logger.error(f"Bulk transaction injection failed: {e}")
            result["status"] = "failed"
            result["errors"].append(str(e))
            progress_service.fail_operation(self.OPERATION_ID, str(e), "Bulk injection failed")
        
        result["end_time"] = datetime.now().isoformat()
        result["duration_seconds"] = (datetime.now() - start_time).total_seconds()
        
        total = result["normal_transactions"] + result["fraud_transactions"]
        logger.info(f"Bulk transaction injection complete: {total} transactions in {result['duration_seconds']:.2f}s")
        
        return result
    
    def _update_progress(self, increment: int = 1, message: Optional[str] = None):
        """Update progress counter and report to progress service."""
        self._current_progress += increment
        progress_service.update_progress(
            self.OPERATION_ID,
            self._current_progress,
            message
        )
    
    def _get_all_accounts(self) -> List[str]:
        """Get all account IDs from the graph."""
        if not self.graph or not self.graph.client:
            raise Exception("Graph service not available")
        
        return self.graph.client.V().hasLabel("account").id_().toList()
    
    def _get_all_accounts_from_kv(self) -> List[str]:
        """Get all account IDs from KV store by scanning users."""
        if not self.kv or not self.kv.is_connected():
            raise Exception("KV service not available")
        
        account_ids = []
        users = self.kv.get_all_users()
        for user in users:
            accounts = user.get('accounts', {})
            account_ids.extend(accounts.keys())
        
        logger.info(f"Retrieved {len(account_ids)} account IDs from KV store")
        return account_ids
    
    def _get_new_accounts(self, days: int = 30) -> List[str]:
        """Get accounts created in the last N days."""
        if not self.graph or not self.graph.client:
            return []
        
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            # Get accounts with created_date > cutoff
            return self.graph.client.V().hasLabel("account") \
                .has("created_date", lambda x: x > cutoff if x else False) \
                .id_().toList()
        except:
            # Fallback: just return first 20 accounts (assume some are new)
            all_accounts = self._get_all_accounts()
            return all_accounts[:min(20, len(all_accounts))]
    
    def _prefetch_account_user_mappings(self) -> Dict[str, str]:
        """
        Get account_id → user_id mappings from KV store.
        
        Uses the nested 'accounts' map in each user record to build
        a reverse mapping from account_id to user_id.
        
        Returns:
            Dict mapping account_id to user_id
        """
        mappings = {}
        try:
            if not self.kv or not self.kv.is_connected():
                logger.warning("KV service not available for account-user mapping")
                return mappings
            
            users = self.kv.get_all_users()
            for user in users:
                user_id = user.get('user_id')
                accounts = user.get('accounts', {})
                for account_id in accounts.keys():
                    mappings[account_id] = user_id
            
            logger.info(f"Pre-fetched {len(mappings)} account→user mappings from KV")
        except Exception as e:
            logger.error(f"Error fetching account-user mappings: {e}")
        
        return mappings
    
    def _prefetch_user_devices(self) -> Dict[str, List[str]]:
        """
        Get user_id → [device_ids] mappings from KV store.
        
        Returns:
            Dict mapping user_id to list of device_ids
        """
        mappings = {}
        try:
            if not self.kv or not self.kv.is_connected():
                return mappings
            
            users = self.kv.get_all_users()
            for user in users:
                user_id = user.get('user_id')
                devices = user.get('devices', {})
                if devices:
                    mappings[user_id] = list(devices.keys())
            
            logger.info(f"Pre-fetched devices for {len(mappings)} users from KV")
        except Exception as e:
            logger.error(f"Error fetching user-device mappings: {e}")
        
        return mappings
    
    def _write_transactions_csv(self, csv_path: str, transactions: List[Dict[str, Any]]) -> bool:
        """
        Write transaction data to CSV in Aerospike Graph bulk loader format.
        
        CSV format:
        ~from,~to,~label,txn_id:String,amount:Double,currency:String,type:String,
        method:String,location:String,timestamp:Date,status:String,gen_type:String,device_id:String
        
        Args:
            csv_path: Path to write CSV file
            transactions: List of transaction dicts with graph edge data
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(csv_path), exist_ok=True)
            
            logger.info(f"📝 Writing transactions CSV to: {csv_path}")
            logger.info(f"   Total transactions to write: {len(transactions)}")
            
            # Log sample account IDs for debugging
            if transactions:
                sample_senders = set()
                sample_receivers = set()
                for txn in transactions[:10]:
                    sample_senders.add(txn.get('sender_account_id', ''))
                    sample_receivers.add(txn.get('receiver_account_id', ''))
                logger.info(f"   Sample sender accounts: {list(sample_senders)[:5]}")
                logger.info(f"   Sample receiver accounts: {list(sample_receivers)[:5]}")
            
            fieldnames = [
                '~from', '~to', '~label',
                'txn_id:String', 'amount:Double', 'currency:String', 'type:String',
                'method:String', 'location:String', 'timestamp:Date', 'status:String',
                'gen_type:String', 'device_id:String'
            ]
            
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for txn in transactions:
                    row = {
                        '~from': txn.get('sender_account_id', ''),
                        '~to': txn.get('receiver_account_id', ''),
                        '~label': 'TRANSACTS',
                        'txn_id:String': txn.get('txn_id', ''),
                        'amount:Double': txn.get('amount', 0),
                        'currency:String': txn.get('currency', self._run_currency()),
                        'type:String': txn.get('type', 'transfer'),
                        'method:String': txn.get('method', 'electronic_transfer'),
                        'location:String': txn.get('location', ''),
                        'timestamp:Date': txn.get('timestamp', ''),
                        'status:String': txn.get('status', 'completed'),
                        'gen_type:String': txn.get('gen_type', 'HISTORICAL'),
                        'device_id:String': txn.get('device_id', ''),
                    }
                    writer.writerow(row)
            
            # Verify file was written (os is already imported at module level)
            if os.path.exists(csv_path):
                file_size = os.path.getsize(csv_path)
                logger.info(f"✅ CSV written successfully: {csv_path} ({file_size} bytes)")
                
                # Read first few lines to verify content
                with open(csv_path, 'r') as f:
                    lines = f.readlines()[:5]
                    logger.info(f"   CSV header: {lines[0].strip() if lines else 'EMPTY'}")
                    if len(lines) > 1:
                        logger.info(f"   First data row: {lines[1].strip()}")
            else:
                logger.error(f"❌ CSV file not found after write: {csv_path}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error writing transactions CSV: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _generate_timestamp(self, days_back_max: int) -> str:
        """Generate a random timestamp within the specified days."""
        days_back = random.randint(0, days_back_max)
        hours_back = random.randint(0, 23)
        minutes_back = random.randint(0, 59)

        dt = datetime.now() - timedelta(days=days_back, hours=hours_back, minutes=minutes_back)
        return dt.isoformat()

    def _recent_fraud_timestamp(self) -> str:
        """Timestamp within the recent fraud window so injected fraud falls inside
        the feature-detection window and is actually detectable."""
        recency = max(1, self.fraud_config.get('fraud_recency_days', 7))
        dt = datetime.now() - timedelta(
            days=random.randint(0, recency - 1),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
        )
        return dt.isoformat()
    
    def _create_transaction(
        self,
        sender_id: str,
        receiver_id: str,
        amount: float,
        timestamp: str,
        txn_type: str = "transfer",
        is_fraud: bool = False
    ) -> Tuple[bool, bool]:
        """
        Create a transaction in both Graph and KV.
        
        Returns:
            Tuple of (graph_success, kv_success)
        """
        txn_id = str(uuid.uuid4())
        location = random.choice(self._run_locations())
        
        graph_success = False
        kv_success = False
        
        # Get sender's user and their devices for device tracking
        device_id = None
        sender_user_id = None
        receiver_user_id = None
        try:
            # Account -> User (via reverse OWNS edge)
            user_ids = self.graph.client.V(sender_id).in_("OWNS").id_().toList()
            if user_ids:
                sender_user_id = user_ids[0]
                # User -> Devices (via USES edge)
                user_devices = self.graph.client.V(sender_user_id).out("USES").id_().toList()
                if user_devices:
                    device_id = random.choice(user_devices)
            
            # Get receiver's user_id
            receiver_user_ids = self.graph.client.V(receiver_id).in_("OWNS").id_().toList()
            if receiver_user_ids:
                receiver_user_id = receiver_user_ids[0]
        except Exception as e:
            logger.debug(f"Could not get user/device for transaction: {e}")
        
        # Write to Graph
        try:
            edge_builder = self.graph.client.V(sender_id) \
                .addE("TRANSACTS") \
                .to(__.V(receiver_id)) \
                .property("txn_id", txn_id) \
                .property("amount", round(amount, 2)) \
                .property("currency", self._run_currency()) \
                .property("type", txn_type) \
                .property("method", "electronic_transfer") \
                .property("location", location) \
                .property("timestamp", timestamp) \
                .property("status", "completed") \
                .property("gen_type", "HISTORICAL_FRAUD" if is_fraud else "HISTORICAL")
            
            # Add device_id if available
            if device_id:
                edge_builder = edge_builder.property("device_id", device_id)
            
            edge_builder.iterate()
            graph_success = True
        except Exception as e:
            logger.warning(f"Error writing transaction to graph: {e}")
        
        # Write to KV (dual-write for both sender and receiver)
        try:
            txn_data = {
                "txn_id": txn_id,
                "amount": round(amount, 2),
                "type": txn_type,
                "method": "electronic_transfer",
                "location": location,
                "timestamp": timestamp,
                "status": "completed",
                "device_id": device_id,  # Include device_id in KV record
            }
            
            # Sender's outgoing transaction (includes sender's user_id and receiver's user_id as counterparty)
            sender_success = self.kv.store_transaction(
                sender_id,
                {**txn_data, "counterparty": receiver_id, "user_id": sender_user_id, "counterparty_user_id": receiver_user_id},
                direction="out"
            )
            
            # Receiver's incoming transaction (includes receiver's user_id and sender's user_id as counterparty)
            receiver_success = self.kv.store_transaction(
                receiver_id,
                {**txn_data, "counterparty": sender_id, "user_id": receiver_user_id, "counterparty_user_id": sender_user_id},
                direction="in"
            )
            
            kv_success = sender_success and receiver_success
            
        except Exception as e:
            logger.warning(f"Error writing transaction to KV: {e}")
        
        return (graph_success, kv_success)
    
    def _generate_normal_transactions(
        self,
        accounts: List[str],
        count: int,
        spread_days: int
    ) -> Dict[str, Any]:
        """Generate normal (non-fraudulent) transactions."""
        result = {"count": 0, "graph_writes": 0, "kv_writes": 0}
        
        for i in range(count):
            # Random sender and receiver
            sender, receiver = random.sample(accounts, 2)
            
            # Normal amount distribution: $50 - $5000
            amount = random.uniform(50, 5000)
            
            # Random timestamp within spread_days
            timestamp = self._generate_timestamp(spread_days)
            
            # Random transaction type
            txn_type = random.choice(self.txn_types)
            
            graph_ok, kv_ok = self._create_transaction(
                sender, receiver, amount, timestamp, txn_type, is_fraud=False
            )
            
            if graph_ok:
                result["graph_writes"] += 1
            if kv_ok:
                result["kv_writes"] += 1
            if graph_ok or kv_ok:
                result["count"] += 1
            
            # Update progress every 100 transactions
            if (i + 1) % 100 == 0:
                self._update_progress(100, f"Normal transactions: {result['count']}/{count}")
            
            if result["count"] % 1000 == 0 and result["count"] > 0:
                logger.info(f"Generated {result['count']} normal transactions...")
        
        # Update remaining progress
        remaining = count % 100
        if remaining > 0:
            self._update_progress(remaining)
        
        return result
    
    def _generate_fraud_patterns(
        self,
        accounts: List[str],
        fraud_txn_count: int,
        spread_days: int
    ) -> Dict[str, Any]:
        """Generate various fraud patterns."""
        result = {
            "total": 0,
            "patterns": {
                "fraud_rings": 0,
                "velocity_anomalies": 0,
                "amount_anomalies": 0,
                "new_account_fraud": 0,
            },
            "graph_writes": 0,
            "kv_writes": 0,
        }
        
        # Allocate fraud transactions to different patterns
        ring_txns = int(fraud_txn_count * 0.4)      # 40% fraud rings
        velocity_txns = int(fraud_txn_count * 0.25) # 25% velocity anomalies
        amount_txns = int(fraud_txn_count * 0.20)   # 20% amount anomalies
        new_acct_txns = fraud_txn_count - ring_txns - velocity_txns - amount_txns  # 15% new account fraud
        
        # 1. Fraud Rings: Tight-knit groups transacting heavily among themselves
        ring_result = self._generate_fraud_rings(accounts, ring_txns, spread_days)
        result["patterns"]["fraud_rings"] = ring_result["count"]
        result["graph_writes"] += ring_result["graph_writes"]
        result["kv_writes"] += ring_result["kv_writes"]
        
        # 2. Velocity Anomalies: Single accounts with burst activity
        velocity_result = self._generate_velocity_anomalies(accounts, velocity_txns, spread_days)
        result["patterns"]["velocity_anomalies"] = velocity_result["count"]
        result["graph_writes"] += velocity_result["graph_writes"]
        result["kv_writes"] += velocity_result["kv_writes"]
        
        # 3. Amount Anomalies: Unusually high-value transactions
        amount_result = self._generate_amount_anomalies(accounts, amount_txns, spread_days)
        result["patterns"]["amount_anomalies"] = amount_result["count"]
        result["graph_writes"] += amount_result["graph_writes"]
        result["kv_writes"] += amount_result["kv_writes"]
        
        # 4. New Account Fraud: New accounts with immediate high activity
        new_acct_result = self._generate_new_account_fraud(accounts, new_acct_txns, spread_days)
        result["patterns"]["new_account_fraud"] = new_acct_result["count"]
        result["graph_writes"] += new_acct_result["graph_writes"]
        result["kv_writes"] += new_acct_result["kv_writes"]
        
        result["total"] = sum(result["patterns"].values())
        
        return result
    
    def _generate_fraud_rings(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int
    ) -> Dict[str, int]:
        """Generate fraud ring transactions - tight-knit groups."""
        result = {"count": 0, "graph_writes": 0, "kv_writes": 0}
        
        ring_count = self.fraud_config['fraud_ring_count']
        ring_size = min(self.fraud_config['fraud_ring_size'], len(accounts) // ring_count)
        txns_per_ring = target_count // ring_count
        
        txn_counter = 0
        for ring_idx in range(ring_count):
            # Select accounts for this ring
            ring_accounts = random.sample(accounts, ring_size)
            
            # Concentrated time window (1-3 days)
            ring_start_days = random.randint(1, max(1, spread_days - 3))
            
            for _ in range(txns_per_ring):
                sender, receiver = random.sample(ring_accounts, 2)
                
                # Structured amounts (common in money laundering)
                amount = random.choice([
                    random.uniform(9000, 9999),    # Just under $10K threshold
                    random.uniform(4500, 5000),   # Half of threshold
                    random.uniform(2000, 3000),   # Structured smaller amounts
                ])
                
                # Concentrated timestamps
                days_back = random.randint(ring_start_days, ring_start_days + 3)
                dt = datetime.now() - timedelta(days=days_back, hours=random.randint(0, 23))
                timestamp = dt.isoformat()
                
                graph_ok, kv_ok = self._create_transaction(
                    sender, receiver, amount, timestamp, "transfer", is_fraud=True
                )
                
                if graph_ok:
                    result["graph_writes"] += 1
                if kv_ok:
                    result["kv_writes"] += 1
                if graph_ok or kv_ok:
                    result["count"] += 1
                
                txn_counter += 1
                if txn_counter % 50 == 0:
                    self._update_progress(50, f"Fraud rings: {result['count']}/{target_count}")
        
        # Update remaining progress
        remaining = txn_counter % 50
        if remaining > 0:
            self._update_progress(remaining)
        
        logger.info(f"Generated {result['count']} fraud ring transactions across {ring_count} rings")
        return result
    
    def _generate_velocity_anomalies(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int
    ) -> Dict[str, int]:
        """Generate velocity anomaly transactions - burst activity."""
        result = {"count": 0, "graph_writes": 0, "kv_writes": 0}
        
        anomaly_count = self.fraud_config['velocity_anomaly_count']
        burst_size = target_count // anomaly_count
        
        # Select accounts for velocity anomalies
        anomaly_accounts = random.sample(accounts, min(anomaly_count, len(accounts) // 2))
        
        txn_counter = 0
        for anomaly_account in anomaly_accounts:
            # Pick a specific day for the burst
            burst_day = random.randint(1, max(1, spread_days - 1))
            
            for _ in range(burst_size):
                # All transactions in a single day (burst)
                receiver = random.choice([a for a in accounts if a != anomaly_account])
                
                # Small to medium amounts (rapid small transfers)
                amount = random.uniform(100, 2000)
                
                # Same day, different hours (high velocity)
                dt = datetime.now() - timedelta(days=burst_day, hours=random.randint(0, 23), minutes=random.randint(0, 59))
                timestamp = dt.isoformat()
                
                graph_ok, kv_ok = self._create_transaction(
                    anomaly_account, receiver, amount, timestamp, "transfer", is_fraud=True
                )
                
                if graph_ok:
                    result["graph_writes"] += 1
                if kv_ok:
                    result["kv_writes"] += 1
                if graph_ok or kv_ok:
                    result["count"] += 1
                
                txn_counter += 1
                if txn_counter % 50 == 0:
                    self._update_progress(50, f"Velocity anomalies: {result['count']}/{target_count}")
        
        # Update remaining progress
        remaining = txn_counter % 50
        if remaining > 0:
            self._update_progress(remaining)
        
        logger.info(f"Generated {result['count']} velocity anomaly transactions")
        return result
    
    def _generate_amount_anomalies(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int
    ) -> Dict[str, int]:
        """Generate amount anomaly transactions - unusually high values."""
        result = {"count": 0, "graph_writes": 0, "kv_writes": 0}
        
        for i in range(target_count):
            sender, receiver = random.sample(accounts, 2)
            
            # High-value amounts (outliers)
            amount = random.choice([
                random.uniform(15000, 50000),    # Very high
                random.uniform(50000, 100000),   # Extremely high
                random.uniform(10000, 15000),    # High
            ])
            
            timestamp = self._generate_timestamp(spread_days)
            
            graph_ok, kv_ok = self._create_transaction(
                sender, receiver, amount, timestamp, "transfer", is_fraud=True
            )
            
            if graph_ok:
                result["graph_writes"] += 1
            if kv_ok:
                result["kv_writes"] += 1
            if graph_ok or kv_ok:
                result["count"] += 1
            
            if (i + 1) % 20 == 0:
                self._update_progress(20, f"Amount anomalies: {result['count']}/{target_count}")
        
        # Update remaining progress
        remaining = target_count % 20
        if remaining > 0:
            self._update_progress(remaining)
        
        logger.info(f"Generated {result['count']} amount anomaly transactions")
        return result
    
    def _generate_new_account_fraud(
        self,
        accounts: List[str],
        target_count: int,
        spread_days: int
    ) -> Dict[str, int]:
        """Generate new account fraud - immediate activity after creation."""
        result = {"count": 0, "graph_writes": 0, "kv_writes": 0}
        
        # Use first few accounts (assuming they're newer) or random selection
        new_account_candidates = accounts[:min(20, len(accounts))]
        txns_per_account = target_count // max(1, len(new_account_candidates))
        
        txn_counter = 0
        for new_account in new_account_candidates[:self.fraud_config['new_account_fraud_count']]:
            # All activity within first few days (immediate)
            for _ in range(txns_per_account):
                receiver = random.choice([a for a in accounts if a != new_account])
                
                # Mix of amounts
                amount = random.uniform(500, 8000)
                
                # Within first 2 days of the spread
                dt = datetime.now() - timedelta(days=random.randint(0, 2), hours=random.randint(0, 23))
                timestamp = dt.isoformat()
                
                graph_ok, kv_ok = self._create_transaction(
                    new_account, receiver, amount, timestamp, "transfer", is_fraud=True
                )
                
                if graph_ok:
                    result["graph_writes"] += 1
                if kv_ok:
                    result["kv_writes"] += 1
                if graph_ok or kv_ok:
                    result["count"] += 1
                
                txn_counter += 1
                if txn_counter % 20 == 0:
                    self._update_progress(20, f"New account fraud: {result['count']}/{target_count}")
        
        # Update remaining progress
        remaining = txn_counter % 20
        if remaining > 0:
            self._update_progress(remaining)
        
        logger.info(f"Generated {result['count']} new account fraud transactions")
        return result


# Factory function
def create_transaction_injector(graph_service, aerospike_service):
    """Create a TransactionInjector instance."""
    return TransactionInjector(graph_service, aerospike_service)
