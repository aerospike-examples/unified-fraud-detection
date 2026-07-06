"""
ADK Fraud-Investigation Agent

Defines the Google ADK agent that replaces the old LangGraph ``llm_agent`` +
``report_generation`` nodes:

    SequentialAgent "fraud_investigation"
      ├─ evidence_collection — ParallelAgent: three specialists investigate
      │     ├─ network_analyst    (fraud rings, counterparties, mule chains)
      │     ├─ device_analyst     (device sharing, spoofing, infra risk)
      │     └─ velocity_analyst   (velocity, bursts, amount anomalies)
      ├─ investigator   — tool-using LlmAgent (ReAct) that SYNTHESIZES the
      │     specialist findings, drills into gaps, and submits + enacts a decision
      └─ report_writer  — LlmAgent that drafts the markdown report

The three specialists run CONCURRENTLY (ADK ``ParallelAgent``), each writing a
findings summary to session ``state`` via its ``output_key``; the investigator
then reads all three. This showcases ADK's parallel multi-agent composition and
fans concurrent reads out across the same Aerospike cluster.

The deterministic pre-steps (alert validation, data collection) run before the
agent and seed the session ``state`` the instructions below read from.

The agent is built once at startup and reused across all investigations; nothing
here is per-request, so it is safe to share a single instance behind the Runner.
"""

import os
import logging
from typing import Any

from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from workflow.assessment import build_evidence_summary
from workflow.nodes.report_generation import (
    build_report_instruction,
    finalize_report,
)
from workflow.tools.investigation_tools_adk import (
    INVESTIGATION_TOOLS,
    get_account_transactions,
    get_counterparty_profile,
    get_counterparty_transactions,
    get_account_risk_features,
    get_device_risk_features,
    detect_fraud_ring,
    get_transaction_network,
)
from workflow.action_tools import enact_decision
from workflow.specialists import (
    NETWORK_ANALYST_NAME,
    DEVICE_ANALYST_NAME,
    VELOCITY_ANALYST_NAME,
    SPECIALIST_NAMES,
    SPECIALIST_OUTPUT_KEYS,
    _SPECIALIST_SPECS,
    _SPECIALIST_SYSTEM,
)

logger = logging.getLogger('investigation.agent')

DEFAULT_MODEL = os.environ.get("ADK_MODEL", "gemini-3.5-flash")

APP_NAME = "fraud_investigation"

INVESTIGATOR_NAME = "investigator"
REPORT_WRITER_NAME = "report_writer"
ACTION_TAKER_NAME = "action_taker"
EVIDENCE_COLLECTION_NAME = "evidence_collection"

# ADK tool objects per specialist (function references for LlmAgent.tools).
_ADK_SPECIALIST_TOOLS = {
    NETWORK_ANALYST_NAME: [detect_fraud_ring, get_transaction_network,
                           get_counterparty_profile, get_counterparty_transactions],
    DEVICE_ANALYST_NAME: [get_device_risk_features, get_account_risk_features],
    VELOCITY_ANALYST_NAME: [get_account_transactions, get_account_risk_features],
}


def _make_specialist_instruction(name: str):
    spec = _SPECIALIST_SPECS[name]

    def _instruction(ctx: ReadonlyContext) -> str:
        state = ctx.state
        evidence = build_evidence_summary(state.get("initial_evidence") or {},
                                          state.get("alert_evidence") or {})
        return _SPECIALIST_SYSTEM.format(
            role=spec["role"], focus=spec["focus"], max_calls=spec["max_calls"], evidence=evidence,
        )

    return _instruction


