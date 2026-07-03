#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "${DIR}/loadgen.env"

MODE="${1:-paired}"
RATE="${2:-0}"
DURATION="${3:-60}"
WORKERS="${4:-16}"
ACCOUNTS="${5:-20000}"

# Optional fraud injection (env-driven; defaults off). When MULES/FRAUDSTERS > 0
# the loadgen marks a fraction of txns as fraud (FRAUD_RATIO), and after the run
# writes flagged_accounts + the fraud_feed update queue so the UI can populate.
MULES="${MULES:-0}"
FRAUDSTERS="${FRAUDSTERS:-0}"
FRAUD_RATIO="${FRAUD_RATIO:-0.0}"
ACCOUNT_PREFIX="${ACCOUNT_PREFIX:-Account}"
USER_PREFIX="${USER_PREFIX:-User}"

exec java -jar "${DIR}/fraud-loadgen.jar" \
  --mode "${MODE}" \
  --host "${AS_HOST}" \
  --port "${AS_PORT}" \
  --namespace "${AS_NAMESPACE}" \
  --graph-host "${GRAPH_HOST}" \
  --graph-port "${GRAPH_PORT}" \
  --accounts-file "${ACCOUNTS_FILE}" \
  --accounts "${ACCOUNTS}" \
  --workers "${WORKERS}" \
  --duration "${DURATION}" \
  --rate "${RATE}" \
  --mules "${MULES}" \
  --fraudsters "${FRAUDSTERS}" \
  --fraud-ratio "${FRAUD_RATIO}" \
  --account-prefix "${ACCOUNT_PREFIX}" \
  --user-prefix "${USER_PREFIX}" \
  --balances
