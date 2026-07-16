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
  --balances
)
# Optional override when account ids are not Account1..N (default matches bulk-load CSVs).
if [[ -n "${ACCOUNTS_FILE:-}" && -f "${ACCOUNTS_FILE}" ]]; then
  ARGS+=(--accounts-file "${ACCOUNTS_FILE}")
fi

exec java -jar "${DIR}/fraud-loadgen.jar" "${ARGS[@]}"
