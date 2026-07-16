#!/usr/bin/env bash
# Materializes .env from Secret Manager, using the VM's attached service
# account (no gcloud CLI or long-lived key required — just the instance's
# metadata-server credentials). Run this on the demo-app host itself, after
# bootstrap-demo-app.sh.
#
# Requires the VM's service account to have roles/secretmanager.secretAccessor
# on: demo-app-google-api-key, demo-app-gemini-api-key, demo-app-auth-username,
# demo-app-auth-password (create-demo-app-secure.sh grants this automatically).
set -euo pipefail

DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${1:-${DIR}/.env}"
TEMPLATE="${DIR}/deploy/gcp/demo.env"

META="http://metadata.google.internal/computeMetadata/v1"
PROJECT="$(curl -sf -H 'Metadata-Flavor: Google' "${META}/project/project-id")"
TOKEN="$(curl -sf -H 'Metadata-Flavor: Google' \
  "${META}/instance/service-accounts/default/token" | jq -r '.access_token')"

fetch_secret() {
  curl -sf -H "Authorization: Bearer ${TOKEN}" \
    "https://secretmanager.googleapis.com/v1/projects/${PROJECT}/secrets/${1}/versions/latest:access" \
    | jq -r '.payload.data' | base64 -d
}

GOOGLE_KEY="$(fetch_secret demo-app-google-api-key)"
GEMINI_KEY="$(fetch_secret demo-app-gemini-api-key)"
AUTH_USER="$(fetch_secret demo-app-auth-username)"
AUTH_PASS="$(fetch_secret demo-app-auth-password)"

umask 077
sed \
  -e "s#^GOOGLE_API_KEY=.*#GOOGLE_API_KEY=${GOOGLE_KEY}#" \
  -e "s#^GEMINI_API_KEY=.*#GEMINI_API_KEY=${GEMINI_KEY}#" \
  -e "s#^AUTH_USERNAME=.*#AUTH_USERNAME=${AUTH_USER}#" \
  -e "s#^AUTH_PASSWORD=.*#AUTH_PASSWORD=${AUTH_PASS}#" \
  "${TEMPLATE}" > "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

echo "Wrote ${ENV_FILE} (mode 600) from Secret Manager."
