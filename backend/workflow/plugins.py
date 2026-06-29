"""
MetricsPlugin

App-wide ADK plugin that instruments the investigation run and feeds the existing
per-investigation :class:`MetricsCollector` (so the frontend metrics panel keeps
working unchanged). It also accumulates the agent's tool calls into session state
under ``tool_calls`` — used by the report's fraud-ring section, the SSE stream,
and KV persistence.

Replaces the old ``InstrumentedAerospikeSaver`` + hand-rolled timing in the
LangGraph nodes. Token counts now come from real ``usage_metadata`` instead of a
character-length estimate.
"""

import os
import time
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from google.adk.plugins.base_plugin import BasePlugin

from workflow.metrics import get_collector
from workflow.agent import INVESTIGATOR_NAME, SPECIALIST_NAMES

logger = logging.getLogger('investigation.plugin')

# Hard cap on evidence-gathering tool calls per investigation. Once hit, the next
# non-submit tool call is short-circuited with a nudge to conclude — keeps a
# thorough agent from running away (and keeps demos snappy). Configurable.
MAX_TOOL_CALLS = int(os.environ.get("ADK_MAX_TOOL_CALLS", "15"))


def _inv_id(state) -> Optional[str]:
    try:
        return state.get("investigation_id")
    except Exception:
        return None


class MetricsPlugin(BasePlugin):
    """Times tools + model calls and records them per investigation."""

    def __init__(self, name: str = "metrics") -> None:
        super().__init__(name=name)
        # Keyed timers so concurrent investigations don't clash.
        self._tool_starts: Dict[str, float] = {}
        self._model_starts: Dict[str, float] = {}

    # ── Tools ────────────────────────────────────────────────────────────────
    async def before_tool_callback(self, *, tool: Any, tool_args: dict, tool_context: Any):
        tool_name = getattr(tool, "name", "")
        agent_name = getattr(tool_context, "agent_name", "") or ""
        # Enforce the tool-call budget ONLY for the investigator (the synthesis
        # agent) — never the exit tool (submit_assessment) or action tool
        # (enact_decision), and never the parallel specialists (each is bounded by
        # its own instruction, and a shared counter would race under concurrency).
        if agent_name == INVESTIGATOR_NAME and tool_name not in ("submit_assessment", "enact_decision"):
            count = int(tool_context.state.get("tool_calls_count", 0))
            if count >= MAX_TOOL_CALLS:
                logger.info(f"Tool-call budget ({MAX_TOOL_CALLS}) reached — nudging agent to submit")
                return {
                    "success": False,
                    "budget_exceeded": True,
                    "message": (
                        f"Tool-call budget of {MAX_TOOL_CALLS} reached. Do not call any more "
                        "evidence tools. Call submit_assessment now with your best assessment "
                        "based on the evidence already gathered."
                    ),
                }
        key = f"{tool_context.invocation_id}:{tool_context.function_call_id}"
        self._tool_starts[key] = time.time()
        return None

    async def after_tool_callback(self, *, tool: Any, tool_args: dict, tool_context: Any, result: dict):
        key = f"{tool_context.invocation_id}:{tool_context.function_call_id}"
        start = self._tool_starts.pop(key, None)
        duration_ms = (time.time() - start) * 1000 if start else 0.0

        tool_name = getattr(tool, "name", "unknown")
        inv_id = _inv_id(tool_context.state)
        if inv_id:
            collector = get_collector(inv_id)
            collector.track_tool_call(tool_name)

        # Accumulate the call into session state (reassign to trigger state tracking).
        state = tool_context.state
        agent_name = getattr(tool_context, "agent_name", "") or ""
        record = {
            "tool": tool_name,
            "params": dict(tool_args or {}),
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "iteration": int(state.get("agent_iterations", 0)),
            "duration_ms": round(duration_ms, 2),
            "agent": agent_name,
        }
        # Parallel specialists write to per-agent keys so their concurrent
        # appends never collide on a single shared list (ADK merges state deltas
        # per key, so one shared list under fan-out would drop entries). The
        # runner merges these back into the final tool_calls at finalize.
        if agent_name in SPECIALIST_NAMES:
            key = f"specialist_tool_calls_{agent_name}"
            calls = list(state.get(key, []))
            calls.append(record)
            state[key] = calls
        else:
            calls = list(state.get("tool_calls", []))
            calls.append(record)
            state["tool_calls"] = calls
            state["tool_calls_count"] = len(calls)
        return None

    # ── Model (LLM) ──────────────────────────────────────────────────────────
    async def before_model_callback(self, *, callback_context: Any, llm_request: Any):
        inv_id = _inv_id(callback_context.state)
        key = f"{callback_context.invocation_id}:{callback_context.agent_name}"
        self._model_starts[key] = time.time()
        # Count each investigator reasoning step as an iteration.
        if callback_context.agent_name == "investigator" and inv_id:
            state = callback_context.state
            state["agent_iterations"] = int(state.get("agent_iterations", 0)) + 1
        return None

    async def after_model_callback(self, *, callback_context: Any, llm_response: Any):
        key = f"{callback_context.invocation_id}:{callback_context.agent_name}"
        start = self._model_starts.pop(key, None)
        # Skip streaming partials — only record on a completed response.
        if getattr(llm_response, "partial", False):
            self._model_starts[key] = start  # keep timer for the final chunk
            return None
        duration_ms = (time.time() - start) * 1000 if start else 0.0

        tokens_in = tokens_out = 0
        usage = getattr(llm_response, "usage_metadata", None)
        if usage:
            tokens_in = getattr(usage, "prompt_token_count", 0) or 0
            tokens_out = getattr(usage, "candidates_token_count", 0) or 0

        inv_id = _inv_id(callback_context.state)
        if inv_id:
            get_collector(inv_id).track_llm_call(duration_ms, tokens_in, tokens_out)
        return None
