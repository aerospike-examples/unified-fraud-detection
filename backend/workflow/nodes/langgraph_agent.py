"""
Shared ReAct tool-loop helpers for the LangGraph investigation engine.

Uses JSON tool-call parsing (compatible with Gemini and Ollama) and delegates
execution to :class:`workflow.tools.investigation_tools.InvestigationTools`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from workflow.assessment import build_evidence_summary, deterministic_assessment
from workflow.llm import LLMConfig
from workflow.metrics import MetricsCollector
from workflow.state import AgentMessage, FinalAssessment, ToolCall, TraceEvent
from workflow.tools.investigation_tools import InvestigationTools

logger = logging.getLogger("investigation.langgraph_agent")

MAX_ITERATIONS = 50
MAX_TOOL_CALLS = 40


def _trace(node: str, type_: str, data: Dict[str, Any]) -> TraceEvent:
    return TraceEvent(
        type=type_,
        node=node,
        timestamp=datetime.now().isoformat(),
        data=data,
    )


def _call_ollama(base_url: str, model: str, prompt: str) -> str:
    with httpx.Client(timeout=600.0) as client:
        response = client.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 800},
            },
        )
        response.raise_for_status()
        return response.json().get("response", "")


def _call_gemini(api_key: str, model: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    with httpx.Client(timeout=300.0) as client:
        response = client.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1500},
            },
        )
        response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "")
    return ""


def call_llm(prompt: str, llm_config: LLMConfig) -> str:
    """Invoke the configured LLM synchronously."""
    if llm_config.provider == "ollama":
        return _call_ollama(llm_config.ollama_base_url, llm_config.ollama_model, prompt)
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is required for Gemini provider")
    return _call_gemini(api_key, llm_config.model, prompt)


def parse_tool_call(response: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Parse a JSON tool call from an LLM response."""
    if not response:
        return None, {}

    cleaned = response.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    for strategy in (
        lambda: json.loads(cleaned),
        lambda: _balanced_brace_json(cleaned),
    ):
        try:
            data = strategy()
            if isinstance(data, dict) and "tool" in data:
                return data.get("tool"), data.get("params", {})
        except Exception:
            pass

    match = re.search(r'\{\s*"tool"\s*:\s*"([^"]+)"', cleaned)
    if match:
        tool_name = match.group(1)
        params: Dict[str, Any] = {}
        params_match = re.search(r'"params"\s*:\s*(\{.*\})', cleaned, re.DOTALL)
        if params_match:
            try:
                params = json.loads(params_match.group(1))
            except Exception:
                params = {}
        return tool_name, params

    logger.warning("[Parse] Failed to extract tool call from: %s...", cleaned[:200])
    return None, {}


def _balanced_brace_json(text: str) -> dict:
    start_idx = text.find("{")
    if start_idx < 0:
        raise ValueError("no json")
    depth = 0
    end_idx = start_idx
    for i, char in enumerate(text[start_idx:], start_idx):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break
    return json.loads(text[start_idx:end_idx])


def _history_block(messages: List[AgentMessage]) -> str:
    lines = []
    for msg in messages:
        if msg["role"] == "assistant":
            lines.append(f"\nAssistant: {msg['content'][:500]}")
        elif msg["role"] == "tool":
            lines.append(f"\nTool Result ({msg.get('tool_name', 'unknown')}): {msg['content'][:1200]}")
    return "".join(lines) if lines else "(First iteration - no history yet)"


def build_json_tool_prompt(
    system: str,
    evidence: str,
    tool_names: List[str],
    messages: List[AgentMessage],
    tool_calls: List[ToolCall],
    iteration: int,
    max_iterations: int,
    max_tool_calls: int,
    submit_hint: str = "",
) -> str:
    """Build a ReAct prompt that expects a single JSON tool call."""
    schemas = InvestigationTools.TOOL_SCHEMAS
    allowed = {s["name"] for s in schemas if s["name"] in tool_names or s["name"] == "submit_assessment"}
    tool_lines = []
    for schema in schemas:
        if schema["name"] not in allowed:
            continue
        params = ", ".join(f'"{k}": ...' for k in schema.get("parameters", {}))
        tool_lines.append(f'- {schema["name"]}: {schema["description"]} params {{{params}}}')

    hint = submit_hint or (
        "Respond with ONLY a JSON object: "
        '{"tool": "<name>", "params": {...}}'
    )
    if iteration >= max_iterations - 1:
        hint = "You are at the iteration limit. Call submit_assessment now if available."

    return f"""{system}

## CASE EVIDENCE
{evidence}

## TOOLS (JSON format only)
{chr(10).join(tool_lines)}

## STATUS
- Iteration: {iteration}/{max_iterations}
- Tool calls: {len(tool_calls)}/{max_tool_calls}
- {hint}

## CONVERSATION
{_history_block(messages)}

YOUR JSON RESPONSE:"""


