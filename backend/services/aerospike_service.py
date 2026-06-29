"""
Aerospike Key-Value Service

This service provides key-value storage operations using Aerospike.
Used for:
- User data storage for risk evaluation
- Risk score and cooldown tracking
- Analyst workflow stage tracking
- Flagged accounts storage
"""

import logging
import math
import os
import csv
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

try:
    import aerospike
    from aerospike import exception as ex
    AEROSPIKE_AVAILABLE = True
except ImportError:
    AEROSPIKE_AVAILABLE = False
    aerospike = None

logger = logging.getLogger('fraud_detection.aerospike')

# Aerospike configuration
AEROSPIKE_HOST = os.environ.get('AEROSPIKE_HOST', 'localhost')
AEROSPIKE_PORT = int(os.environ.get('AEROSPIKE_KV_PORT', '3000'))
AEROSPIKE_NAMESPACE = os.environ.get('AEROSPIKE_NAMESPACE', 'test')

# Set names
SET_USERS = 'users'
SET_EVALUATIONS = 'evaluations'
SET_FLAGGED_ACCOUNTS = 'flagged_accounts'
SET_WORKFLOW = 'workflow'
SET_CONFIG = 'config'
SET_HISTORY = 'detection_history'
# New sets for enhanced data model
SET_TRANSACTIONS = 'transactions'      # PK = {account_id}:{year_month}
SET_ACCOUNT_FACT = 'account_fact'      # PK = account_id
SET_DEVICE_FACT = 'device_fact'        # PK = device_id
SET_INVESTIGATIONS = 'investigations'  # PK = investigation_id
SET_CASE_MEMORY = 'case_memory'          # cross-engine case recall (adk-aerospike, case_ prefix)

# Aerospike bin name limit is 15 characters
# Map long bin names to short versions
BIN_NAME_MAP = {
    'schedule_enabled': 'sched_enabled',
    'accounts_processed': 'accts_proc',
    'accounts_flagged': 'accts_flagged',
    'new_flagged_count': 'new_flag_cnt',
    'cooldown_days': 'cooldown_days',
    'risk_threshold': 'risk_thresh',
    'workflow_status': 'wf_status',
    'flagged_at': 'flagged_at',
    'last_evaluated': 'last_eval',
    'evaluation_count': 'eval_count',
    # Flagged account fields
    'suspicious_transactions': 'suspicious_txn',
    'total_flagged_amount': 'total_flag_amt',
    'transaction_count': 'txn_count',
    'investigation_started': 'invest_started',
    'resolution_notes': 'resol_notes',
    'assigned_analyst': 'assigned_anlst',
    'account_holder': 'acct_holder',
    'account_predictions': 'acct_preds',
    'highest_risk_account_id': 'high_risk_acct',
    # Investigation bins (15 char limit)
    'investigation_id': 'inv_id',
    'initial_evidence': 'init_evidence',
    'final_assessment': 'final_assess',
    'agent_iterations': 'agent_iters',
    'report_markdown': 'report_md',
    'completed_steps': 'compl_steps',
    # Account-fact bins (15 char limit)
    'txn_out_count_7d': 'txn_out_7d',
    'txn_out_count_24h_peak': 'txn_24h_peak',
    'avg_txn_per_day_7d': 'avg_txn_day',
    'max_txn_per_hour_7d': 'max_txn_hr',
    'transaction_zscore': 'txn_zscore',
    'total_out_amount_7d': 'out_amt_7d',
    'avg_out_amount_7d': 'avg_out_amt',
    'max_out_amount_7d': 'max_out_amt',
    'amount_zscore_7d': 'amt_zscore',
    'unique_recipients_7d': 'uniq_recip',
    'new_recipient_ratio_7d': 'new_recip_rat',
    'recipient_entropy_7d': 'recip_entropy',
    'device_count_7d': 'dev_count',
    'shared_device_account_count_7d': 'shared_dev_ct',
    'account_age_days': 'acct_age_days',
    'first_txn_delay_days': 'first_txn_dly',
    'historical_txn_mean': 'hist_txn_mean',
    'historical_amt_mean': 'hist_amt_mean',
    'historical_amt_std': 'hist_amt_std',
    'last_computed': 'last_computed',
    # Device-fact bins
    'shared_account_count_7d': 'shared_acct_ct',
    'flagged_account_count': 'flag_acct_ct',
    'avg_account_risk_score': 'avg_acct_risk',
    'max_account_risk_score': 'max_acct_risk',
    'new_account_rate_7d': 'new_acct_7d',
    # Transaction bins
    'counterparty': 'counterparty',
    'direction': 'direction',
}
# Reverse map for reading
BIN_NAME_REVERSE = {v: k for k, v in BIN_NAME_MAP.items()}


