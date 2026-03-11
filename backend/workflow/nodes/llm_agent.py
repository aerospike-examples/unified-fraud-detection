"""
LLM Reasoning Agent Node

ReAct-style agent that uses tools to gather evidence and make decisions.
The LLM decides what data to collect, how many hops to traverse, etc.
Loops until it calls submit_assessment or hits safety limits.
"""

import json
import re
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import logging

from openai import AsyncOpenAI

from services.llm_config import get_llm_config
from workflow.state import InvestigationState, AgentMessage, ToolCall, FinalAssessment, TraceEvent
from workflow.tools.investigation_tools import InvestigationTools
from workflow.metrics import get_collector

logger = logging.getLogger('investigation.llm_agent')

# Safety limits (generous — let the agent decide when it has enough evidence)
MAX_ITERATIONS = 50
MAX_TOOL_CALLS = 40
TIMEOUT_SECONDS = 600


async def llm_agent_node(
    state: InvestigationState,
    aerospike_service: Any,
    graph_service: Any
) -> Dict[str, Any]:
    """
    LLM reasoning agent that iteratively gathers evidence and makes decisions.
    
    The agent:
    1. Receives initial evidence from data_collection
    2. Uses available tools to gather more data as needed
    3. Calls submit_assessment when it has enough evidence
    
    Args:
        state: Current investigation state
        aerospike_service: Aerospike KV service
        graph_service: Aerospike Graph service
        
    Returns:
        Updated state with final assessment
    """
    from services.investigation_progress import update_progress

    user_id = state["user_id"]
    investigation_id = state["investigation_id"]
    node_name = "llm_agent"
    
    # Get metrics collector for this investigation
    metrics = get_collector(investigation_id)
    
    logger.info(f"[{node_name}] Starting LLM agent for user {user_id}")
    
    # Initialize tools with metrics collector
    tools = InvestigationTools(aerospike_service, graph_service, user_id, metrics)
    
    # Get initial evidence
    initial_evidence = state.get("initial_evidence", {})
    alert_evidence = state.get("alert_evidence", {})
    
    # Build initial context for LLM
    evidence_summary = _build_evidence_summary(initial_evidence, alert_evidence)
    
    # Agent loop state
    agent_messages: List[AgentMessage] = []
    tool_calls: List[ToolCall] = []
    accumulated_evidence: Dict[str, Any] = {"initial": evidence_summary}
    trace_events: List[TraceEvent] = []
    
    # Emit start
    trace_events.append(TraceEvent(
        type="node_start",
        node=node_name,
        timestamp=datetime.now().isoformat(),
        data={"user_id": user_id, "max_iterations": MAX_ITERATIONS}
    ))
    
    iteration = 0
    final_assessment = None
    error_count = 0
    
    def _publish_progress():
        """Push current agent state into the shared progress store."""
        serializable_tool_calls = []
        for tc in tool_calls:
            serializable_tool_calls.append({
                "tool": tc.get("tool", ""),
                "params": tc.get("params", {}),
                "result": tc.get("result"),
                "timestamp": tc.get("timestamp", ""),
                "iteration": tc.get("iteration", 0),
            })
        update_progress(investigation_id, {
            "currentNode": node_name,
            "currentPhase": "reasoning",
            "agentIterations": iteration,
            "toolCalls": serializable_tool_calls,
        })
    
    try:
        while iteration < MAX_ITERATIONS:
            iteration += 1
            
            logger.info(f"[{node_name}] Iteration {iteration}/{MAX_ITERATIONS}")
            
            # Emit iteration event
            trace_events.append(TraceEvent(
                type="agent_iteration",
                node=node_name,
                timestamp=datetime.now().isoformat(),
                data={"iteration": iteration, "tool_calls_so_far": len(tool_calls)}
            ))
            
            # Build prompt for this iteration
            prompt = _build_agent_prompt(
                evidence_summary,
                accumulated_evidence,
                agent_messages,
                tool_calls,
                iteration
            )
            
            # Call LLM (supports both Gemini and Ollama via env config)
            try:
                llm_start = time.time()
                llm_response = await _call_llm(prompt)
                llm_duration = (time.time() - llm_start) * 1000
                
                # Track LLM call metrics (estimate tokens based on char length)
                tokens_in = len(prompt) // 4  # rough estimate
                tokens_out = len(llm_response) // 4 if llm_response else 0
                metrics.track_llm_call(llm_duration, tokens_in, tokens_out)
                
                # Record message
                agent_messages.append(AgentMessage(
                    role="assistant",
                    content=llm_response,
                    timestamp=datetime.now().isoformat()
                ))
                
                # Emit thinking event
                trace_events.append(TraceEvent(
                    type="agent_thinking",
                    node=node_name,
                    timestamp=datetime.now().isoformat(),
                    data={
                        "iteration": iteration,
                        "response_preview": llm_response[:200] if llm_response else "",
                        "llm_duration_ms": round(llm_duration, 2)
                    }
                ))
                
            except Exception as e:
                logger.error(f"[{node_name}] LLM call failed: {e}")
                error_count += 1
                
                if error_count >= 3:
                    logger.warning(f"[{node_name}] Falling back to deterministic assessment")
                    final_assessment = _deterministic_assessment(initial_evidence, alert_evidence)
                    break
                
                continue
            
            # Parse tool call from response
            tool_name, tool_params = _parse_tool_call(llm_response)
            
            if not tool_name:
                logger.warning(f"[{node_name}] No valid tool call in response, retrying")
                error_count += 1
                if error_count >= 3:
                    final_assessment = _deterministic_assessment(initial_evidence, alert_evidence)
                    break
                continue
            
            error_count = 0  # Reset on successful parse
            
            # Check if this is the exit tool
            if tool_name == "submit_assessment":
                logger.info(f"[{node_name}] Agent submitted assessment")
                
                final_assessment = FinalAssessment(
                    typology=tool_params.get("typology", "unknown"),
                    risk_level=tool_params.get("risk_level", "medium"),
                    risk_score=tool_params.get("risk_score", 50),
                    decision=tool_params.get("decision", "allow_monitor"),
                    reasoning=tool_params.get("reasoning", "Assessment submitted by agent"),
                    iteration=iteration,
                    tool_calls_made=len(tool_calls)
                )
                
                # Emit assessment event
                trace_events.append(TraceEvent(
                    type="assessment",
                    node=node_name,
                    timestamp=datetime.now().isoformat(),
                    data={
                        "typology": final_assessment["typology"],
                        "risk_level": final_assessment["risk_level"],
                        "risk_score": final_assessment["risk_score"],
                        "decision": final_assessment["decision"]
                    }
                ))
                
                _publish_progress()
                break
            
            # Check tool call limit
            if len(tool_calls) >= MAX_TOOL_CALLS:
                logger.warning(f"[{node_name}] Hit tool call limit, forcing assessment")
                final_assessment = _deterministic_assessment(
                    initial_evidence, 
                    alert_evidence,
                    accumulated_evidence
                )
                break
            
            # Execute the tool
            logger.info(f"[{node_name}] Executing tool: {tool_name}({tool_params})")
            
            tool_result = tools.execute_tool(tool_name, tool_params)
            
            # Record tool call
            tool_call = ToolCall(
                tool=tool_name,
                params=tool_params,
                result=tool_result,
                timestamp=datetime.now().isoformat(),
                iteration=iteration
            )
            tool_calls.append(tool_call)
            
            # Add result to accumulated evidence
            accumulated_evidence[f"{tool_name}_{len(tool_calls)}"] = tool_result
            
            # Emit tool event
            trace_events.append(TraceEvent(
                type="tool_call",
                node=node_name,
                timestamp=datetime.now().isoformat(),
                data={
                    "tool": tool_name,
                    "params": tool_params,
                    "result_summary": tools.tool_calls[-1].get("result_summary", ""),
                    "iteration": iteration
                }
            ))
            
            # Add tool result to messages for next iteration
            agent_messages.append(AgentMessage(
                role="tool",
                content=json.dumps(tool_result, default=str),
                timestamp=datetime.now().isoformat(),
                tool_name=tool_name
            ))
            
            # Publish intermediate progress after each iteration
            _publish_progress()
        
        # If we hit max iterations without assessment
        if not final_assessment:
            logger.warning(f"[{node_name}] Max iterations reached, using deterministic assessment")
            final_assessment = _deterministic_assessment(
                initial_evidence, 
                alert_evidence,
                accumulated_evidence
            )
        
        # Emit complete
        trace_events.append(TraceEvent(
            type="node_complete",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={
                "iterations": iteration,
                "tool_calls": len(tool_calls),
                "typology": final_assessment["typology"],
                "risk_level": final_assessment["risk_level"]
            }
        ))
        
        logger.info(
            f"[{node_name}] Complete - {iteration} iterations, "
            f"{len(tool_calls)} tool calls, "
            f"typology: {final_assessment['typology']}"
        )
        
        return {
            "final_assessment": final_assessment,
            "agent_messages": agent_messages,
            "tool_calls": tool_calls,
            "agent_iterations": iteration,
            "current_node": "report_generation",
            "current_phase": "report",
            "trace_events": trace_events
        }
        
    except Exception as e:
        logger.error(f"[{node_name}] Agent error: {e}")
        
        trace_events.append(TraceEvent(
            type="error",
            node=node_name,
            timestamp=datetime.now().isoformat(),
            data={"error": str(e)}
        ))
        
        # Return with deterministic assessment on error
        return {
            "final_assessment": _deterministic_assessment(initial_evidence, alert_evidence),
            "agent_messages": agent_messages,
            "tool_calls": tool_calls,
            "agent_iterations": iteration,
            "current_node": "report_generation",
            "current_phase": "report",
            "error_message": str(e),
            "trace_events": trace_events
        }