def run_tool_loop(
    *,
    node_name: str,
    system_prompt: str,
    evidence: str,
    allowed_tools: List[str],
    tools_engine: InvestigationTools,
    metrics: MetricsCollector,
    llm_config: LLMConfig,
    max_iterations: int,
    max_tool_calls: int,
    include_submit: bool = False,
    initial_evidence: Optional[Dict[str, Any]] = None,
    alert_evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a bounded ReAct loop; returns findings text, tool_calls, traces, assessment."""
    names = list(allowed_tools)
    if include_submit:
        names.append("submit_assessment")

    messages: List[AgentMessage] = []
    tool_calls: List[ToolCall] = []
    trace_events: List[TraceEvent] = []
    iteration = 0
    error_count = 0
    final_assessment: Optional[FinalAssessment] = None
    findings_text = ""

    trace_events.append(_trace(node_name, "node_start", {"user_id": tools_engine.user_id}))

    while iteration < max_iterations:
        iteration += 1
        trace_events.append(
            _trace(
                node_name,
                "agent_iteration",
                {"iteration": iteration, "tool_calls_so_far": len(tool_calls)},
            )
        )

        prompt = build_json_tool_prompt(
            system_prompt,
            evidence,
            names,
            messages,
            tool_calls,
            iteration,
            max_iterations,
            max_tool_calls,
        )

        try:
            llm_start = time.time()
            llm_response = call_llm(prompt, llm_config)
            llm_duration = (time.time() - llm_start) * 1000
            metrics.track_llm_call(llm_duration, len(prompt) // 4, len(llm_response) // 4)
            messages.append(
                AgentMessage(role="assistant", content=llm_response, timestamp=datetime.now().isoformat())
            )
            if node_name == "llm_agent":
                trace_events.append(
                    _trace(
                        node_name,
                        "agent_thinking",
                        {
                            "iteration": iteration,
                            "response_preview": (llm_response or "")[:200],
                            "llm_duration_ms": round(llm_duration, 2),
                        },
                    )
                )
        except Exception as exc:
            logger.error("[%s] LLM call failed: %s", node_name, exc)
            error_count += 1
            if error_count >= 3 and include_submit and initial_evidence is not None:
                final_assessment = deterministic_assessment(initial_evidence, alert_evidence or {})
                break
            continue

        tool_name, tool_params = parse_tool_call(llm_response)
        if not tool_name:
            error_count += 1
            if error_count >= 3:
                if include_submit and initial_evidence is not None:
                    final_assessment = deterministic_assessment(initial_evidence, alert_evidence or {})
                break
            continue
        error_count = 0

        if tool_name == "submit_assessment" and include_submit:
            final_assessment = FinalAssessment(
                typology=tool_params.get("typology", "unknown"),
                risk_level=tool_params.get("risk_level", "medium"),
                risk_score=int(tool_params.get("risk_score", 50)),
                decision=tool_params.get("decision", "allow_monitor"),
                account_id=(tool_params.get("account_id") or "").strip(),
                reasoning=tool_params.get("reasoning", "Assessment submitted by agent"),
                iteration=iteration,
                tool_calls_made=len(tool_calls),
            )
            trace_events.append(
                _trace(
                    node_name,
                    "assessment",
                    {
                        "typology": final_assessment["typology"],
                        "risk_level": final_assessment["risk_level"],
                        "risk_score": final_assessment["risk_score"],
                        "decision": final_assessment["decision"],
                    },
                )
            )
            break

        if tool_name not in names:
            continue

        if len(tool_calls) >= max_tool_calls:
            if include_submit and initial_evidence is not None:
                final_assessment = deterministic_assessment(initial_evidence, alert_evidence or {})
            break

        result = tools_engine.execute_tool(tool_name, tool_params)
        tc = ToolCall(
            tool=tool_name,
            params=tool_params,
            result=result,
            timestamp=datetime.now().isoformat(),
            iteration=iteration,
        )
        tool_calls.append(tc)
        metrics.track_tool_call(tool_name)
        trace_events.append(
            _trace(
                node_name,
                "tool_call",
                {
                    "tool": tool_name,
                    "params": tool_params,
                    "iteration": iteration,
                    "agent": node_name,
                },
            )
        )
        messages.append(
            AgentMessage(
                role="tool",
                content=json.dumps(result, default=str),
                timestamp=datetime.now().isoformat(),
                tool_name=tool_name,
            )
        )

        if not include_submit and llm_response.strip() and iteration >= 1:
            # Specialists finish after producing a narrative summary in the last assistant turn.
            if not parse_tool_call(llm_response)[0] and len(tool_calls) >= 1:
                findings_text = llm_response.strip()[:1500]
                break

    if not findings_text and messages:
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg["content"].strip():
                findings_text = msg["content"].strip()[:1500]
                break

    if include_submit and not final_assessment and initial_evidence is not None:
        final_assessment = deterministic_assessment(initial_evidence, alert_evidence or {})

    trace_events.append(
        _trace(
            node_name,
            "node_complete",
            {"iterations": iteration, "tool_calls": len(tool_calls)},
        )
    )

    out: Dict[str, Any] = {
        "tool_calls": tool_calls,
        "trace_events": trace_events,
        "findings_text": findings_text,
    }
    if include_submit:
        out["final_assessment"] = final_assessment
        out["agent_iterations"] = iteration
        out["agent_messages"] = messages
    return out


def format_prior_cases(prior_cases: List[Dict[str, Any]]) -> str:
    if not prior_cases:
        return ""
    lines = [
        "\n## RELATED PRIOR CASES (recalled from long-term memory)",
        "These past investigations referenced this account or its connections:",
    ]
    for case in prior_cases[:5]:
        who = case.get("holder") or case.get("user_id") or "?"
        matched = ", ".join(case.get("matched_on") or [])
        lines.append(
            f"- {who} (acct {case.get('account_id')}): typology={case.get('typology')}, "
            f"prior decision={case.get('decision')} — shared entity: {matched}"
        )
    return "\n".join(lines) + "\n"


def format_specialist_findings(state: Dict[str, Any]) -> str:
    blocks = []
    mapping = [
        ("NETWORK ANALYST", state.get("network_findings", "")),
        ("DEVICE & INFRASTRUCTURE ANALYST", state.get("device_findings", "")),
        ("VELOCITY & TRANSACTION ANALYST", state.get("velocity_findings", "")),
    ]
    for title, text in mapping:
        text = (text or "").strip()
        if text:
            blocks.append(f"### {title}\n{text}")
    if not blocks:
        return "(No specialist findings were produced — investigate directly with your tools.)"
    return "\n\n".join(blocks)
