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
        max_calls=2,
        focus=(
            "Counterparties and money-movement patterns. Use get_transaction_network "
            "(hops=1) OR detect_fraud_ring (hops=1) — pick ONE, not both. Then vet at most "
            "1-2 suspicious counterparties with get_counterparty_profile. Be fast and decisive."
        ),
    ),
    DEVICE_ANALYST_NAME: dict(
        role="DEVICE & INFRASTRUCTURE ANALYST",
        max_calls=1,
        focus=(
            "Device and account infrastructure risk only. One call to get_account_risk_features "
            "on the flagged account is usually enough. Add get_device_risk_features only if "
            "devices are present in the evidence."
        ),
    ),
    VELOCITY_ANALYST_NAME: dict(
        role="VELOCITY & TRANSACTION ANALYST",
        max_calls=2,
        focus=(
            "Velocity and amount behavior. get_account_risk_features first, then "
            "get_account_transactions (days=7) only if velocity scores look anomalous. "
            "Quantify any burst in one line."
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
