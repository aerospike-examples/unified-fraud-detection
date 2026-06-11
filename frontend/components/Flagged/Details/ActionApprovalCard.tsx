"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ShieldAlert, Check, X, Loader2 } from "lucide-react";
import type { PendingAction } from "@/hooks/useInvestigation";

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
  onReject: () => void;
  /** True while the resume stream is in flight (buttons disabled). */
  submitting?: boolean;
}

export function ActionApprovalCard({
  pendingAction,
  onApprove,
  onReject,
  submitting = false,
}: ActionApprovalCardProps) {
  const decisionLabel =
    DECISION_LABELS[pendingAction.decision] || pendingAction.decision;

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

        <div className="flex gap-3">
          <Button
            onClick={onApprove}
            disabled={submitting}
            className="flex-1 bg-red-600 hover:bg-red-700 text-white"
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Check className="h-4 w-4" />
            )}
            Approve & Enact
          </Button>
          <Button
            onClick={onReject}
            disabled={submitting}
            variant="outline"
            className="flex-1 border-slate-300 text-slate-700 hover:bg-slate-100"
          >
            <X className="h-4 w-4" />
            Reject
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default ActionApprovalCard;
