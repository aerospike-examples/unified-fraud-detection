"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Brain, AlertTriangle, CheckCircle2, Snowflake, Ban, ShieldAlert, Eye } from "lucide-react";
import type { PriorCase } from "@/hooks/useInvestigation";

const DECISION: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  full_block: { label: "Confirmed Fraud", cls: "text-red-700 bg-red-50 border-red-200", icon: <Ban className="h-3.5 w-3.5" /> },
  temporary_freeze: { label: "Temporary Freeze", cls: "text-cyan-700 bg-cyan-50 border-cyan-200", icon: <Snowflake className="h-3.5 w-3.5" /> },
  escalate_compliance: { label: "Escalated", cls: "text-blue-700 bg-blue-50 border-blue-200", icon: <ShieldAlert className="h-3.5 w-3.5" /> },
  allow_monitor: { label: "Monitoring", cls: "text-indigo-700 bg-indigo-50 border-indigo-200", icon: <Eye className="h-3.5 w-3.5" /> },
  step_up_auth: { label: "Step-up Auth", cls: "text-indigo-700 bg-indigo-50 border-indigo-200", icon: <Eye className="h-3.5 w-3.5" /> },
  clear: { label: "Cleared", cls: "text-emerald-700 bg-emerald-50 border-emerald-200", icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
};

export function PriorCasesPanel({ priorCases }: { priorCases: PriorCase[] }) {
  if (!priorCases || priorCases.length === 0) return null;

  const anyFraud = priorCases.some((c) => c.decision === "full_block");

  return (
    <Card className={`shadow-sm ${anyFraud ? "border-2 border-amber-300 bg-amber-50/40" : "border-slate-200 bg-white"}`}>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg text-slate-900">
          <Brain className="h-5 w-5 text-violet-600" />
          Related Prior Cases
          <span className="rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700">
            ADK long-term memory
          </span>
        </CardTitle>
        <CardDescription className="text-slate-500">
          {priorCases.length} past investigation{priorCases.length === 1 ? "" : "s"} referenced this account or its
          connections — recalled from Aerospike-backed memory.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {priorCases.map((c) => {
          const d = DECISION[c.decision || ""] || { label: c.decision || "—", cls: "text-slate-600 bg-slate-50 border-slate-200", icon: null };
          return (
            <div key={c.investigation_id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3">
              <div className="min-w-0">
                <p className="font-medium text-slate-900">
                  {c.holder || c.user_id}
                  <span className="ml-2 font-mono text-xs text-slate-500">{c.account_id}</span>
                </p>
                <p className="text-xs text-slate-500">
                  typology: <span className="text-slate-700">{c.typology || "—"}</span>
                  {c.matched_on && c.matched_on.length > 0 && (
                    <>
                      {" · "}linked via <span className="font-mono text-slate-600">{c.matched_on.join(", ")}</span>
                    </>
                  )}
                </p>
              </div>
              <span className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium ${d.cls}`}>
                {d.icon}
                {d.label}
              </span>
            </div>
          );
        })}
        {anyFraud && (
          <p className="flex items-center gap-1.5 pt-1 text-xs font-medium text-amber-700">
            <AlertTriangle className="h-3.5 w-3.5" />
            A connected account was confirmed fraud in a prior investigation.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export default PriorCasesPanel;
