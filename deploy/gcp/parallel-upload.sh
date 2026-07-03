#!/usr/bin/env bash
# Highly parallel, resumable upload of graph_csv tree to GCS.
set -euo pipefail

SRC="${1:?source dir, e.g. /data/graph_csv}"
DEST="${2:?gcs prefix, e.g. gs://unified-fraud-demo-data/1B}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Installing google-cloud-cli..."
  sudo apt-get update -qq
  sudo apt-get install -y -qq apt-transport-https ca-certificates gnupg curl
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | \
    sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list >/dev/null
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  sudo apt-get update -qq
  sudo apt-get install -y -qq google-cloud-cli
fi

if [[ -f "${HOME}/sa-key.json" ]]; then
  gcloud auth activate-service-account test-bulk-loader@firefly-aerospike.iam.gserviceaccount.com \
    --key-file="${HOME}/sa-key.json" --quiet
fi

DEST_PATH="${DEST}/graph_csv"
LOG="/data/upload-$(echo "${DEST}" | tr '/' '-').log"

echo "Uploading ${SRC} -> ${DEST_PATH}/"
echo "Started: $(date -Is)"

# Parallel file uploads; re-run safely skips existing objects with same checksum
gcloud storage cp -r "${SRC}" "${DEST}/" --recursive -j 64 2>&1 | tee -a "${LOG}"

echo "Done: $(date -Is)"
gcloud storage du -s "${DEST_PATH}/" || true
