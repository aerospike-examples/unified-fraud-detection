#!/usr/bin/env bash
# Manages a scale-out fleet of paired Aerospike Graph Service + load-gen VMs,
# so throughput can be multiplied by running N independent (graph, loadgen)
# pairs instead of hand-managing single boxes like demo-amd-graph /
# demo-amd-load-gen-1.
#
# Usage:
#   ./load_runner.sh create  <count>                      # idempotent: tops up an existing fleet to <count> pairs
#   ./load_runner.sh destroy <count>
#   ./load_runner.sh start   <count> [loadgen-args...]   # forwarded to run-loadgen.sh
#   ./load_runner.sh stop    <count> [loadgen|graph|all]  # default: all
#   ./load_runner.sh status  <count>
#   ./load_runner.sh report  <count>                      # achieved txn/s across the fleet
#
# `stop` never powers off or deletes the VMs (only `destroy` does that) — it
# just kills the loadgen java process and/or stops the `ags` graph container,
# so a stopped fleet's VMs (and, unless you asked to stop it, the graph
# service) stay up and billing/warm for a quick restart.
#
# Examples:
#   ./load_runner.sh create 10
#   ./load_runner.sh start 10 paired 50000 3600 64 0
#   MULES=200 FRAUDSTERS=200 FRAUD_RATIO=0.02 ./load_runner.sh start 10 paired 0 1800 64 500000
#   RING_POOL_SIZE=12 RING_RATIO=0.4 FRAUD_RATIO=0.02 ./load_runner.sh start 10 paired 0 1800 64 500000
#   KV_MODEL=flat BALANCES=false ./load_runner.sh start 20 kv 0 300 64 0   # max Aerospike throughput
#   ./load_runner.sh stop 10                 # stop loadgen + graph (original behavior)
#   ./load_runner.sh stop 10 loadgen         # stop just the loadgen processes
#   ./load_runner.sh report 10               # what tps is the fleet actually achieving?
#   ./load_runner.sh destroy 10
#
# VMs are named deterministically: graph-demo-amd-<i> / loadgen-amd-demo-<i>
# for i in 1..count, placed in the same zone as the Aerospike KV cluster
# (auto-discovered), on the same "default" network/subnet as demo-amd-graph
# and demo-app (no external IP; SSH via IAP tunnel, same as the rest of this
# deploy/gcp/ tooling).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${DIR}/../.." && pwd)"

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
MACHINE_TYPE="${MACHINE_TYPE:-n2d-standard-32}"
IMAGE_FAMILY="${IMAGE_FAMILY:-debian-13}"
IMAGE_PROJECT="${IMAGE_PROJECT:-debian-cloud}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-100GB}"
NETWORK="${NETWORK:-default}"
SUBNET="${SUBNET:-default}"
FLEET_LABEL="${FLEET_LABEL:-amd-loadtest}"

# Aerospike cluster this fleet talks to (aerolab4-managed nodes), identified
# by the aerolab4 cluster-name label so it survives node count changes.
AEROSPIKE_CLUSTER_NAME="${AEROSPIKE_CLUSTER_NAME:-demo-amd}"
AEROSPIKE_NAMESPACE="${AEROSPIKE_NAMESPACE:-test}"
AS_PORT="${AS_PORT:-3000}"
GRAPH_PORT="${GRAPH_PORT:-8182}"

GRAPH_PREFIX="graph-demo-amd"
LOADGEN_PREFIX="loadgen-amd-demo"

SSH_FLAGS=(--tunnel-through-iap --project="${PROJECT}")
# Cap concurrent SSH/SCP fan-out — 40 parallel gcloud compute ssh calls race on
# ~/.ssh/google_compute_known_hosts and intermittently fail host-key verification.
MAX_PARALLEL="${MAX_PARALLEL:-8}"
# Seconds between successive instance launches. Each JVM opens its whole
# Aerospike connection pool up front, so launching the fleet in lockstep hits
# the cluster with one huge burst of connects; spacing them lets the ramp be
# absorbed. Set to 0 to launch as fast as MAX_PARALLEL allows.
START_STAGGER_SECS="${START_STAGGER_SECS:-2}"

log() { echo "[load_runner] $*" >&2; }
die() { echo "[load_runner] ERROR: $*" >&2; exit 1; }

# Wait until fewer than MAX_PARALLEL background jobs are running.
throttle_jobs() {
  while (( $(jobs -pr 2>/dev/null | wc -l) >= MAX_PARALLEL )); do
    wait -n 2>/dev/null || wait
  done
}

