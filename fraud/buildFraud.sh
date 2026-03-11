#!/bin/bash
set -e

# ==============================================
# Fraud Demo Deployment Script
# ==============================================
# Deploys the fraud detection demo on two EC2
# instances managed by AeroLab:
#   - AGS instance: Aerospike Graph Service + Zipkin
#   - App instance: Frontend + Backend + Generator
#
# Prerequisites:
#   - aeropsike-cloud/setup.sh must be completed
#   - Aerospike Cloud cluster must be active
#   - VPC peering must be configured
# ==============================================

FRAUD_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${FRAUD_SCRIPT_DIR}/configure.sh"

# Load Aerospike Cloud state
if [ ! -f "${ACS_CONFIG_DIR}/current_cluster.sh" ]; then
    echo "❌ ERROR: No Aerospike Cloud cluster found!"
    echo "Please run 'aeropsike-cloud/setup.sh' first."
    exit 1
fi
source "${ACS_CONFIG_DIR}/current_cluster.sh"

# Load cluster config
CLUSTER_CONFIG="${ACS_CONFIG_DIR}/${ACS_CLUSTER_NAME}/${ACS_CLUSTER_ID}/cluster_config.sh"
if [ ! -f "$CLUSTER_CONFIG" ]; then
    echo "❌ ERROR: Cluster config not found at ${CLUSTER_CONFIG}"
    echo "Cluster may not be active yet. Run 'aeropsike-cloud/setup.sh' first."
    exit 1
fi
source "$CLUSTER_CONFIG"

# Load DB user config
DB_USER_CONFIG="${ACS_CONFIG_DIR}/${ACS_CLUSTER_NAME}/${ACS_CLUSTER_ID}/db_user.sh"
if [ ! -f "$DB_USER_CONFIG" ]; then
    echo "❌ ERROR: Database user not configured!"
    echo "Please run 'aeropsike-cloud/setup.sh' first."
    exit 1
fi
source "$DB_USER_CONFIG"

# Load client config (for VPC/subnet info)
if [ ! -f "${CLIENT_CONFIG_DIR}/client_config.sh" ]; then
    echo "❌ ERROR: Client config not found!"
    echo "Please run 'aeropsike-cloud/setup.sh' first."
    exit 1
fi
source "${CLIENT_CONFIG_DIR}/client_config.sh"

# Check for Gemini API key
if [ -z "$GEMINI_API_KEY" ]; then
    echo "⚠️  WARNING: GEMINI_API_KEY not set. LLM-powered fraud analysis will not work."
    echo "Set it in your environment: export GEMINI_API_KEY='your-key-here'"
    echo ""
fi

# TLS certificate
TLS_CERT="${ACS_CONFIG_DIR}/${ACS_CLUSTER_NAME}/${ACS_CLUSTER_ID}/ca.pem"
if [ ! -f "$TLS_CERT" ]; then
    echo "⚠️  WARNING: TLS certificate not found at ${TLS_CERT}"
    echo "AGS and backend may not be able to connect to Aerospike Cloud."
    echo ""
fi

# Create fraud tracking directory
mkdir -p "$FRAUD_CONFIG_DIR"

echo "============================================"
echo "Fraud Demo Deployment"
echo "============================================"
echo ""
echo "  Cluster:      ${ACS_CLUSTER_NAME} (${ACS_CLUSTER_HOSTNAME})"
echo "  DB User:      ${DB_USER}"
echo "  Repo:         ${FRAUD_REPO} (branch: ${FRAUD_BRANCH})"
echo "  AGS Instance: ${FRAUD_AGS_NAME} (${FRAUD_AGS_INSTANCE_TYPE})"
echo "  App Instance: ${FRAUD_APP_NAME} (${FRAUD_APP_INSTANCE_TYPE})"
echo ""

# ============================================
# Configure AeroLab
# ============================================
echo "Configuring aerolab backend..."
aerolab config backend -t aws -r "${CLIENT_AWS_REGION}" &>/dev/null

# ============================================
# Phase 1: Create AGS Instance
# ============================================
echo ""
echo "============================================"
echo "Phase 1: AGS Instance"
echo "============================================"
echo ""

AGS_EXISTS=$(aerolab client list -j 2>/dev/null | jq -r "(. // []) | .[] | select(.ClientName == \"${FRAUD_AGS_NAME}\") | .ClientName" | head -1)

if [ -n "$AGS_EXISTS" ]; then
    echo "✓ AGS instance '${FRAUD_AGS_NAME}' already exists"
