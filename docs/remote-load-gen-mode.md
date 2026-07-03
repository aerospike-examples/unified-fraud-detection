# Remote Load-Generation Mode

The app supports two data-source modes, selected by the backend env var
`DATA_SOURCE_MODE`:

- `local` (default): users/transactions live in Aerospike KV, entity counts are
  computed by KV scans, and the Admin panel can bulk load, inject transactions,
  compute features, run detection, and clear data.
- `remote`: the dataset is generated and bulk loaded **externally** into the
  Aerospike Graph at billion scale. KV holds only a small working set. Counts
  come from the Graph summary API and app-side ingest/detection are disabled.

Set it in `deploy/gcp/demo.env` / `docker-compose.demo.yml`.

## What changes in remote mode

- `GET /config` reports `mode: "remote"` and capability flags. The frontend
  reads this and hides the Admin write operations (bulk load, inject, compute
  features, run detection, clear) and the RT Transaction Generation tab.
- Dashboard / users / transactions / aerospike stats are served from the Graph
  summary API (`aerospike.graph.admin.metadata.summary`), cached ~30s. Counts:
  - `users`  = vertex count for label `user`
  - `accounts` = vertex count for label `account`
  - `devices` = vertex count for label `device`
  - `txns`   = edge count for label `TRANSACTS` (one edge per transaction)
- `/users`, `/users/{id}`, `/transactions` are backed by bounded Gremlin
  traversals instead of KV scans.
- The review queue (`/flagged-accounts*`) reads the small KV `flagged_accounts`
  set. Investigation reads per-user KV working-set records; on a KV miss it
  **lazily hydrates** them from the Graph (see below) before proceeding.
- The investigation LLM agent's tools are mode-aware: `get_account_transactions`,
  `get_counterparty_profile`, and `get_counterparty_transactions` read from the
  Graph in remote mode (there are no KV transactions and counterparties aren't
  hydrated). `detect_fraud_ring` / `get_transaction_network` are graph-native in
  both modes. Feature tools accept both short and long fact field names.
- The mutating endpoints return HTTP 409 if called in remote mode.

## Lazy KV hydration (read-through cache)

The only KV set the external pipeline *must* write is `flagged_accounts`.
Everything else the investigation needs from KV — the `users` record (profile +
nested account/device maps) and the per-account `account_fact` / per-device
`device_fact` features — is materialized on demand the first time an analyst
opens/investigates a flagged user.

This happens server-side in the investigation `data_collection` node
(`_ensure_remote_hydration` → `services/hydration_service.py`):

1. If a KV `users` record already exists, it's a no-op.
2. Otherwise the user profile, accounts, and devices are read from the Graph via
   bounded traversals and written to KV.
3. For each account (capped at `max_accounts`), an `account_fact` is computed
   from a bounded traversal of its `TRANSACTS` edges (capped at
   `max_edges_per_account` so supernodes stay fast), scored with the same ML
   model used in local mode, and persisted. Device facts are derived similarly.

Because the bulk-loaded graph is static, these cached records never go stale.
Time-window features (24h peak, z-scores, new-recipient ratio, first-txn delay)
are left at neutral defaults since they aren't cheaply derivable from a bounded
traversal; the derivable signals (counts, amounts, unique recipients, recipient
entropy, device count, account age) are populated and drive scoring.

## Seeding the review queue (important)

The bulk-load CSVs from `scripts/generate-ags-csv.py` are **clean by default**:
every user's `risk_score` is `0.0` and every account/device `fraud_flag` is
`False`, with random transaction recipients. With no fraud signal and detection
disabled, the review queue is empty and there is nothing to investigate. Choose
one of these to give the demo signal:

1. **Generate with a fraud signal** — pass `--fraud-rate` to the generator:

   ```bash
   python scripts/generate-ags-csv.py --users 1000000000 --txns-per-user 2 \
       --fraud-rate 0.0005 --out_dir ./data/graph_csv
   ```

   A fraction of users get a high `risk_score`, `fraud_flag=True` accounts/devices,
   newer accounts, and higher-velocity/higher-value fan-out transactions baked
   into the CSVs. Note this only sets graph properties; you still need
   `flagged_accounts` KV records (option 2) to populate the queue itself.

2. **Seed after load** — run the seeder against the loaded graph. It injects
   signal (and optionally rings) for a bounded set of users and writes the KV
   `flagged_accounts` records that back the queue:

   ```bash
   # Flag 100 users, wire rings, inject high-velocity fan-out, warm KV cache
   python scripts/seed-remote-flags.py --count 100 --ring --velocity --hydrate
   # Or flag specific users
   python scripts/seed-remote-flags.py --users User10,User11,User12
   ```

   The seeder is fully bounded per user (no full-graph scans), safe on a
   billion-vertex graph, and reuses the app's own graph/KV services and ML model.
   `--velocity` adds high-value fan-out `TRANSACTS` edges so the *computed*
   account features look genuinely fraudulent (not just flagged), making the
   investigation internally coherent. `--ring` adds shared-device + inter-account
   edges so `detect_fraud_ring` lights up.