# Sequential SSH to each fleet VM so google_compute_known_hosts is fully
# populated before parallel start/stop/report — avoids "Host key verification
# failed" when many gcloud compute ssh calls write the same file at once.
warm_ssh_hosts() {
  local n="$1" kind="${2:-loadgen}"
  log "pre-warming SSH host keys for ${n} ${kind} instance(s)..."
  local i name
  for i in $(seq_1_to "${n}"); do
    if [[ "${kind}" == graph ]]; then name="$(graph_name "${i}")"
    else name="$(loadgen_name "${i}")"; fi
    gcloud compute ssh "${SSH_FLAGS[@]}" --zone="${FLEET_ZONE}" "${name}" \
      --command "true" >/dev/null 2>&1 \
      || log "[${name}] ssh warm-up failed (will retry on operation)"
  done
}

usage() {
  sed -n '2,33p' "$0"
  exit 1
}

require_count() {
  local n="${1:-}"
  [[ "${n}" =~ ^[0-9]+$ && "${n}" -gt 0 ]] || die "count must be a positive integer, got: '${n}'"
}

seq_1_to() { seq 1 "$1"; }

graph_name() { echo "${GRAPH_PREFIX}-$1"; }
loadgen_name() { echo "${LOADGEN_PREFIX}-$1"; }

# --- Aerospike cluster discovery -------------------------------------------

# Sets AS_ZONE and AS_SEED_IP from the running aerolab4 cluster nodes.
discover_aerospike() {
  local line
  line="$(gcloud compute instances list \
    --project="${PROJECT}" \
    --filter="labels.aerolab4cluster_name=${AEROSPIKE_CLUSTER_NAME} AND status=RUNNING" \
    --format="csv[no-heading](name,zone,networkInterfaces[0].networkIP)" \
    --sort-by=name | head -n1)"
  [[ -n "${line}" ]] || die "no running Aerospike cluster nodes found with label aerolab4cluster_name=${AEROSPIKE_CLUSTER_NAME}"
  AS_ZONE="$(echo "${line}" | cut -d, -f2)"
  AS_SEED_IP="$(echo "${line}" | cut -d, -f3)"
  log "Aerospike cluster '${AEROSPIKE_CLUSTER_NAME}': zone=${AS_ZONE} seed=${AS_SEED_IP}"
}

# Zone to operate the fleet in: prefer an already-created fleet VM's zone (so
# start/stop/destroy work even if the Aerospike cluster was later torn down),
# falling back to the Aerospike cluster's zone (used by create).
discover_fleet_zone() {
  local zone
  zone="$(gcloud compute instances list \
    --project="${PROJECT}" \
    --filter="name=(${GRAPH_PREFIX}-1 OR ${LOADGEN_PREFIX}-1)" \
    --format="value(zone)" | head -n1)"
  if [[ -n "${zone}" ]]; then
    FLEET_ZONE="${zone}"
  else
    discover_aerospike
    FLEET_ZONE="${AS_ZONE}"
  fi
  log "operating in zone=${FLEET_ZONE}"
}

# --- Startup scripts ---------------------------------------------------------
# Static (no local interpolation) — instance-specific values are read from
# the GCE metadata server at boot, so a single `instances create` call with
# N positional names can share one startup-script across the whole fleet.

GRAPH_STARTUP_SCRIPT='#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/graph-bootstrap.log) 2>&1
echo "[graph-bootstrap] starting"