else
    echo "Creating AGS instance: ${FRAUD_AGS_NAME}..."
    aerolab client create base \
        -c 1 \
        -n "${FRAUD_AGS_NAME}" \
        --instance-type "${FRAUD_AGS_INSTANCE_TYPE}" \
        --ebs=50 \
        --aws-expire="${CLIENT_AWS_EXPIRE}" || {
        echo "❌ ERROR: Failed to create AGS instance"
        exit 1
    }
    echo "✓ AGS instance created"
fi

# Extract AGS details
AGS_INFO=$(aerolab client list -j 2>/dev/null | jq -r "(. // []) | .[] | select(.ClientName == \"${FRAUD_AGS_NAME}\")")
AGS_PUBLIC_IP=$(echo "$AGS_INFO" | jq -r '.PublicIp')
AGS_PRIVATE_IP=$(echo "$AGS_INFO" | jq -r '.PrivateIp')
AGS_INSTANCE_ID=$(echo "$AGS_INFO" | jq -r '.InstanceId')

echo "  Public IP:  ${AGS_PUBLIC_IP}"
echo "  Private IP: ${AGS_PRIVATE_IP}"

# ============================================
# Phase 2: Create App Instance
# ============================================
echo ""
echo "============================================"
echo "Phase 2: App Instance"
echo "============================================"
echo ""

APP_EXISTS=$(aerolab client list -j 2>/dev/null | jq -r "(. // []) | .[] | select(.ClientName == \"${FRAUD_APP_NAME}\") | .ClientName" | head -1)

if [ -n "$APP_EXISTS" ]; then
    echo "✓ App instance '${FRAUD_APP_NAME}' already exists"
else
    echo "Creating App instance: ${FRAUD_APP_NAME}..."
    aerolab client create base \
        -c 1 \
        -n "${FRAUD_APP_NAME}" \
        --instance-type "${FRAUD_APP_INSTANCE_TYPE}" \
        --ebs=50 \
        --aws-expire="${CLIENT_AWS_EXPIRE}" || {
        echo "❌ ERROR: Failed to create App instance"
        exit 1
    }
    echo "✓ App instance created"
fi

# Extract App details
APP_INFO=$(aerolab client list -j 2>/dev/null | jq -r "(. // []) | .[] | select(.ClientName == \"${FRAUD_APP_NAME}\")")
APP_PUBLIC_IP=$(echo "$APP_INFO" | jq -r '.PublicIp')
APP_PRIVATE_IP=$(echo "$APP_INFO" | jq -r '.PrivateIp')
APP_INSTANCE_ID=$(echo "$APP_INFO" | jq -r '.InstanceId')

echo "  Public IP:  ${APP_PUBLIC_IP}"
echo "  Private IP: ${APP_PRIVATE_IP}"

# ============================================
# Phase 3: Open Security Group Ports
# ============================================
echo ""
echo "============================================"
echo "Phase 3: Security Group Configuration"
echo "============================================"
echo ""

open_port() {
    local instance_id=$1
    local port=$2
    local description=$3

    local sg_id=$(aws ec2 describe-instances \
        --instance-ids "${instance_id}" \
        --region "${CLIENT_AWS_REGION}" \
        --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
        --output text 2>/dev/null)

    if [ -n "$sg_id" ] && [ "$sg_id" != "None" ]; then
        aws ec2 authorize-security-group-ingress \
            --group-id "$sg_id" \
            --protocol tcp \
            --port "$port" \
            --cidr "0.0.0.0/0" \
            --region "${CLIENT_AWS_REGION}" 2>/dev/null && \
            echo "  ✓ Opened port ${port} (${description})" || \
            echo "  ℹ️  Port ${port} already open (${description})"
    fi
}

echo "Opening ports on AGS instance..."
open_port "$AGS_INSTANCE_ID" 8182 "Gremlin"
open_port "$AGS_INSTANCE_ID" 9090 "AGS Health"
open_port "$AGS_INSTANCE_ID" 9411 "Zipkin"

echo ""
echo "Opening ports on App instance..."
open_port "$APP_INSTANCE_ID" 8080 "Frontend"
open_port "$APP_INSTANCE_ID" 4000 "Backend API"
open_port "$APP_INSTANCE_ID" 4001 "Generator"

# ============================================
# Phase 4: Install Docker on Both Instances
# ============================================
echo ""
echo "============================================"
echo "Phase 4: Docker Installation"
echo "============================================"
echo ""

