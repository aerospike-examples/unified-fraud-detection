#!/bin/bash

# ==============================================
# Fraud Demo Destroy Script
# ==============================================
# Tears down the fraud demo EC2 instances.
# Does NOT destroy the Aerospike Cloud cluster
# or the Pegasus client/Grafana instances.
# ==============================================

FRAUD_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${FRAUD_SCRIPT_DIR}/configure.sh"

echo "============================================"
echo "Destroying Fraud Demo Instances"
echo "============================================"
echo ""

FAILED_COMPONENTS=()

# Configure aerolab
aerolab config backend -t aws -r "${CLIENT_AWS_REGION}" &>/dev/null

# Destroy AGS instance
echo "Checking for AGS instance: ${FRAUD_AGS_NAME}..."
AGS_EXISTS=$(aerolab client list -j 2>/dev/null | jq -r "(. // []) | .[] | select(.ClientName == \"${FRAUD_AGS_NAME}\") | .ClientName" | head -1)

if [ -n "$AGS_EXISTS" ]; then
    echo "  Destroying AGS instance..."
    if aerolab client destroy -n "${FRAUD_AGS_NAME}" -f 2>/dev/null; then
        echo "  ✓ AGS instance destroyed"
    else
        echo "  ❌ Failed to destroy AGS instance"
        FAILED_COMPONENTS+=("AGS Instance")
    fi
else
    echo "  ℹ️  AGS instance not found (already destroyed)"
fi

echo ""

# Destroy App instance
echo "Checking for App instance: ${FRAUD_APP_NAME}..."
APP_EXISTS=$(aerolab client list -j 2>/dev/null | jq -r "(. // []) | .[] | select(.ClientName == \"${FRAUD_APP_NAME}\") | .ClientName" | head -1)

if [ -n "$APP_EXISTS" ]; then
    echo "  Destroying App instance..."
    if aerolab client destroy -n "${FRAUD_APP_NAME}" -f 2>/dev/null; then
        echo "  ✓ App instance destroyed"
    else
        echo "  ❌ Failed to destroy App instance"
        FAILED_COMPONENTS+=("App Instance")
    fi
else
    echo "  ℹ️  App instance not found (already destroyed)"
fi

# Clean up fraud config
if [ -d "$FRAUD_CONFIG_DIR" ]; then
    echo ""
    echo "Cleaning up fraud configuration..."
    rm -rf "$FRAUD_CONFIG_DIR"
    echo "  ✓ Fraud configuration removed"
fi

# Summary
echo ""
echo "============================================"
echo "Destroy Summary"
echo "============================================"
if [ ${#FAILED_COMPONENTS[@]} -eq 0 ]; then
    echo "✓ All fraud demo components destroyed successfully"
    echo ""
    echo "Note: Aerospike Cloud cluster and Pegasus instances are still running."
    echo "To destroy everything: cd aeropsike-cloud && ./destroy.sh"
    echo "To redeploy fraud demo: cd fraud && ./buildFraud.sh"
    exit 0
else
    echo "⚠️  Some components failed to destroy:"
    for component in "${FAILED_COMPONENTS[@]}"; do
        echo "  - ${component}"
    done
    echo ""
    echo "Check AWS console for remaining instances."
    exit 1
fi
