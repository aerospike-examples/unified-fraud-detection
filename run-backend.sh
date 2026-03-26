#!/bin/bash
# Run the backend locally (requires Python 3.12+)
# Make sure Aerospike and Graph Service are running via Docker first.

set -e

cd "$(dirname "$0")/backend"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

# Load .env from project root
set -a
[ -f ../".env" ] && source ../".env"
set +a

export AEROSPIKE_HOST=${AEROSPIKE_HOST:-localhost}
export LLM_PROVIDER=${LLM_PROVIDER:-gemini}

echo "Starting backend on http://localhost:4000"
uvicorn main:app --host 0.0.0.0 --port 4000 --reload --loop asyncio