install_docker_on() {
    local instance_name=$1
    echo "Installing Docker on ${instance_name}..."

    aerolab client attach -n "${instance_name}" -l 1 -- bash -c '
        if command -v docker &>/dev/null; then
            echo "Docker already installed"
            docker --version
        else
            apt-get update -qq
            apt-get install -y -qq docker.io docker-compose-v2 git > /dev/null 2>&1
            systemctl start docker
            systemctl enable docker
            echo "Docker installed successfully"
            docker --version
        fi
    ' 2>&1 | tail -5

    echo "  ✓ Docker ready on ${instance_name}"
}

install_docker_on "${FRAUD_AGS_NAME}"
echo ""
install_docker_on "${FRAUD_APP_NAME}"

# ============================================
# Phase 5: Copy TLS Certificate
# ============================================
echo ""
echo "============================================"
echo "Phase 5: TLS Certificate Distribution"
echo "============================================"
echo ""

if [ -f "$TLS_CERT" ]; then
    for inst_name in "${FRAUD_AGS_NAME}" "${FRAUD_APP_NAME}"; do
        echo "Copying TLS cert to ${inst_name}..."
        aerolab client attach -n "${inst_name}" -l 1 -- bash -c "mkdir -p /root/tls"
        aerolab files upload -c -n "${inst_name}" -l 1 "${TLS_CERT}" /root/tls/ca.pem 2>/dev/null || \
            aerolab client attach -n "${inst_name}" -l 1 -- bash -c "cat > /root/tls/ca.pem" < "$TLS_CERT"
        echo "  ✓ TLS cert copied to ${inst_name}"
    done
else
    echo "⚠️  No TLS certificate found, skipping"
fi

# ============================================
# Phase 6: Deploy AGS
# ============================================
echo ""
echo "============================================"
echo "Phase 6: Deploy Aerospike Graph Service"
echo "============================================"
echo ""

aerolab client attach -n "${FRAUD_AGS_NAME}" -l 1 -- bash -c "
set -e

cd /root

if [ -d 'unified-fraud-detection' ]; then
    echo 'Repo already exists, pulling latest...'
    cd unified-fraud-detection
    git fetch origin
    git checkout '${FRAUD_BRANCH}'
    git pull origin '${FRAUD_BRANCH}'
else
    echo 'Cloning repo...'
    git clone -b '${FRAUD_BRANCH}' '${FRAUD_REPO}'
    cd unified-fraud-detection
fi

echo 'Creating .env file...'
cat > .env <<ENVEOF
AEROSPIKE_HOST=${ACS_CLUSTER_HOSTNAME}
AEROSPIKE_KV_PORT=${SERVICE_PORT:-4000}
AEROSPIKE_NAMESPACE=${NAMESPACE_NAME:-test}
AEROSPIKE_TLS_ENABLE=true
AEROSPIKE_TLS_NAME=${ACS_CLUSTER_HOSTNAME}
AEROSPIKE_USER=${DB_USER}
AEROSPIKE_PASSWORD=${DB_PASSWORD}
ENVEOF

