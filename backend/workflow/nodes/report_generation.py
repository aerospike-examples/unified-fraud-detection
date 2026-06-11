"""
Report Generation helpers.

Under ADK, the markdown report is produced by the ``report_writer`` LlmAgent.
This module provides:

- ``REPORT_PROMPT_TEMPLATE`` + ``build_report_instruction`` — the instruction the
  report agent reasons over, formatted from session state.
- ``finalize_report`` — deterministic post-processing applied to the agent's
  markdown (appends the fraud-ring Mermaid section and a metadata footer).
- ``generate_fallback_report`` — a fully deterministic report used when the LLM
  is unavailable.

These run as plain Python (no LLM calls) so the rendered report is identical to
the previous implementation.
"""

from datetime import datetime
from typing import Dict, Any
import logging

logger = logging.getLogger('investigation.report_generation')

REPORT_PROMPT_TEMPLATE = """You are a fraud analyst AI. Generate a comprehensive investigation report in Markdown format.

## Investigation Details
- Investigation ID: {investigation_id}
- User ID: {user_id}
- Started: {started_at}

## Alert Information
- Detection Rule: {trigger_rule}
- Flag Reason: {flag_reason}
- Original Risk Score: {original_score}

## User Profile
- Name: {user_name}
- Location: {location}
- Account Age: {account_age_days} days
- KYC Status: {kyc_completeness}

## Account Summary
- Total Accounts: {account_count}
- Total Balance: ${total_balance}
- Flagged Accounts: {flagged_account_count}
- Device Count: {device_count}
- Flagged Devices: {flagged_device_count}

## Pre-Computed Risk Signals
- Max Velocity Z-Score: {max_velocity_zscore}
- Max Amount Z-Score: {max_amount_zscore}
- Max New Recipient Ratio: {max_new_recipient_ratio}
- Max Shared Accounts on Device: {max_shared_accounts}

## AI Investigation Summary
The AI agent analyzed this case in {iterations} iterations, making {tool_calls} tool calls to gather evidence.

### Assessment
- **Fraud Typology**: {typology}
- **Risk Level**: {risk_level}
- **Risk Score**: {risk_score}/100
- **Recommended Action**: {decision}

### Agent's Reasoning
{reasoning}

Generate a professional investigation report with the following sections:
1. Executive Summary (2-3 sentences)
2. Key Risk Factors (bullet points)
3. Evidence Summary (what the AI agent found)
4. Risk Assessment Analysis
5. Recommendation and Rationale
6. Next Steps for Analyst

Do NOT include an investigation timeline or tool call log. Use clear, professional language suitable for compliance review.
"""


def build_report_instruction(state: Dict[str, Any]) -> str:
    """Format the report-writer instruction from session state."""
    alert = state.get("alert_evidence") or {}
    initial = state.get("initial_evidence") or {}
    assessment = state.get("final_assessment") or {}
    tool_calls = state.get("tool_calls") or []

    profile = initial.get("profile", {})
    metrics = initial.get("account_metrics", {})
    accounts = initial.get("accounts", {})
    devices = initial.get("devices", {})

    return REPORT_PROMPT_TEMPLATE.format(
        investigation_id=state.get("investigation_id", "N/A"),
        user_id=state.get("user_id", "N/A"),
        started_at=state.get("started_at", ""),
        trigger_rule=alert.get("trigger_rule", alert.get("trigger_type", "ML Detection")),
        flag_reason=alert.get("flag_reason", "N/A"),
        original_score=alert.get("original_score", 0),
        user_name=profile.get("name", "Unknown"),
        location=profile.get("location", "Unknown"),
        account_age_days=metrics.get("account_age_days", 0),
        kyc_completeness=metrics.get("kyc_completeness", "unknown"),
        account_count=len(accounts),
        total_balance=metrics.get("total_balance", 0),
        flagged_account_count=metrics.get("flagged_account_count", 0),
        device_count=len(devices),
        flagged_device_count=metrics.get("flagged_device_count", 0),
        max_velocity_zscore=metrics.get("max_velocity_zscore", 0),
        max_amount_zscore=metrics.get("max_amount_zscore", 0),
        max_new_recipient_ratio=metrics.get("max_new_recipient_ratio", 0),
        max_shared_accounts=metrics.get("max_shared_accounts_on_device", 0),
        iterations=state.get("agent_iterations", 0),
        tool_calls=len(tool_calls),
        typology=assessment.get("typology", "unknown"),
        risk_level=assessment.get("risk_level", "unknown"),
        risk_score=assessment.get("risk_score", 0),
        decision=assessment.get("decision", "pending"),
        reasoning=assessment.get("reasoning", "No reasoning provided"),
    )


