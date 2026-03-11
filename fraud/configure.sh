#!/bin/bash
# ==============================================
# Fraud Demo Configuration
# ==============================================
# This file configures the fraud detection demo
# deployment on top of an Aerospike Cloud cluster.
#
# Prerequisites:
#   - Aerospike Cloud cluster must be running (run aeropsike-cloud/setup.sh first)
#   - VPC peering must be complete
#   - Cluster config must exist in ~/.aerospike-cloud/
# ==============================================

# Load the main Aerospike Cloud configuration
FRAUD_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${FRAUD_SCRIPT_DIR}/../aeropsike-cloud/configure.sh"

# ==============================================
# Fraud Demo Repository
# ==============================================
FRAUD_REPO="https://github.com/aerospike-examples/unified-fraud-detection.git"
FRAUD_BRANCH="aerofraud-cloud"

# ==============================================
# AGS (Aerospike Graph Service) Instance Config
# ==============================================
FRAUD_AGS_NAME="FraudAGS_${ACS_CLUSTER_NAME}"
FRAUD_AGS_INSTANCE_TYPE="r6i.xlarge"

# ==============================================
# App Instance Config (Frontend + Backend + Generator)
# ==============================================
FRAUD_APP_NAME="FraudApp_${ACS_CLUSTER_NAME}"
FRAUD_APP_INSTANCE_TYPE="c6i.xlarge"

# ==============================================
# Application Settings
# ==============================================

# Load .env file from fraud/ directory if it exists
if [ -f "${FRAUD_SCRIPT_DIR}/.env" ]; then
    set -a
    source "${FRAUD_SCRIPT_DIR}/.env"
    set +a
fi

# Gemini API key for LLM-powered fraud analysis
GEMINI_API_KEY="${GEMINI_API_KEY:-}"

# LLM provider: "gemini" or "ollama"
LLM_PROVIDER="${LLM_PROVIDER:-gemini}"

# ==============================================
# Tracking directories
# ==============================================
FRAUD_CONFIG_DIR="${ACS_CONFIG_DIR}/${ACS_CLUSTER_NAME}/fraud"