# ─────────────────────────────────────────────────────────────────────────────
# Investigator instruction (senior fraud analyst, tool-driven ReAct)
# ─────────────────────────────────────────────────────────────────────────────
_INVESTIGATOR_SYSTEM = """You are a SENIOR FRAUD ANALYST investigating a flagged account. You are methodical and decisive — thorough where it matters, efficient everywhere else.

Three specialists (network, device, and velocity analysts) have ALREADY investigated this account IN PARALLEL. Their findings are in the "SPECIALIST FINDINGS" section below. Your job is to SYNTHESIZE their work, not redo it.

## YOUR INVESTIGATION APPROACH
1. START from the SPECIALIST FINDINGS below and the case evidence. The specialists have ALREADY done the broad evidence gathering across the network, device, and velocity domains — treat their findings as your primary evidence.
2. In MOST cases the specialist findings are sufficient to decide. Call a tool ONLY if a specialist explicitly flagged a gap, or you must resolve a DIRECT conflict between two specialists. Do NOT re-pull evidence the specialists already reported.
3. You may call recall_similar_investigations AT MOST ONCE (and only if prior cases would change your decision). Never call the same tool twice.

## EFFICIENCY
Because the specialists did the broad gathering, you should reach a confident conclusion in 0-3 tool calls — often zero. Going straight from the specialist findings to submit_assessment is the expected path. Quality of reasoning over the combined evidence matters far more than volume of tool calls.

Call tools one logical step at a time and reason over each result before the next.
When you are confident, call submit_assessment exactly once with a typology, risk level, risk score,
recommended decision, the PRIMARY flagged account_id your decision should act on, and detailed
reasoning citing specific evidence. After submit_assessment returns, STOP — a later step drafts the
report and then enacts your decision (destructive actions require analyst approval; you do NOT enact
the action yourself).

Valid values:
- typology: account_takeover, money_mule, synthetic_identity, promo_abuse, friendly_fraud, card_testing, fraud_ring, suspicious_activity, legitimate
- risk_level: low, medium, high, critical
- risk_score: integer 0-100
- decision: allow_monitor, step_up_auth, temporary_freeze, full_block, escalate_compliance

## SPECIALIST FINDINGS (parallel pre-analysis)
{findings}

## CASE EVIDENCE
{evidence}
"""


def _format_specialist_findings(state) -> str:
    """Render the three parallel specialists' findings for the synthesizer."""
    blocks = []
    for name in SPECIALIST_NAMES:
        text = (state.get(SPECIALIST_OUTPUT_KEYS[name]) or "").strip()
        title = _SPECIALIST_SPECS[name]["role"]
        if text:
            blocks.append(f"### {title}\n{text}")
    if not blocks:
        return "(No specialist findings were produced — investigate directly with your tools.)"
    return "\n\n".join(blocks)


def _format_prior_cases(state) -> str:
    """Render related prior investigations recalled from long-term memory."""
    cases = state.get("prior_cases") or []
    if not cases:
        return ""
    lines = ["\n## RELATED PRIOR CASES (recalled from long-term memory)",
             "These past investigations referenced this account or its connections — weigh them:"]
    for c in cases[:5]:
        who = c.get("holder") or c.get("user_id") or "?"
        matched = ", ".join(c.get("matched_on") or [])
        lines.append(
            f"- {who} (acct {c.get('account_id')}): typology={c.get('typology')}, "
            f"prior decision={c.get('decision')} — shared entity: {matched}")
    return "\n".join(lines) + "\n"


def _investigator_instruction(ctx: ReadonlyContext) -> str:
    """Build the investigator instruction from seeded session state + parallel findings."""
    state = ctx.state
    initial = state.get("initial_evidence") or {}
    alert = state.get("alert_evidence") or {}
    evidence = build_evidence_summary(initial, alert) + _format_prior_cases(state)
    findings = _format_specialist_findings(state)
    return _INVESTIGATOR_SYSTEM.format(evidence=evidence, findings=findings)


# ─────────────────────────────────────────────────────────────────────────────
# Action taker: enacts the decision AFTER the report is written. Destructive
# actions pause here for analyst approval (ADK tool-confirmation).
# ─────────────────────────────────────────────────────────────────────────────
_ACTION_TAKER_SYSTEM = """You enforce a fraud decision that a senior analyst has ALREADY made and documented. Do NOT re-investigate, second-guess, or change the decision.

Call enact_decision EXACTLY ONCE with:
- decision: {decision}
- account_id: {account_id}  (if this is blank, use the PRIMARY flagged account from the evidence below)
- reason: one concise sentence justifying the action (you may summarize this: {reason})

Do not call any other tool. Destructive actions (temporary_freeze, full_block, escalate_compliance) will pause for a human analyst's approval before they take effect; non-destructive ones apply immediately. After enact_decision returns, stop.

## EVIDENCE (for the primary flagged account id, if needed)
{evidence}
"""