def finalize_report(response: str, state: Dict[str, Any]) -> str:
    """Public entry point: apply deterministic post-processing to LLM markdown."""
    return _clean_report(response, state)


def generate_fallback_report(state: Dict[str, Any]) -> str:
    """Public entry point: fully deterministic report when the LLM is unavailable."""
    return _generate_fallback_report(state)


def _build_fraud_ring_section(tool_calls: list, user_id: str) -> str:
    """Build a Fraud Ring Analysis markdown section from detect_fraud_ring tool results."""
    ring_result = None
    for call in tool_calls:
        if call.get("tool") == "detect_fraud_ring":
            result = call.get("result", {})
            if result.get("success") and result.get("is_fraud_ring"):
                ring_result = result
                break  # use the first positive hit
    
    if not ring_result:
        return ""
    
    confidence = ring_result.get("ring_confidence", 0)
    members = ring_result.get("ring_members", [])
    potential = ring_result.get("potential_ring", {})
    evidence = ring_result.get("evidence", [])
    
    density = potential.get("cluster_density", 0)
    triangles = potential.get("triangle_count", 0)
    reciprocal = potential.get("reciprocal_partner_count", 0)
    high_vol = potential.get("high_volume_pair_count", 0)
    high_vol_pairs = potential.get("high_volume_pairs", [])
    cluster_members = potential.get("cluster_members", [])
    
    # Build mermaid graph
    mermaid_nodes = []
    mermaid_edges = []
    
    # Add target node
    mermaid_nodes.append(f'    {user_id}(("{user_id}<br/>TARGET"))')
    mermaid_nodes.append(f'    style {user_id} fill:#dbeafe,stroke:#2563eb,stroke-width:3px')
    
    # Add ring members
    member_ids = set()
    for m in members:
        mid = m.get("user_id", "")
        if mid and mid != user_id:
            member_ids.add(mid)
            name = m.get("name", "Unknown")
            risk = m.get("risk_score", 0)
            label = f"{mid}<br/>{name}"
            mermaid_nodes.append(f'    {mid}(("{label}"))')
            if risk >= 70:
                mermaid_nodes.append(f'    style {mid} fill:#fecaca,stroke:#dc2626,stroke-width:2px')
            elif risk >= 40:
                mermaid_nodes.append(f'    style {mid} fill:#fed7aa,stroke:#ea580c,stroke-width:2px')
            else:
                mermaid_nodes.append(f'    style {mid} fill:#d1fae5,stroke:#16a34a,stroke-width:2px')
    
    # Add edges from high-volume pairs (target <-> partner)
    vol_map = {p.get("user_id"): p.get("transaction_count", 0) for p in high_vol_pairs}
    added_edges = set()
    
    for m in members:
        mid = m.get("user_id", "")
        if mid and mid != user_id:
            vol = vol_map.get(mid, 0)
            edge_key = tuple(sorted([user_id, mid]))
            if edge_key not in added_edges:
                added_edges.add(edge_key)
                if vol >= 50:
                    mermaid_edges.append(f'    {user_id} -- "{vol} txns" --> {mid}')
                else:
                    mermaid_edges.append(f'    {user_id} -.- {mid}')
    
    # Add edges between cluster members (from triangles)
    triangle_list = potential.get("triangles", [])
    for tri in triangle_list:
        tri_members = tri.get("members", [])
        for i in range(len(tri_members)):
            for j in range(i + 1, len(tri_members)):
                a, b = tri_members[i], tri_members[j]
                if a != user_id and b != user_id:
                    edge_key = tuple(sorted([a, b]))
                    if edge_key not in added_edges and a in member_ids and b in member_ids:
                        added_edges.add(edge_key)
                        mermaid_edges.append(f'    {a} -.- {b}')
    
    mermaid_diagram = "```mermaid\ngraph LR\n" + "\n".join(mermaid_nodes) + "\n" + "\n".join(mermaid_edges) + "\n```"
    
    # Build members table
    member_rows = []
    for m in members:
        mid = m.get("user_id", "")
        name = m.get("name", "Unknown")
        risk = m.get("risk_score", 0)
        conn = m.get("connection_type", "unknown").replace("_", " ")
        vol = vol_map.get(mid, 0)
        vol_str = f"{vol} txns" if vol else "—"
        risk_indicator = "🔴" if risk >= 70 else ("🟡" if risk >= 40 else "🟢")
        member_rows.append(f"| {mid} | {name} | {risk_indicator} {risk:.0f} | {conn} | {vol_str} |")
    
    member_table = "\n".join(member_rows) if member_rows else "| — | — | — | — | — |"
    
    # Build evidence list
    evidence_lines = "\n".join(f"- {e}" for e in evidence) if evidence else "- No specific evidence"
    
    section = f"""
---

## Fraud Ring Analysis

> **Potential fraud ring detected** with **{confidence}% confidence** involving **{len(members)} members**.

### Ring Structure

{mermaid_diagram}

### Ring Statistics
| Metric | Value |
|--------|-------|
| Cluster Density | {density * 100:.0f}% |
| Transaction Triangles | {triangles} |
| Reciprocal Money Flows | {reciprocal} |
| High Volume Pairs | {high_vol} |

### Ring Members
| User ID | Name | Risk | Connection | Txn Volume |
|---------|------|------|------------|------------|
{member_table}

### Evidence
{evidence_lines}
"""
    return section


