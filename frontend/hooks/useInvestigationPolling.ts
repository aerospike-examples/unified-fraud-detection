"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import type {
  InvestigationState,
  WorkflowStep,
  ToolCall,
  FinalAssessment,
  PerformanceMetrics,
} from "./useInvestigation";

const POLL_INTERVAL_MS = 1500;

const initialState: InvestigationState = {
  status: "idle",
  currentNode: "",
  currentPhase: "",
  steps: [],
  completedSteps: [],
  traceEvents: [],
  toolCalls: [],
  agentIterations: 0,
};

const DEFAULT_STEPS: WorkflowStep[] = [
  { id: "alert_validation", name: "Alert Validation", description: "Validate initial alert", phase: "validation" },
  { id: "data_collection", name: "Data Collection", description: "Collect evidence", phase: "collection" },
  { id: "llm_agent", name: "AI Agent Investigation", description: "AI analysis", phase: "analysis" },
  { id: "report_generation", name: "Report Generation", description: "Generate report", phase: "reporting" },
];

export function useInvestigationPolling() {
  const [state, setState] = useState<InvestigationState>(initialState);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const investigationIdRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    investigationIdRef.current = null;
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const startInvestigation = useCallback(
    async (userId: string, _investigationId?: string) => {
      cleanup();

      setState({
        ...initialState,
        status: "connecting",
        user_id: userId,
        steps: DEFAULT_STEPS,
      });

      try {
        const res = await fetch(`/api/investigation/${userId}/start-poll`, {
          method: "POST",
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `Start failed (${res.status})`);
        }

        const data = await res.json();
        const invId: string = data.investigation_id;
        investigationIdRef.current = invId;

        setState((prev) => ({
          ...prev,
          status: "running",
          investigation_id: invId,
          user_id: userId,
        }));

        intervalRef.current = setInterval(async () => {
          try {
            const pollRes = await fetch(`/api/investigation/${invId}/poll`);
            if (!pollRes.ok) return;
            const progress = await pollRes.json();

            setState((prev) => {
              const toolCalls: ToolCall[] = (progress.toolCalls || []).map(
                (tc: any) => ({
                  tool: tc.tool || "",
                  params: tc.params || {},
                  result: tc.result,
                  timestamp: tc.timestamp || "",
                  iteration: tc.iteration || 0,
                })
              );

              const updates: Partial<InvestigationState> = {
                currentNode: progress.currentNode || prev.currentNode,
                currentPhase: progress.currentPhase || prev.currentPhase,
                completedSteps: progress.completedSteps || prev.completedSteps,
                toolCalls,
                agentIterations: progress.agentIterations ?? prev.agentIterations,
              };

              if (progress.initialEvidence) {
                updates.initialEvidence = progress.initialEvidence;
              }
              if (progress.alertEvidence) {
                updates.alertEvidence = progress.alertEvidence;
              }
              if (progress.finalAssessment) {
                updates.finalAssessment = progress.finalAssessment as FinalAssessment;
              }
              if (progress.report) {
                updates.report = progress.report;
              }
              if (progress.performanceMetrics) {
                updates.performanceMetrics = progress.performanceMetrics as PerformanceMetrics;
              }

              const status: InvestigationState["status"] =
                progress.status === "completed"
                  ? "completed"
                  : progress.status === "error"
                  ? "error"
                  : prev.status;

              if (progress.error) {
                updates.error = progress.error;
              }

              if (status === "completed" || status === "error") {
                if (intervalRef.current) {
                  clearInterval(intervalRef.current);
                  intervalRef.current = null;
                }
              }

              return { ...prev, ...updates, status };
            });
          } catch {
            // Network blip — keep polling
          }
        }, POLL_INTERVAL_MS);
      } catch (error) {
        setState((prev) => ({
          ...prev,
          status: "error",
          error: error instanceof Error ? error.message : "Failed to start investigation",
        }));
      }
    },
    [cleanup]
  );

  const stopInvestigation = useCallback(() => {
    cleanup();
    setState((prev) => ({
      ...prev,
      status: prev.status === "completed" ? "completed" : "idle",
    }));
  }, [cleanup]);

  const reset = useCallback(() => {
    cleanup();
    setState(initialState);
  }, [cleanup]);

  const loadExistingInvestigation = useCallback(async (userId: string): Promise<boolean> => {
    try {
      const response = await fetch(`/api/investigation/user/${userId}/latest`);
      if (!response.ok) return false;

      const data = await response.json();
      if (!data.found || !data.investigation) return false;

      const inv = data.investigation;

      setState({
        investigation_id: inv.investigation_id,
        user_id: inv.user_id,
        status: "completed",
        currentNode: "report_generation",
        currentPhase: "complete",
        steps: DEFAULT_STEPS,
        completedSteps: inv.completed_steps || ["alert_validation", "data_collection", "llm_agent", "report_generation"],
        initialEvidence: inv.initial_evidence,
        finalAssessment: inv.final_assessment,
        toolCalls: inv.tool_calls || [],
        traceEvents: [],
        report: inv.report_markdown,
        agentIterations: inv.agent_iterations || 0,
      });

      return true;
    } catch {
      return false;
    }
  }, []);

  const getStepStatus = useCallback(
    (stepId: string): "pending" | "running" | "completed" => {
      if (state.completedSteps.includes(stepId)) return "completed";
      if (state.currentNode === stepId) return "running";
      return "pending";
    },
    [state.completedSteps, state.currentNode]
  );

  const progress =
    state.steps.length > 0
      ? Math.round((state.completedSteps.length / state.steps.length) * 100)
      : 0;

  return {
    ...state,
    progress,
    startInvestigation,
    stopInvestigation,
    reset,
    getStepStatus,
    loadExistingInvestigation,
  };
}
