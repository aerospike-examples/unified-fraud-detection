"""
ADK Fraud-Investigation Agent

Defines the Google ADK agent that replaces the old LangGraph ``llm_agent`` +
``report_generation`` nodes:

    SequentialAgent "fraud_investigation"
      ├─ investigator   — tool-using LlmAgent (ReAct-style reasoning over Aerospike)
      └─ report_writer  — LlmAgent that drafts the markdown report

The deterministic pre-steps (alert validation, data collection) run before the
agent and seed the session ``state`` the instructions below read from.

The agent is built once at startup and reused across all investigations; nothing
here is per-request, so it is safe to share a single instance behind the Runner.
"""

import os
import logging
from typing import Any

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from workflow.assessment import build_evidence_summary
from workflow.nodes.report_generation import (
    build_report_instruction,
    finalize_report,
)
from workflow.tools.investigation_tools_adk import INVESTIGATION_TOOLS

logger = logging.getLogger('investigation.agent')

DEFAULT_MODEL = os.environ.get("ADK_MODEL", "gemini-3.5-flash")

APP_NAME = "fraud_investigation"

INVESTIGATOR_NAME = "investigator"
REPORT_WRITER_NAME = "report_writer"


# ─────────────────────────────────────────────────────────────────────────────
# Investigator instruction (senior fraud analyst, tool-driven ReAct)
# ─────────────────────────────────────────────────────────────────────────────
_INVESTIGATOR_SYSTEM = """You are a SENIOR FRAUD ANALYST investigating a flagged account. You are methodical and decisive — thorough where it matters, efficient everywhere else.

## YOUR INVESTIGATION APPROACH
You decide what to investigate and when you have enough evidence. Be focused, not exhaustive:

1. START by reviewing the profile, accounts, devices, and pre-computed risk features below.
2. PULL transaction history for the flagged/suspicious accounts using get_account_transactions.
3. INVESTIGATE the 2-3 MOST suspicious counterparties only (highest volume, newest, highest risk,
   or most repeated) — do NOT investigate every counterparty:
   - get_counterparty_profile: who are they? new account? high risk? flagged?
   - get_counterparty_transactions: receiving from many sources? rapid transfers? mule pattern?
4. CHECK risk features for clearly anomalous accounts with get_account_risk_features.
5. IF you suspect coordinated fraud, use detect_fraud_ring once to analyze the network graph.
6. Optionally call recall_similar_investigations once if related prior cases would change your view.

## EFFICIENCY
Aim to reach a confident conclusion in roughly 8-12 tool calls. Do NOT re-pull data you already
have, and do NOT investigate low-signal counterparties. Quality of reasoning matters more than
volume of tool calls.

Call tools one logical step at a time and reason over each result before the next.
When you are confident, call submit_assessment exactly once as your FINAL action with a typology,
risk level, risk score, recommended decision, and detailed reasoning citing specific evidence.
After submit_assessment, stop.

Valid values:
- typology: account_takeover, money_mule, synthetic_identity, promo_abuse, friendly_fraud, card_testing, fraud_ring, suspicious_activity, legitimate
- risk_level: low, medium, high, critical
- risk_score: integer 0-100
- decision: allow_monitor, step_up_auth, temporary_freeze, full_block, escalate_compliance

## CASE EVIDENCE
{evidence}
"""


def _investigator_instruction(ctx: ReadonlyContext) -> str:
    """Build the investigator instruction from seeded session state."""
    state = ctx.state
    initial = state.get("initial_evidence") or {}
    alert = state.get("alert_evidence") or {}
    evidence = build_evidence_summary(initial, alert)
    return _INVESTIGATOR_SYSTEM.format(evidence=evidence)


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


def build_investigation_agent(model: str = None) -> SequentialAgent:
    """Build the two-stage fraud-investigation SequentialAgent."""
    model = model or DEFAULT_MODEL

    investigator = LlmAgent(
        name=INVESTIGATOR_NAME,
        model=model,
        instruction=_investigator_instruction,
        tools=INVESTIGATION_TOOLS,
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

    agent = SequentialAgent(
        name=APP_NAME,
        sub_agents=[investigator, report_writer],
        description="Investigate a flagged account, then write the fraud report.",
    )
    logger.info(f"Built ADK investigation agent (model={model})")
    return agent