def _build_tool_call_summary(tool_calls: list) -> str:
    """Build a summary of tool calls for the report."""
    if not tool_calls:
        return "No tool calls made - assessment based on initial evidence only."
    
    lines = []
    for i, call in enumerate(tool_calls[:10], 1):  # Limit to 10
        tool = call.get("tool", "unknown")
        params = call.get("params", {})
        timestamp = call.get("timestamp", "")
        
        # Format parameters
        params_str = ", ".join(f"{k}={v}" for k, v in params.items())[:50]
        
        lines.append(f"{i}. **{tool}**({params_str})")
    
    if len(tool_calls) > 10:
        lines.append(f"... and {len(tool_calls) - 10} more tool calls")
    
    return "\n".join(lines)


def _clean_report(response: str, state: Dict[str, Any]) -> str:
    """Clean and format the generated report."""
    initial = state.get("initial_evidence") or {}
    profile = initial.get("profile", {})
    tool_calls = state.get("tool_calls") or []
    user_id = state.get("user_id", "")
    
    # Add header if not present
    if not response.strip().startswith("#"):
        user_name = profile.get("name", "Unknown")
        response = f"# Fraud Investigation Report\n## User: {user_name}\n\n{response}"
    
    # Append fraud ring section (for LLM-generated reports)
    fraud_ring_section = _build_fraud_ring_section(tool_calls, user_id)
    if fraud_ring_section:
        response = response.strip() + "\n" + fraud_ring_section
    
    # Add footer with metadata
    footer = f"""

---
*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  
*Investigation ID: {state.get('investigation_id', 'N/A')}*  
*User ID: {user_id or 'N/A'}*  
*AI Agent Iterations: {state.get('agent_iterations', 0)}*  
*Tool Calls Made: {len(tool_calls)}*
"""
    
    return response.strip() + footer


