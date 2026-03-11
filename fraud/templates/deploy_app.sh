#!/bin/bash
set -e

echo "============================================"
echo "Deploying Fraud Demo Application"
echo "============================================"

REPO_URL="$1"
BRANCH="$2"
AEROSPIKE_HOST="$3"
AEROSPIKE_PORT="$4"
AEROSPIKE_USER="$5"
AEROSPIKE_PASSWORD="$6"
AEROSPIKE_NAMESPACE="$7"
AGS_PRIVATE_IP="$8"
APP_PUBLIC_IP="$9"
GEMINI_API_KEY="${10}"
LLM_PROVIDER="${11:-gemini}"

if [ -z "$REPO_URL" ] || [ -z "$AEROSPIKE_HOST" ] || [ -z "$AGS_PRIVATE_IP" ]; then
    echo "❌ ERROR: Missing required arguments"
    echo "Usage: deploy_app.sh <repo> <branch> <host> <port> <user> <pass> <ns> <ags_ip> <public_ip> <gemini_key> [llm_provider]"
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
GEMINI_API_KEY=${GEMINI_API_KEY}
LLM_PROVIDER=${LLM_PROVIDER}
EOF

echo "Creating App-only compose file..."
cat > docker-compose.app.yaml <<COMPOSE
services:
  backend:
    build:
      context: .
      dockerfile: backend.Dockerfile
    container_name: "asgraph-backend"
    depends_on:
      zipkin-local:
        condition: service_healthy
    environment:
      - AEROSPIKE_HOST=\${AEROSPIKE_HOST}
      - AEROSPIKE_KV_PORT=\${AEROSPIKE_KV_PORT:-4000}
      - AEROSPIKE_NAMESPACE=\${AEROSPIKE_NAMESPACE:-test}
      - AEROSPIKE_TLS_ENABLE=\${AEROSPIKE_TLS_ENABLE:-true}
      - AEROSPIKE_TLS_NAME=\${AEROSPIKE_TLS_NAME}
      - AEROSPIKE_TLS_CAFILE=/tls/ca.pem
      - AEROSPIKE_USER=\${AEROSPIKE_USER}
      - AEROSPIKE_PASSWORD=\${AEROSPIKE_PASSWORD}
      - GRAPH_HOST_ADDRESS=${AGS_PRIVATE_IP}
      - LLM_PROVIDER=\${LLM_PROVIDER:-gemini}
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      - OLLAMA_MODEL=mistral
      - GEMINI_API_KEY=\${GEMINI_API_KEY:-}
      - GEMINI_MODEL=\${GEMINI_MODEL:-gemini-2.0-flash}
    volumes:
      - ./data:/data
      - ./tls:/tls
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
    container_name: "asgraph-frontend"
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
    container_name: "asgraph-generator"
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
COMPOSE

mkdir -p data tls

echo "Stopping any existing containers..."
docker compose -f docker-compose.app.yaml down 2>/dev/null || true

echo "Building and starting application..."
docker compose -f docker-compose.app.yaml up -d --build

echo ""
echo "Waiting for backend to become healthy..."
for i in $(seq 1 60); do
    if curl -s http://localhost:4000/health > /dev/null 2>&1; then
        echo "✓ Backend is healthy!"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "⚠️  Backend not healthy yet after 300s, check logs with: docker logs asgraph-backend"
    fi
    sleep 5
done

echo ""
echo "✓ App deployment complete"
echo ""
echo "Access the application at: http://${APP_PUBLIC_IP}:8080"
