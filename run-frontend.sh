#!/bin/bash
# Run the frontend locally (requires Node.js 18+)

set -e

cd "$(dirname "$0")/frontend"

if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

export BACKEND_URL=${BACKEND_URL:-http://localhost:4000}
export NEXT_PUBLIC_BACKEND_URL=${NEXT_PUBLIC_BACKEND_URL:-http://localhost:4000}

echo "Starting frontend on http://localhost:8080"
npm run dev -- -p 8080