def _build_evidence_summary(initial: Dict[str, Any], alert: Dict[str, Any]) -> str:
    """Build a text summary of initial KV-sourced evidence for LLM."""
    
    profile = initial.get("profile", {})
    accounts = initial.get("accounts", {})        # dict: account_id -> {...}
    devices = initial.get("devices", {})           # dict: device_id -> {...}
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
    
    # Add account details with fraud flags
    for aid, acc in accounts.items():
        flag = " [FLAGGED FRAUD]" if acc.get("is_fraud") else ""
        lines.append(
            f"  - {aid}: type={acc.get('type', 'unknown')}, "
            f"balance=${acc.get('balance', 0):,.2f}, "
            f"status={acc.get('status', 'active')}{flag}"
        )
    
    # Add account risk features if available
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
    
    # Add device details with fraud flags
    for did, dev in devices.items():
        flag = " [FLAGGED FRAUD]" if dev.get("is_fraud") else ""
        lines.append(
            f"  - {did}: type={dev.get('type', 'unknown')}, "
            f"os={dev.get('os', 'unknown')}, "
            f"browser={dev.get('browser', 'unknown')}{flag}"
        )
    
    # Add device risk features if available
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


def _build_agent_prompt(
    evidence_summary: str,
    accumulated_evidence: Dict[str, Any],
    messages: List[AgentMessage],
    tool_calls: List[ToolCall],
    iteration: int
) -> str:
    """Build the prompt for the LLM agent with a fraud-analyst system prompt."""
    
    tool_descriptions = InvestigationTools.get_tool_descriptions()
    
    # Build conversation history
    history = ""
    for msg in messages:  # Full history — LangGraph checkpointer persists state
        if msg["role"] == "assistant":
            history += f"\nAssistant: {msg['content'][:500]}"
        elif msg["role"] == "tool":
            tool_name = msg.get("tool_name", "unknown")
            history += f"\nTool Result ({tool_name}): {msg['content'][:1200]}"
    
    # Minimal status hint — let the agent decide its own investigation plan
    action_hint = (
        "You are a thorough investigator. Take your time. "
        "Investigate ALL suspicious counterparties, not just the first one. "
        "Check transaction patterns, profiles, and risk features before concluding. "
        "Only call submit_assessment when you are confident in your analysis."
    )
    if iteration >= MAX_ITERATIONS - 1:
        action_hint = "You are at the iteration limit. Please submit your final assessment now using submit_assessment."
    
    prompt = f"""You are a SENIOR FRAUD ANALYST investigating a flagged account. You are thorough, methodical, and leave no stone unturned.

## YOUR INVESTIGATION APPROACH
You decide what to investigate and when you have enough evidence. Be thorough:

1. START by reviewing the profile, accounts, devices, and pre-computed risk features below.
2. PULL transaction history for each suspicious account using get_account_transactions.
3. INVESTIGATE ALL counterparties you find suspicious — not just the first one:
   - get_counterparty_profile: who are they? new account? high risk? flagged?
   - get_counterparty_transactions: what's their behavior? receiving from many sources? rapid transfers? mule pattern?
4. CHECK risk features for accounts with anomalies using get_account_risk_features.
5. IF you suspect coordinated fraud, use detect_fraud_ring to analyze the network graph.
6. ONLY submit your assessment when you have thoroughly investigated. A good investigation covers:
   - Transaction patterns of each account
   - Profiles of key counterparties (especially those with high volume or suspicious patterns)
   - Risk features of anomalous accounts
   - Network analysis if fraud ring suspected

{evidence_summary}

## TOOLS AVAILABLE
{tool_descriptions}

## CURRENT STATUS
- Iteration: {iteration}/{MAX_ITERATIONS}
- Tool calls made: {len(tool_calls)}/{MAX_TOOL_CALLS}
- Note: {action_hint}

## CONVERSATION HISTORY
{history if history else "(First iteration - no history yet)"}

## RESPONSE FORMAT
You MUST respond with ONLY a valid JSON object. No other text, no markdown, no explanations.

To call a tool:
{{"tool": "get_account_transactions", "params": {{"account_id": "A527001", "days": 30}}}}

To investigate a counterparty:
{{"tool": "get_counterparty_profile", "params": {{"user_id": "U045"}}}}

To get counterparty transaction behavior:
{{"tool": "get_counterparty_transactions", "params": {{"user_id": "U045", "days": 30}}}}

To check for fraud rings in the graph:
{{"tool": "detect_fraud_ring", "params": {{"hops": 2}}}}

To submit your final assessment (only when you have enough evidence):
{{"tool": "submit_assessment", "params": {{"typology": "money_mule", "risk_level": "high", "risk_score": 85, "decision": "temporary_freeze", "reasoning": "Account A527001 shows rapid outbound transfers ($45K in 7 days, velocity z-score 3.2) to 5 new recipients. Counterparty U045 is a 3-day-old account receiving from 12 sources - classic mule pattern. Counterparty U112 has risk score 67 and 4 flagged transactions. Fraud ring detection negative."}}}}

Valid values:
- typology: account_takeover, money_mule, synthetic_identity, promo_abuse, friendly_fraud, card_testing, fraud_ring, suspicious_activity, legitimate
- risk_level: low, medium, high, critical
- risk_score: integer 0-100
- decision: allow_monitor, step_up_auth, temporary_freeze, full_block, escalate_compliance

YOUR JSON RESPONSE:"""

    return prompt