meta() { curl -s -f -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
AS_SEED_IP="$(meta as-seed-ip)"
AEROSPIKE_NAMESPACE="$(meta aerospike-namespace)"

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

docker pull aerospike/aerospike-graph-service:latest

if docker ps --format "{{.Names}}" | grep -qx ags; then
  echo "[graph-bootstrap] ags already running"
elif docker ps -a --format "{{.Names}}" | grep -qx ags; then
  echo "[graph-bootstrap] starting existing ags container"
  docker start ags
else
  echo "[graph-bootstrap] creating ags container (seed=${AS_SEED_IP} ns=${AEROSPIKE_NAMESPACE})"
  docker run -d --name ags --restart unless-stopped \
    -p 8182:8182 \
    -e aerospike.client.host="${AS_SEED_IP}" \
    -e aerospike.client.namespace="${AEROSPIKE_NAMESPACE}" \
    aerospike/aerospike-graph-service:latest
fi
echo "[graph-bootstrap] done"
'

# Only installs OS packages — the per-user home directory doesn't exist yet
# at boot (gcloud/OS Login provisions it lazily on first SSH login), so
# ~/fraud-loadgen is created later, on-demand, by cmd_start over SSH.
LOADGEN_STARTUP_SCRIPT='#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/loadgen-bootstrap.log) 2>&1
echo "[loadgen-bootstrap] starting"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openjdk-21-jre-headless curl
echo "[loadgen-bootstrap] done"
'

# --- create ------------------------------------------------------------------

cmd_create() {
  local n="$1"
  require_count "${n}"
  discover_aerospike

  local all_graph_names=() all_loadgen_names=()
  for i in $(seq_1_to "${n}"); do
    all_graph_names+=("$(graph_name "${i}")")
    all_loadgen_names+=("$(loadgen_name "${i}")")
  done

  # Idempotent "ensure fleet size >= n": skip any name (from either role) that
  # already exists (any status), so `create 40` on top of an existing 10-pair
  # fleet only creates the missing 11..40 instead of erroring on 1..10.
  local existing; existing="$(gcloud compute instances list --project="${PROJECT}" \
    --filter="name=(${all_graph_names[*]// / OR } OR ${all_loadgen_names[*]// / OR })" \
    --format="value(name)")"

  local graph_names=() loadgen_names=()
  for name in "${all_graph_names[@]}"; do
    grep -qx "${name}" <<<"${existing}" && log "skip ${name} (already exists)" || graph_names+=("${name}")
  done
  for name in "${all_loadgen_names[@]}"; do
    grep -qx "${name}" <<<"${existing}" && log "skip ${name} (already exists)" || loadgen_names+=("${name}")
  done

  if [[ "${#graph_names[@]}" -eq 0 && "${#loadgen_names[@]}" -eq 0 ]]; then
    log "fleet already has ${n} pair(s), nothing to create"
    cmd_status
    return 0
  fi

  local tmpdir; tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' RETURN
  printf '%s' "${GRAPH_STARTUP_SCRIPT}" > "${tmpdir}/graph-startup.sh"
  printf '%s' "${LOADGEN_STARTUP_SCRIPT}" > "${tmpdir}/loadgen-startup.sh"

  local pids=() rc=0
  if [[ "${#graph_names[@]}" -gt 0 ]]; then
    log "creating ${#graph_names[@]} graph VM(s): ${graph_names[*]}"
    (
      gcloud compute instances create "${graph_names[@]}" \
        --project="${PROJECT}" --zone="${AS_ZONE}" \
        --machine-type="${MACHINE_TYPE}" \
        --image-family="${IMAGE_FAMILY}" --image-project="${IMAGE_PROJECT}" \
        --boot-disk-size="${BOOT_DISK_SIZE}" \
        --network="${NETWORK}" --subnet="${SUBNET}" --no-address \
        --labels="fleet=${FLEET_LABEL},role=graph" \
        --metadata="as-seed-ip=${AS_SEED_IP},aerospike-namespace=${AEROSPIKE_NAMESPACE}" \
        --metadata-from-file="startup-script=${tmpdir}/graph-startup.sh"
    ) & pids+=($!)
  fi
  if [[ "${#loadgen_names[@]}" -gt 0 ]]; then
    log "creating ${#loadgen_names[@]} loadgen VM(s): ${loadgen_names[*]}"
    (
      gcloud compute instances create "${loadgen_names[@]}" \
        --project="${PROJECT}" --zone="${AS_ZONE}" \
        --machine-type="${MACHINE_TYPE}" \
        --image-family="${IMAGE_FAMILY}" --image-project="${IMAGE_PROJECT}" \
        --boot-disk-size="${BOOT_DISK_SIZE}" \
        --network="${NETWORK}" --subnet="${SUBNET}" --no-address \
        --labels="fleet=${FLEET_LABEL},role=loadgen" \
        --metadata-from-file="startup-script=${tmpdir}/loadgen-startup.sh"
    ) & pids+=($!)
  fi

  for pid in "${pids[@]}"; do wait "${pid}" || rc=1; done
  [[ "${rc}" -eq 0 ]] || die "one or more instance-create calls failed (see above)"

  log "create complete. Graph services take ~1-2min to pull the image and come up."
  cmd_status
}

# --- destroy -----------------------------------------------------------------

cmd_destroy() {
  local n="$1"
  require_count "${n}"
  discover_fleet_zone

  local graph_names=() loadgen_names=()
  for i in $(seq_1_to "${n}"); do
    graph_names+=("$(graph_name "${i}")")
    loadgen_names+=("$(loadgen_name "${i}")")
  done

  log "deleting ${n} graph VM(s) and ${n} loadgen VM(s) in zone ${FLEET_ZONE}"
  local pids=() rc=0
  (
    gcloud compute instances delete "${graph_names[@]}" \
      --project="${PROJECT}" --zone="${FLEET_ZONE}" --quiet
  ) & pids+=($!)
  (
    gcloud compute instances delete "${loadgen_names[@]}" \
      --project="${PROJECT}" --zone="${FLEET_ZONE}" --quiet
  ) & pids+=($!)

  for pid in "${pids[@]}"; do wait "${pid}" || rc=1; done
  [[ "${rc}" -eq 0 ]] || die "one or more instance-delete calls failed (see above)"
  log "destroy complete"
}

# --- start ---------------------------------------------------------------

# Internal IP of a fleet VM by name.
instance_ip() {
  gcloud compute instances describe "$1" \
    --project="${PROJECT}" --zone="${FLEET_ZONE}" \
    --format="value(networkInterfaces[0].networkIP)"
}

# Retries an SSH command a few times — useful right after `create`, where the
# instance may still be finishing boot / OS Login user provisioning.
ssh_retry() {
  local name="$1" cmd="$2" tries=8
  local i
  for ((i = 1; i <= tries; i++)); do
    if gcloud compute ssh "${SSH_FLAGS[@]}" --zone="${FLEET_ZONE}" "${name}" --command "${cmd}"; then
      return 0
    fi
    log "[${name}] ssh not ready yet (attempt ${i}/${tries}), retrying in 10s..."
    sleep 10
  done
  return 1
}

cmd_start() {
  local n="$1"; shift
  require_count "${n}"
  local loadgen_args=("$@")
  [[ "${#loadgen_args[@]}" -gt 0 ]] || loadgen_args=(paired 50000 3600 64 0)

  discover_fleet_zone
  discover_aerospike

  if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
    log "building loadgen jar (mvn package; set SKIP_BUILD=1 to reuse the existing jar)"
    (cd "${REPO_ROOT}/loadgen" && mvn -q -DskipTests package)
  fi
  local jar="${REPO_ROOT}/loadgen/target/fraud-loadgen.jar"
  [[ -f "${jar}" ]] || die "jar not found at ${jar} (build failed?)"

  log "starting ${n} loadgen instance(s) against their paired graph node, Aerospike seed=${AS_SEED_IP}"
  log "loadgen args: ${loadgen_args[*]} (max_parallel=${MAX_PARALLEL})"

  warm_ssh_hosts "${n}" loadgen

  local tmpdir; tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' RETURN

  local pids=() rc=0
  for i in $(seq_1_to "${n}"); do
    throttle_jobs
    (
      local lg="$(loadgen_name "${i}")"
      local gr="$(graph_name "${i}")"
      local graph_ip; graph_ip="$(instance_ip "${gr}")"
      [[ -n "${graph_ip}" ]] || { log "could not resolve IP for ${gr}, skipping ${lg}"; exit 1; }

      local envfile="${tmpdir}/loadgen.env.${i}"
      cat > "${envfile}" <<ENVEOF
AS_HOST=${AS_SEED_IP}
AS_PORT=${AS_PORT}
AS_NAMESPACE=${AEROSPIKE_NAMESPACE}
GRAPH_HOST=${graph_ip}
GRAPH_PORT=${GRAPH_PORT}
ENVEOF

      log "[${lg}] waiting for SSH + preparing ~/fraud-loadgen"
      ssh_retry "${lg}" "mkdir -p ~/fraud-loadgen" || { log "[${lg}] never became SSH-reachable"; exit 1; }

      log "[${lg}] pushing jar + config (graph=${gr} @ ${graph_ip})"
      gcloud compute scp "${SSH_FLAGS[@]}" --zone="${FLEET_ZONE}" \
        "${jar}" "${DIR}/run-loadgen.sh" "${envfile}" \
        "${lg}:~/fraud-loadgen/" >/dev/null

      # NB: match by process name ("java"), not `pkill -f <pattern>` — the
      # whole remote_cmd string (including any -f pattern) becomes this very
      # shell's own argv over SSH, so an -f pattern that appears elsewhere in
      # the command line (e.g. in "run-loadgen.sh" itself) matches the
      # invoking shell and kills the SSH session mid-script.
      local remote_cmd="cd ~/fraud-loadgen && mv loadgen.env.${i} loadgen.env && chmod +x run-loadgen.sh;"
      remote_cmd+=" pkill -x java 2>/dev/null || true; sleep 2;"
      [[ -n "${FRAUD_RATIO:-}" ]] && remote_cmd+=" export FRAUD_RATIO=${FRAUD_RATIO};"
      [[ -n "${MULES:-}" ]] && remote_cmd+=" export MULES=${MULES};"
      [[ -n "${FRAUDSTERS:-}" ]] && remote_cmd+=" export FRAUDSTERS=${FRAUDSTERS};"
      [[ -n "${RING_POOL_SIZE:-}" ]] && remote_cmd+=" export RING_POOL_SIZE=${RING_POOL_SIZE};"
      [[ -n "${RING_RATIO:-}" ]] && remote_cmd+=" export RING_RATIO=${RING_RATIO};"
      [[ -n "${ACCOUNT_PREFIX:-}" ]] && remote_cmd+=" export ACCOUNT_PREFIX=${ACCOUNT_PREFIX};"
      [[ -n "${USER_PREFIX:-}" ]] && remote_cmd+=" export USER_PREFIX=${USER_PREFIX};"
      [[ -n "${KV_MODEL:-}" ]] && remote_cmd+=" export KV_MODEL=${KV_MODEL};"
      [[ -n "${KV_MAX_CONNS:-}" ]] && remote_cmd+=" export KV_MAX_CONNS=${KV_MAX_CONNS};"
      [[ -n "${BALANCES:-}" ]] && remote_cmd+=" export BALANCES=${BALANCES};"
      [[ -n "${KV_TTL:-}" ]] && remote_cmd+=" export KV_TTL=${KV_TTL};"
      [[ -n "${READ_RATIO:-}" ]] && remote_cmd+=" export READ_RATIO=${READ_RATIO};"
      remote_cmd+=" nohup ./run-loadgen.sh $(printf '%q ' "${loadgen_args[@]}") > loadgen.log 2>&1 < /dev/null & disown; sleep 1; echo started"

      log "[${lg}] launching loadgen"
      ssh_retry "${lg}" "${remote_cmd}" || { log "[${lg}] launch failed"; exit 1; }
    ) & pids+=($!)
    if [[ "${START_STAGGER_SECS}" != "0" ]]; then
      sleep "${START_STAGGER_SECS}"
    fi
  done

  for pid in "${pids[@]}"; do
    if ! wait "${pid}"; then
      rc=1
    fi
  done
  if [[ "${rc}" -ne 0 ]]; then
    die "one or more loadgen instances failed to start (see above)"
  fi
  log "start complete: ${n} loadgen instance(s) running"
}

# --- stop --------------------------------------------------------------------

cmd_stop() {
  local n="$1" target="${2:-all}"
  require_count "${n}"
  case "${target}" in
    loadgen|graph|all) ;;
    *) die "stop target must be 'loadgen', 'graph', or 'all' — got '${target}'" ;;
  esac
  discover_fleet_zone

  log "stopping (target=${target}) on ${n} pair(s) — VMs stay running either way (max_parallel=${MAX_PARALLEL})"
  if [[ "${target}" == "loadgen" || "${target}" == "all" ]]; then
    warm_ssh_hosts "${n}" loadgen
  fi
  if [[ "${target}" == "graph" || "${target}" == "all" ]]; then
    warm_ssh_hosts "${n}" graph
  fi
  local pids=() rc=0
  for i in $(seq_1_to "${n}"); do
    if [[ "${target}" == "loadgen" || "${target}" == "all" ]]; then
      throttle_jobs
      (
        local lg="$(loadgen_name "${i}")"
        gcloud compute ssh "${SSH_FLAGS[@]}" --zone="${FLEET_ZONE}" "${lg}" --command \
          "pkill -x java 2>/dev/null || true; echo stopped" \
          || log "[${lg}] stop failed (instance unreachable?)"
      ) & pids+=($!)
    fi
    if [[ "${target}" == "graph" || "${target}" == "all" ]]; then
      throttle_jobs
      (
        local gr="$(graph_name "${i}")"
        gcloud compute ssh "${SSH_FLAGS[@]}" --zone="${FLEET_ZONE}" "${gr}" --command \
          "sudo docker stop ags 2>/dev/null || true; echo stopped" \
          || log "[${gr}] stop failed (instance unreachable?)"
      ) & pids+=($!)
    fi
  done

  for pid in "${pids[@]}"; do wait "${pid}" || rc=1; done
  [[ "${rc}" -eq 0 ]] || log "some stop commands failed — check individual instances"
  log "stop complete"
}