3. **Inject via the load-gen** — the Java load-gen (`loadgen/`) can designate a
   fraud cohort and, as it drives live traffic, emit fraudulent transactions
   *and* populate the queue. This is the closest match to a real deployment: the
   fraud arrives as part of the transaction stream, not a batch job.

   ```bash
   # 200 mules (fan-in) + 200 fraudsters (fan-out), ~2% of txns fraudulent
   MULES=200 FRAUDSTERS=200 FRAUD_RATIO=0.02 \
     deploy/gcp/run-loadgen.sh paired 0 120 16 500000
   ```

   - **Cohort sizing is by count** (`--mules`, `--fraudsters`), taken as the first
     N accounts from the accounts file.
   - **Patterns are realistic**: mules receive concentrated fan-in; fraudsters
     originate high-value fan-out/bursts. Fraud edges carry `is_fraud=true` +
     `fraud_score` (surfaced in the transaction list and graph-backed tools).
   - **Account → user mapping is deterministic with a graph fallback**:
     `Account{n}` → `User{n}` (configurable via `--account-prefix`/`--user-prefix`),
     falling back to `in('OWNS')` on the graph for non-matching id schemes.
   - After the run it writes one `flagged_accounts` record per cohort account
     (KV, keyed by `user_id`) using the same shortened bin names the backend
     expects, so no format translation is needed.

For a real deployment the external load-gen pipeline should perform the
equivalent of option 2 or 3 (write `flagged_accounts` + `fraud_feed`), optionally
after injecting fraud structure like option 1.

## Fraud update queue — `fraud_feed` set (how the UI finds new fraud)

After injecting a cohort, the load-gen also writes a single-record **update
queue** so the frontend can discover freshly-flagged accounts *without* scanning
the billion-row dataset. It is a single record in the `fraud_feed` set (key
`fraud_feed`):

```json
{
  "run_id": "2026-07-03T19:40:00Z",   // changes every injection run
  "run_started": "2026-07-03T19:40:00Z",
  "last_updated": "2026-07-03T19:40:07Z",
  "total": 400,                          // accounts flagged this run
  "recent": [                            // capped preview (newest first on read)
    {"user_id": "User12", "account_id": "Account12", "risk_score": 88.1,
     "reason": "Rapid fan-out of high-value transfers", "ts": "..."}
  ]
}
```

The backend exposes it at `GET /flagged-accounts/updates`
(`aerospike_service.get_fraud_feed()`). The Flagged Accounts page polls this
every 15s; when `run_id`/`total` change it shows a "new fraud detected" banner
and revalidates the queue + stats. The `total` counter increments per flagged
account; only the first 100 entries are kept in `recent` to keep the record
small for large cohorts.

## What the external pipeline is responsible for

The bulk graph load handles vertices/edges. Beyond that, only the review queue
is required — the rest of the KV working set is hydrated lazily (above). All keys
are in namespace `test`.

### 1. Review queue — `flagged_accounts` set (required)

Key = `user_id`. One record per flagged user:

```json
{
  "account_id": "Account123",       // highest-risk account (or user_id)
  "user_id": "User123",
  "account_holder": "Jane Doe",
  "risk_score": 82.5,
  "status": "pending_review",        // pending_review | under_investigation | confirmed_fraud | cleared
  "flag_reason": "High velocity + shared device",
  "flagged_date": "2026-07-03T10:00:00Z",
  "total_flagged_amount": 45000.0,
  "account_predictions": [
    {"account_id": "Account123", "risk_score": 82.5}
  ]
}
```

### 2. Features + `users` records (optional — lazily hydrated otherwise)

The `account_fact` / `device_fact` feature records and the KV `users` profile
(nested `accounts` / `devices` maps) are hydrated from the Graph on first
investigation (see "Lazy KV hydration" above), so the pipeline **does not need
to write them**.

Pre-writing them is still supported and takes precedence (an existing KV `users`
record short-circuits hydration). Pre-write only if you want richer, fully
time-windowed features than the bounded on-demand computation produces, or to
avoid the one-time hydration latency on first open. Field shapes match
`feature_service` output (e.g. `txn_out_7d`, `txn_zscore`, `uniq_recip`,
`dev_count`, `acct_age_days`, plus `risk_score`).

### 3. Aggregate stats — `config` set, key `aggregate_stats` (optional)

Total amount and fraud rate cannot be derived cheaply from the graph summary.
Provide them here so the dashboard shows real values instead of blanks:

```json
{
  "total_amount": 1234567890.0,
  "fraud_rate": 3.2,
  "flagged": 10240,
  "txns": 3000000000,
  "blocked": 4000,
  "review": 6240,
  "clean": 2999989760
}
```

Any subset of fields is allowed; missing fields fall back to graph-derived
counts. Where a value is genuinely not derivable at scale (dashboard amount /
fraud rate, user risk buckets, txn disposition breakdown), the UI renders "—"
(unknown) rather than a misleading `0`/`$0`.

**The Java load-gen writes this record automatically.** While it runs it refreshes
`config:aggregate_stats` every 5s with the live `total_amount` and `fraud_rate`
of the traffic it is generating (it never writes `txns`/`users`, so the
graph-summary counts stay authoritative). So if you drive traffic with the
load-gen during the demo, the dashboard's Total Amount and Fraud Detection Rate
show real, moving numbers with no manual seeding.

## Notes

- Detection is not scheduled in remote mode (the startup scheduler skips it).
- Transaction edge label is `TRANSACTS`; vertex labels are lowercase
  `user` / `account` / `device` (see `scripts/generate-ags-csv.py`).
- The `/transactions` list uses an unordered bounded `range()` over TRANSACTS
  edges (a global order-by-timestamp over billions of edges is not feasible).
