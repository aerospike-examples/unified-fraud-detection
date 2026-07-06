"""
Shared assessment helpers.

- ``build_evidence_summary``: render the KV-sourced baseline evidence as the text
  block the investigator agent reasons over (moved out of the old LangGraph
  ``llm_agent`` node, unchanged in behavior).
- ``deterministic_assessment``: rule-based fallback used when the LLM is
  unavailable or errors, so an investigation always yields an assessment.
"""

from typing import Dict, Any

from workflow.state import FinalAssessment


def build_evidence_summary(initial: Dict[str, Any], alert: Dict[str, Any]) -> str:
    """Build a text summary of initial KV-sourced evidence for the agent."""

    profile = initial.get("profile", {})
    accounts = initial.get("accounts", {})            # dict: account_id -> {...}
    devices = initial.get("devices", {})              # dict: device_id -> {...}
    account_facts = initial.get("account_facts", {})  # dict: account_id -> 15 features
    device_facts = initial.get("device_facts", {})    # dict: device_id -> 5 features
    metrics = initial.get("account_metrics", {})

    lines = [
        "# INVESTIGATION EVIDENCE (from KV Store)",
        "",
        "## Alert Information",
        f"- Trigger Type: {alert.get('trigger_type', 'Unknown')}",
        f"- Original Risk Score: {alert.get('original_score', 0)}",
        f"- Flag Reason: {alert.get('flag_reason', 'Not specified')}",
        "",
        "## User Profile",
        f"- User ID: {initial.get('user_id', 'Unknown')}",
        f"- Name: {profile.get('name', 'Unknown')}",
        f"- Location: {profile.get('location', 'Unknown')}",
        f"- Occupation: {profile.get('occupation', 'Unknown')}",
        f"- Account Age: {metrics.get('account_age_days', 0)} days",
        f"- Current Risk Score: {metrics.get('profile_risk_score', 0)}",
        f"- KYC Status: {metrics.get('kyc_completeness', 'unknown')}",
        "",
        "## Accounts ({} total, balance: ${:,.2f})".format(len(accounts), metrics.get('total_balance', 0)),
    ]

    for aid, acc in accounts.items():
        flag = " [FLAGGED FRAUD]" if acc.get("is_fraud") else ""
        lines.append(
            f"  - {aid}: type={acc.get('type', 'unknown')}, "
            f"balance=${acc.get('balance', 0):,.2f}, "
            f"status={acc.get('status', 'active')}{flag}"
        )

    if account_facts:
        lines.extend(["", "## Pre-Computed Account Risk Features"])
        for aid, facts in account_facts.items():
            if facts:
                velocity_flag = " [VELOCITY ANOMALY]" if facts.get("transaction_zscore", 0) > 2.0 else ""
                amount_flag = " [AMOUNT ANOMALY]" if facts.get("amount_zscore_7d", 0) > 2.0 else ""
                new_recip_flag = " [HIGH NEW RECIPIENTS]" if facts.get("new_recipient_ratio_7d", 0) > 0.5 else ""

                lines.append(
                    f"  - {aid}: txn_count_7d={facts.get('txn_out_count_7d', 0)}, "
                    f"total_out=${facts.get('total_out_amount_7d', 0):,.2f}, "
                    f"velocity_zscore={facts.get('transaction_zscore', 0):.1f}{velocity_flag}, "
                    f"amount_zscore={facts.get('amount_zscore_7d', 0):.1f}{amount_flag}, "
                    f"unique_recipients={facts.get('unique_recipients_7d', 0)}, "
                    f"new_recipient_ratio={facts.get('new_recipient_ratio_7d', 0):.2f}{new_recip_flag}"
                )

    lines.extend([
        "",
        "## Devices ({} total)".format(len(devices)),
    ])

    for did, dev in devices.items():
        flag = " [FLAGGED FRAUD]" if dev.get("is_fraud") else ""
        lines.append(
            f"  - {did}: type={dev.get('type', 'unknown')}, "
            f"os={dev.get('os', 'unknown')}, "
            f"browser={dev.get('browser', 'unknown')}{flag}"
        )

    if device_facts:
        lines.extend(["", "## Pre-Computed Device Risk Features"])
        for did, facts in device_facts.items():
            if facts:
                shared_flag = " [SHARED DEVICE]" if facts.get("shared_account_count_7d", 0) > 2 else ""
                flagged_flag = " [HAS FLAGGED ACCOUNTS]" if facts.get("flagged_account_count", 0) > 0 else ""

                lines.append(
                    f"  - {did}: shared_accounts={facts.get('shared_account_count_7d', 0)}{shared_flag}, "
                    f"flagged_accounts={facts.get('flagged_account_count', 0)}{flagged_flag}, "
                    f"avg_risk={facts.get('avg_account_risk_score', 0):.0f}, "
                    f"max_risk={facts.get('max_account_risk_score', 0):.0f}"
                )

    lines.extend([
        "",
        "## Summary Metrics",
        f"- Has Flagged Account: {metrics.get('has_flagged_account', False)} ({metrics.get('flagged_account_count', 0)} flagged)",
        f"- Has Flagged Device: {metrics.get('has_flagged_device', False)} ({metrics.get('flagged_device_count', 0)} flagged)",
        f"- Max Velocity Z-Score: {metrics.get('max_velocity_zscore', 0)}",
        f"- Max Amount Z-Score: {metrics.get('max_amount_zscore', 0)}",
        f"- Max New Recipient Ratio: {metrics.get('max_new_recipient_ratio', 0)}",
    ])

    return "\n".join(lines)


