"use client";

import { useState, useCallback, useRef, useEffect } from "react";

// Connect directly to backend to avoid Next.js proxy buffering SSE
// In production, this would be configured via environment variable
const BACKEND_URL = typeof window !== 'undefined' 
  ? (process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:4000")
  : "http://localhost:4000";

// Types for investigation state
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

// New agentic workflow types
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
  agent?: string;
}

// The three parallel evidence-collection specialists (ADK ParallelAgent stage).
export type SpecialistName = "network_analyst" | "device_analyst" | "velocity_analyst";
export const SPECIALISTS: { id: SpecialistName; label: string }[] = [
  { id: "network_analyst", label: "Network Analyst" },
  { id: "device_analyst", label: "Device Analyst" },
  { id: "velocity_analyst", label: "Velocity Analyst" },
];

// Human-in-the-loop: a destructive action the agent has paused on, awaiting
// analyst approval before it is enforced.
export interface PendingAction {
  investigation_id: string;
  user_id: string;
  fc_id: string;
  hint: string;
  decision: string;
  account_id: string;
  reason: string;
}

// An action the agent actually enacted (after approval, or immediately for
// non-destructive decisions).
export interface EnactedAction {
  status: string;
  action: string;
  account_id: string;
  effect: string;
  ok?: boolean;
}

export interface PerformanceMetrics {
  // Timing
  total_duration_ms: number;
  node_durations: Record<string, number>;
  
  // Database calls
  total_db_calls: number;
  kv_calls: number;
  graph_calls: number;
  kv_time_ms: number;
  graph_time_ms: number;
  
  // Checkpoints
  checkpoint_calls: number;
  checkpoint_time_ms: number;
  
  // LLM
  llm_calls: number;
  llm_time_ms: number;
  llm_tokens_in: number;
  llm_tokens_out: number;
  
  // Tool usage
  tool_calls_count: number;
  tool_breakdown: Record<string, number>;
}

export interface InvestigationState {
  investigation_id?: string;
  user_id?: string;
  status: "idle" | "connecting" | "running" | "awaiting_confirmation" | "completed" | "error";
  currentNode: string;
  currentPhase: string;
  steps: WorkflowStep[];
  completedSteps: string[];
  traceEvents: TraceEvent[];
  
  // Agentic workflow state
  initialEvidence?: Record<string, any>;
  toolCalls: ToolCall[];
  agentIterations: number;
  finalAssessment?: FinalAssessment;

  // Parallel evidence-collection specialist findings (keyed by specialist id)
  specialistFindings: Partial<Record<SpecialistName, string>>;

  // Human-in-the-loop action approval
  pendingAction?: PendingAction;
  enactedActions: EnactedAction[];

  // Legacy evidence (for backwards compatibility)
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
  
  // LLM results
  typology?: TypologyAssessment;
  risk?: RiskAssessment;
  decision?: Decision;
  report?: string;
  
  // Performance metrics
  performanceMetrics?: PerformanceMetrics;
  
  // Error
  error?: string;
}

const initialState: InvestigationState = {
  status: "idle",
  currentNode: "",
  currentPhase: "",
  steps: [],
  completedSteps: [],
  traceEvents: [],
  toolCalls: [],
  agentIterations: 0,
  enactedActions: [],
  specialistFindings: {},
};

