#!/bin/bash
set -e

echo "============================================"
echo "Deploying Aerospike Graph Service"
echo "============================================"

REPO_URL="$1"
BRANCH="$2"
AEROSPIKE_HOST="$3"
AEROSPIKE_PORT="$4"
AEROSPIKE_USER="$5"
AEROSPIKE_PASSWORD="$6"
AEROSPIKE_NAMESPACE="$7"

if [ -z "$REPO_URL" ] || [ -z "$AEROSPIKE_HOST" ]; then
    echo "❌ ERROR: Missing required arguments"
    echo "Usage: deploy_ags.sh <repo_url> <branch> <host> <port> <user> <password> <namespace>"
    exit 1
fi

cd /root

if [ -d "unified-fraud-detection" ]; then
    echo "Repo already exists, pulling latest..."
    cd unified-fraud-detection
    git fetch origin
    git checkout "${BRANCH}"
    git pull origin "${BRANCH}"
else
    echo "Cloning repo..."
    git clone -b "${BRANCH}" "${REPO_URL}"
    cd unified-fraud-detection
fi

echo "Creating .env file..."
cat > .env <<EOF
AEROSPIKE_HOST=${AEROSPIKE_HOST}
AEROSPIKE_KV_PORT=${AEROSPIKE_PORT}
AEROSPIKE_NAMESPACE=${AEROSPIKE_NAMESPACE}
AEROSPIKE_TLS_ENABLE=true
AEROSPIKE_TLS_NAME=${AEROSPIKE_HOST}
AEROSPIKE_USER=${AEROSPIKE_USER}
AEROSPIKE_PASSWORD=${AEROSPIKE_PASSWORD}
EOF

echo "Creating AGS-only compose file..."
cat > docker-compose.ags.yaml <<'COMPOSE'
services:
  aerospike-graph-service:
    image: aerospike/aerospike-graph-service:3.0.0
    container_name: asgraph-service
    depends_on:
      zipkin:
        condition: service_healthy
    volumes:
      - ./data:/data
      - ./tls:/opt/aerospike-graph/aerospike-client-tls:ro
    environment:
      - aerospike.client.namespace=${AEROSPIKE_NAMESPACE:-test}
      - aerospike.client.host=${AEROSPIKE_HOST}
      - aerospike.client.port=${AEROSPIKE_KV_PORT:-4000}
      - aerospike.client.tls=true
      - aerospike.client.user=${AEROSPIKE_USER}
      - aerospike.client.password=${AEROSPIKE_PASSWORD}
      - aerospike.client.auth.mode=INTERNAL
      - aerospike.graph.index.vertex.label.enabled=true
      - aerospike.graph.query-tracing.threshold-ms=5
      - aerospike.graph.query-tracing.opentelemetry-host=zipkin
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:9090/healthcheck"]
      interval: 5s
      timeout: 10s
      retries: 4
    networks:
      - asgraph_net
    ports:
      - "8182:8182"
      - "9090:9090"

  zipkin:
    image: openzipkin/zipkin
    container_name: asgraph-zipkin
    networks:
      - asgraph_net
    ports:
      - "9411:9411"
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:9411/health"]
      interval: 5s
      timeout: 10s
      retries: 4

networks:
  asgraph_net:
    name: asgraph_net
COMPOSE

mkdir -p data tls

echo "Stopping any existing containers..."
docker compose -f docker-compose.ags.yaml down 2>/dev/null || true

echo "Starting AGS + Zipkin..."
docker compose -f docker-compose.ags.yaml up -d

echo ""
echo "Waiting for AGS to become healthy..."
for i in $(seq 1 30); do
    if docker compose -f docker-compose.ags.yaml ps | grep -q "healthy"; then
        echo "✓ AGS is healthy!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "⚠️  AGS not healthy yet after 150s, check logs with: docker logs asgraph-service"
    fi
    sleep 5
done

echo ""
echo "✓ AGS deployment complete"
