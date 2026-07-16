#!/usr/bin/env bash
# Creates a hardened replacement for the compromised `demo-app` instance.
#
# Compared to the old deployment, this:
#   - Has NO external IP. The demo is only reachable via an IAP tunnel
#     (gcloud compute start-iap-tunnel), never from the open internet.
#   - Scopes SSH/app-port ingress to Google's IAP range only (35.235.240.0/20),
#     under a fresh network tag — the old demo-app-http / demo-app-public-web
#     rules (open to 0.0.0.0/0) are left untouched but no longer apply here.
#   - Uses OS Login (IAM-managed SSH identities) instead of static/project
#     SSH keys, and explicitly blocks the ~450 stray keys sitting in this
#     project's metadata (block-project-ssh-keys=TRUE).
#   - Runs as a dedicated, minimally-scoped service account instead of the
#     shared default Compute Engine service account.
#   - Enables full Shielded VM protections (Secure Boot + vTPM + integrity
#     monitoring) — the old instance had Secure Boot disabled.
#   - Pulls secrets (Gemini API key, demo auth password) from Secret Manager
#     at boot instead of shipping them in a plaintext .env baked in advance.
#
# Usage:
#   ./create-demo-app-secure.sh [INSTANCE_NAME]
#
# Requires: gcloud authenticated with permissions to create service accounts,
# IAM bindings, firewall rules, secrets, and compute instances.

set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
ZONE="${ZONE:-us-central1-a}"
REGION="${ZONE%-*}"
INSTANCE_NAME="${1:-demo-app-v2}"
NETWORK="${NETWORK:-default}"
SUBNET="${SUBNET:-default}"
TAG="demo-app-secure"
SA_NAME="demo-app-runtime"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
OPERATOR_EMAIL="${OPERATOR_EMAIL:-$(gcloud config get-value account 2>/dev/null)}"
MACHINE_TYPE="${MACHINE_TYPE:-c4d-standard-16}"
IAP_RANGE="35.235.240.0/20"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-100}"

echo "== Project: ${PROJECT}  Zone: ${ZONE}  Instance: ${INSTANCE_NAME} =="

# ---------------------------------------------------------------------------
# 1. Dedicated, minimally-scoped service account for the VM itself.
# ---------------------------------------------------------------------------
if ! gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Demo app runtime (secrets + logging/monitoring only)"
fi

for role in roles/logging.logWriter roles/monitoring.metricWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${role}" \
    --condition=None >/dev/null
done

# Grant access to only the specific secrets it needs — not project-wide accessor.
for secret in demo-app-google-api-key demo-app-gemini-api-key demo-app-auth-username demo-app-auth-password; do
  gcloud secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
done

# ---------------------------------------------------------------------------
# 2. Firewall: IAP-only ingress under a fresh tag. No 0.0.0.0/0 rule is
#    created for this tag, so this instance is unreachable from the public
#    internet regardless of what happens to have an external IP (it has none).
# ---------------------------------------------------------------------------
if ! gcloud compute firewall-rules describe demo-app-secure-iap-ingress >/dev/null 2>&1; then
  gcloud compute firewall-rules create demo-app-secure-iap-ingress \
    --network="${NETWORK}" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:22,tcp:4000,tcp:8080 \
    --source-ranges="${IAP_RANGE}" \
    --target-tags="${TAG}" \
    --description="SSH + demo app ports, IAP tunnel only"
fi

# ---------------------------------------------------------------------------
# 3. OS Login + IAP tunnel IAM for the operator running the demo.
# ---------------------------------------------------------------------------
if [[ -n "${OPERATOR_EMAIL}" ]]; then
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="user:${OPERATOR_EMAIL}" \
    --role="roles/compute.osAdminLogin" \
    --condition=None >/dev/null
  gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="user:${OPERATOR_EMAIL}" \
    --role="roles/iap.tunnelResourceAccessor" \
    --condition=None >/dev/null
else
  echo "WARNING: could not determine OPERATOR_EMAIL — grant roles/compute.osAdminLogin and roles/iap.tunnelResourceAccessor manually." >&2
fi

# ---------------------------------------------------------------------------
# 4. The VM itself.
# ---------------------------------------------------------------------------
gcloud compute instances create "${INSTANCE_NAME}" \
  --zone="${ZONE}" \
  --machine-type="${MACHINE_TYPE}" \
  --image-family=debian-13 \
  --image-project=debian-cloud \
  --boot-disk-size="${BOOT_DISK_SIZE}GB" \
  --network="${NETWORK}" \
  --subnet="${SUBNET}" \
  --no-address \
  --tags="${TAG}" \
  --service-account="${SA_EMAIL}" \
  --scopes=cloud-platform \
  --shielded-secure-boot \
  --shielded-vtpm \
  --shielded-integrity-monitoring \
  --metadata=block-project-ssh-keys=TRUE,enable-oslogin=TRUE,enable-oslogin-2fa=TRUE

cat <<EOF

== Instance created with no external IP. ==

To reach it, open an IAP tunnel from your workstation (requires
roles/iap.tunnelResourceAccessor + roles/compute.osAdminLogin, granted above
for ${OPERATOR_EMAIL:-<your account>}):

  # SSH
  gcloud compute ssh ${INSTANCE_NAME} --zone=${ZONE} --tunnel-through-iap

  # Frontend (demo UI)
  gcloud compute start-iap-tunnel ${INSTANCE_NAME} 8080 \\
    --local-host-port=localhost:8080 --zone=${ZONE}

  # Backend (only needed if NEXT_PUBLIC_BACKEND_URL points at it directly)
  gcloud compute start-iap-tunnel ${INSTANCE_NAME} 4000 \\
    --local-host-port=localhost:4000 --zone=${ZONE}

Then browse to http://localhost:8080.

Next, over the SSH tunnel:
  git clone <repo-url> ~/unified-fraud-detection && cd ~/unified-fraud-detection
  ./deploy/gcp/bootstrap-demo-app.sh   # installs docker, unattended-upgrades
  # log out/in once so the docker group membership takes effect, then:
  ./deploy/gcp/fetch-secrets.sh        # pulls secrets from Secret Manager into .env
  docker compose -f docker-compose.demo.yml up -d --build
EOF