def deterministic_assessment(
    initial: Dict[str, Any],
    alert: Dict[str, Any],
    accumulated: Dict[str, Any] = None,
) -> FinalAssessment:
    """Rule-based fallback assessment when the LLM is unavailable."""

    metrics = initial.get("account_metrics", {})
    trigger_type = alert.get("trigger_type", "unknown")
    original_score = alert.get("original_score", 50)

    risk_score = original_score

    if metrics.get("has_flagged_account"):
        risk_score += 20
    if metrics.get("has_flagged_device"):
        risk_score += 15

    max_velocity = metrics.get("max_velocity_zscore", 0)
    if max_velocity > 3.0:
        risk_score += 15
    elif max_velocity > 2.0:
        risk_score += 10

    max_amount_z = metrics.get("max_amount_zscore", 0)
    if max_amount_z > 3.0:
        risk_score += 15
    elif max_amount_z > 2.0:
        risk_score += 10

    new_recip = metrics.get("max_new_recipient_ratio", 0)
    if new_recip > 0.7:
        risk_score += 10

    max_shared = metrics.get("max_shared_accounts_on_device", 0)
    if max_shared > 3:
        risk_score += 15
    elif max_shared > 1:
        risk_score += 5

    risk_score = min(100, risk_score)

    if risk_score >= 80:
        risk_level = "critical"
    elif risk_score >= 60:
        risk_level = "high"
    elif risk_score >= 40:
        risk_level = "medium"
    else:
        risk_level = "low"

    typology = "suspicious_activity"
    evidence_signals = []

    if max_shared > 3:
        typology = "fraud_ring"
        evidence_signals.append("multiple accounts sharing same device")
    if metrics.get("has_flagged_account"):
        evidence_signals.append("account(s) flagged as fraudulent")
    if metrics.get("has_flagged_device"):
        evidence_signals.append("device(s) flagged as fraudulent")
    if max_velocity > 2.0 and new_recip > 0.5:
        typology = "money_mule"
        evidence_signals.append("high transaction velocity with mostly new recipients")
    elif max_velocity > 2.0:
        evidence_signals.append(f"unusual transaction velocity (z-score: {max_velocity:.1f})")
    if max_amount_z > 2.0:
        evidence_signals.append(f"unusual transaction amounts (z-score: {max_amount_z:.1f})")
    if new_recip > 0.7:
        evidence_signals.append(f"very high new recipient ratio ({new_recip:.0%})")

    if risk_score >= 80:
        decision = "temporary_freeze"
    elif risk_score >= 60:
        decision = "step_up_auth"
    else:
        decision = "allow_monitor"

    trigger_rule = alert.get("trigger_rule", alert.get("trigger_type", "ML Detection"))
    reasoning = (
        f"Assessment based on {trigger_rule} (original score {original_score}). "
        f"Evidence: {', '.join(evidence_signals) if evidence_signals else 'no strong signals detected'}. "
        f"Velocity z-score: {max_velocity:.1f}, Amount z-score: {max_amount_z:.1f}, "
        f"New recipient ratio: {new_recip:.2f}, Shared device accounts: {max_shared}."
    )

    return FinalAssessment(
        typology=typology,
        risk_level=risk_level,
        risk_score=risk_score,
        decision=decision,
        reasoning=reasoning,
        iteration=0,
        tool_calls_made=0,
    )
