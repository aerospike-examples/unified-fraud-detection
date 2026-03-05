# Fraud Detection Data Model

This document defines the graph and KV data models used by the fraud detection application. The system uses **Aerospike Graph** (vertices and edges) and **Aerospike KV** (users, transactions, features); the Transaction list in the UI is served from KV.

---

## Aerospike Graph

### Vertices

Vertex IDs are stored in `~id` in the CSV bulk-load format (e.g. `U0000001`, `A00000011`, `DEV0000001`).

#### 1. User
- **Label:** `user`
- **Properties:**
  - `name` (string) - Full name
  - `email` (string) - Email address
  - `phone` (string) - Phone number
  - `age` (int) - Age
  - `location` (string) - User's location/city
  - `occupation` (string) - User's occupation (default: "Unknown")
  - `risk_score` (float) - Risk assessment score (default: 0.0)
  - `signup_date` (datetime) - Date when user signed up

#### 2. Account
- **Label:** `account`
- **Properties:**
  - `type` (string) - Account type (e.g. "checking", "savings", "credit")
  - `balance` (float) - Current account balance
  - `status` (string) - Account status (default: "active")
  - `bank_name` (string) - Name of the bank
  - `created_date` (datetime) - Date when account was created
  - `fraud_flag` (boolean) - Whether account is flagged as fraudulent (default: false)

#### 3. Device
- **Label:** `device`
- **Properties:**
  - `type` (string) - Device type
  - `os` (string), `browser` (string), `fingerprint` (string)
  - `first_seen` (datetime), `last_login` (datetime)
  - `login_count` (int)
  - `fraud_flag` (boolean) - Whether device is flagged (default: false)

---

### Edges

#### 1. OWNS
- **From:** `user`
- **To:** `account`
- **Properties:** `since` (datetime)

Represents ownership of an account by a user.

#### 2. USES
- **From:** `user`
- **To:** `device`
- **Properties:** `first_used` (datetime), `last_used` (datetime), `usage_count` (int)

Represents a user's use of a device.

#### 3. TRANSACTS
- **From:** `account` (sender)
- **To:** `account` (receiver)
- **Properties on the edge:**
  - `txn_id`, `amount`, `currency`, `type`, `method`, `location`, `timestamp`, `status`, `gen_type`, `device_id`
  - When fraud is detected: `is_fraud`, `fraud_score`, `fraud_status`, `eval_timestamp`, `details`

There is no transaction vertex; each transaction is a single **TRANSACTS** edge from sender account to receiver account with all transaction fields on the edge.

---

### Data flow and query patterns

**Transaction model (edge-based):**
- One **TRANSACTS** edge per transaction (sender account to receiver account).
- Transaction properties live on the edge.

**Example query patterns:**
- Outgoing transactions from an account: `V(account).outE("TRANSACTS")`
- Incoming transactions to an account: `V(account).inE("TRANSACTS")`
- All transactions (in or out): `V(account).bothE("TRANSACTS")`
- Traverse to the other account: `V(account).outE("TRANSACTS").inV()` or `.outV()` as appropriate

---

### RT1 fraud detection

The RT1 rule checks whether the sender or receiver account (or their connections) is linked to flagged accounts.

**Detection flow:**
1. A transaction is stored as a TRANSACTS edge (and in KV).
2. RT1 runs and checks for flagged-account connections.
3. If fraud is detected, the system updates the **TRANSACTS edge** with `is_fraud`, `fraud_score`, `fraud_status`, `eval_timestamp`, `details` and updates the **KV store** so the Transaction page and stats reflect the result.

The **Transaction list in the UI** is served from **Aerospike KV** (by day), not from the graph.

---

## Aerospike KV store

The application also uses Aerospike KV for users, transactions, and precomputed features. Namespace is `test` (configurable via environment).

### Sets

| Set | Record key | Purpose |
|-----|------------|--------|
| **users** | `user_id` | User profile plus nested maps `accounts` and `devices`; risk and workflow fields (e.g. `last_eval`, `eval_count`, `wf_status`). |
| **transactions** | `account_id:YYYY-MM-DD` | One record per account per day; bin `txs` is a map from timestamp to transaction entry. The **Transaction page** and stats read from this set (by day). |
| **account_fact** | `account_id` | Precomputed account features (e.g. txn counts, amounts, device exposure) for ML. |
| **device_fact** | `device_id` | Precomputed device features. |
| **flagged_accounts** | `user_id` | Flagged-account and investigation metadata. |
| **config** | e.g. `detection_config` | Configuration. |
| **detection_history** | `job_id` | Detection job results. |
| **investigations** | `investigation_id` | Completed investigation reports and evidence. |

### Bulk load (users)

When bulk load runs (e.g. from Admin UI with a locale), the generator produces CSV under `data/graph_csv/`. Data is loaded into:
- **Graph:** user, account, device vertices and OWNS, USES edges.
- **KV:** `users` set with one record per user; each record includes nested `accounts` and `devices` maps. Defaults include `risk_score` 0, account/device `is_fraud` false.

### Transactions in KV

Transactions are written to KV on manual creation, bulk generation, and bulk inject. Each transaction appears in two KV records (sender and receiver) under the key `account_id:YYYY-MM-DD` with direction `out` or `in`. The Transaction page and stats read from the **transactions** set by day. The graph holds **TRANSACTS** edges for traversal and fraud rules (RT1, RT2, RT3).

---

## Dual storage summary

The application uses both **Aerospike Graph** (vertices and TRANSACTS edges) and **Aerospike KV** (users, transactions, account_fact, device_fact, etc.). The **Transaction page** reads from KV; the graph is used for relationship traversal and fraud detection rules.
