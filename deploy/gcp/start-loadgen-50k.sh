#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
# shellcheck source=/dev/null
source "${DIR}/loadgen.env"

pkill -f 'java.*fraud-loadgen.jar' 2>/dev/null || true
sleep 2

export FRAUD_RATIO="${FRAUD_RATIO:-0.000002}"  # 1/500k txns fraudulent (~1 flag per 10s at 50k tps)

exec nohup "${DIR}/run-loadgen.sh" paired 50000 3600 64 0 > "${DIR}/loadgen.log" 2>&1 &
