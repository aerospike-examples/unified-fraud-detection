"use client";

import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Dialog, DialogHeader, DialogTitle, DialogDescription, DialogContent, DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ShieldAlert, Check } from "lucide-react";
import { DISPOSITIONS, type PendingAction } from "@/hooks/useInvestigation";

const MermaidDiagram = dynamic(() => import("@/components/MermaidDiagram"), { ssr: false });

const DECISION_LABELS: Record<string, string> = {
  temporary_freeze: "Temporary Freeze",
  full_block: "Full Block",
  escalate_compliance: "Escalate to Compliance",
  step_up_auth: "Step-up Authentication",
  allow_monitor: "Allow & Monitor",
};

interface DecisionReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pendingAction: PendingAction;
  report?: string;
  onApprove: () => void;
  /** Reject the agent's action and enact a different disposition instead. */
  onOverride: (decision: string) => void;
}

// Read the full investigation report and decide the agent's proposed action in
// one focused view (opens when the agent pauses for approval).
export function DecisionReviewDialog({
  open, onOpenChange, pendingAction, report, onApprove, onOverride,
}: DecisionReviewDialogProps) {
  const decisionLabel = DECISION_LABELS[pendingAction.decision] || pendingAction.decision;
  const alternatives = DISPOSITIONS.filter((d) => d.id !== pendingAction.decision);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2 text-slate-900">
          <ShieldAlert className="h-5 w-5 text-amber-600" />
          Review report &amp; decide
        </DialogTitle>
        <DialogDescription className="text-slate-600">
          The agent recommends{" "}
          <span className="font-semibold text-red-700">{decisionLabel}</span> on{" "}
          <span className="font-mono text-slate-900">{pendingAction.account_id}</span>. Read the
          full investigation report below, then approve or reject.
        </DialogDescription>
      </DialogHeader>

      <DialogContent>
        {pendingAction.reason && (
          <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <span className="font-medium">Reason: </span>{pendingAction.reason}
          </div>
        )}
        <div className="max-h-[55vh] overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-4">
          {report ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: ({ children }) => <h1 className="text-xl font-bold text-slate-900 mb-3 mt-4 first:mt-0 pb-2 border-b border-slate-200">{children}</h1>,
                h2: ({ children }) => <h2 className="text-lg font-bold text-slate-900 mb-2 mt-4 first:mt-0">{children}</h2>,
                h3: ({ children }) => <h3 className="text-base font-semibold text-slate-800 mb-2 mt-4 first:mt-0">{children}</h3>,
                p: ({ children }) => <p className="text-sm text-slate-700 leading-relaxed mb-3">{children}</p>,
                ul: ({ children }) => <ul className="list-disc pl-5 space-y-1 mb-3 text-sm text-slate-700">{children}</ul>,
                ol: ({ children }) => <ol className="list-decimal pl-5 space-y-1 mb-3 text-sm text-slate-700">{children}</ol>,
                li: ({ children }) => <li className="text-sm text-slate-700">{children}</li>,
                strong: ({ children }) => <strong className="font-semibold text-slate-900">{children}</strong>,
                table: ({ children }) => <table className="w-full text-sm my-3 border border-slate-200 rounded">{children}</table>,
                th: ({ children }) => <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600 uppercase border-b border-slate-200">{children}</th>,
                td: ({ children }) => <td className="px-3 py-2 text-sm text-slate-700 border-b border-slate-100">{children}</td>,
                code: ({ className, children, ...props }) => {
                  const match = /language-mermaid/.exec(className || "");
                  if (match) {
                    const chart = String(children).replace(/\n$/, "");
                    return <MermaidDiagram chart={chart} />;
                  }
                  return <code className="bg-slate-200 text-slate-800 px-1.5 py-0.5 rounded text-xs font-mono" {...props}>{children}</code>;
                },
                pre: ({ children }) => <div className="my-3">{children}</div>,
                hr: () => <hr className="my-4 border-slate-200" />,
              }}
            >
              {report}
            </ReactMarkdown>
          ) : (
            <p className="text-sm text-slate-500">The report is still being generated…</p>
          )}
        </div>
      </DialogContent>

      <DialogFooter className="flex-col items-stretch gap-3 sm:flex-col sm:items-stretch sm:space-x-0">
        <Button onClick={onApprove} className="w-full bg-red-600 hover:bg-red-700 text-white">
          <Check className="h-4 w-4 mr-1" />
          Approve &amp; Enact: {decisionLabel}
        </Button>
        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
            Or set a different disposition
          </p>
          <div className="grid grid-cols-2 gap-2">
            {alternatives.map((d) => (
              <Button
                key={d.id}
                variant="outline"
                onClick={() => onOverride(d.id)}
                className="justify-start border-slate-300 text-slate-700 hover:bg-slate-100"
              >
                {d.label}
              </Button>
            ))}
          </div>
        </div>
      </DialogFooter>
    </Dialog>
  );
}

export default DecisionReviewDialog;
