"""
Report Generation Node

Uses LLM to generate a comprehensive markdown report of the investigation.
Updated for agentic workflow - uses FinalAssessment from LLM agent.

Data Source: Ollama (Mistral)
"""

from datetime import datetime
from typing import Dict, Any
import logging
import os
import httpx

from workflow.state import InvestigationState, TraceEvent

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

CRITICAL FORMATTING RULES:
- Use proper Markdown with BLANK LINES between every section, heading, and paragraph.
- Each ## heading MUST be on its own line with a blank line before and after it.
- Each bullet point (- item) must be on its own line.
- Do NOT put multiple sections on the same line.
- Do NOT include an investigation timeline or tool call log.
- Use clear, professional language suitable for compliance review.
"""


async def report_generation_node(
    state: InvestigationState,
    ollama_client: Any = None  # Not used, we create our own client
) -> Dict[str, Any]:
    """
    Generate investigation report using LLM.
    
    Args:
        state: Current investigation state with FinalAssessment
        ollama_client: (Deprecated) HTTP client for Ollama
        
    Returns:
        Updated state with report markdown
    """
    user_id = state["user_id"]
    node_name = "report_generation"
    
    logger.info(f"[{node_name}] Starting report generation for user {user_id}")
    
    trace_events = []
    
    # Emit start event
    trace_events.append(TraceEvent(
        type="node_start",
        node=node_name,
        timestamp=datetime.now().isoformat(),
        data={"user_id": user_id, "llm_powered": True}
    ))
    
    try:
        # Extract evidence from new state structure
        alert = state.get("alert_evidence") or {}
        initial = state.get("initial_evidence") or {}
        assessment = state.get("final_assessment") or {}
        tool_calls = state.get("tool_calls") or []
        
        profile = initial.get("profile", {})
        metrics = initial.get("account_metrics", {})
        accounts = initial.get("accounts", {})    # dict: account_id -> {...}
        devices = initial.get("devices", {})      # dict: device_id -> {...}
        
        prompt = REPORT_PROMPT_TEMPLATE.format(
            investigation_id=state.get("investigation_id", "N/A"),
            user_id=user_id,
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
        
        # Call LLM (supports both Gemini and Ollama via env config)
        response = await _call_llm(prompt)
        
        # Clean up the response
        report_markdown = _clean_report(response, state)
        
        # Emit complete event
        trace_events.append(TraceEvent(
            type="node_complete",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={"status": "success", "report_length": len(report_markdown)}
        ))
        
        logger.info(f"[{node_name}] Report generation complete - {len(report_markdown)} characters")
        
        return {
            "report_markdown": report_markdown,
            "current_phase": "report",
            "current_node": "complete",
            "workflow_status": "completed",
            "trace_events": trace_events
        }
        
    except Exception as e:
        logger.error(f"[{node_name}] Error during report generation: {e}")
        
        trace_events.append(TraceEvent(
            type="error",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={"error": str(e)}
        ))
        
        # Generate fallback report
        fallback_report = _generate_fallback_report(state)
        
        return {
            "report_markdown": fallback_report,
            "current_phase": "report",
            "current_node": "complete",
            "workflow_status": "completed",
            "error_message": str(e),
            "trace_events": trace_events
        }


async def _call_llm(prompt: str) -> str:
    """Call the configured LLM provider using shared config from llm_agent."""
    from workflow.nodes.llm_agent import _get_llm_config
    
    cfg = _get_llm_config()
    provider = cfg["provider"]
    
    if provider == "gemini":
        api_key = cfg["gemini_api_key"]
        model = cfg["gemini_model"]
        if not api_key:
            raise ValueError("Gemini API key is not configured.")
        
        logger.info(f"[Report] Calling Gemini API with model {model}")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                url,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1500}
                }
            )
            response.raise_for_status()
            result = response.json()
            candidates = result.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return ""
    else:
        api_key = cfg["mistral_api_key"]
        model = cfg["mistral_model"]
        if not api_key:
            raise ValueError("Mistral API key is not configured.")
        
        reasoning_effort = cfg.get("mistral_reasoning_effort", "none")
        logger.info(f"[Report] Calling Mistral API with model {model} (reasoning={reasoning_effort})")
        
        is_magistral = model.startswith("magistral")
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 1500,
        }
        if not is_magistral and reasoning_effort == "high":
            body["reasoning_effort"] = "high"
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json=body,
            )
            response.raise_for_status()
            result = response.json()
            choices = result.get("choices", [])
            if not choices:
                return ""
            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                return "".join(c.get("text", "") for c in content if c.get("type") == "text")
            return content if isinstance(content, str) else ""


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


def _clean_report(response: str, state: InvestigationState) -> str:
    """Clean and format the generated report.
    
    Aggressively ensures proper markdown structure even when the LLM
    returns everything on a single line or with minimal newlines.
    """
    import re as _re
    
    initial = state.get("initial_evidence") or {}
    profile = initial.get("profile", {})
    tool_calls = state.get("tool_calls") or []
    user_id = state.get("user_id", "")
    
    # Strip markdown code fences that some LLMs (Mistral) wrap their response in
    response = response.strip()
    if response.startswith("```"):
        first_newline = response.find('\n')
        if first_newline != -1:
            response = response[first_newline + 1:]
        if response.rstrip().endswith("```"):
            response = response.rstrip()[:-3]
        response = response.strip()
    
    newline_count = response.count('\n')
    logger.info(f"[Report] Raw LLM output: {len(response)} chars, {newline_count} newlines")
    
    # Normalize em-dashes and en-dashes used as bullet markers
    response = _re.sub(r'[—–]\s*\*\*', '- **', response)
    response = _re.sub(r'[—–]\s+([A-Z])', r'- \1', response)
    
    # Remove stray lone `#` separators before actual headings (e.g. "# # Heading" → "## Heading")
    response = _re.sub(r'(?:^|\n)\s*#\s*\n?\s*(#{1,4}\s)', r'\n\n\1', response)
    response = _re.sub(r'(\S)\s+#\s+(#{1,4}\s)', r'\1\n\n\2', response)
    
    # Step 1: Ensure newlines BEFORE heading markers (# to ####)
    response = _re.sub(r'([^\n])(#{1,4}\s)', r'\1\n\n\2', response)
    
    # Step 2: Ensure newlines BEFORE bullet points (- text or * text)
    response = _re.sub(r'([^\n])(- )', r'\1\n\2', response)
    response = _re.sub(r'([^\n])(\* )', r'\1\n\2', response)
    
    # Step 3: Ensure newlines BEFORE and AFTER horizontal rules (--- or more)
    response = _re.sub(r'([^\n])(---+)', r'\1\n\n\2', response)
    response = _re.sub(r'(---+)([^\n])', r'\1\n\n\2', response)
    
    # Step 4: Split heading lines that have body text appended
    # e.g. "## Executive Summary This investigation found..." should become
    # "## Executive Summary\n\nThis investigation found..."
    def _split_heading_line(line: str) -> str:
        m = _re.match(r'^(#{1,4}\s+.{3,80}?(?:Summary|Factors|Analysis|Recommendation|Steps|Evidence|Assessment|Report|Rationale|Conclusion|Details|Information|Overview|Findings|Profile))\s+([A-Z(])', line)
        if m:
            return m.group(1) + '\n\n' + m.group(2) + line[m.end():]
        m2 = _re.match(r'^(#{1,4}\s+\S+(?:\s+\S+){0,6})\s{2,}(.+)', line)
        if m2:
            return m2.group(1) + '\n\n' + m2.group(2)
        return line
    
    lines = response.split('\n')
    lines = [_split_heading_line(line) for line in lines]
    response = '\n'.join(lines)
    
    # Step 5: Ensure blank lines AFTER headings (heading line followed immediately by text)
    response = _re.sub(r'(^#{1,4}\s+[^\n]+)\n([^#\-\*\n])', r'\1\n\n\2', response, flags=_re.MULTILINE)
    
    # Step 6: Collapse 3+ consecutive newlines into 2
    response = _re.sub(r'\n{3,}', '\n\n', response)
    
    # Step 7: Add header if not present
    if not response.strip().startswith("#"):
        user_name = profile.get("name", "Unknown")
        response = f"# Fraud Investigation Report\n## User: {user_name}\n\n{response}"
    
    final_newlines = response.count('\n')
    logger.info(f"[Report] After cleanup: {len(response)} chars, {final_newlines} newlines (was {newline_count})")
    
    # Append fraud ring section
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


def _generate_fallback_report(state: InvestigationState) -> str:
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
