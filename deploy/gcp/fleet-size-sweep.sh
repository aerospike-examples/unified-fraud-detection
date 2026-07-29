#!/usr/bin/env bash
# Sweep fleet size (number of loadgen/graph pairs), holding per-instance settings
# constant. Stops the full fleet before each stage so only the target count is
# active — `start N` alone does not stop instances N+1..max.
#
# Usage:
#   ./fleet-size-sweep.sh [max_fleet] [duration_sec]
#
# Example (default: sweep 10,20,25,30,33,36,38,40 @ 300s each):
#   FRAUD_RATIO=0.0000001 ./fleet-size-sweep.sh 40 300
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
MAX_FLEET="${1:-40}"
DURATION="${2:-300}"
FRAUD_RATIO="${FRAUD_RATIO:-0.0000001}"
SKIP_BUILD="${SKIP_BUILD:-1}"

# Fleet sizes to test (must be <= MAX_FLEET).
SIZES=(10 20 25 30 33 36 38 40)

LOG="/tmp/fleet_size_sweep_$(date +%Y%m%d_%H%M%S).log"
echo "Fleet size sweep: sizes=${SIZES[*]} duration=${DURATION}s fraud_ratio=${FRAUD_RATIO}" | tee "$LOG"
echo "Log: $LOG" | tee -a "$LOG"

# After start, confirm java is actually running on every instance in the fleet.
verify_fleet_running() {
  local count="$1"
  local missing=() i name
  sleep 12
  for i in $(seq 1 "${count}"); do
    name="loadgen-amd-demo-${i}"
    if ! gcloud compute ssh --tunnel-through-iap --project="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}" \
        --zone=us-central1-a "${name}" --command "pgrep -x java >/dev/null" >/dev/null 2>&1; then
      missing+=("${name}")
    fi
  done
  if ((${#missing[@]} > 0)); then
    echo "ERROR: loadgen not running on: ${missing[*]}" | tee -a "$LOG"
    return 1
  fi
  echo "verified ${count}/${count} loadgen instance(s) running java" | tee -a "$LOG"
}

for count in "${SIZES[@]}"; do
  if (( count > MAX_FLEET )); then
    echo "skip count=${count} (> max_fleet=${MAX_FLEET})" | tee -a "$LOG"
    continue
  fi

  echo "" | tee -a "$LOG"
  echo "========== FLEET=${count}  DURATION=${DURATION}s  $(date -u) ==========" | tee -a "$LOG"

  # Ensure only `count` instances are generating load — stop any stragglers from
  # a prior larger stage before starting the smaller fleet.
  # Don't pipe through `tail` — with `set -o pipefail`, tail closing early sends
  # SIGPIPE upstream and aborts the whole sweep.
  "${DIR}/load_runner.sh" stop "${MAX_FLEET}" loadgen 2>&1 | tee -a "$LOG"

  FRAUD_RATIO="${FRAUD_RATIO}" SKIP_BUILD="${SKIP_BUILD}" \
    "${DIR}/load_runner.sh" start "${count}" paired 0 "${DURATION}" 64 0 \
    2>&1 | tee -a "$LOG"

  verify_fleet_running "${count}" || {
    echo "retrying start for fleet=${count}..." | tee -a "$LOG"
    FRAUD_RATIO="${FRAUD_RATIO}" SKIP_BUILD="${SKIP_BUILD}" \
      "${DIR}/load_runner.sh" start "${count}" paired 0 "${DURATION}" 64 0 \
      2>&1 | tee -a "$LOG"
    verify_fleet_running "${count}" || {
      echo "FATAL: fleet=${count} failed verification after retry — aborting sweep" | tee -a "$LOG"
      exit 1
    }
  }
  wait_sec=$((DURATION + 15))
  echo "waiting ${wait_sec}s for run to finish..." | tee -a "$LOG"
  sleep "${wait_sec}"

  echo "--- report (fleet=${count}) ---" | tee -a "$LOG"
  if ! "${DIR}/load_runner.sh" report "${count}" 2>&1 | tee -a "$LOG"; then
    echo "WARN: report failed for fleet=${count}, continuing sweep" | tee -a "$LOG"
  fi
done

echo "" | tee -a "$LOG"
echo "Fleet size sweep complete at $(date -u)" | tee -a "$LOG"
echo "LOG=$LOG"