async def _call_llm(prompt: str) -> str:
    """Call the configured LLM provider via the OpenAI-compatible API."""
    config = get_llm_config()

    if not config.get("api_key"):
        raise ValueError("LLM API key is not configured. Set it via the Agent Setup tab or GEMINI_API_KEY env var.")

    logger.info(f"[LLM] Calling {config['provider']} model={config['model']}")
    start = time.time()

    try:
        client = AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            timeout=300.0,
        )
        response = await client.chat.completions.create(
            model=config["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
        )
        elapsed = time.time() - start
        logger.info(f"[LLM] {config['provider']} responded in {elapsed:.1f}s")
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"[LLM] {config['provider']} error: {e}")
        raise


def _parse_tool_call(response: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Parse tool call from LLM response with improved handling."""
    
    if not response:
        return None, {}
    
    # Clean response - remove markdown code blocks if present
    cleaned = response.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    
    # Strategy 1: Try direct JSON parse (if response is just JSON)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and "tool" in data:
            logger.debug(f"[Parse] Strategy 1 success: {data.get('tool')}")
            return data.get("tool"), data.get("params", {})
    except Exception:
        pass
    
    # Strategy 2: Find JSON object with balanced braces
    try:
        start_idx = cleaned.find('{')
        if start_idx >= 0:
            depth = 0
            end_idx = start_idx
            for i, char in enumerate(cleaned[start_idx:], start_idx):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
            
            if end_idx > start_idx:
                json_str = cleaned[start_idx:end_idx]
                data = json.loads(json_str)
                if isinstance(data, dict) and "tool" in data:
                    logger.debug(f"[Parse] Strategy 2 success: {data.get('tool')}")
                    return data.get("tool"), data.get("params", {})
    except Exception:
        pass
    
    # Strategy 3: Regex for tool pattern with nested params
    try:
        # Match {"tool": "...", "params": {...}}
        pattern = r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"params"\s*:\s*(\{[^}]*\}|\{\})\s*\}'
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            tool_name = match.group(1)
            params_str = match.group(2)
            try:
                params = json.loads(params_str)
            except:
                params = {}
            logger.debug(f"[Parse] Strategy 3 success: {tool_name}")
            return tool_name, params
    except Exception:
        pass
    
    # Strategy 4: Look for tool name without params
    try:
        pattern = r'"tool"\s*:\s*"([^"]+)"'
        match = re.search(pattern, cleaned)
        if match:
            tool_name = match.group(1)
            # Try to extract params separately
            params = {}
            params_match = re.search(r'"params"\s*:\s*(\{[^}]*\})', cleaned)
            if params_match:
                try:
                    params = json.loads(params_match.group(1))
                except:
                    pass
            logger.debug(f"[Parse] Strategy 4 success: {tool_name}")
            return tool_name, params
    except Exception:
        pass
    
    # Strategy 5: Fix common JSON issues and retry
    try:
        # Replace single quotes with double quotes
        fixed = cleaned.replace("'", '"')
        # Fix unquoted keys
        fixed = re.sub(r'(\{|\,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1 "\2":', fixed)
        
        start_idx = fixed.find('{')
        end_idx = fixed.rfind('}') + 1
        if start_idx >= 0 and end_idx > start_idx:
            data = json.loads(fixed[start_idx:end_idx])
            if isinstance(data, dict) and "tool" in data:
                logger.debug(f"[Parse] Strategy 5 success: {data.get('tool')}")
                return data.get("tool"), data.get("params", {})
    except Exception:
        pass
    
    logger.warning(f"[Parse] Failed to extract tool call from: {cleaned[:200]}...")
    return None, {}


def _deterministic_assessment(
    initial: Dict[str, Any],
    alert: Dict[str, Any],
    accumulated: Dict[str, Any] = None
) -> FinalAssessment:
    """
    Fallback deterministic assessment when LLM fails.
    Uses rule-based logic based on KV-sourced evidence.
    """
    
    metrics = initial.get("account_metrics", {})
    account_facts = initial.get("account_facts", {})
    device_facts = initial.get("device_facts", {})
    trigger_type = alert.get("trigger_type", "unknown")
    original_score = alert.get("original_score", 50)
    
    # Calculate risk score starting from alert score
    risk_score = original_score
    
    # Adjust based on flagged accounts
    if metrics.get("has_flagged_account"):
        risk_score += 20
    
    # Adjust based on flagged devices
    if metrics.get("has_flagged_device"):
        risk_score += 15
    
    # Adjust based on velocity anomalies
    max_velocity = metrics.get("max_velocity_zscore", 0)
    if max_velocity > 3.0:
        risk_score += 15
    elif max_velocity > 2.0:
        risk_score += 10
    
    # Adjust based on amount anomalies
    max_amount_z = metrics.get("max_amount_zscore", 0)
    if max_amount_z > 3.0:
        risk_score += 15
    elif max_amount_z > 2.0:
        risk_score += 10
    
    # Adjust based on new recipient ratio
    new_recip = metrics.get("max_new_recipient_ratio", 0)
    if new_recip > 0.7:
        risk_score += 10
    
    # Adjust based on shared devices from device_facts
    max_shared = metrics.get("max_shared_accounts_on_device", 0)
    if max_shared > 3:
        risk_score += 15
    elif max_shared > 1:
        risk_score += 5
    
    # Cap at 100
    risk_score = min(100, risk_score)
    
    # Determine risk level
    if risk_score >= 80:
        risk_level = "critical"
    elif risk_score >= 60:
        risk_level = "high"
    elif risk_score >= 40:
        risk_level = "medium"
    else:
        risk_level = "low"
    
    # Determine typology based on strongest signal (evidence-based, not trigger codes)
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
    
    # Determine decision
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
        tool_calls_made=0
    )
