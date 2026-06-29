"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ShieldAlert, Check, Loader2, FileText } from "lucide-react";
import { DISPOSITIONS, type PendingAction } from "@/hooks/useInvestigation";

// Maps the agent's decision codes to analyst-facing labels.
const DECISION_LABELS: Record<string, string> = {
  temporary_freeze: "Temporary Freeze",
  full_block: "Full Block",
  escalate_compliance: "Escalate to Compliance",
  step_up_auth: "Step-up Authentication",
  allow_monitor: "Allow & Monitor",
};

interface ActionApprovalCardProps {
  pendingAction: PendingAction;
  onApprove: () => void;
  /** Reject the agent's action and enact a different disposition instead. */
  onOverride: (decision: string) => void;
  /** Open the full report + decide dialog. */
  onReview?: () => void;
  /** True while the resume stream is in flight (buttons disabled). */
  submitting?: boolean;
}

export function ActionApprovalCard({
  pendingAction,
  onApprove,
  onOverride,
  onReview,
  submitting = false,
}: ActionApprovalCardProps) {
  const decisionLabel =
    DECISION_LABELS[pendingAction.decision] || pendingAction.decision;
  // Alternatives = every disposition except the one the agent recommended.
  const alternatives = DISPOSITIONS.filter((d) => d.id !== pendingAction.decision);

  return (
    <Card className="border-2 border-amber-400 bg-amber-50 shadow-md animate-in fade-in">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-amber-900">
          <ShieldAlert className="h-5 w-5 text-amber-600" />
          Analyst Approval Required
        </CardTitle>
        <p className="text-sm text-amber-800">
          The AI agent has paused and is requesting approval before taking a
          destructive action on this account.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg border border-amber-200 bg-white p-4 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Proposed Action
            </span>
            <span className="rounded-full bg-red-100 px-3 py-1 text-sm font-semibold text-red-700">
              {decisionLabel}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Account
            </span>
            <span className="font-mono text-sm text-slate-900">
              {pendingAction.account_id}
            </span>
          </div>
          {pendingAction.reason && (
            <div className="pt-1">
              <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Reason
              </span>
              <p className="mt-1 text-sm text-slate-700">
                {pendingAction.reason}
              </p>
            </div>
          )}
        </div>

        {onReview && (
          <Button
            onClick={onReview}
            variant="outline"
            className="w-full border-amber-300 bg-white text-amber-800 hover:bg-amber-100"
          >
            <FileText className="h-4 w-4" />
            Review full report &amp; decide
          </Button>
        )}

        <Button
          onClick={onApprove}
          disabled={submitting}
          className="w-full bg-red-600 hover:bg-red-700 text-white"
        >
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Approve &amp; Enact: {decisionLabel}
        </Button>

        <div className="pt-1">
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
            Or set a different disposition
          </p>
          <div className="grid grid-cols-1 gap-2">
            {alternatives.map((d) => (
              <Button
                key={d.id}
                onClick={() => onOverride(d.id)}
                disabled={submitting}
                variant="outline"
                className="justify-start border-slate-300 text-slate-700 hover:bg-slate-100"
              >
                {d.label}
              </Button>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default ActionApprovalCard;
