"""
Investigation State - TypedDict definition for LangGraph workflow state.

This state is passed through all workflow nodes and accumulates evidence
from each investigation phase.

Updated for agentic tool-calling architecture.
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated
from operator import add
from datetime import datetime


class AlertEvidence(TypedDict):
    """Evidence from alert validation."""
    trigger_type: str  # RT1, RT2, RT3, ML, MANUAL
    trigger_rule: str
    trigger_timestamp: str
    flag_reason: str
    original_score: float
    previous_flags_count: int


class InitialEvidence(TypedDict):
    """Initial evidence from data collection node (all from KV store)."""
    user_id: str
    profile: Dict[str, Any]
    accounts: Dict[str, Dict[str, Any]]       # account_id -> {type, balance, status, is_fraud, ...}
    devices: Dict[str, Dict[str, Any]]         # device_id -> {type, os, browser, is_fraud, ...}
    account_facts: Dict[str, Dict[str, Any]]   # account_id -> 15 pre-computed risk features
    device_facts: Dict[str, Dict[str, Any]]    # device_id -> 5 pre-computed risk features
    account_metrics: Dict[str, Any]
    alert_evidence: Dict[str, Any]


class AgentMessage(TypedDict):
    """Message in agent conversation history."""
    role: str  # assistant, tool
    content: str
    timestamp: str
    tool_name: Optional[str]  # For tool results


class ToolCall(TypedDict):
    """Record of a tool invocation."""
    tool: str
    params: Dict[str, Any]
    result: Dict[str, Any]
    timestamp: str
    iteration: int


class FinalAssessment(TypedDict, total=False):
    """Final assessment from LLM agent."""
    typology: str  # account_takeover, money_mule, synthetic_identity, fraud_ring, etc.
    risk_level: str  # low, medium, high, critical
    risk_score: int  # 0-100
    decision: str  # allow_monitor, step_up_auth, temporary_freeze, full_block, escalate_compliance
    account_id: str  # primary flagged account for action enforcement
    reasoning: str
    iteration: int
    tool_calls_made: int


class TraceEvent(TypedDict):
    """Event for SSE streaming."""
    type: str  # node_start, node_complete, evidence, tool_call, agent_thinking, agent_iteration, assessment, error
    node: str
    timestamp: str
    data: Dict[str, Any]


class PerformanceMetrics(TypedDict):
    """Performance metrics for the investigation workflow."""
    # Timing
    total_duration_ms: float
    node_durations: Dict[str, float]  # node_name -> duration_ms
    
    # Database calls
    total_db_calls: int
    kv_calls: int
    graph_calls: int
    kv_time_ms: float
    graph_time_ms: float
    
    # Checkpoints
    checkpoint_calls: int
    checkpoint_time_ms: float
    
    # LLM
    llm_calls: int
    llm_time_ms: float
    llm_tokens_in: int
    llm_tokens_out: int
    
    # Tool usage
    tool_calls_count: int
    tool_breakdown: Dict[str, int]  # tool_name -> count


# ─────────────────────────────────────────────────────────────────────────────
# Legacy types (kept for compatibility with report generation)
# ─────────────────────────────────────────────────────────────────────────────

class GraphNode(TypedDict):
    """Graph node for visualization."""
    id: str
    label: str
    type: str  # user, account, device, transaction
    risk_level: Optional[str]  # low, medium, high, critical
    is_flagged: bool
    properties: Dict[str, Any]


class GraphEdge(TypedDict):
    """Graph edge for visualization."""
    source: str
    target: str
    type: str  # OWNS, USES, TRANSACTS
    label: Optional[str]
    properties: Dict[str, Any]


class DimensionalScores(TypedDict):
    """Risk scores across different dimensions."""
    behavioral_anomaly: float  # 0-100
    network_risk: float  # 0-100
    velocity_risk: float  # 0-100
    typology_match: float  # 0-100
    infrastructure_risk: float  # 0-100


class TypologyAssessment(TypedDict):
    """Fraud typology classification."""
    primary_typology: str
    confidence: float
    reasoning: str
    secondary_typology: Optional[str]
    indicators: List[str]


class RiskAssessment(TypedDict):
    """Risk synthesis."""
    dimension_scores: DimensionalScores
    amplification_factor: float
    final_risk_score: float
    risk_level: str
    reasoning: str


class DecisionRecommendation(TypedDict):
    """Action recommendation."""
    recommended_action: str
    confidence: float
    reasoning: str
    alternative_action: Optional[str]
    requires_human_review: bool


# ─────────────────────────────────────────────────────────────────────────────
# Main State
# ─────────────────────────────────────────────────────────────────────────────

class InvestigationState(TypedDict):
    """
    Main state for the fraud investigation LangGraph workflow.
    
    Updated for agentic tool-calling architecture with:
    - Initial evidence from data_collection
    - Agent conversation history
    - Tool call audit trail
    - Final assessment from LLM agent
    """
    # Investigation identification
    investigation_id: str
    user_id: str
    started_at: str
    
    # Phase 1: Alert validation
    alert_evidence: Optional[AlertEvidence]
    
    # Phase 2: Data collection (baseline evidence)
    initial_evidence: Optional[InitialEvidence]
    
    # Phase 3: LLM Agent state
    agent_messages: List[AgentMessage]  # Conversation history
    tool_calls: List[ToolCall]  # Investigator tool invocations
    tool_results: Dict[str, Any]  # Accumulated tool results
    agent_iterations: int  # Loop counter
    tool_calls_count: int

    # Parallel specialist outputs (ADK parity)
    network_findings: str
    device_findings: str
    velocity_findings: str
    specialist_tool_calls_network_analyst: List[ToolCall]
    specialist_tool_calls_device_analyst: List[ToolCall]
    specialist_tool_calls_velocity_analyst: List[ToolCall]

    # Cross-case memory
    prior_cases: List[Dict[str, Any]]

    # Enacted fraud-mitigation actions
    enacted_actions: List[Dict[str, Any]]

    # HITL pause metadata (LangGraph interrupt)
    pending_confirmation: Optional[Dict[str, Any]]
    
    # Phase 4: Final assessment
    final_assessment: Optional[FinalAssessment]
    
    # Phase 5: Report
    report_markdown: str
    
    # Workflow control
    current_phase: str  # alert_validation, data_collection, llm_reasoning, report
    current_node: str
    workflow_status: str  # running, completed, error
    error_message: Optional[str]
    
    # SSE streaming
    trace_events: Annotated[List[TraceEvent], add]


def create_initial_state(
    investigation_id: str,
    user_id: str
) -> InvestigationState:
    """Create initial state for a new investigation."""
    return InvestigationState(
        # Identification
        investigation_id=investigation_id,
        user_id=user_id,
        started_at=datetime.now().isoformat(),
        
        # Phase 1: Alert validation
        alert_evidence=None,
        
        # Phase 2: Data collection
        initial_evidence=None,
        
        # Phase 3: LLM Agent state
        agent_messages=[],
        tool_calls=[],
        tool_results={},
        agent_iterations=0,
        tool_calls_count=0,
        network_findings="",
        device_findings="",
        velocity_findings="",
        specialist_tool_calls_network_analyst=[],
        specialist_tool_calls_device_analyst=[],
        specialist_tool_calls_velocity_analyst=[],
        prior_cases=[],
        enacted_actions=[],
        pending_confirmation=None,
        
        # Phase 4: Final assessment
        final_assessment=None,
        
        # Phase 5: Report
        report_markdown="",
        
        # Workflow control
        current_phase="alert_validation",
        current_node="start",
        workflow_status="running",
        error_message=None,
        
        # SSE streaming
        trace_events=[]
    )