def _generate_fallback_report(state: Dict[str, Any]) -> str:
    """Generate a structured report when LLM fails (uses KV-sourced evidence)."""
    alert = state.get("alert_evidence") or {}
    initial = state.get("initial_evidence") or {}
    assessment = state.get("final_assessment") or {}
    tool_calls = state.get("tool_calls") or []
    
    profile = initial.get("profile", {})
    metrics = initial.get("account_metrics", {})
    accounts = initial.get("accounts", {})    # dict: account_id -> {...}
    devices = initial.get("devices", {})      # dict: device_id -> {...}
    account_facts = initial.get("account_facts", {})
    device_facts = initial.get("device_facts", {})
    
    report = f"""# Fraud Investigation Report

## Executive Summary

Investigation of user **{profile.get('name', 'Unknown')}** ({state.get('user_id', 'N/A')}) 
triggered by **{alert.get('trigger_rule', alert.get('trigger_type', 'ML Detection'))}** detection.

**AI Assessment**: {assessment.get('typology', 'Unknown').upper()} - {assessment.get('risk_level', 'Unknown').upper()} RISK ({assessment.get('risk_score', 0)}/100)

**Recommended Action**: {assessment.get('decision', 'pending').replace('_', ' ').title()}

---

## Key Risk Factors

"""
    
    # Add risk factors from KV data
    risk_factors = []
    if metrics.get("has_flagged_account"):
        risk_factors.append(f"- **Flagged accounts detected**: {metrics.get('flagged_account_count', 0)} accounts")
    if metrics.get("has_flagged_device"):
        risk_factors.append(f"- **Flagged devices detected**: {metrics.get('flagged_device_count', 0)} devices")
    if metrics.get("max_velocity_zscore", 0) > 2.0:
        risk_factors.append(f"- **Velocity anomaly**: z-score {metrics.get('max_velocity_zscore', 0)}")
    if metrics.get("max_amount_zscore", 0) > 2.0:
        risk_factors.append(f"- **Amount anomaly**: z-score {metrics.get('max_amount_zscore', 0)}")
    if metrics.get("max_new_recipient_ratio", 0) > 0.5:
        risk_factors.append(f"- **High new recipient ratio**: {metrics.get('max_new_recipient_ratio', 0):.0%}")
    if metrics.get("max_shared_accounts_on_device", 0) > 2:
        risk_factors.append(f"- **Shared device**: {metrics.get('max_shared_accounts_on_device', 0)} accounts on same device")
    if metrics.get("account_age_days", 365) < 30:
        risk_factors.append(f"- **New account** ({metrics.get('account_age_days', 0)} days old)")
    if alert.get("original_score", 0) >= 70:
        risk_factors.append(f"- **High initial risk score**: {alert.get('original_score', 0)}")
    
    if risk_factors:
        report += "\n".join(risk_factors)
    else:
        report += "- No critical risk factors identified"
    
    # Build account details
    account_lines = []
    for aid, acc in accounts.items():
        flag = " [FLAGGED]" if acc.get("is_fraud") else ""
        account_lines.append(f"| {aid} | {acc.get('type', 'unknown')} | ${acc.get('balance', 0):,.2f} | {acc.get('status', 'active')}{flag} |")
    account_table = "\n".join(account_lines) if account_lines else "| No accounts found | | | |"
    
    # Build device details
    device_lines = []
    for did, dev in devices.items():
        flag = " [FLAGGED]" if dev.get("is_fraud") else ""
        device_lines.append(f"| {did} | {dev.get('type', 'unknown')} | {dev.get('os', 'unknown')} | {dev.get('browser', 'unknown')}{flag} |")
    device_table = "\n".join(device_lines) if device_lines else "| No devices found | | | |"
    
    # Build fraud ring section from tool calls
    fraud_ring_section = _build_fraud_ring_section(tool_calls, state.get("user_id", ""))
    
    report += f"""

---

## AI Investigation Summary

The AI agent analyzed this case in **{state.get('agent_iterations', 0)} iterations**, 
making **{len(tool_calls)} tool calls** to gather evidence.

### Assessment Details
| Attribute | Value |
|-----------|-------|
| Typology | {assessment.get('typology', 'Unknown')} |
| Risk Level | {assessment.get('risk_level', 'Unknown')} |
| Risk Score | {assessment.get('risk_score', 0)}/100 |
| Decision | {assessment.get('decision', 'pending')} |

### Agent's Reasoning
{assessment.get('reasoning', 'No reasoning provided')}

---

## Evidence Summary

### User Profile
| Attribute | Value |
|-----------|-------|
| Name | {profile.get('name', 'Unknown')} |
| Location | {profile.get('location', 'Unknown')} |
| Occupation | {profile.get('occupation', 'Unknown')} |
| Account Age | {metrics.get('account_age_days', 0)} days |
| KYC Status | {metrics.get('kyc_completeness', 'Unknown')} |
| Risk Score | {metrics.get('profile_risk_score', 0)} |

### Accounts ({len(accounts)} total, balance: ${metrics.get('total_balance', 0):,.2f})
| Account ID | Type | Balance | Status |
|------------|------|---------|--------|
{account_table}

### Devices ({len(devices)} total)
| Device ID | Type | OS | Browser |
|-----------|------|----|---------|
{device_table}

### Risk Signals Summary
| Metric | Value |
|--------|-------|
| Max Velocity Z-Score | {metrics.get('max_velocity_zscore', 0)} |
| Max Amount Z-Score | {metrics.get('max_amount_zscore', 0)} |
| Max New Recipient Ratio | {metrics.get('max_new_recipient_ratio', 0)} |
| Max Shared Accounts on Device | {metrics.get('max_shared_accounts_on_device', 0)} |
{fraud_ring_section}
---

## Recommendation

**Recommended Action**: {assessment.get('decision', 'pending').replace('_', ' ').title()}

Based on the {assessment.get('risk_level', 'unknown')} risk level and {assessment.get('typology', 'unknown')} classification, 
{'immediate action is recommended' if assessment.get('risk_score', 0) >= 70 else 'continued monitoring is advised'}.

---

## Next Steps for Analyst

1. Review the tool call findings above
2. Verify the AI assessment against manual review
3. {'Take immediate action on high-risk items' if assessment.get('risk_score', 0) >= 70 else 'Document findings for ongoing monitoring'}
4. Update case status based on final decision
5. Complete compliance documentation

---
*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  
*Investigation ID: {state.get('investigation_id', 'N/A')}*  
*User ID: {state.get('user_id', 'N/A')}*  
*AI Agent Iterations: {state.get('agent_iterations', 0)}*  
*Tool Calls Made: {len(tool_calls)}*
"""
    
    return report
