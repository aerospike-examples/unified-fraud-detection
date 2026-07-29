#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "${DIR}/loadgen.env"

MODE="${1:-paired}"
RATE="${2:-0}"
DURATION="${3:-60}"
WORKERS="${4:-16}"
ACCOUNTS="${5:-0}"

# Live fraud: fraction of txns marked fraudulent; accounts flagged on detection (KV required).
FRAUD_RATIO="${FRAUD_RATIO:-0.0}"
ACCOUNT_PREFIX="${ACCOUNT_PREFIX:-Account}"
USER_PREFIX="${USER_PREFIX:-User}"
# Ring-pool bias: fraction of fraud txns drawn from a small rotating per-worker
# cohort instead of fully random accounts, so live fraud builds the same kind
# of dense/reciprocal structure detect_fraud_ring looks for (see
# TransactionGenerator.fraudTransaction). 0 ratio or pool-size disables it.
RING_POOL_SIZE="${RING_POOL_SIZE:-12}"
RING_RATIO="${RING_RATIO:-0.4}"

# KV layout: bucketed keeps the demo-readable account:hour CDT map; flat writes
# one constant-size record per txn for max-throughput runs.
KV_MODEL="${KV_MODEL:-bucketed}"
# Aerospike connections per node (0 = auto). The fleet shares the server's
# proto-fd-max with the always-on AGS instances, so this is a fleet-wide budget.
KV_MAX_CONNS="${KV_MAX_CONNS:-0}"
# Per-account balance increments double the KV ops per txn; off for pure
# throughput runs.
BALANCES="${BALANCES:-true}"
# The flat model writes a brand new record per txn, so an unbounded run fills
# the namespace and trips stop-writes. Expire benchmark data by default; the
# demo-readable bucketed model keeps the server default (no TTL).
# Fraction of KV ops that are reads of previously written records; 0.5 = 50/50.
READ_RATIO="${READ_RATIO:-0.0}"
if [[ "${KV_MODEL}" == "flat" ]]; then
  KV_TTL="${KV_TTL:-3600}"
else
  KV_TTL="${KV_TTL:-0}"
fi

ARGS=(
  --mode "${MODE}"
  --host "${AS_HOST}"
  --port "${AS_PORT}"
  --namespace "${AS_NAMESPACE}"
  --graph-host "${GRAPH_HOST}"
  --graph-port "${GRAPH_PORT}"
  --accounts "${ACCOUNTS}"
  --workers "${WORKERS}"
  --duration "${DURATION}"
  --rate "${RATE}"
  --fraud-ratio "${FRAUD_RATIO}"
  --account-prefix "${ACCOUNT_PREFIX}"
  --user-prefix "${USER_PREFIX}"
  --ring-pool-size "${RING_POOL_SIZE}"
  --ring-ratio "${RING_RATIO}"
  --kv-model "${KV_MODEL}"
  --kv-max-conns "${KV_MAX_CONNS}"
  --kv-ttl "${KV_TTL}"
  --read-ratio "${READ_RATIO}"
  --balances "${BALANCES}"
)
# Optional override when account ids are not Account1..N (default matches bulk-load CSVs).
if [[ -n "${ACCOUNTS_FILE:-}" && -f "${ACCOUNTS_FILE}" ]]; then
  ARGS+=(--accounts-file "${ACCOUNTS_FILE}")
fi

exec java -jar "${DIR}/fraud-loadgen.jar" "${ARGS[@]}"
