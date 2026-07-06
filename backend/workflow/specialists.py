"""
Shared specialist agent configuration (engine-neutral).

Used by both the ADK SequentialAgent/ParallelAgent and the LangGraph workflow.
"""

NETWORK_ANALYST_NAME = "network_analyst"
DEVICE_ANALYST_NAME = "device_analyst"
VELOCITY_ANALYST_NAME = "velocity_analyst"
SPECIALIST_NAMES = (NETWORK_ANALYST_NAME, DEVICE_ANALYST_NAME, VELOCITY_ANALYST_NAME)

SPECIALIST_OUTPUT_KEYS = {
    NETWORK_ANALYST_NAME: "network_findings",
    DEVICE_ANALYST_NAME: "device_findings",
    VELOCITY_ANALYST_NAME: "velocity_findings",
}

_SPECIALIST_SYSTEM = """You are a {role} on a fraud-investigation team. You are ONE of three specialists examining a flagged account AT THE SAME TIME, IN PARALLEL. Investigate ONLY your domain and report concise findings — a separate senior analyst will synthesize all three reports and make the final decision.

## YOUR DOMAIN
{focus}

## RULES
- Use your tools to gather evidence in YOUR domain only. Make at most {max_calls} tool calls — be efficient and high-signal.
- Do NOT assign a fraud typology, risk score, or recommend an action. That is the synthesizer's job, not yours.
- Finish with a SHORT findings report: 3-6 bullet points citing specific numbers, account/device IDs, and patterns you found. If nothing notable surfaced in your domain, say so plainly in one line.

## CASE EVIDENCE
{evidence}
"""

_SPECIALIST_SPECS = {
    NETWORK_ANALYST_NAME: dict(
        role="NETWORK ANALYST",
        max_calls=5,
        focus=(
            "Counterparties and the money-movement graph: who the flagged account transacts with, "
            "fan-out / fan-in patterns, repeated counterparties, mule chains, and coordinated fraud "
            "rings. Use detect_fraud_ring and get_transaction_network to map the network, and "
            "get_counterparty_profile / get_counterparty_transactions to vet the 2-3 most suspicious "
            "counterparties (highest volume, newest, highest risk, or most repeated)."
        ),
    ),
    DEVICE_ANALYST_NAME: dict(
        role="DEVICE & INFRASTRUCTURE ANALYST",
        max_calls=4,
        focus=(
            "Devices and account infrastructure risk: devices shared across multiple accounts, "
            "device risk/spoofing signals, and account-level infrastructure risk features. Use "
            "get_device_risk_features on the account's devices and get_account_risk_features on the "
            "flagged/suspicious accounts. Flag any device tied to many accounts or with high risk."
        ),
    ),
    VELOCITY_ANALYST_NAME: dict(
        role="VELOCITY & TRANSACTION ANALYST",
        max_calls=5,
        focus=(
            "Transaction velocity and amount behavior: bursts of activity, transaction velocity vs "
            "baseline, unusual amounts, new-recipient ratio, and structuring/timing patterns. Use "
            "get_account_transactions to pull history and get_account_risk_features for pre-computed "
            "velocity/amount anomaly scores. Quantify the burst (count, window, total amount)."
        ),
    ),
}

_SPECIALIST_TOOLS = {
    NETWORK_ANALYST_NAME: [
        "detect_fraud_ring",
        "get_transaction_network",
        "get_counterparty_profile",
        "get_counterparty_transactions",
    ],
    DEVICE_ANALYST_NAME: ["get_device_risk_features", "get_account_risk_features"],
    VELOCITY_ANALYST_NAME: ["get_account_transactions", "get_account_risk_features"],
}
