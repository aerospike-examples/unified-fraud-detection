"""
ADK Investigation Tools

Google ADK function tools for the fraud-investigation agent. These are thin
wrappers around the existing :class:`InvestigationTools` engine (which holds all
the Aerospike KV lookups and the Gremlin graph/fraud-ring logic) so the heavy
query code lives in exactly one place.

ADK builds each tool's schema from the function signature, type hints, and
docstring — so the docstrings below are the agent-facing tool descriptions.

Conventions
-----------
- ``user_id`` and ``investigation_id`` are read from ``tool_context.state`` (seeded
  before the run), never passed by the model.
- Aerospike + Graph services are process singletons bound once at startup via
  :func:`init_tools`, so the agent/runner can be built once and reused across all
  investigations.
- Per-investigation DB-call metrics are recorded via ``get_collector``.
"""

from typing import Any, Callable, Dict, List
import asyncio
import logging
import os
from datetime import date, datetime
from decimal import Decimal

from google.adk.tools import ToolContext

from workflow.tools.investigation_tools import InvestigationTools
from workflow.metrics import get_collector

logger = logging.getLogger('investigation.tools_adk')

# Hard cap on any single tool call. This is a backstop ABOVE the graph server's
# evaluationTimeout + the transport read timeout, so it should almost never fire;
# it exists so a wedged tool can never stall an investigation indefinitely.
TOOL_TIMEOUT_S = float(os.environ.get('TOOL_TIMEOUT_S', '90'))

# ─────────────────────────────────────────────────────────────────────────────
# Service binding (set once at startup)
# ─────────────────────────────────────────────────────────────────────────────
_aerospike_service: Any = None
_graph_service: Any = None


def init_tools(aerospike_service: Any, graph_service: Any) -> None:
    """Bind the Aerospike KV and Graph service singletons used by all tools."""
    global _aerospike_service, _graph_service
    _aerospike_service = aerospike_service
    _graph_service = graph_service
    logger.info("ADK investigation tools bound to Aerospike + Graph services")


def _engine(tool_context: ToolContext) -> InvestigationTools:
    """Build an InvestigationTools engine bound to this investigation's context."""
    user_id = tool_context.state.get("user_id")
    investigation_id = tool_context.state.get("investigation_id")
    metrics = get_collector(investigation_id) if investigation_id else None
    return InvestigationTools(_aerospike_service, _graph_service, user_id, metrics)