# --- status --------------------------------------------------------------------

cmd_status() {
  # Count is accepted for interface symmetry with the other subcommands but
  # isn't required to filter — every VM this script creates is labeled, so
  # the whole fleet (however it was sized) always shows up.
  gcloud compute instances list --project="${PROJECT}" \
    --filter="labels.fleet=${FLEET_LABEL}" \
    --format="table(name,zone.basename(),machineType.basename(),status,networkInterfaces[0].networkIP,labels.role)" \
    --sort-by=name
}

# --- report --------------------------------------------------------------------

# Achieved throughput: Metrics.java logs "mode=... throughput=N txn/s
# total=N errors=N" once per second per instance — pull each instance's most
# recent line and sum, since target --rate is a request, not a guarantee.
cmd_report() {
  local n="$1"
  require_count "${n}"
  discover_fleet_zone

  log "sampling latest per-instance throughput from ${n} loadgen log(s) (max_parallel=${MAX_PARALLEL})..."
  warm_ssh_hosts "${n}" loadgen
  local tmpdir; tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' RETURN

  local pids=()
  for i in $(seq_1_to "${n}"); do
    throttle_jobs
    (
      local lg="$(loadgen_name "${i}")"
      local line
      # Instances are launched staggered, so a run's last sample can be its
      # final partial second while later instances are still at full speed.
      # Report the peak second alongside it so a skewed tail doesn't read as a
      # slow instance.
      line="$(gcloud compute ssh "${SSH_FLAGS[@]}" --zone="${FLEET_ZONE}" "${lg}" --command \
        'f=~/fraud-loadgen/loadgen.log; p=$(grep -oE "throughput=[0-9]+" "$f" 2>/dev/null | cut -d= -f2 | sort -rn | head -1); echo "peak_tps=${p:-0} $(grep "c.a.f.Metrics" "$f" 2>/dev/null | tail -1)"' 2>/dev/null)"
      echo "${lg} ${line}" > "${tmpdir}/${i}.out"
    ) & pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "${pid}" || true; done

  local total_tps=0 total_peak=0 total_txns=0 total_errors=0 up=0
  printf "%-22s %12s %12s %14s %10s\n" "INSTANCE" "TXN/S" "PEAK_TXN/S" "TOTAL_TXNS" "ERRORS"
  for i in $(seq_1_to "${n}"); do
    local out; out="$(cat "${tmpdir}/${i}.out" 2>/dev/null)"
    local lg; lg="$(echo "${out}" | awk '{print $1}')"
    local tps peak txns errs
    peak="$(echo "${out}" | grep -oE 'peak_tps=[0-9]+' | cut -d= -f2 || true)"
    tps="$(echo "${out}" | grep -oE 'throughput=[0-9]+' | cut -d= -f2 || true)"
    txns="$(echo "${out}" | grep -oE 'total=[0-9]+' | cut -d= -f2 || true)"
    errs="$(echo "${out}" | grep -oE 'errors=[0-9]+' | cut -d= -f2 || true)"
    if [[ -z "${tps}" ]]; then
      printf "%-22s %12s %12s %14s %10s\n" "${lg:-loadgen-amd-demo-${i}}" "no data" "-" "-" "-"
      continue
    fi
    printf "%-22s %12s %12s %14s %10s\n" "${lg}" "${tps}" "${peak:-0}" "${txns:-0}" "${errs:-0}"
    total_tps=$((total_tps + tps))
    total_peak=$((total_peak + ${peak:-0}))
    total_txns=$((total_txns + ${txns:-0}))
    total_errors=$((total_errors + ${errs:-0}))
    up=$((up + 1))
  done
  echo "---"
  log "fleet achieved: ${total_tps} txn/s (sum of peak seconds: ${total_peak}) across ${up}/${n} reporting instance(s), ${total_txns} total txns this run, ${total_errors} errors"
}

# --- main ----------------------------------------------------------------

[[ $# -ge 1 ]] || usage
COMMAND="$1"; shift || true
[[ $# -ge 1 ]] || usage

case "${COMMAND}" in
  create)  cmd_create "$@" ;;
  destroy) cmd_destroy "$@" ;;
  start)   cmd_start "$@" ;;
  stop)    cmd_stop "$@" ;;
  status)  cmd_status "$1" ;;
  report)  cmd_report "$1" ;;
  *) usage ;;
esac
