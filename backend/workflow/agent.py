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

logger = logging.getLogger('investigation.agent')

DEFAULT_MODEL = os.environ.get("ADK_MODEL", "gemini-3.5-flash")

APP_NAME = "fraud_investigation"

INVESTIGATOR_NAME = "investigator"
REPORT_WRITER_NAME = "report_writer"

# Parallel evidence-collection specialists (ADK ParallelAgent stage).
EVIDENCE_COLLECTION_NAME = "evidence_collection"
NETWORK_ANALYST_NAME = "network_analyst"
DEVICE_ANALYST_NAME = "device_analyst"
VELOCITY_ANALYST_NAME = "velocity_analyst"
SPECIALIST_NAMES = (NETWORK_ANALYST_NAME, DEVICE_ANALYST_NAME, VELOCITY_ANALYST_NAME)

# state output_key each specialist writes its findings summary to.
SPECIALIST_OUTPUT_KEYS = {
    NETWORK_ANALYST_NAME: "network_findings",
    DEVICE_ANALYST_NAME: "device_findings",
    VELOCITY_ANALYST_NAME: "velocity_findings",
}


# ─────────────────────────────────────────────────────────────────────────────
# Specialist instruction (one parallel domain analyst)
# ─────────────────────────────────────────────────────────────────────────────
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


# role / focus / tool-budget per specialist.
_SPECIALIST_SPECS = {
    NETWORK_ANALYST_NAME: dict(
        role="NETWORK ANALYST",
        max_calls=5,
        focus=(
            "Counterparties and the money-movement graph: who the flagged account transacts with, "
            "fan-out / fan-in patterns, repeated counterparties, mule chains, and coordinated fraud "
            "rings. Use detect_fraud_ring and get_transaction_network to map the network, and "
            "get_counterparty_profile / get_counterparty_transactions to vet the 2-3 most suspicious "
            "counterparties (highest volume, newest, highest risk, or most repeated)."
        ),
    ),
    DEVICE_ANALYST_NAME: dict(
        role="DEVICE & INFRASTRUCTURE ANALYST",
        max_calls=4,
        focus=(
            "Devices and account infrastructure risk: devices shared across multiple accounts, "
            "device risk/spoofing signals, and account-level infrastructure risk features. Use "
            "get_device_risk_features on the account's devices and get_account_risk_features on the "
            "flagged/suspicious accounts. Flag any device tied to many accounts or with high risk."
        ),
    ),
    VELOCITY_ANALYST_NAME: dict(
        role="VELOCITY & TRANSACTION ANALYST",
        max_calls=5,
        focus=(
            "Transaction velocity and amount behavior: bursts of activity, transaction velocity vs "
            "baseline, unusual amounts, new-recipient ratio, and structuring/timing patterns. Use "
            "get_account_transactions to pull history and get_account_risk_features for pre-computed "
            "velocity/amount anomaly scores. Quantify the burst (count, window, total amount)."
        ),
    ),
}

_SPECIALIST_TOOLS = {
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
When you are confident:
1. Call submit_assessment exactly once with a typology, risk level, risk score, recommended
   decision, and detailed reasoning citing specific evidence.
2. THEN call enact_decision once with that same decision and the primary flagged account_id to
   enforce it. Destructive actions (temporary_freeze, full_block, escalate_compliance) will require
   a human analyst's approval before they take effect; non-destructive ones apply immediately.
After enact_decision returns, stop.

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


def _investigator_instruction(ctx: ReadonlyContext) -> str:
    """Build the investigator instruction from seeded session state + parallel findings."""
    state = ctx.state
    initial = state.get("initial_evidence") or {}
    alert = state.get("alert_evidence") or {}
    evidence = build_evidence_summary(initial, alert)
    findings = _format_specialist_findings(state)
    return _INVESTIGATOR_SYSTEM.format(evidence=evidence, findings=findings)


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
        tools=_SPECIALIST_TOOLS[name],
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
        tools=INVESTIGATION_TOOLS + [enact_decision],
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
        sub_agents=[evidence_collection, investigator, report_writer],
        description="Gather evidence in parallel, synthesize an assessment, then write the fraud report.",
    )
    logger.info(f"Built ADK investigation agent (model={model}, parallel evidence collection)")
    return agent