export function useInvestigation() {
  const [state, setState] = useState<InvestigationState>(initialState);
  const eventSourceRef = useRef<EventSource | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  // Latest pending action, kept in a ref so approveAction reads it without
  // stale-closure or in-updater side effects.
  const pendingActionRef = useRef<PendingAction | null>(null);

  // Cleanup function
  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return cleanup;
  }, [cleanup]);

  // Attach all SSE listeners to an EventSource. Shared by the initial run and
  // the human-in-the-loop resume stream so both react to the same events.
  const attachStreamListeners = useCallback((eventSource: EventSource) => {
        eventSource.onopen = () => {
          console.log("[Investigation] SSE connection opened");
          setState((prev) => ({
            ...prev,
            status: "running",
          }));
        };

        // Handle 'start' event
        eventSource.addEventListener("start", (event) => {
          console.log("[Investigation] Start event received:", event.data);
          const data = JSON.parse(event.data);
          setState((prev) => ({
            ...prev,
            investigation_id: data.investigation_id,
            user_id: data.user_id,
            steps: data.steps || [],
            status: "running",
          }));
        });

        // Handle 'trace' event
        eventSource.addEventListener("trace", (event) => {
          const trace: TraceEvent = JSON.parse(event.data);
          console.log(`[Investigation] Trace: ${trace.type} - ${trace.node}`, trace.data);
          setState((prev) => {
            const newCompletedSteps = [...prev.completedSteps];
            const newToolCalls = [...prev.toolCalls];
            let newIterations = prev.agentIterations;
            let newAssessment = prev.finalAssessment;
            
            // Mark step as complete when node_complete event arrives
            if (trace.type === "node_complete" && !newCompletedSteps.includes(trace.node)) {
              newCompletedSteps.push(trace.node);
              console.log(`[Investigation] Step completed: ${trace.node}`);
            }
            
            // Track tool calls from agent
            if (trace.type === "tool_call" && trace.data) {
              const toolCall: ToolCall = {
                tool: trace.data.tool || "unknown",
                params: trace.data.params || {},
                timestamp: trace.timestamp || trace.data.timestamp,
                iteration: trace.data.iteration || newIterations,
                agent: trace.data.agent,
              };
              newToolCalls.push(toolCall);
              console.log(`[Investigation] Tool call: ${toolCall.tool} [${toolCall.agent || "?"}]`);
            }

            // A parallel specialist finished and reported its findings
            let newFindings = prev.specialistFindings;
            if (trace.type === "specialist_finding" && trace.data?.agent) {
              newFindings = {
                ...prev.specialistFindings,
                [trace.data.agent as SpecialistName]: trace.data.finding || "",
              };
            }
            
            // Track agent iterations
            if (trace.type === "agent_iteration" && trace.data?.iteration) {
              newIterations = Math.max(newIterations, trace.data.iteration);
            }
            
            // Track final assessment
            if (trace.type === "assessment" && trace.data) {
              newAssessment = {
                typology: trace.data.typology || "unknown",
                risk_level: trace.data.risk_level || "unknown",
                risk_score: trace.data.risk_score || 0,
                decision: trace.data.decision || "pending",
                reasoning: trace.data.reasoning || "",
                iteration: trace.data.iteration || newIterations,
                tool_calls_made: newToolCalls.length,
              };
            }
            
            return {
              ...prev,
              currentNode: trace.node,
              traceEvents: [...prev.traceEvents, trace],
              completedSteps: newCompletedSteps,
              toolCalls: newToolCalls,
              agentIterations: newIterations,
              finalAssessment: newAssessment,
              specialistFindings: newFindings,
            };
          });
        });

        // Handle 'progress' event
        eventSource.addEventListener("progress", (event) => {
          const data = JSON.parse(event.data);
          
          setState((prev) => {
            const updates: Partial<InvestigationState> = {
              currentNode: data.node || prev.currentNode,
              currentPhase: data.phase || prev.currentPhase,
            };

            // Update evidence based on node
            if (data.alert_evidence) {
              updates.alertEvidence = data.alert_evidence;
            }
            if (data.account_profile) {
              updates.accountProfile = data.account_profile;
            }
            if (data.network_evidence) {
              updates.networkEvidence = data.network_evidence;
            }
            if (data.timeline_evidence) {
              updates.timelineEvidence = data.timeline_evidence;
            }
            if (data.typology_assessment) {
              updates.typology = data.typology_assessment;
            }
            if (data.risk_assessment) {
              updates.risk = data.risk_assessment;
            }
            if (data.decision) {
              updates.decision = data.decision;
            }
            if (data.report_markdown) {
              updates.report = data.report_markdown;
            }
            
            // Handle new agentic workflow data
            if (data.initial_evidence) {
              updates.initialEvidence = data.initial_evidence;
            }
            if (data.final_assessment) {
              updates.finalAssessment = data.final_assessment;
            }
            if (data.tool_calls) {
              updates.toolCalls = data.tool_calls;
            }
            if (data.agent_iterations !== undefined) {
              updates.agentIterations = data.agent_iterations;
            }
            if (data.enacted_actions) {
              updates.enactedActions = data.enacted_actions;
            }
            if (data.specialist_findings) {
              updates.specialistFindings = data.specialist_findings;
            }

            return { ...prev, ...updates };
          });
        });

        // Handle 'action_confirmation_required' event (human-in-the-loop).
        // The agent has paused on a destructive action; surface it for approval.
        eventSource.addEventListener("action_confirmation_required", (event) => {
          const data = JSON.parse(event.data) as PendingAction;
          console.log("[Investigation] Action confirmation required:", data);
          eventSource.close();
          pendingActionRef.current = data;
          setState((prev) => ({
            ...prev,
            status: "awaiting_confirmation",
            pendingAction: data,
          }));
        });

        // Handle 'metrics' event
        eventSource.addEventListener("metrics", (event) => {
          const data = JSON.parse(event.data);
          console.log("[Investigation] Performance metrics received:", data);
          setState((prev) => ({
            ...prev,
            performanceMetrics: data.data || data,
          }));
        });

        // Handle 'complete' event
        eventSource.addEventListener("complete", (event) => {
          const data = JSON.parse(event.data);
          setState((prev) => ({
            ...prev,
            status: "completed",
            investigation_id: data.investigation_id,
            pendingAction: undefined,
          }));
          eventSource.close();
        });

        // Handle 'error' event
        eventSource.addEventListener("error", (event) => {
          // Check if it's a custom error event or connection error
          if (event instanceof MessageEvent && event.data) {
            try {
              const data = JSON.parse(event.data);
              setState((prev) => ({
                ...prev,
                status: "error",
                error: data.error || "Unknown error",
              }));
            } catch {
              // Not a custom error, likely connection issue
            }
          }
        });

        // Handle connection errors
        eventSource.onerror = (error) => {
          console.error("[Investigation] SSE error:", error, "readyState:", eventSource.readyState);
          if (eventSource.readyState === EventSource.CLOSED) {
            setState((prev) => {
              // Don't clobber a clean stop: completed runs and HITL pauses both
              // close the stream on purpose.
              if (prev.status === "completed" || prev.status === "awaiting_confirmation") {
                return prev;
              }
              return { ...prev, status: "error", error: "Connection lost" };
            });
          }
        };

        // Generic message handler to catch any unhandled events
        eventSource.onmessage = (event) => {
          console.log("[Investigation] Generic message:", event.data);
        };
  }, []);

  // Start investigation
  const startInvestigation = useCallback(
    async (userId: string, investigationId?: string) => {
      cleanup();

      setState((prev) => ({
        ...initialState,
        status: "connecting",
        user_id: userId,
        investigation_id: investigationId,
      }));

      try {
        // Build SSE URL - connect directly to backend
        let url = `${BACKEND_URL}/investigation/${userId}/stream`;
        if (investigationId) {
          url += `?investigation_id=${investigationId}`;
        }

        console.log("[Investigation] Connecting to:", url);

        const eventSource = new EventSource(url);
        eventSourceRef.current = eventSource;
        attachStreamListeners(eventSource);
      } catch (error) {
        setState((prev) => ({
          ...prev,
          status: "error",
          error: error instanceof Error ? error.message : "Failed to start investigation",
        }));
      }
    },
    [cleanup, attachStreamListeners]
  );

  // Approve or reject the agent's pending destructive action (human-in-the-loop).
  // Opens the resume SSE stream so the agent continues with the analyst's decision.
  const approveAction = useCallback(
    (approved: boolean) => {
      const pending = pendingActionRef.current;
      if (!pending) return;

      cleanup();
      pendingActionRef.current = null;
      const url = `${BACKEND_URL}/investigation/${pending.investigation_id}/resume?approved=${approved}`;
      console.log("[Investigation] Resuming with decision:", { approved, url });

      try {
        const eventSource = new EventSource(url);
        eventSourceRef.current = eventSource;
        attachStreamListeners(eventSource);
        setState((prev) => ({ ...prev, status: "running", pendingAction: undefined }));
      } catch (error) {
        setState((prev) => ({
          ...prev,
          status: "error",
          error: error instanceof Error ? error.message : "Failed to resume investigation",
        }));
      }
    },
    [cleanup, attachStreamListeners]
  );

  // Stop investigation
  const stopInvestigation = useCallback(() => {
    cleanup();
    setState((prev) => ({
      ...prev,
      status: prev.status === "completed" ? "completed" : "idle",
    }));
  }, [cleanup]);

  // Reset state
  const reset = useCallback(() => {
    cleanup();
    pendingActionRef.current = null;
    setState(initialState);
  }, [cleanup]);

  // Load existing investigation from KV store
  const loadExistingInvestigation = useCallback(async (userId: string): Promise<boolean> => {
    try {
      console.log(`[Investigation] Checking for existing investigation for user ${userId}`);
      
      const response = await fetch(`/api/investigation/user/${userId}/latest`);
      if (!response.ok) {
        console.log("[Investigation] No existing investigation found (API error)");
        return false;
      }
      
      const data = await response.json();
      
      if (!data.found || !data.investigation) {
        console.log("[Investigation] No existing investigation found for user");
        return false;
      }
      
      const inv = data.investigation;
      console.log(`[Investigation] Found existing investigation: ${inv.investigation_id}`);
      
      // Restore state from saved investigation
      setState({
        investigation_id: inv.investigation_id,
        user_id: inv.user_id,
        status: "completed",
        currentNode: "report_generation",
        currentPhase: "complete",
        steps: [
          { id: "alert_validation", name: "Alert Validation", description: "Validate initial alert", phase: "validation" },
          { id: "data_collection", name: "Data Collection", description: "Collect evidence", phase: "collection" },
          { id: "llm_agent", name: "AI Agent Investigation", description: "AI analysis", phase: "analysis" },
          { id: "report_generation", name: "Report Generation", description: "Generate report", phase: "reporting" },
        ],
        completedSteps: inv.completed_steps || ["alert_validation", "data_collection", "llm_agent", "report_generation"],
        initialEvidence: inv.initial_evidence,
        finalAssessment: inv.final_assessment,
        toolCalls: inv.tool_calls || [],
        traceEvents: [],
        report: inv.report_markdown,
        agentIterations: inv.agent_iterations || 0,
        enactedActions: inv.enacted_actions || [],
        specialistFindings: inv.specialist_findings || {},
      });
      
      console.log("[Investigation] Restored existing investigation state");
      return true;
      
    } catch (error) {
      console.error("[Investigation] Error loading existing investigation:", error);
      return false;
    }
  }, []);

  // Get step status
  const getStepStatus = useCallback(
    (stepId: string): "pending" | "running" | "completed" => {
      if (state.completedSteps.includes(stepId)) {
        return "completed";
      }
      if (state.currentNode === stepId) {
        return "running";
      }
      return "pending";
    },
    [state.completedSteps, state.currentNode]
  );

  // Calculate progress percentage
  const progress = state.steps.length > 0
    ? Math.round((state.completedSteps.length / state.steps.length) * 100)
    : 0;

  return {
    ...state,
    progress,
    startInvestigation,
    stopInvestigation,
    approveAction,
    reset,
    getStepStatus,
    loadExistingInvestigation,
  };
}
