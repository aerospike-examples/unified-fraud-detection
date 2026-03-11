export interface WorkflowStep {
  id: string;
  name: string;
  description: string;
  phase: string;
}

export interface TraceEvent {
  type: string;
  node: string;
  timestamp: string;
  data: Record<string, any>;
}

export interface DimensionalScores {
  behavioral_anomaly: number;
  network_risk: number;
  velocity_risk: number;
  typology_match: number;
  infrastructure_risk: number;
}

export interface TypologyAssessment {
  primary_typology: string;
  confidence: number;
  reasoning: string;
  secondary_typology?: string;
  indicators: string[];
}

export interface RiskAssessment {
  dimension_scores: DimensionalScores;
  amplification_factor: number;
  final_risk_score: number;
  risk_level: string;
  reasoning: string;
}

export interface Decision {
  recommended_action: string;
  confidence: number;
  reasoning: string;
  alternative_action?: string;
  requires_human_review: boolean;
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  risk_level?: string;
  is_flagged: boolean;
  properties: Record<string, any>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  label?: string;
  properties: Record<string, any>;
}

export interface FinalAssessment {
  typology: string;
  risk_level: string;
  risk_score: number;
  decision: string;
  reasoning: string;
  iteration: number;
  tool_calls_made: number;
}

export interface ToolCall {
  tool: string;
  params: Record<string, any>;
  result?: Record<string, any>;
  timestamp: string;
  iteration: number;
}

export interface PerformanceMetrics {
  total_duration_ms: number;
  node_durations: Record<string, number>;

  total_db_calls: number;
  kv_calls: number;
  graph_calls: number;
  kv_time_ms: number;
  graph_time_ms: number;

  checkpoint_calls: number;
  checkpoint_time_ms: number;

  llm_calls: number;
  llm_time_ms: number;
  llm_tokens_in: number;
  llm_tokens_out: number;

  tool_calls_count: number;
  tool_breakdown: Record<string, number>;
}

export interface InvestigationState {
  investigation_id?: string;
  user_id?: string;
  status: "idle" | "connecting" | "running" | "completed" | "error";
  currentNode: string;
  currentPhase: string;
  steps: WorkflowStep[];
  completedSteps: string[];
  traceEvents: TraceEvent[];

  initialEvidence?: Record<string, any>;
  toolCalls: ToolCall[];
  agentIterations: number;
  finalAssessment?: FinalAssessment;

  alertEvidence?: Record<string, any>;
  accountProfile?: Record<string, any>;
  networkEvidence?: {
    shared_device_users: any[];
    shared_device_count: number;
    flagged_connections: any[];
    fraud_ring_members: string[];
    network_topology: string;
    hub_score: number;
    subgraph_nodes: GraphNode[];
    subgraph_edges: GraphEdge[];
  };
  timelineEvidence?: Record<string, any>;

  typology?: TypologyAssessment;
  risk?: RiskAssessment;
  decision?: Decision;
  report?: string;

  performanceMetrics?: PerformanceMetrics;

  error?: string;
}