class AerospikeService:
    """
    Service for Aerospike key-value operations.
    """
    
    def __init__(self):
        self.client = None
        self.connected = False
        self.namespace = AEROSPIKE_NAMESPACE
        
    def connect(self) -> bool:
        """Connect to Aerospike cluster."""
        if not AEROSPIKE_AVAILABLE:
            logger.warning("Aerospike Python client not available. Using fallback storage.")
            return False
            
        try:
            config = {
                'hosts': [(AEROSPIKE_HOST, AEROSPIKE_PORT)]
            }
            self.client = aerospike.client(config).connect()
            self.connected = True
            logger.info(f"✅ Connected to Aerospike at {AEROSPIKE_HOST}:{AEROSPIKE_PORT}")
            # Create secondary indexes after connection
            self.create_secondary_indexes()
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to Aerospike: {e}")
            self.connected = False
            return False
    
    def create_secondary_indexes(self):
        """Create secondary indexes for efficient queries."""
        if not self.is_connected():
            return
        
        try:
            # Create index on 'day' bin for transactions set (for querying by date)
            self.client.index_string_create(
                self.namespace,
                SET_TRANSACTIONS,
                'day',
                'idx_txn_day'
            )
            logger.info("✅ Created secondary index 'idx_txn_day' on transactions.day")
        except ex.IndexFoundError:
            # Index already exists - this is fine
            logger.info("ℹ️ Secondary index 'idx_txn_day' already exists")
        except Exception as e:
            logger.warning(f"⚠️ Failed to create secondary index 'idx_txn_day': {e}")
    
    def close(self):
        """Close Aerospike connection."""
        if self.client and self.connected:
            try:
                self.client.close()
                self.connected = False
                logger.info("✅ Disconnected from Aerospike")
            except Exception as e:
                logger.warning(f"Error closing Aerospike connection: {e}")
    
    def is_connected(self) -> bool:
        """Check if connected to Aerospike."""
        return self.connected and self.client is not None
    
    # ----------------------------------------------------------------------------------------------------------
    # Generic Key-Value Operations
    # ----------------------------------------------------------------------------------------------------------
    
    def _shorten_bin_names(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Shorten bin names to fit Aerospike's 15-character limit and filter None values."""
        shortened = {}
        for k, v in data.items():
            # Skip None values - Aerospike can't serialize them
            if v is None:
                continue
            new_key = BIN_NAME_MAP.get(k, k)
            # If still too long, truncate
            if len(new_key) > 15:
                new_key = new_key[:15]
            shortened[new_key] = v
        return shortened
    
    def _expand_bin_names(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Expand shortened bin names back to original."""
        if data is None:
            return None
        expanded = {}
        for k, v in data.items():
            expanded[BIN_NAME_REVERSE.get(k, k)] = v
        return expanded
    
    def put(self, set_name: str, key: str, data: Dict[str, Any], ttl: int = 0) -> bool:
        """
        Store a record in Aerospike.
        
        Args:
            set_name: The set name
            key: Record key
            data: Dictionary of data to store
            ttl: Time-to-live in seconds (0 = never expire)
        """
        if not self.is_connected():
            return False
            
        try:
            record_key = (self.namespace, set_name, key)
            meta = {'ttl': ttl} if ttl > 0 else {}
            # Shorten bin names to fit Aerospike's 15-char limit
            shortened_data = self._shorten_bin_names(data)
            self.client.put(record_key, shortened_data, meta=meta)
            return True
        except Exception as e:
            logger.error(f"Error putting record {key} in {set_name}: {e}")
            return False
    
    def batch_put(self, set_name: str, records: List[tuple]) -> Dict[str, int]:
        """
        Store multiple records in Aerospike using batch write for better performance.
        
        Args:
            set_name: The set name
            records: List of tuples (key, data_dict) to store
            
        Returns:
            Dict with 'success' and 'failed' counts
        """
        result = {"success": 0, "failed": 0}
        
        if not self.is_connected() or not records:
            return result
        
        try:
            # Process records in parallel using batch write
            # Aerospike batch_write uses BatchRecords
            from aerospike_helpers.batch import records as batch_records
            from aerospike import operations as ops  # Use aerospike.operations, not aerospike_helpers.operations
            
            batch_recs = batch_records.BatchRecords()
            
            for key, data in records:
                record_key = (self.namespace, set_name, key)
                # Shorten bin names
                shortened_data = self._shorten_bin_names(data)
                # Create write operations for each bin
                write_ops = [ops.write(k, v) for k, v in shortened_data.items()]
                batch_recs.batch_write.add(key=record_key, ops=write_ops)
            
            # Execute batch write
            self.client.batch_write(batch_recs)
            
            # Count results
            for rec in batch_recs.batch_write:
                if rec.result == 0:  # AEROSPIKE_OK
                    result["success"] += 1
                else:
                    result["failed"] += 1
            
            logger.info(f"batch_put completed: {result['success']} success, {result['failed']} failed")
                    
        except ImportError:
            # Fallback to sequential writes if batch helpers not available
            logger.warning("Batch helpers not available, falling back to sequential writes")
            for key, data in records:
                if self.put(set_name, key, data):
                    result["success"] += 1
                else:
                    result["failed"] += 1
        except Exception as e:
            logger.error(f"Error in batch_put: {e}")
            # Fallback to sequential on error
            for key, data in records:
                if self.put(set_name, key, data):
                    result["success"] += 1
                else:
                    result["failed"] += 1
        
        return result
    
    def batch_get(self, keys: List[tuple]) -> List[Optional[tuple]]:
        """
        Batch read multiple records from Aerospike.
        
        Works with both old (get_many) and new (batch_read) API versions.
        
        Args:
            keys: List of (namespace, set, key) tuples
            
        Returns:
            List of records in same order as keys. Each record is either:
            - (key, meta, bins) tuple if found
            - None if not found
        """
        if not self.is_connected() or not keys:
            return [None] * len(keys)
        
        try:
            # Try new API first (aerospike >= 7.0.0)
            # batch_read(keys) returns BatchRecords with batch_records list
            if hasattr(self.client, 'batch_read'):
                batch_result = self.client.batch_read(keys)
                
                results = []
                for rec in batch_result.batch_records:
                    # result == 0 means success, result == 2 means not found
                    if rec.result == 0 and rec.record:
                        # rec.record is already (key, meta, bins) tuple
                        results.append(rec.record)
                    else:
                        results.append(None)
                return results
            
            # Fallback to old API (get_many)
            elif hasattr(self.client, 'get_many'):
                return self.client.get_many(keys)
            
            else:
                # Neither available, fall back to sequential gets
                results = []
                for key in keys:
                    try:
                        rec = self.client.get(key)
                        results.append(rec if rec and rec[2] else None)
                    except Exception:
                        results.append(None)
                return results
                
        except Exception as e:
            logger.error(f"Error in batch_get: {e}")
            return [None] * len(keys)
    
    def get(self, set_name: str, key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a record from Aerospike.
        
        Args:
            set_name: The set name
            key: Record key
            
        Returns:
            Record data or None if not found
        """
        if not self.is_connected():
            return None
            
        try:
            record_key = (self.namespace, set_name, key)
            _, _, bins = self.client.get(record_key)
            # Expand shortened bin names back to original
            return self._expand_bin_names(bins)
        except ex.RecordNotFound:
            return None
        except Exception as e:
            logger.error(f"Error getting record {key} from {set_name}: {e}")
            return None
    
    def delete(self, set_name: str, key: str) -> bool:
        """Delete a record from Aerospike."""
        if not self.is_connected():
            return False
            
        try:
            record_key = (self.namespace, set_name, key)
            self.client.remove(record_key)
            return True
        except ex.RecordNotFound:
            return True  # Already deleted
        except Exception as e:
            logger.error(f"Error deleting record {key} from {set_name}: {e}")
            return False
    
    def exists(self, set_name: str, key: str) -> bool:
        """Check if a record exists."""
        if not self.is_connected():
            return False
            
        try:
            record_key = (self.namespace, set_name, key)
            _, meta = self.client.exists(record_key)
            return meta is not None
        except Exception as e:
            logger.error(f"Error checking existence of {key} in {set_name}: {e}")
            return False
    
    def scan_all(self, set_name: str, limit: int = 10000) -> List[Dict[str, Any]]:
        """
        Scan all records in a set.
        
        Args:
            set_name: The set name
            limit: Maximum records to return
            
        Returns:
            List of records
        """
        if not self.is_connected():
            return []
            
        try:
            records = []
            scan = self.client.scan(self.namespace, set_name)
            
            def callback(record):
                if len(records) < limit:
                    _, _, bins = record
                    # Expand shortened bin names back to original
                    records.append(self._expand_bin_names(bins))
            
            scan.foreach(callback)
            return records
        except Exception as e:
            logger.error(f"Error scanning {set_name}: {e}")
            return []
    
    def truncate_set(self, set_name: str) -> bool:
        """Delete all records in a set."""
        if not self.is_connected():
            return False
            
        try:
            self.client.truncate(self.namespace, set_name, 0)
            logger.info(f"Truncated set {set_name}")
            return True
        except Exception as e:
            logger.error(f"Error truncating {set_name}: {e}")
            return False
    
    # ----------------------------------------------------------------------------------------------------------
    # User Operations
    # ----------------------------------------------------------------------------------------------------------
    
    def _load_accounts_data(self, accounts_csv: str) -> Dict[str, Dict[str, Any]]:
        """Load accounts from CSV into a dictionary keyed by account_id."""
        accounts = {}
        try:
            with open(accounts_csv, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    account_id = row.get('~id', '')
                    if account_id:
                        accounts[account_id] = {
                            "type": row.get('type:String', ''),
                            "balance": float(row.get('balance:Double', 0)) if row.get('balance:Double') else 0.0,
                            "bank_name": row.get('bank_name:String', ''),
                            "status": row.get('status:String', 'active'),
                            "created_date": row.get('created_date:Date', ''),
                            "is_fraud": False,  # Always False - computed by ML
                        }
            logger.info(f"Loaded {len(accounts)} accounts from CSV")
        except FileNotFoundError:
            logger.warning(f"Accounts CSV not found: {accounts_csv}")
        except Exception as e:
            logger.error(f"Error loading accounts: {e}")
        return accounts
    
    def _load_devices_data(self, devices_csv: str) -> Dict[str, Dict[str, Any]]:
        """Load devices from CSV into a dictionary keyed by device_id."""
        devices = {}
        try:
            with open(devices_csv, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    device_id = row.get('~id', '')
                    if device_id:
                        devices[device_id] = {
                            "type": row.get('type:String', ''),
                            "os": row.get('os:String', ''),
                            "browser": row.get('browser:String', ''),
                            "fingerprint": row.get('fingerprint:String', ''),
                            "first_seen": row.get('first_seen:Date', ''),
                            "last_login": row.get('last_login:Date', ''),
                            "is_fraud": False,  # Always False - computed by ML
                        }
            logger.info(f"Loaded {len(devices)} devices from CSV")
        except FileNotFoundError:
            logger.warning(f"Devices CSV not found: {devices_csv}")
        except Exception as e:
            logger.error(f"Error loading devices: {e}")
        return devices
    
    def _load_ownership_mapping(self, owns_csv: str) -> Dict[str, List[str]]:
        """Load user->accounts mapping from owns.csv."""
        user_accounts = {}
        try:
            with open(owns_csv, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    user_id = row.get('~from', '')
                    account_id = row.get('~to', '')
                    if user_id and account_id:
                        if user_id not in user_accounts:
                            user_accounts[user_id] = []
                        user_accounts[user_id].append(account_id)
            logger.info(f"Loaded ownership mapping for {len(user_accounts)} users")
        except FileNotFoundError:
            logger.warning(f"Owns CSV not found: {owns_csv}")
        except Exception as e:
            logger.error(f"Error loading ownership mapping: {e}")
        return user_accounts
    
    def _load_usage_mapping(self, uses_csv: str) -> Dict[str, List[str]]:
        """Load user->devices mapping from uses.csv."""
        user_devices = {}
        try:
            with open(uses_csv, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    user_id = row.get('~from', '')
                    device_id = row.get('~to', '')
                    if user_id and device_id:
                        if user_id not in user_devices:
                            user_devices[user_id] = []
                        user_devices[user_id].append(device_id)
            logger.info(f"Loaded usage mapping for {len(user_devices)} users")
        except FileNotFoundError:
            logger.warning(f"Uses CSV not found: {uses_csv}")
        except Exception as e:
            logger.error(f"Error loading usage mapping: {e}")
        return user_devices

    def load_users_from_csv(self, csv_path: str = None, clear_existing: bool = True) -> Dict[str, Any]:
        """
        Load users from CSV file into Aerospike using batch write for better performance.
        Also loads accounts and devices data and populates the nested maps.
        
        Args:
            csv_path: Path to users CSV file (or base path for all CSVs)
            clear_existing: If True, truncate existing users first (clears evaluation timestamps)
            
        Returns:
            Result dict with count and status
        """
        if csv_path is None:
            csv_path = "/data/graph_csv/vertices/users/users.csv"
        
        # Determine base path for all CSV files
        base_path = "/data/graph_csv"
        if csv_path and "vertices/users" in csv_path:
            base_path = csv_path.replace("/vertices/users/users.csv", "")
        
        result = {
            "success": False,
            "loaded": 0,
            "errors": 0,
            "accounts_loaded": 0,
            "devices_loaded": 0,
            "message": ""
        }
        
        # Clear existing users to reset evaluation timestamps
        if clear_existing:
            self.truncate_set(SET_USERS)
            logger.info("Cleared existing users before reload")
        
        try:
            # Step 1: Load accounts and devices data
            accounts_csv = f"{base_path}/vertices/accounts/accounts.csv"
            devices_csv = f"{base_path}/vertices/devices/devices.csv"
            owns_csv = f"{base_path}/edges/ownership/owns.csv"
            uses_csv = f"{base_path}/edges/usage/uses.csv"
            
            logger.info("Loading accounts and devices data...")
            accounts_data = self._load_accounts_data(accounts_csv)
            devices_data = self._load_devices_data(devices_csv)
            
            # Step 2: Load ownership and usage mappings
            logger.info("Loading ownership and usage mappings...")
            user_accounts_map = self._load_ownership_mapping(owns_csv)
            user_devices_map = self._load_usage_mapping(uses_csv)
            
            # Step 3: Load users and populate with accounts/devices
            batch_records = []
            parse_errors = 0
            total_accounts = 0
            total_devices = 0
            
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    user_id = row.get('~id', '')
                    if not user_id:
                        parse_errors += 1
                        continue
                    
                    # Build accounts map for this user
                    user_accounts = {}
                    for account_id in user_accounts_map.get(user_id, []):
                        if account_id in accounts_data:
                            user_accounts[account_id] = accounts_data[account_id]
                            total_accounts += 1
                    
                    # Build devices map for this user
                    user_devices = {}
                    for device_id in user_devices_map.get(user_id, []):
                        if device_id in devices_data:
                            user_devices[device_id] = devices_data[device_id]
                            total_devices += 1
                    
                    # Parse user data with populated accounts/devices maps
                    user_data = {
                        "user_id": user_id,
                        "name": row.get('name:String', ''),
                        "email": row.get('email:String', ''),
                        "phone": row.get('phone:String', ''),
                        "age": int(row.get('age:Int', 0)) if row.get('age:Int') else 0,
                        "location": row.get('location:String', ''),
                        "occupation": row.get('occupation:String', ''),
                        "risk_score": 0.0,  # Initial risk score (will be computed)
                        "signup_date": row.get('signup_date:Date', ''),
                        "created_at": datetime.now().isoformat(),
                        # Nested maps for accounts and devices - NOW POPULATED!
                        "accounts": user_accounts,
                        "devices": user_devices,
                        # Evaluation tracking
                        "last_eval": None,
                        "eval_count": 0,
                        # risk_score is set to 0.0 above — ML detection will update it
                        # Workflow tracking
                        "wf_status": None,
                        "flagged_date": None,
                        "analyst": None,
                        "resolution": None,
                        "resol_date": None,
                        "resol_notes": None
                    }
                    
                    batch_records.append((user_id, user_data))
            
            # Write ALL records at once using batch_put
            logger.info(f"Batch writing {len(batch_records)} users to Aerospike...")
            batch_result = self.batch_put(SET_USERS, batch_records)
            result["loaded"] = batch_result.get("success", 0)
            result["errors"] = batch_result.get("failed", 0) + parse_errors
            result["accounts_loaded"] = total_accounts
            result["devices_loaded"] = total_devices
            
            result["success"] = True
            result["message"] = f"Loaded {result['loaded']} users with {total_accounts} accounts and {total_devices} devices"
            logger.info(result["message"])
            
        except FileNotFoundError:
            result["message"] = f"CSV file not found: {csv_path}"
            logger.error(result["message"])
        except Exception as e:
            result["message"] = f"Error loading users: {str(e)}"
            logger.error(result["message"])
        
        return result
    
    def load_sample_flagged_accounts(self) -> Dict[str, Any]:
        """
        Load sample flagged accounts for demo purposes.
        Uses existing users from Aerospike and marks some as flagged.
        
        Returns:
            Result dict with count and status
        """
        import random
        
        result = {
            "success": False,
            "loaded": 0,
            "message": ""
        }
        
        try:
            # Get existing users
            users = self.get_all_users(limit=100)
            if not users:
                result["message"] = "No users found to flag"
                return result
            
            # Select random users to flag (about 15-20 users)
            num_to_flag = min(20, len(users))
            users_to_flag = random.sample(users, num_to_flag)
            
            # Different statuses and risk levels for variety
            statuses = [
                ("pending_review", 8),     # Most common
                ("under_investigation", 4),
                ("confirmed_fraud", 2),
                ("cleared", 4)
            ]
            
            risk_reasons = [
                "Multiple failed transactions in short period",
                "Unusual transaction pattern detected",
                "Connection to known fraudulent accounts",
                "High-value transactions from new account",
                "Rapid succession of international transfers",
                "Multiple device changes in 24 hours",
                "IP address associated with fraud ring",
                "Behavioral anomaly in spending pattern",
                "Account linked to flagged device",
                "Suspicious login location changes"
            ]
            
            risk_factors = [
                ["velocity_spike", "unusual_amount"],
                ["network_connection", "flagged_contact"],
                ["device_fingerprint", "ip_reputation"],
                ["behavioral_anomaly", "time_pattern"],
                ["geographic_anomaly", "velocity_spike"],
                ["new_account_risk", "high_value_transaction"]
            ]
            
            status_idx = 0
            status_count = 0
            current_status, max_count = statuses[status_idx]
            
            for i, user in enumerate(users_to_flag):
                user_id = user.get("user_id", f"user_{i}")
                
                # Cycle through statuses
                if status_count >= max_count:
                    status_idx = (status_idx + 1) % len(statuses)
                    current_status, max_count = statuses[status_idx]
                    status_count = 0
                
                # Generate risk score based on status
                if current_status == "pending_review":
                    risk_score = random.randint(70, 85)
                elif current_status == "under_investigation":
                    risk_score = random.randint(75, 90)
                elif current_status == "confirmed_fraud":
                    risk_score = random.randint(85, 98)
                else:  # cleared
                    risk_score = random.randint(50, 69)
                
                # Calculate dates
                now = datetime.now()
                days_ago = random.randint(1, 30)
                flagged_date = now - timedelta(days=days_ago)
                
                suspicious_txns = random.randint(3, 25)
                flagged_amount = round(random.uniform(5000, 150000), 2)
                
                flagged_account = {
                    "account_id": user_id,
                    "user_id": user_id,
                    "account_holder": user.get("name", f"User {user_id}"),
                    "email": user.get("email", f"{user_id}@example.com"),
                    "risk_score": risk_score,
                    "status": current_status,
                    "flag_reason": random.choice(risk_reasons),
                    "reason": random.choice(risk_reasons),  # Keep for backwards compat
                    "risk_factors": random.choice(risk_factors),
                    "flagged_date": flagged_date.isoformat(),
                    "last_activity": (now - timedelta(hours=random.randint(1, 72))).isoformat(),
                    "suspicious_transactions": suspicious_txns,
                    "total_flagged_amount": flagged_amount,
                    "transaction_count": random.randint(50, 300),
                    "total_amount": round(random.uniform(10000, 500000), 2),
                    "model_version": "v1.0-mock",
                    "confidence": round(random.uniform(0.75, 0.95), 2),
                    "created_at": flagged_date.isoformat()
                }
                
                # Add resolution data for confirmed_fraud and cleared
                if current_status in ["confirmed_fraud", "cleared"]:
                    resolution_date = flagged_date + timedelta(days=random.randint(1, 7))
                    flagged_account["resolution"] = "fraud" if current_status == "confirmed_fraud" else "safe"
                    flagged_account["resolution_date"] = resolution_date.isoformat()
                    flagged_account["resolution_notes"] = (
                        "Account confirmed as fraudulent after investigation" 
                        if current_status == "confirmed_fraud" 
                        else "Investigation found no evidence of fraud"
                    )
                    flagged_account["resolved_by"] = "analyst@demo.com"
                
                # Add investigation data for under_investigation
                if current_status == "under_investigation":
                    flagged_account["assigned_analyst"] = "analyst@demo.com"
                    flagged_account["investigation_started"] = (flagged_date + timedelta(hours=random.randint(1, 24))).isoformat()
                
                if self.flag_account(flagged_account):
                    result["loaded"] += 1
                
                status_count += 1
            
            result["success"] = True
            result["message"] = f"Loaded {result['loaded']} sample flagged accounts"
            logger.info(result["message"])
            
        except Exception as e:
            result["message"] = f"Error loading sample flagged accounts: {str(e)}"
            logger.error(result["message"])
        
        return result
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by ID."""
        return self.get(SET_USERS, user_id)
    
    def update_user(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """Update user fields."""
        user = self.get_user(user_id)
        if not user:
            return False
        
        user.update(updates)
        user["updated_at"] = datetime.now().isoformat()
        return self.put(SET_USERS, user_id, user)
    
    def get_all_users(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Get all users."""
        return self.scan_all(SET_USERS, limit)
    
    def get_users_for_evaluation(self, cooldown_days: int = 7, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Get users that need risk evaluation (not in cooldown).
        
        Args:
            cooldown_days: Days before re-evaluation
            limit: Maximum users to return
        """
        all_users = self.scan_all(SET_USERS, limit=10000)
        eligible_users = []
        cooldown_threshold = datetime.now() - timedelta(days=cooldown_days)
        
        for user in all_users:
            last_evaluated = user.get('last_evaluated')
            
            # Include if never evaluated or cooldown expired
            if last_evaluated is None:
                eligible_users.append(user)
            else:
                try:
                    eval_date = datetime.fromisoformat(last_evaluated)
                    if eval_date < cooldown_threshold:
                        eligible_users.append(user)
                except:
                    eligible_users.append(user)
            
            if len(eligible_users) >= limit:
                break
        
        return eligible_users
    
    def get_all_users_paginated(self, page: int = 1, page_size: int = 20, 
                                 order_by: str = 'name', order: str = 'asc', 
                                 query: str = None) -> Dict[str, Any]:
        """
        Get paginated users from KV store with optional search and sorting.
        
        Args:
            page: Page number (1-indexed)
            page_size: Number of users per page
            order_by: Field to sort by
            order: Sort direction ('asc' or 'desc')
            query: Optional search term for filtering by name or user_id
            
        Returns:
            Dict with result, total count, total_pages and pagination info
        """
        users = self.scan_all(SET_USERS, limit=100000)
        
        # Filter by search query if provided
        if query:
            query_lower = query.lower()
            users = [
                u for u in users 
                if query_lower in u.get('name', '').lower() 
                or query_lower in u.get('user_id', '').lower()
            ]
        
        # Ensure each user has an 'id' field (frontend expects this)
        for user in users:
            if 'id' not in user:
                user['id'] = user.get('user_id', '')
        
        # Sort users
        reverse = order.lower() == 'desc'
        try:
            users.sort(key=lambda x: x.get(order_by, '') or '', reverse=reverse)
        except Exception:
            # If sorting fails, continue with unsorted
            pass
        
        # Paginate
        total = len(users)
        total_pages = math.ceil(total / page_size) if page_size > 0 else 0
        start = (page - 1) * page_size
        end = start + page_size
        
        return {
            'result': users[start:end],  # Frontend expects 'result' not 'results'
            'total': total,
            'total_pages': total_pages,
            'page': page,
            'page_size': page_size
        }
    
    def get_user_stats(self) -> Dict[str, Any]:
        """
        Get user statistics from KV store.
        
        Returns:
            Dict with total_users, total_low_risk, total_med_risk, total_high_risk
        """
        if not self.is_connected():
            return {"total_users": 0, "total_low_risk": 0, "total_med_risk": 0, "total_high_risk": 0}
        
        try:
            total_users = 0
            total_low_risk = 0
            total_med_risk = 0
            total_high_risk = 0
            
            scan = self.client.scan(self.namespace, SET_USERS)
            
            def process_user(record):
                nonlocal total_users, total_low_risk, total_med_risk, total_high_risk
                if record and len(record) > 2 and record[2]:
                    bins = record[2]
                    total_users += 1
                    risk_score = bins.get('risk_score', 0) or 0
                    if risk_score >= 70:
                        total_high_risk += 1
                    elif risk_score >= 25:
                        total_med_risk += 1
                    else:
                        total_low_risk += 1
            
            scan.foreach(process_user)
            
            return {
                "total_users": total_users,
                "total_low_risk": total_low_risk,
                "total_med_risk": total_med_risk,
                "total_high_risk": total_high_risk
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {"total_users": 0, "total_low_risk": 0, "total_med_risk": 0, "total_high_risk": 0}
    
    def get_transaction_stats(self) -> Dict[str, Any]:
        """
        Get transaction statistics from KV store.
        
        Returns:
            Dict with total_txns, total_blocked, total_review, total_clean
        """
        if not self.is_connected():
            return {"total_txns": 0, "total_blocked": 0, "total_review": 0, "total_clean": 0}
        
        try:
            total_txns = 0
            total_blocked = 0
            total_review = 0
            total_clean = 0
            
            scan = self.client.scan(self.namespace, SET_TRANSACTIONS)
            
            def process_txn_record(record):
                nonlocal total_txns, total_blocked, total_review, total_clean
                if record and len(record) > 2 and record[2]:
                    bins = record[2]
                    txs_map = bins.get('txs', {})
                    for ts, txn in txs_map.items():
                        # Only count outgoing to avoid double counting
                        if txn.get('direction') != 'out':
                            continue
                        total_txns += 1
                        if txn.get('is_fraud'):
                            fraud_score = txn.get('fraud_score', 0) or 0
                            if fraud_score >= 90:
                                total_blocked += 1
                            else:
                                total_review += 1
                        else:
                            total_clean += 1
            
            scan.foreach(process_txn_record)
            
            return {
                "total_txns": total_txns,
                "total_blocked": total_blocked,
                "total_review": total_review,
                "total_clean": total_clean
            }
        except Exception as e:
            logger.error(f"Error getting transaction stats: {e}")
            return {"total_txns": 0, "total_blocked": 0, "total_review": 0, "total_clean": 0}
    
    def get_flagged_transactions(self, page: int = 1, page_size: int = 12) -> Dict[str, Any]:
        """
        Get paginated list of flagged transactions from KV store.
        
        Args:
            page: Page number (1-indexed)
            page_size: Number of transactions per page
            
        Returns:
            Dict with result, total count, total_pages and pagination info
        """
        if not self.is_connected():
            return {'result': [], 'total': 0, 'total_pages': 0, 'page': page, 'page_size': page_size}
        
        try:
            flagged_txns = []
            
            scan = self.client.scan(self.namespace, SET_TRANSACTIONS)
            
            def process_txn_record(record):
                if record and len(record) > 2 and record[2]:
                    bins = record[2]
                    txs_map = bins.get('txs', {})
                    account_id = bins.get('account_id', '')
                    day = bins.get('day', '')
                    for ts, txn in txs_map.items():
                        # Only include outgoing fraud transactions
                        if txn.get('direction') == 'out' and txn.get('is_fraud'):
                            flagged_txns.append({
                                'id': txn.get('txn_id', ''),
                                'txn_id': txn.get('txn_id', ''),
                                'account_id': account_id,
                                'day': day,
                                'sender': account_id,
                                'receiver': txn.get('counterparty', ''),
                                'amount': txn.get('amount', 0),
                                'fraud_score': txn.get('fraud_score', 0) or 0,
                                'timestamp': ts,
                                'location': txn.get('location', ''),
                                'fraud_status': 'blocked' if (txn.get('fraud_score', 0) or 0) >= 90 else 'review',
                                'type': txn.get('type', ''),
                                'is_fraud': True,
                            })
            
            scan.foreach(process_txn_record)
            
            # Sort by timestamp descending
            flagged_txns.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            # Paginate
            total = len(flagged_txns)
            total_pages = math.ceil(total / page_size) if page_size > 0 else 0
            start = (page - 1) * page_size
            end = start + page_size
            
            return {
                'result': flagged_txns[start:end],
                'total': total,
                'total_pages': total_pages,
                'page': page,
                'page_size': page_size
            }
        except Exception as e:
            logger.error(f"Error getting flagged transactions: {e}")
            return {'result': [], 'total': 0, 'total_pages': 0, 'page': page, 'page_size': page_size}
    
    def update_user_evaluation(self, user_id: str, risk_score: float) -> bool:
        """Update user's evaluation data and risk score."""
        user = self.get_user(user_id)
        if not user:
            return False
        
        user["last_eval"] = datetime.now().isoformat()
        user["risk_score"] = risk_score
        user["eval_count"] = user.get("eval_count", 0) + 1
        
        return self.put(SET_USERS, user_id, user)
    
    def add_account_to_user(self, user_id: str, account_data: Dict[str, Any]) -> bool:
        """
        Add an account to a user's accounts map.
        
        Args:
            user_id: The user ID
            account_data: Account data including account_id, type, balance, bank_name, etc.
        """
        user = self.get_user(user_id)
        if not user:
            return False
        
        account_id = account_data.get('account_id')
        if not account_id:
            return False
        
        accounts = user.get('accounts', {})
        accounts[account_id] = {
            'type': account_data.get('type', ''),
            'balance': float(account_data.get('balance', 0)),
            'bank_name': account_data.get('bank_name', ''),
            'status': account_data.get('status', 'active'),
            'created_date': account_data.get('created_date', ''),
            'is_fraud': False,  # Initialize at creation time
        }
        user['accounts'] = accounts
        
        return self.put(SET_USERS, user_id, user)
    
    def add_device_to_user(self, user_id: str, device_data: Dict[str, Any]) -> bool:
        """
        Add a device to a user's devices map.
        
        Args:
            user_id: The user ID
            device_data: Device data including device_id, type, os, browser, etc.
        """
        user = self.get_user(user_id)
        if not user:
            return False
        
        device_id = device_data.get('device_id')
        if not device_id:
            return False
        
        devices = user.get('devices', {})
        devices[device_id] = {
            'type': device_data.get('type', ''),
            'os': device_data.get('os', ''),
            'browser': device_data.get('browser', ''),
            'fingerprint': device_data.get('fingerprint', ''),
            'first_seen': device_data.get('first_seen', ''),
            'last_login': device_data.get('last_login', ''),
            'is_fraud': False,  # Initialize at creation time
        }
        user['devices'] = devices
        
        return self.put(SET_USERS, user_id, user)
    
    def get_user_accounts(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all accounts for a user."""
        user = self.get_user(user_id)
        return user.get('accounts', {}) if user else {}
    
    def get_user_devices(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all devices for a user."""
        user = self.get_user(user_id)
        return user.get('devices', {}) if user else {}
    
    def update_account_balance(self, user_id: str, account_id: str, delta: float) -> float:
        """
        Update account balance atomically.
        
        Args:
            user_id: The user who owns the account
            account_id: The account to update
            delta: Amount to add (positive) or subtract (negative)
            
        Returns:
            New balance (can be negative)
            
        Raises:
            ValueError: If user or account not found
        """
        user = self.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")
        
        accounts = user.get('accounts', {})
        if account_id not in accounts:
            raise ValueError(f"Account {account_id} not found for user {user_id}")
        
        current = accounts[account_id].get('balance', 0.0)
        new_balance = current + delta
        accounts[account_id]['balance'] = new_balance
        user['accounts'] = accounts
        self.put(SET_USERS, user_id, user)
        
        return new_balance
    
    def flag_account_in_user(self, user_id: str, account_id: str, is_fraud: bool) -> bool:
        """
        Update is_fraud flag for an account in user's accounts map.
        
        Args:
            user_id: The user ID
            account_id: The account ID to flag
            is_fraud: True if fraudulent, False otherwise
            
        Returns:
            True if successful, False otherwise
        """
        user = self.get_user(user_id)
        if not user or 'accounts' not in user:
            logger.warning(f"User {user_id} not found or has no accounts")
            return False
        
        if account_id not in user['accounts']:
            logger.warning(f"Account {account_id} not found in user {user_id}")
            return False
        
        user['accounts'][account_id]['is_fraud'] = is_fraud
        success = self.put(SET_USERS, user_id, user)
        if success:
            logger.info(f"✅ Flagged account {account_id} in user {user_id}: is_fraud={is_fraud}")
        return success
    
    def flag_device_in_user(self, user_id: str, device_id: str, is_fraud: bool) -> bool:
        """
        Update is_fraud flag for a device in user's devices map.
        
        Args:
            user_id: The user ID
            device_id: The device ID to flag
            is_fraud: True if fraudulent, False otherwise
            
        Returns:
            True if successful, False otherwise
        """
        user = self.get_user(user_id)
        if not user or 'devices' not in user:
            logger.warning(f"User {user_id} not found or has no devices")
            return False
        
        if device_id not in user['devices']:
            logger.warning(f"Device {device_id} not found in user {user_id}")
            return False
        
        user['devices'][device_id]['is_fraud'] = is_fraud
        success = self.put(SET_USERS, user_id, user)
        if success:
            logger.info(f"✅ Flagged device {device_id} in user {user_id}: is_fraud={is_fraud}")
        return success
    
    def flag_transaction_in_kv(self, account_id: str, timestamp: str, is_fraud: bool, fraud_score: float = 0, txn_id: str = None) -> bool:
        """
        Update is_fraud flag for a transaction in KV store.
        
        Args:
            account_id: The account ID (sender or receiver)
            timestamp: Transaction timestamp (used for record key lookup)
            is_fraud: True if fraudulent, False otherwise
            fraud_score: The fraud score to set
            txn_id: Optional transaction ID to match by (more reliable than timestamp)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected():
            return False
        
        try:
            record_key = self._get_transaction_key(account_id, timestamp)
            key = (self.namespace, SET_TRANSACTIONS, record_key)
            
            _, _, bins = self.client.get(key)
            if not bins or 'txs' not in bins:
                logger.warning(f"Transaction record not found for {account_id} at {timestamp}")
                return False
            
            # Find transaction by txn_id if provided (more reliable), otherwise by timestamp
            found_ts = None
            for ts, txn in bins['txs'].items():
                if txn_id and txn.get('txn_id') == txn_id:
                    found_ts = ts
                    break
                elif not txn_id and ts == timestamp:
                    found_ts = ts
                    break
            
            if not found_ts:
                logger.warning(f"Transaction not found in record (txn_id={txn_id}, timestamp={timestamp})")
                return False
            
            bins['txs'][found_ts]['is_fraud'] = is_fraud
            bins['txs'][found_ts]['fraud_score'] = fraud_score
            self.client.put(key, bins)
            logger.info(f"✅ Flagged transaction for {account_id}: is_fraud={is_fraud}, fraud_score={fraud_score}")
            return True
            
        except ex.RecordNotFound:
            logger.warning(f"Transaction record not found for {account_id} at {timestamp}")
            return False
        except Exception as e:
            logger.error(f"Error flagging transaction in KV: {e}")
            return False
    
    # ----------------------------------------------------------------------------------------------------------
    # Flagged Accounts Operations
    # ----------------------------------------------------------------------------------------------------------
    
    def flag_account(self, account_data: Dict[str, Any]) -> bool:
        """Store a flagged account record.
        
        Uses user_id as the key since flagging is user-level (a user may have multiple accounts).
        The account_id field stores the highest risk account ID for reference.
        """
        # Use user_id as the key (flagging is user-level, not account-level)
        user_id = account_data.get('user_id')
        if not user_id:
            return False
        
        account_data["flagged_date"] = datetime.now().isoformat()
        account_data["status"] = account_data.get("status", "pending_review")
        
        return self.put(SET_FLAGGED_ACCOUNTS, user_id, account_data)
    
    def get_flagged_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Get a flagged account by ID."""
        return self.get(SET_FLAGGED_ACCOUNTS, account_id)
    
    def get_all_flagged_accounts(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get all flagged accounts."""
        return self.scan_all(SET_FLAGGED_ACCOUNTS, limit)
    
    def update_flagged_account(self, account_id: str, updates: Dict[str, Any]) -> bool:
        """Update a flagged account."""
        account = self.get_flagged_account(account_id)
        if not account:
            return False
        
        account.update(updates)
        account["updated_at"] = datetime.now().isoformat()
        return self.put(SET_FLAGGED_ACCOUNTS, account_id, account)
    
    def delete_flagged_account(self, account_id: str) -> bool:
        """Delete a flagged account."""
        return self.delete(SET_FLAGGED_ACCOUNTS, account_id)
    
    def clear_all_flagged_accounts(self) -> bool:
        """Clear all flagged accounts."""
        return self.truncate_set(SET_FLAGGED_ACCOUNTS)
    
    # ----------------------------------------------------------------------------------------------------------
    # Workflow Operations
    # ----------------------------------------------------------------------------------------------------------
    
    def update_workflow_status(self, user_id: str, status: str, analyst: str = None, notes: str = None) -> bool:
        """
        Update the workflow status for a user/account.
        
        Status values: pending_review, under_investigation, confirmed_fraud, cleared
        """
        user = self.get_user(user_id)
        if not user:
            return False
        
        user["workflow_status"] = status
        user["workflow_updated_at"] = datetime.now().isoformat()
        
        if analyst:
            user["assigned_analyst"] = analyst
        if notes:
            user["workflow_notes"] = notes
        
        if status in ["confirmed_fraud", "cleared"]:
            user["resolution"] = status
            user["resolution_date"] = datetime.now().isoformat()
            if notes:
                user["resolution_notes"] = notes
        
        return self.put(SET_USERS, user_id, user)
    
    def get_users_by_workflow_status(self, status: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get users with a specific workflow status."""
        all_users = self.scan_all(SET_USERS, limit=10000)
        return [u for u in all_users if u.get('workflow_status') == status][:limit]
    
    # ----------------------------------------------------------------------------------------------------------
    # Configuration Operations
    # ----------------------------------------------------------------------------------------------------------
    
    def get_config(self, config_key: str = "detection_config") -> Optional[Dict[str, Any]]:
        """Get configuration."""
        return self.get(SET_CONFIG, config_key)
    
    def save_config(self, config: Dict[str, Any], config_key: str = "detection_config") -> bool:
        """Save configuration."""
        config["updated_at"] = datetime.now().isoformat()
        return self.put(SET_CONFIG, config_key, config)
    
    # ----------------------------------------------------------------------------------------------------------
    # History Operations
    # ----------------------------------------------------------------------------------------------------------
    
    def add_detection_history(self, job_result: Dict[str, Any]) -> bool:
        """Add a detection job result to history."""
        job_id = job_result.get("job_id", f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        return self.put(SET_HISTORY, job_id, job_result)
    
    def get_detection_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get detection job history."""
        records = self.scan_all(SET_HISTORY, limit=100)
        # Sort by start_time descending
        records.sort(key=lambda x: x.get('start_time', ''), reverse=True)
        return records[:limit]
    
    # ----------------------------------------------------------------------------------------------------------
    # Transaction Operations (KV storage for feature computation)
    # ----------------------------------------------------------------------------------------------------------
    
    def _get_transaction_key(self, account_id: str, timestamp: str = None) -> str:
        """Generate transaction record key: {account_id}:{year_month}"""
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except:
                dt = datetime.now()
        else:
            dt = datetime.now()
        return f"{account_id}:{dt.strftime('%Y-%m-%d')}"
    
    def store_transaction(self, account_id: str, txn_data: Dict[str, Any], direction: str = "out") -> bool:
        """
        Store a transaction in the KV transactions set.
        
        Args:
            account_id: The account ID (sender or receiver)
            txn_data: Transaction data including txn_id, amount, type, counterparty, etc.
            direction: "out" for outgoing (sent), "in" for incoming (received)
        
        The transaction is stored in a map keyed by timestamp within a record
        partitioned by account_id and year-month.
        """
        if not self.is_connected():
            return False
        
        try:
            timestamp = txn_data.get('timestamp', datetime.now().isoformat())
            record_key = self._get_transaction_key(account_id, timestamp)
            
            # Build transaction entry
            txn_entry = {
                'txn_id': txn_data.get('txn_id', ''),
                'amount': float(txn_data.get('amount', 0)),
                'type': txn_data.get('type', 'transfer'),
                'counterparty': txn_data.get('counterparty', ''),
                'user_id': txn_data.get('user_id', ''),  # Sender's user_id
                'counterparty_user_id': txn_data.get('counterparty_user_id', ''),  # Receiver's user_id
                'direction': direction,
                'method': txn_data.get('method', 'electronic'),
                'location': txn_data.get('location', ''),
                'status': txn_data.get('status', 'completed'),
                'device_id': txn_data.get('device_id', ''),  # Device used for transaction
                'is_fraud': False,  # Initialize at creation time
            }
            
            # Get existing record or create new
            key = (self.namespace, SET_TRANSACTIONS, record_key)
            try:
                _, _, bins = self.client.get(key)
                txs_map = bins.get('txs', {}) if bins else {}
            except ex.RecordNotFound:
                txs_map = {}
            
            # Add transaction to map (key = timestamp)
            txs_map[timestamp] = txn_entry
            
            # Store updated record
            data = {
                'txs': txs_map,
                'account_id': account_id,
                'day': record_key.split(':')[1]  # YYYY-MM-DD format for secondary index
            }
            self.client.put(key, data)
            return True
            
        except Exception as e:
            logger.error(f"Error storing transaction for {account_id}: {e}")
            return False
    
    def batch_store_transactions(self, transactions: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Batch store multiple transactions to KV.
        Groups transactions by record_key (account_id:date) for efficient writes.
        
        Args:
            transactions: List of transaction dicts with account_id, direction, txn_data
            
        Returns:
            Dict with 'success' and 'failed' counts
        """
        result = {"success": 0, "failed": 0}
        
        if not self.is_connected() or not transactions:
            return result
        
        try:
            # Group transactions by record_key (account_id:date)
            grouped = {}  # {record_key: {'txs': {timestamp: txn_entry}, 'account_id': ..., 'day': ...}}
            
            for txn in transactions:
                account_id = txn.get('account_id')
                timestamp = txn.get('timestamp', datetime.now().isoformat())
                record_key = self._get_transaction_key(account_id, timestamp)
                
                if record_key not in grouped:
                    grouped[record_key] = {
                        'txs': {},
                        'account_id': account_id,
                        'day': record_key.split(':')[1]  # YYYY-MM-DD format
                    }
                
                txn_entry = {
                    'txn_id': txn.get('txn_id', ''),
                    'amount': float(txn.get('amount', 0)),
                    'type': txn.get('type', 'transfer'),
                    'counterparty': txn.get('counterparty', ''),
                    'user_id': txn.get('user_id', ''),
                    'counterparty_user_id': txn.get('counterparty_user_id', ''),
                    'direction': txn.get('direction', 'out'),
                    'method': txn.get('method', 'electronic'),
                    'location': txn.get('location', ''),
                    'status': txn.get('status', 'completed'),
                    'device_id': txn.get('device_id', ''),
                    'is_fraud': txn.get('is_fraud', False),
                }
                grouped[record_key]['txs'][timestamp] = txn_entry
            
            # Batch write all grouped records
            batch_records = [(key, data) for key, data in grouped.items()]
            batch_result = self.batch_put(SET_TRANSACTIONS, batch_records)
            
            result["success"] = batch_result.get("success", 0)
            result["failed"] = batch_result.get("failed", 0)
            
            logger.info(f"Batch stored {result['success']} transaction records ({len(transactions)} transactions)")
            return result
            
        except Exception as e:
            logger.error(f"Error batch storing transactions: {e}")
            result["failed"] = len(transactions)
            return result
    
    def get_transactions_for_account(self, account_id: str, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get transactions for an account within a sliding window.
        Uses batch read + map filtering for efficiency.
        
        Args:
            account_id: The account ID
            days: Number of days to look back (configurable cooldown)
            
        Returns:
            List of transactions within the window
        """
        if not self.is_connected():
            return []
        
        try:
            now = datetime.now()
            cutoff = (now - timedelta(days=days)).isoformat()
            
            # Generate record keys for each day (daily partitions)
            keys = []
            for i in range(days + 1):  # Include today plus lookback days
                day_date = now - timedelta(days=i)
                record_key = f"{account_id}:{day_date.strftime('%Y-%m-%d')}"
                keys.append((self.namespace, SET_TRANSACTIONS, record_key))
            
            # Batch read records
            records = self.batch_get(keys)
            
            # Filter transactions by timestamp
            transactions = []
            for record in records:
                if record and record[2]:  # bins exist
                    txs_map = record[2].get('txs', {})
                    for ts, txn in txs_map.items():
                        if ts >= cutoff:
                            transactions.append({**txn, 'timestamp': ts})
            
            # Sort by timestamp descending
            transactions.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            return transactions
            
        except Exception as e:
            logger.error(f"Error getting transactions for {account_id}: {e}")
            return []
    
    def batch_get_transactions(self, account_ids: List[str], days: int = 7) -> Dict[str, List[Dict[str, Any]]]:
        """
        Batch read transactions for multiple accounts.
        
        Args:
            account_ids: List of account IDs
            days: Number of days to look back
            
        Returns:
            Dict mapping account_id to list of transactions
        """
        result = {aid: [] for aid in account_ids}
        
        if not self.is_connected() or not account_ids:
            return result
        
        try:
            now = datetime.now()
            cutoff = (now - timedelta(days=days)).isoformat()
            
            # Generate all keys for all accounts (daily partitions)
            keys = []
            key_to_account = {}
            
            for account_id in account_ids:
                for i in range(days + 1):  # Include today plus lookback days
                    day_date = now - timedelta(days=i)
                    record_key = f"{account_id}:{day_date.strftime('%Y-%m-%d')}"
                    key = (self.namespace, SET_TRANSACTIONS, record_key)
                    keys.append(key)
                    key_to_account[record_key] = account_id
            
            # Batch read
            records = self.batch_get(keys)
            
            # Process results
            for record in records:
                if record and record[2]:
                    bins = record[2]
                    account_id = bins.get('account_id', '')
                    txs_map = bins.get('txs', {})
                    
                    if account_id in result:
                        for ts, txn in txs_map.items():
                            if ts >= cutoff:
                                result[account_id].append({**txn, 'timestamp': ts})
            
            # Sort each account's transactions
            for account_id in result:
                result[account_id].sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            return result
            
        except Exception as e:
            logger.error(f"Error batch getting transactions: {e}")
            return result
    
    def get_transactions_by_day(self, day: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """
        Get all transactions for a specific day using secondary index query.
        
        Args:
            day: Date in format YYYY-MM-DD
            page: Page number (1-indexed)
            page_size: Number of transactions per page
            
        Returns:
            Dict with result, total count, total_pages and pagination info
        """
        if not self.is_connected():
            return {'result': [], 'total': 0, 'total_pages': 0, 'page': page, 'page_size': page_size}
        
        try:
            # Query using secondary index on 'day' bin
            query = self.client.query(self.namespace, SET_TRANSACTIONS)
            query.where(aerospike.predicates.equals('day', day))
            
            # Collect all transactions from matching records
            all_transactions = []
            
            def process_record(record):
                if record and len(record) > 2 and record[2]:
                    bins = record[2]
                    txs_map = bins.get('txs', {})
                    account_id = bins.get('account_id', '')
                    record_day = bins.get('day', '')
                    for ts, txn in txs_map.items():
                        # Only include outgoing transactions to avoid duplicates
                        # Each transaction is stored twice (sender=out, receiver=in)
                        if txn.get('direction') != 'out':
                            continue
                        
                        # Format transaction for frontend (expects id, sender, receiver, fraud_score)
                        # Include account_id and day for detail page KV lookup
                        formatted_txn = {
                            'id': txn.get('txn_id', ''),
                            'txn_id': txn.get('txn_id', ''),
                            'account_id': account_id,  # For KV lookup
                            'day': record_day,  # For KV lookup
                            'sender': account_id,
                            'receiver': txn.get('counterparty', ''),
                            'user_id': txn.get('user_id', ''),  # Sender's user
                            'counterparty_user_id': txn.get('counterparty_user_id', ''),  # Receiver's user
                            'amount': txn.get('amount', 0),
                            'fraud_score': txn.get('fraud_score', 0) or 0,
                            'timestamp': ts,
                            'location': txn.get('location', ''),
                            'fraud_status': 'blocked' if txn.get('is_fraud') else 'clean',
                            'type': txn.get('type', ''),
                            'method': txn.get('method', ''),
                            'status': txn.get('status', 'completed'),
                            'device_id': txn.get('device_id', ''),
                            'is_fraud': txn.get('is_fraud', False),
                        }
                        all_transactions.append(formatted_txn)
            
            query.foreach(process_record)
            
            # Sort by timestamp descending
            all_transactions.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            # Paginate
            total = len(all_transactions)
            total_pages = math.ceil(total / page_size) if page_size > 0 else 0
            start = (page - 1) * page_size
            end = start + page_size
            
            return {
                'result': all_transactions[start:end],  # Frontend expects 'result' not 'results'
                'total': total,
                'total_pages': total_pages,
                'page': page,
                'page_size': page_size
            }
            
        except Exception as e:
            logger.error(f"Error getting transactions by day {day}: {e}")
            return {'result': [], 'total': 0, 'total_pages': 0, 'page': page, 'page_size': page_size}
    
    def get_transaction_by_id(self, account_id: str, day: str, txn_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single transaction from KV by account_id, day, and txn_id.
        
        Args:
            account_id: The account ID (used in KV key)
            day: The day in YYYY-MM-DD format (used in KV key)
            txn_id: The transaction ID to find
            
        Returns:
            Transaction dict with full details, or None if not found
        """
        if not self.is_connected():
            return None
        
        try:
            # Construct the KV key: {account_id}:{day}
            record_key = f"{account_id}:{day}"
            key = (self.namespace, SET_TRANSACTIONS, record_key)
            
            _, _, bins = self.client.get(key)
            if not bins:
                return None
            
            txs_map = bins.get('txs', {})
            
            # Search for the transaction by txn_id in the map
            for ts, txn in txs_map.items():
                if txn.get('txn_id') == txn_id:
                    # Return formatted transaction with all details
                    return {
                        'txn_id': txn.get('txn_id', ''),
                        'account_id': account_id,
                        'day': day,
                        'amount': txn.get('amount', 0),
                        'type': txn.get('type', 'transfer'),
                        'method': txn.get('method', 'electronic'),
                        'location': txn.get('location', ''),
                        'timestamp': ts,
                        'status': txn.get('status', 'completed'),
                        'counterparty': txn.get('counterparty', ''),
                        'user_id': txn.get('user_id', ''),
                        'counterparty_user_id': txn.get('counterparty_user_id', ''),
                        'device_id': txn.get('device_id', ''),
                        'direction': txn.get('direction', 'out'),
                        'is_fraud': txn.get('is_fraud', False),
                        'fraud_score': txn.get('fraud_score', 0),
                    }
            
            return None
            
        except ex.RecordNotFound:
            return None
        except Exception as e:
            logger.error(f"Error getting transaction {txn_id} for account {account_id} on {day}: {e}")
            return None
    
    # ----------------------------------------------------------------------------------------------------------
    # Account-Fact Operations (Pre-computed features for ML)
    # ----------------------------------------------------------------------------------------------------------
    
    def get_account_fact(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Get computed features for an account."""
        return self.get(SET_ACCOUNT_FACT, account_id)
    
    def update_account_fact(self, account_id: str, features: Dict[str, Any]) -> bool:
        """
        Update computed features for an account.
        
        Args:
            account_id: The account ID
            features: Dict of computed features (uses short bin names internally)
        """
        features['account_id'] = account_id
        features['last_computed'] = datetime.now().isoformat()
        return self.put(SET_ACCOUNT_FACT, account_id, features)
    
    def batch_get_account_facts(self, account_ids: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Batch read account facts for multiple accounts.
        
        Args:
            account_ids: List of account IDs
            
        Returns:
            Dict mapping account_id to features (or None if not found)
        """
        result = {aid: None for aid in account_ids}
        
        if not self.is_connected() or not account_ids:
            return result
        
        try:
            keys = [(self.namespace, SET_ACCOUNT_FACT, aid) for aid in account_ids]
            records = self.batch_get(keys)
            
            for i, record in enumerate(records):
                if record and record[2]:
                    result[account_ids[i]] = self._expand_bin_names(record[2])
            
            return result
            
        except Exception as e:
            logger.error(f"Error batch getting account facts: {e}")
            return result
    
    def get_all_account_facts(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Get all account facts."""
        return self.scan_all(SET_ACCOUNT_FACT, limit)
    
    # ----------------------------------------------------------------------------------------------------------
    # Device-Fact Operations (Pre-computed features for device flagging)
    # ----------------------------------------------------------------------------------------------------------
    
    def get_device_fact(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get computed features for a device."""
        return self.get(SET_DEVICE_FACT, device_id)
    
    def update_device_fact(self, device_id: str, features: Dict[str, Any]) -> bool:
        """
        Update computed features for a device.
        
        Args:
            device_id: The device ID
            features: Dict of computed features
        """
        features['device_id'] = device_id
        features['last_computed'] = datetime.now().isoformat()
        return self.put(SET_DEVICE_FACT, device_id, features)
    
    def batch_get_device_facts(self, device_ids: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Batch read device facts for multiple devices.
        
        Args:
            device_ids: List of device IDs
            
        Returns:
            Dict mapping device_id to features (or None if not found)
        """
        result = {did: None for did in device_ids}
        
        if not self.is_connected() or not device_ids:
            return result
        
        try:
            keys = [(self.namespace, SET_DEVICE_FACT, did) for did in device_ids]
            records = self.batch_get(keys)
            
            for i, record in enumerate(records):
                if record and record[2]:
                    result[device_ids[i]] = self._expand_bin_names(record[2])
            
            return result
            
        except Exception as e:
            logger.error(f"Error batch getting device facts: {e}")
            return result
    
    def get_all_device_facts(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Get all device facts."""
        return self.scan_all(SET_DEVICE_FACT, limit)
    
    # ----------------------------------------------------------------------------------------------------------
    # Statistics
    # ----------------------------------------------------------------------------------------------------------

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """
        Get dashboard statistics entirely from KV store.
        Returns: users, txns, flagged, amount, fraud_rate, health
        """
        if not self.is_connected():
            return {
                "users": 0, "txns": 0, "flagged": 0,
                "amount": 0.0, "fraud_rate": 0.0, "health": "disconnected"
            }

        try:
            # Count users
            total_users = 0
            scan_users = self.client.scan(self.namespace, SET_USERS)
            def count_users(record):
                nonlocal total_users
                if record and len(record) > 2 and record[2]:
                    total_users += 1
            scan_users.foreach(count_users)

            # Scan transactions for totals
            total_txns = 0
            total_flagged = 0
            total_amount = 0.0

            scan_txns = self.client.scan(self.namespace, SET_TRANSACTIONS)
            def process_txn(record):
                nonlocal total_txns, total_flagged, total_amount
                if record and len(record) > 2 and record[2]:
                    bins = record[2]
                    txs_map = bins.get('txs', {})
                    for ts, txn in txs_map.items():
                        # Only count outgoing to avoid double counting
                        if txn.get('direction') != 'out':
                            continue
                        total_txns += 1
                        total_amount += float(txn.get('amount', 0) or 0)
                        if txn.get('is_fraud'):
                            total_flagged += 1

            scan_txns.foreach(process_txn)

            fraud_rate = (total_flagged / total_txns * 100) if total_txns > 0 else 0.0

            return {
                "users": total_users,
                "txns": total_txns,
                "flagged": total_flagged,
                "amount": round(total_amount, 2),
                "fraud_rate": round(fraud_rate, 2),
                "health": "connected"
            }
        except Exception as e:
            logger.error(f"Error getting dashboard stats from KV: {e}")
            return {
                "users": 0, "txns": 0, "flagged": 0,
                "amount": 0.0, "fraud_rate": 0.0, "health": "error"
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about stored data.
        Optimized: single scan of users for workflow status counts,
        and lightweight set-count queries for other sets.
        """
        if not self.is_connected():
            return {
                "users_count": 0, "flagged_accounts_count": 0,
                "account_facts_count": 0, "device_facts_count": 0,
                "transaction_records_count": 0,
                "pending_review": 0, "under_investigation": 0,
                "confirmed_fraud": 0, "cleared": 0,
                "connected": False
            }

        # Single scan of users to count workflow statuses
        users_count = 0
        pending_review = 0
        under_investigation = 0
        confirmed_fraud = 0
        cleared = 0

        try:
            scan = self.client.scan(self.namespace, SET_USERS)
            def count_user(record):
                nonlocal users_count, pending_review, under_investigation, confirmed_fraud, cleared
                if record and len(record) > 2 and record[2]:
                    users_count += 1
                    wf = record[2].get('wf_status') or record[2].get('workflow_status', '')
                    if wf == 'pending_review':
                        pending_review += 1
                    elif wf == 'under_investigation':
                        under_investigation += 1
                    elif wf == 'confirmed_fraud':
                        confirmed_fraud += 1
                    elif wf == 'cleared':
                        cleared += 1
            scan.foreach(count_user)
        except Exception as e:
            logger.error(f"Error scanning users for stats: {e}")

        # Count other sets with lightweight scans (count only, don't load full records)
        def _count_set(set_name: str) -> int:
            count = 0
            try:
                s = self.client.scan(self.namespace, set_name)
                def inc(record):
                    nonlocal count
                    count += 1
                s.foreach(inc)
            except Exception:
                pass
            return count

        return {
            "users_count": users_count,
            "flagged_accounts_count": _count_set(SET_FLAGGED_ACCOUNTS),
            "account_facts_count": _count_set(SET_ACCOUNT_FACT),
            "device_facts_count": _count_set(SET_DEVICE_FACT),
            "transaction_records_count": _count_set(SET_TRANSACTIONS),
            "pending_review": pending_review,
            "under_investigation": under_investigation,
            "confirmed_fraud": confirmed_fraud,
            "cleared": cleared,
            "connected": True
        }
    
    def truncate_all_data(self) -> Dict[str, bool]:
        """Truncate all data sets (for fresh bulk load)."""
        return {
            "users": self.truncate_set(SET_USERS),
            "flagged_accounts": self.truncate_set(SET_FLAGGED_ACCOUNTS),
            "account_facts": self.truncate_set(SET_ACCOUNT_FACT),
            "device_facts": self.truncate_set(SET_DEVICE_FACT),
            "transactions": self.truncate_set(SET_TRANSACTIONS),
            "evaluations": self.truncate_set(SET_EVALUATIONS),
            "history": self.truncate_set(SET_HISTORY),
            "investigations": self.truncate_set(SET_INVESTIGATIONS),
            # LangGraph checkpoint sets (INVESTIGATION_ENGINE=langgraph)
            "lg_checkpoints": self.truncate_set("lg_cp"),
            "lg_checkpoint_writes": self.truncate_set("lg_cp_w"),
            "lg_checkpoint_meta": self.truncate_set("lg_cp_meta"),
            # Cross-engine case memory (ADK + LangGraph via case_memory.py)
            "case_memory": self.truncate_set(SET_CASE_MEMORY),
            # Google ADK session/artifact sets (INVESTIGATION_ENGINE=adk only)
            "adk_sessions": self.truncate_set("adk_sessions"),
            "adk_app_state": self.truncate_set("adk_app_state"),
            "adk_user_state": self.truncate_set("adk_user_state"),
            "adk_artifacts": self.truncate_set("adk_artifacts"),
        }
    
    # ----------------------------------------------------------------------------------------------------------
    # Investigation Operations
    # ----------------------------------------------------------------------------------------------------------
    
    def put_investigation(self, investigation_id: str, data: Dict[str, Any]) -> bool:
        """
        Store a completed investigation result.
        
        Args:
            investigation_id: Unique investigation ID
            data: Investigation data including:
                - user_id: The user that was investigated
                - completed_at: Timestamp of completion
                - initial_evidence: Evidence collected
                - final_assessment: AI assessment result
                - tool_calls: Tools called during investigation
                - report_markdown: Generated report
        """
        if not self.is_connected():
            return False
        
        try:
            # Add metadata
            data["investigation_id"] = investigation_id
            data["stored_at"] = datetime.now().isoformat()
            
            return self.put(SET_INVESTIGATIONS, investigation_id, data)
        except Exception as e:
            logger.error(f"Error storing investigation {investigation_id}: {e}")
            return False
    
    def get_investigation(self, investigation_id: str) -> Optional[Dict[str, Any]]:
        """Get an investigation by ID."""
        return self.get(SET_INVESTIGATIONS, investigation_id)
    
    def get_user_latest_investigation(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the most recent completed investigation for a user.
        
        Args:
            user_id: The user ID to find investigations for
            
        Returns:
            The most recent investigation or None
        """
        if not self.is_connected():
            return None
        
        try:
            # Scan all investigations and filter by user_id
            all_investigations = self.scan_all(SET_INVESTIGATIONS, limit=1000)
            
            # Filter by user_id and sort by completed_at
            user_investigations = [
                inv for inv in all_investigations 
                if inv.get("user_id") == user_id
            ]
            
            if not user_investigations:
                return None
            
            # Sort by completed_at descending (most recent first)
            user_investigations.sort(
                key=lambda x: x.get("completed_at", ""),
                reverse=True
            )
            
            return user_investigations[0]
            
        except Exception as e:
            logger.error(f"Error getting latest investigation for user {user_id}: {e}")
            return None
    
    def get_user_investigation_history(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get investigation history for a user.
        
        Args:
            user_id: The user ID
            limit: Maximum number of investigations to return
            
        Returns:
            List of investigations sorted by date (newest first)
        """
        if not self.is_connected():
            return []
        
        try:
            all_investigations = self.scan_all(SET_INVESTIGATIONS, limit=1000)
            
            user_investigations = [
                {
                    "investigation_id": inv.get("investigation_id"),
                    "completed_at": inv.get("completed_at"),
                    "risk_score": inv.get("final_assessment", {}).get("risk_score"),
                    "risk_level": inv.get("final_assessment", {}).get("risk_level"),
                    "typology": inv.get("final_assessment", {}).get("typology"),
                }
                for inv in all_investigations 
                if inv.get("user_id") == user_id
            ]
            
            user_investigations.sort(
                key=lambda x: x.get("completed_at", ""),
                reverse=True
            )
            
            return user_investigations[:limit]
            
        except Exception as e:
            logger.error(f"Error getting investigation history for user {user_id}: {e}")
            return []


# Singleton instance
aerospike_service = AerospikeService()