def _action_taker_instruction(ctx: ReadonlyContext) -> str:
    state = ctx.state
    final = state.get("final_assessment") or {}
    evidence = build_evidence_summary(state.get("initial_evidence") or {}, state.get("alert_evidence") or {})
    return _ACTION_TAKER_SYSTEM.format(
        decision=final.get("decision", "allow_monitor"),
        account_id=final.get("account_id", "") or "",
        reason=(final.get("reasoning", "") or "")[:300],
        evidence=evidence,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Report writer: post-process the drafted markdown deterministically
# ─────────────────────────────────────────────────────────────────────────────
def _state_snapshot(state) -> dict:
    """Snapshot ADK state to a plain dict. ``CallbackContext.state`` is an ADK
    ``State`` (use ``to_dict``); ``ReadonlyContext.state`` is a ``mappingproxy``
    (use ``dict``)."""
    if hasattr(state, "to_dict"):
        return state.to_dict()
    return dict(state)


def _report_instruction(ctx: ReadonlyContext) -> str:
    return build_report_instruction(_state_snapshot(ctx.state))


def _finalize_report_callback(callback_context: CallbackContext):
    """After the report agent drafts markdown, append the fraud-ring Mermaid
    section + metadata footer (or fall back to a fully deterministic report)."""
    state = callback_context.state
    raw = state.get("report_markdown", "") or ""
    final = finalize_report(raw, _state_snapshot(state))
    state["report_markdown"] = final
    # Returning None keeps the agent's own message; the canonical report lives in
    # state["report_markdown"], which the SSE translator and persistence read.
    return None


def _build_specialist(name: str, model: str) -> LlmAgent:
    """Build one parallel evidence-collection specialist."""
    return LlmAgent(
        name=name,
        model=model,
        instruction=_make_specialist_instruction(name),
        tools=_ADK_SPECIALIST_TOOLS[name],
        output_key=SPECIALIST_OUTPUT_KEYS[name],
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
        # Leaf agents in the ParallelAgent — no transfer.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def build_investigation_agent(model: str = None) -> SequentialAgent:
    """Build the three-stage fraud-investigation SequentialAgent
    (parallel evidence collection → investigator synthesis → report)."""
    model = model or DEFAULT_MODEL

    # Stage 1: three specialists investigate concurrently (ADK ParallelAgent).
    evidence_collection = ParallelAgent(
        name=EVIDENCE_COLLECTION_NAME,
        sub_agents=[_build_specialist(n, model) for n in SPECIALIST_NAMES],
        description="Concurrently gather network, device, and velocity evidence on the flagged account.",
    )

    investigator = LlmAgent(
        name=INVESTIGATOR_NAME,
        model=model,
        instruction=_investigator_instruction,
        tools=INVESTIGATION_TOOLS,  # assesses only; enacting happens later
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
        # The investigator owns its turn; no transfer to peers/parent.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    report_writer = LlmAgent(
        name=REPORT_WRITER_NAME,
        model=model,
        instruction=_report_instruction,
        output_key="report_markdown",
        generate_content_config=types.GenerateContentConfig(temperature=0.4),
        after_agent_callback=_finalize_report_callback,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    # Stage 4: enact the decision AFTER the report exists. Destructive actions
    # pause here for analyst approval (so the analyst reviews the report first).
    action_taker = LlmAgent(
        name=ACTION_TAKER_NAME,
        model=model,
        instruction=_action_taker_instruction,
        tools=[enact_decision],
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    agent = SequentialAgent(
        name=APP_NAME,
        sub_agents=[evidence_collection, investigator, report_writer, action_taker],
        description="Gather evidence in parallel, assess, write the report, then enact the decision.",
    )
    logger.info(f"Built ADK investigation agent (model={model}, parallel evidence + post-report action)")
    return agent