def _json_safe(obj: Any) -> Any:
    """Coerce a tool result into types ADK's Aerospike session store can persist.

    The graph value-maps can surface datetimes/Decimals (e.g. a counterparty's
    signup_date), and the Aerospike client can't serialize those — a single such
    value in a tool response makes ADK's session append_event fail and aborts the
    whole investigation ("Unable to serialize unknown Python native type"). We
    normalize to str/float/list/dict/primitives so persistence always succeeds.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (bytes, bytearray)):
        try:
            return bytes(obj).decode("utf-8", "replace")
        except Exception:
            return str(obj)
    return str(obj)


async def _run_tool_in_thread(name: str, fn: Callable[[], dict]) -> dict:
    """Run a blocking tool body in a worker thread so it does NOT block (or, for
    graph tools, RE-ENTER) the asyncio event loop.

    This is what lets ADK's ParallelAgent specialists genuinely overlap instead
    of serializing on every lookup, AND — critically for the graph tools — keeps
    the synchronous gremlin driver off the main loop entirely. The gremlin
    transport is built with call_from_event_loop=False, so it owns its own loop
    and is safe to drive from a plain worker thread; driving it on the main loop
    (call_from_event_loop=True) re-enters ADK's ParallelAgent TaskGroup and
    crashes the run with a GeneratorExit. Both the Aerospike client and (under
    the graph query lock) the gremlin connection are safe to use this way.
    """
    try:
        result = await asyncio.wait_for(asyncio.to_thread(fn), timeout=TOOL_TIMEOUT_S)
        return _json_safe(result)
    except asyncio.TimeoutError:
        logger.warning("[Tool] %s exceeded %ss — returning timeout error", name, TOOL_TIMEOUT_S)
        return {"success": False, "error": f"{name} timed out after {int(TOOL_TIMEOUT_S)}s", "timed_out": True}
    except Exception as e:  # noqa: BLE001 — surface any failure to the agent, don't crash the run
        logger.exception("[Tool] %s failed", name)
        return {"success": False, "error": str(e)}


# Backwards-compatible alias for KV-only tools.
_run_kv_tool = _run_tool_in_thread


async def _run_graph_tool(name: str, fn: Callable[[], dict]) -> dict:
    """Run a gremlin-backed tool body off the main loop, serialized by the graph
    query lock (one shared websocket connection driving its own internal loop)."""
    lock = getattr(_graph_service, "query_lock", None)

    def _locked() -> dict:
        if lock is not None:
            with lock:
                return fn()
        return fn()

    return await _run_tool_in_thread(name, _locked)


# ─────────────────────────────────────────────────────────────────────────────
# Evidence-gathering tools (KV + Graph)
# ─────────────────────────────────────────────────────────────────────────────
async def get_account_transactions(account_id: str, tool_context: ToolContext, days: int = 30) -> dict:
    """Pull the transaction ledger for a specific account. Use this to analyze
    spending patterns, velocity, amounts, counterparties, and detect unusual
    behavior. Each transaction includes the counterparty_user_id which you can
    investigate further.

    Args:
        account_id: The account ID to pull transactions for (e.g. A527001).
        days: Days to look back, 1-90 (default 30).
    """
    eng = _engine(tool_context)
    return await _run_graph_tool(
        "get_account_transactions",
        lambda: eng.get_account_transactions(account_id=account_id, days=days),
    )


async def get_counterparty_profile(user_id: str, tool_context: ToolContext) -> dict:
    """Get the profile of a user the suspect has been transacting with. Returns
    their name, location, signup date, risk score, accounts (with balances and
    fraud flags), and devices. Use this after seeing suspicious transactions to
    investigate the other party.

    Args:
        user_id: The counterparty's user_id (from transaction data).
    """
    eng = _engine(tool_context)
    return await _run_graph_tool(
        "get_counterparty_profile",
        lambda: eng.get_counterparty_profile(user_id=user_id),
    )


async def get_counterparty_transactions(user_id: str, tool_context: ToolContext, days: int = 30) -> dict:
    """Get all transactions across all accounts of a counterparty. Use this to
    build a behavioral profile: are they receiving money from many sources (mule
    pattern)? Making rapid transfers? What is their transaction volume?

    Args:
        user_id: The counterparty's user_id.
        days: Days to look back, 1-90 (default 30).
    """
    eng = _engine(tool_context)
    return await _run_graph_tool(
        "get_counterparty_transactions",
        lambda: eng.get_counterparty_transactions(user_id=user_id, days=days),
    )


async def get_account_risk_features(account_id: str, tool_context: ToolContext) -> dict:
    """Get pre-computed ML risk features for an account. Returns velocity (txn
    count, peak hour activity), amount patterns (total, average, z-score),
    counterparty spread (unique recipients, new recipient ratio, entropy), device
    exposure, and lifecycle metrics (account age, first transaction delay).

    Args:
        account_id: The account ID to get risk features for.
    """
    eng = _engine(tool_context)
    return await _run_kv_tool(
        "get_account_risk_features",
        lambda: eng.get_account_risk_features(account_id=account_id),
    )


async def get_device_risk_features(device_id: str, tool_context: ToolContext) -> dict:
    """Get pre-computed risk features for a device. Returns shared account count
    (how many accounts use this device), flagged account count, average and max
    account risk scores, and new account rate. High shared_account_count or
    flagged_account_count suggests the device is part of a fraud ring.

    Args:
        device_id: The device ID to get risk features for.
    """
    eng = _engine(tool_context)
    return await _run_kv_tool(
        "get_device_risk_features",
        lambda: eng.get_device_risk_features(device_id=device_id),
    )


async def detect_fraud_ring(tool_context: ToolContext, hops: int = 1) -> dict:
    """Detect whether the investigated user is part of a coordinated fraud ring by
    analyzing the network graph. Checks shared devices, flagged entities,
    device+transaction overlap, transaction triangles/cycles, reciprocal money
    flow, subgraph density, and abnormal inter-member volume. Use this when you
    suspect coordinated fraud across multiple accounts.

    Args:
        hops: Network traversal depth, 1-3 (default 2).
    """
    eng = _engine(tool_context)
    return await _run_graph_tool("detect_fraud_ring", lambda: eng.detect_fraud_ring(hops=hops))


async def get_transaction_network(tool_context: ToolContext, hops: int = 1, min_amount: float = 0) -> dict:
    """Visualize the money-flow network around the user. Multi-hop traversal
    through transaction edges showing who sent money to whom, total amounts,
    transaction counts, and which accounts on the path are flagged. Use this to
    trace money trails and identify high-risk flow paths.

    Args:
        hops: Network traversal depth, 1-3 (default 2).
        min_amount: Only include edges with total amount above this threshold.
    """
    eng = _engine(tool_context)
    return await _run_graph_tool(
        "get_transaction_network",
        lambda: eng.get_transaction_network(hops=hops, min_amount=min_amount),
    )


async def recall_similar_investigations(query: str, tool_context: ToolContext) -> dict:
    """Search long-term memory for past investigations relevant to the current
    case (e.g. related users, counterparties, or fraud typologies seen before).
    Use this early to check whether the suspect or their counterparties have
    appeared in prior investigations.

    Args:
        query: A short natural-language description of what to recall
            (e.g. "money mule rings involving user U045").
    """
    try:
        response = await tool_context.search_memory(query)
    except Exception as e:  # memory service optional / may be empty
        logger.warning(f"recall_similar_investigations failed: {e}")
        return {"success": False, "found": False, "error": str(e), "memories": []}

    memories = getattr(response, "memories", None) or []
    results: List[Dict[str, Any]] = []
    for mem in memories:
        text_parts = []
        for part in getattr(mem, "content", None).parts if getattr(mem, "content", None) else []:
            if getattr(part, "text", None):
                text_parts.append(part.text)
        results.append({
            "author": getattr(mem, "author", None),
            "timestamp": getattr(mem, "timestamp", None),
            "text": " ".join(text_parts)[:1000],
        })
    return _json_safe({"success": True, "found": bool(results), "count": len(results), "memories": results[:10]})


# ─────────────────────────────────────────────────────────────────────────────
# Exit tool: submit the final assessment into session state
# ─────────────────────────────────────────────────────────────────────────────
_VALID_TYPOLOGIES = [
    "account_takeover", "money_mule", "synthetic_identity", "promo_abuse",
    "friendly_fraud", "card_testing", "fraud_ring", "suspicious_activity",
    "legitimate", "unknown",
]
_VALID_RISK_LEVELS = ["low", "medium", "high", "critical"]
_VALID_DECISIONS = [
    "allow_monitor", "step_up_auth", "temporary_freeze", "full_block",
    "escalate_compliance",
]


def submit_assessment(
    typology: str,
    risk_level: str,
    risk_score: int,
    decision: str,
    account_id: str,
    reasoning: str,
    tool_context: ToolContext,
) -> dict:
    """Submit your final fraud assessment. Call this exactly once, as your LAST
    action, when you have gathered enough evidence to make a decision. After
    calling it, stop — do not call any more tools.

    Args:
        typology: Fraud type — one of account_takeover, money_mule,
            synthetic_identity, promo_abuse, friendly_fraud, card_testing,
            fraud_ring, suspicious_activity, legitimate.
        risk_level: One of low, medium, high, critical.
        risk_score: Integer risk score 0-100.
        decision: Recommended action — one of allow_monitor, step_up_auth,
            temporary_freeze, full_block, escalate_compliance.
        account_id: The PRIMARY flagged account your decision should act on
            (e.g. A000885901) — taken from the evidence.
        reasoning: Detailed reasoning citing specific evidence from your
            investigation.
    """
    typology = typology if typology in _VALID_TYPOLOGIES else "unknown"
    risk_level = risk_level if risk_level in _VALID_RISK_LEVELS else "medium"
    decision = decision if decision in _VALID_DECISIONS else "allow_monitor"
    try:
        risk_score = min(100, max(0, int(risk_score)))
    except (TypeError, ValueError):
        risk_score = 50

    state = tool_context.state
    tool_calls_made = int(state.get("tool_calls_count", 0))
    assessment = {
        "typology": typology,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "decision": decision,
        "account_id": (account_id or "").strip(),
        "reasoning": reasoning,
        "iteration": int(state.get("agent_iterations", 0)),
        "tool_calls_made": tool_calls_made,
    }
    # Persist into session state so the report stage and the SSE translator can read it.
    state["final_assessment"] = assessment
    logger.info(f"[submit_assessment] {typology} / {risk_level} / {risk_score}")
    return {"success": True, "is_final_assessment": True, **assessment}


# Ordered tool list for the investigator agent.
INVESTIGATION_TOOLS = [
    get_account_transactions,
    get_counterparty_profile,
    get_counterparty_transactions,
    get_account_risk_features,
    get_device_risk_features,
    detect_fraud_ring,
    get_transaction_network,
    recall_similar_investigations,
    submit_assessment,
]