echo 'Creating AGS compose file...'
cat > docker-compose.ags.yaml <<'COMPOSEEOF'
services:
  aerospike-graph-service:
    image: aerospike/aerospike-graph-service:3.0.0
    container_name: asgraph-service
    depends_on:
      zipkin:
        condition: service_healthy
    volumes:
      - ./data:/data
      - /root/tls:/opt/aerospike-graph/aerospike-client-tls:ro
    environment:
      - aerospike.client.namespace=${NAMESPACE_NAME:-test}
      - aerospike.client.host=${ACS_CLUSTER_HOSTNAME}
      - aerospike.client.port=4000
      - aerospike.client.tls=true
      - aerospike.client.user=${DB_USER}
      - aerospike.client.password=${DB_PASSWORD}
      - aerospike.client.auth.mode=INTERNAL
      - aerospike.graph.index.vertex.label.enabled=true
      - aerospike.graph.query-tracing.threshold-ms=5
      - aerospike.graph.query-tracing.opentelemetry-host=zipkin
    healthcheck:
      test: [\"CMD\", \"wget\", \"--no-verbose\", \"--tries=1\", \"--spider\", \"http://localhost:9090/healthcheck\"]
      interval: 5s
      timeout: 10s
      retries: 4
    networks:
      - asgraph_net
    ports:
      - \"8182:8182\"
      - \"9090:9090\"

  zipkin:
    image: openzipkin/zipkin
    container_name: asgraph-zipkin
    networks:
      - asgraph_net
    ports:
      - \"9411:9411\"
    healthcheck:
      test: [\"CMD\", \"wget\", \"--no-verbose\", \"--tries=1\", \"--spider\", \"http://localhost:9411/health\"]
      interval: 5s
      timeout: 10s
      retries: 4

networks:
  asgraph_net:
    name: asgraph_net
COMPOSEEOF

mkdir -p data

echo 'Stopping existing containers...'
docker compose -f docker-compose.ags.yaml down 2>/dev/null || true

echo 'Starting AGS + Zipkin...'
docker compose -f docker-compose.ags.yaml up -d
" 2>&1

echo ""
echo "Waiting for AGS to become healthy..."
for i in $(seq 1 30); do
    AGS_HEALTH=$(aerolab client attach -n "${FRAUD_AGS_NAME}" -l 1 -- bash -c \
        "curl -s http://localhost:9090/healthcheck 2>/dev/null | head -1" 2>/dev/null | tr -d '\r\n')
    if [ -n "$AGS_HEALTH" ] && echo "$AGS_HEALTH" | grep -qi "healthy\|ok\|UP"; then
        echo "✓ AGS is healthy!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "⚠️  AGS not healthy yet after 150s"
        echo "Check logs: aerolab client attach -n ${FRAUD_AGS_NAME} -l 1 -- docker logs asgraph-service"
    fi
    printf "\r  Waiting... (%d/30)" "$i"
    sleep 5
done
echo ""

# ============================================
# Phase 7: Deploy App
# ============================================
echo ""
echo "============================================"
echo "Phase 7: Deploy Application"
echo "============================================"
echo ""

aerolab client attach -n "${FRAUD_APP_NAME}" -l 1 -- bash -c "
set -e

cd /root

if [ -d 'unified-fraud-detection' ]; then
    echo 'Repo already exists, pulling latest...'
    cd unified-fraud-detection
    git fetch origin
    git checkout '${FRAUD_BRANCH}'
    git pull origin '${FRAUD_BRANCH}'
else
    echo 'Cloning repo...'
    git clone -b '${FRAUD_BRANCH}' '${FRAUD_REPO}'
    cd unified-fraud-detection
fi

echo 'Creating .env file...'
cat > .env <<ENVEOF
AEROSPIKE_HOST=${ACS_CLUSTER_HOSTNAME}
AEROSPIKE_KV_PORT=${SERVICE_PORT:-4000}
AEROSPIKE_NAMESPACE=${NAMESPACE_NAME:-test}
AEROSPIKE_TLS_ENABLE=true
AEROSPIKE_TLS_NAME=${ACS_CLUSTER_HOSTNAME}
AEROSPIKE_USER=${DB_USER}
AEROSPIKE_PASSWORD=${DB_PASSWORD}
GEMINI_API_KEY=${GEMINI_API_KEY:-}
LLM_PROVIDER=${LLM_PROVIDER:-gemini}
ENVEOF

echo 'Creating App compose file...'
cat > docker-compose.app.yaml <<COMPOSEEOF
services:
  backend:
    build:
      context: .
      dockerfile: backend.Dockerfile
    container_name: asgraph-backend
    depends_on:
      zipkin-local:
        condition: service_healthy
    environment:
      - AEROSPIKE_HOST=${ACS_CLUSTER_HOSTNAME}
      - AEROSPIKE_KV_PORT=4000
      - AEROSPIKE_NAMESPACE=${NAMESPACE_NAME:-test}
      - AEROSPIKE_TLS_ENABLE=true
      - AEROSPIKE_TLS_NAME=${ACS_CLUSTER_HOSTNAME}
      - AEROSPIKE_TLS_CAFILE=/tls/ca.pem
      - AEROSPIKE_USER=${DB_USER}
      - AEROSPIKE_PASSWORD=${DB_PASSWORD}
      - GRAPH_HOST_ADDRESS=${AGS_PRIVATE_IP}
      - LLM_PROVIDER=${LLM_PROVIDER:-gemini}
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      - OLLAMA_MODEL=mistral
      - GEMINI_API_KEY=${GEMINI_API_KEY:-}
      - GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.0-flash}
    volumes:
      - ./data:/data
      - /root/tls:/tls:ro
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://0.0.0.0:4000/health"]
      interval: 5s
      timeout: 10s
      retries: 4
    networks:
      - app_net
    ports:
      - "4000:4000"

  frontend:
    build:
      context: .
      dockerfile: frontend.Dockerfile
    container_name: asgraph-frontend
    depends_on:
      backend:
        condition: service_healthy
    environment:
      - BACKEND_URL=http://asgraph-backend:4000
      - NEXT_PUBLIC_BACKEND_URL=http://${APP_PUBLIC_IP}:4000
    networks:
      - app_net
    ports:
      - "8080:8080"

  generator:
    build:
      context: .
      dockerfile: generator.Dockerfile
    container_name: asgraph-generator
    depends_on:
      backend:
        condition: service_healthy
    networks:
      - app_net
    ports:
      - "4001:4001"

  zipkin-local:
    image: openzipkin/zipkin
    container_name: asgraph-zipkin-local
    networks:
      - app_net
    ports:
      - "9411:9411"
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:9411/health"]
      interval: 5s
      timeout: 10s
      retries: 4

networks:
  app_net:
    name: app_net
COMPOSEEOF

mkdir -p data

echo 'Stopping existing containers...'
docker compose -f docker-compose.app.yaml down 2>/dev/null || true

echo 'Building and starting application...'
docker compose -f docker-compose.app.yaml up -d --build
" 2>&1

echo ""
echo "Waiting for backend to become healthy..."
for i in $(seq 1 60); do
    BACKEND_HEALTH=$(aerolab client attach -n "${FRAUD_APP_NAME}" -l 1 -- bash -c \
        "curl -s http://localhost:4000/health 2>/dev/null | head -1" 2>/dev/null | tr -d '\r\n')
    if [ -n "$BACKEND_HEALTH" ]; then
        echo "✓ Backend is healthy!"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "⚠️  Backend not healthy yet after 300s"
        echo "Check logs: aerolab client attach -n ${FRAUD_APP_NAME} -l 1 -- docker logs asgraph-backend"
    fi
    printf "\r  Waiting... (%d/60)" "$i"
    sleep 5
done
echo ""

# ============================================
# Save Configuration
# ============================================
echo ""
echo "Saving fraud demo configuration..."

cat > "${FRAUD_CONFIG_DIR}/fraud_config.sh" <<EOF
# Fraud Demo Configuration
# Generated on: $(date)

# Cluster Association
export ACS_CLUSTER_ID="${ACS_CLUSTER_ID}"
export ACS_CLUSTER_NAME="${ACS_CLUSTER_NAME}"

# AGS Instance
export FRAUD_AGS_NAME="${FRAUD_AGS_NAME}"
export AGS_PUBLIC_IP="${AGS_PUBLIC_IP}"
export AGS_PRIVATE_IP="${AGS_PRIVATE_IP}"
export AGS_INSTANCE_ID="${AGS_INSTANCE_ID}"

# App Instance
export FRAUD_APP_NAME="${FRAUD_APP_NAME}"
export APP_PUBLIC_IP="${APP_PUBLIC_IP}"
export APP_PRIVATE_IP="${APP_PRIVATE_IP}"
export APP_INSTANCE_ID="${APP_INSTANCE_ID}"

# URLs
export FRAUD_FRONTEND_URL="http://${APP_PUBLIC_IP}:8080"
export FRAUD_BACKEND_URL="http://${APP_PUBLIC_IP}:4000"
export FRAUD_AGS_URL="http://${AGS_PUBLIC_IP}:8182"
export FRAUD_ZIPKIN_URL="http://${AGS_PUBLIC_IP}:9411"
EOF

echo "✓ Configuration saved to ${FRAUD_CONFIG_DIR}/fraud_config.sh"

# ============================================
# Summary
# ============================================
echo ""
echo "╔════════════════════════════════════════════════════════════════════════╗"
echo "║              ✓ FRAUD DEMO DEPLOYMENT COMPLETE!                       ║"
echo "╚════════════════════════════════════════════════════════════════════════╝"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  FRAUD DEMO APPLICATION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Frontend:       http://${APP_PUBLIC_IP}:8080"
echo "  Backend API:    http://${APP_PUBLIC_IP}:4000"
echo "  Generator:      http://${APP_PUBLIC_IP}:4001"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  GRAPH SERVICE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Gremlin:        ws://${AGS_PUBLIC_IP}:8182/gremlin"
echo "  Health:         http://${AGS_PUBLIC_IP}:9090/healthcheck"
echo "  Zipkin:         http://${AGS_PUBLIC_IP}:9411"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  USEFUL COMMANDS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  SSH to AGS:     aerolab client attach -n ${FRAUD_AGS_NAME} -l 1"
echo "  SSH to App:     aerolab client attach -n ${FRAUD_APP_NAME} -l 1"
echo "  AGS Logs:       aerolab client attach -n ${FRAUD_AGS_NAME} -l 1 -- docker logs -f asgraph-service"
echo "  Backend Logs:   aerolab client attach -n ${FRAUD_APP_NAME} -l 1 -- docker logs -f asgraph-backend"
echo "  Destroy:        cd fraud && ./destroy.sh"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
