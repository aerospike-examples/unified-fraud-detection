#!/usr/bin/env bash
# Idempotently prepend the correct Aerospike Graph bulk-load header to every CSV
# part file in a graph_csv tree on GCS, using server-side `compose` (no data
# download / re-upload). Safe to re-run: a file whose first byte is already "~"
# is skipped.
#
# Root cause: the generator historically wrote headers only on shard 0
# (write_headers = worker_id == 0), so part_00001..part_NNNNN had no header and
# the loader read their first data row as the header.
#
# Usage:
#   ./fix-csv-headers.sh gs://unified-fraud-demo-data/1B/graph_csv [gs://.../10B/graph_csv ...]
#
# Env:
#   PARALLEL   number of concurrent workers per type (default 32)
#   BUCKET     bucket for the temporary header objects (default: unified-fraud-demo-data)
set -euo pipefail

PARALLEL="${PARALLEL:-32}"
BUCKET="${BUCKET:-unified-fraud-demo-data}"
HDR_PREFIX="gs://${BUCKET}/_hdrfix/headers"

# type-dir | file-glob-prefix | header line
SPECS=(
  "vertices/users|users_part_|~id,~label,name:String,email:String,phone:String,age:Int,location:String,occupation:String,risk_score:Double,signup_date:Date"
  "vertices/accounts|accounts_part_|~id,~label,type:String,balance:Double,bank_name:String,status:String,created_date:Date,fraud_flag:Boolean"
  "vertices/devices|devices_part_|~id,~label,type:String,os:String,browser:String,fingerprint:String,first_seen:Date,last_login:Date,login_count:Int,fraud_flag:Boolean"
  "edges/ownership|owns_part_|~from,~to,~label,since:Date"
  "edges/usage|uses_part_|~from,~to,~label,first_used:Date,last_used:Date,usage_count:Int"
  "edges/transactions|transacts_part_|~from,~to,~label,txn_id:String,amount:Double,currency:String,type:String,method:String,location:String,timestamp:Date,status:String,gen_type:String,device_id:String"
)

if [[ $# -lt 1 ]]; then
  echo "usage: $0 gs://bucket/prefix/graph_csv [more roots...]" >&2
  exit 2
fi

# --- 1. Create header objects once (CRLF terminated to match csv.writer) ---
echo "Creating header objects under ${HDR_PREFIX}/ ..."
tmpd="$(mktemp -d)"
trap 'rm -rf "$tmpd"' EXIT
declare -A HDR_OBJ
for spec in "${SPECS[@]}"; do
  IFS='|' read -r dir prefix header <<<"$spec"
  key="${dir//\//_}"
  printf '%s\r\n' "$header" > "${tmpd}/${key}.csv"
  gcloud storage cp "${tmpd}/${key}.csv" "${HDR_PREFIX}/${key}.csv" --quiet
  HDR_OBJ["$dir"]="${HDR_PREFIX}/${key}.csv"
done

# --- 2. Worker: prepend header in place unless first byte is already "~" ---
fix_one() {
  local f="$1" hdr="$2"
  local first
  first="$(gsutil cat -r 0-0 "$f" 2>/dev/null || true)"
  if [[ "$first" == "~" ]]; then
    echo "SKIP  $f"
    return 0
  fi
  if gcloud storage objects compose "$hdr" "$f" "$f" --quiet 2>/dev/null; then
    echo "FIXED $f"
  else
    echo "ERROR $f"
  fi
}
export -f fix_one

# --- 3. Walk each root / type ---
for root in "$@"; do
  echo "=================================================================="
  echo "ROOT: $root   ($(date -Is))"
  for spec in "${SPECS[@]}"; do
    IFS='|' read -r dir prefix header <<<"$spec"
    hdr="${HDR_OBJ[$dir]}"
    echo "--- ${dir} ---"
    gcloud storage ls "${root}/${dir}/${prefix}*.csv" 2>/dev/null \
      | xargs -r -P "$PARALLEL" -I {} bash -c 'fix_one "$1" "$2"' _ {} "$hdr"
  done
done

echo "All done: $(date -Is)"
