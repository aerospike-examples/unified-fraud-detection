"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Network, Smartphone, Activity, Loader2, CheckCircle2, Layers } from "lucide-react";
import { SPECIALISTS, type SpecialistName, type ToolCall } from "@/hooks/useInvestigation";

const ICONS: Record<SpecialistName, React.ReactNode> = {
  network_analyst: <Network className="h-4 w-4" />,
  device_analyst: <Smartphone className="h-4 w-4" />,
  velocity_analyst: <Activity className="h-4 w-4" />,
};

const BLURB: Record<SpecialistName, string> = {
  network_analyst: "Counterparties, fan-out, fraud rings",
  device_analyst: "Device sharing, spoofing, infra risk",
  velocity_analyst: "Velocity, bursts, amount anomalies",
};

interface EvidenceSpecialistsProps {
  specialistFindings: Partial<Record<SpecialistName, string>>;
  toolCalls: ToolCall[];
  /** True while the AI Investigation step is active (specialists may still be running). */
  active: boolean;
}

export function EvidenceSpecialists({ specialistFindings, toolCalls, active }: EvidenceSpecialistsProps) {
  const anyActivity =
    toolCalls.some((t) => t.agent && t.agent !== "investigator") ||
    Object.keys(specialistFindings).length > 0;

  // Only render once the parallel stage has produced something (or is running).
  if (!active && !anyActivity) return null;

  return (
    <Card className="bg-white border-slate-200 shadow-sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg text-slate-900">
          <Layers className="h-5 w-5 text-indigo-600" />
          Parallel Evidence Collection
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
            ADK ParallelAgent
          </span>
        </CardTitle>
        <CardDescription className="text-slate-500">
          Three specialist agents investigate concurrently, then the senior analyst synthesizes their findings.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {SPECIALISTS.map(({ id, label }) => {
            const finding = specialistFindings[id];
            const calls = toolCalls.filter((t) => t.agent === id);
            const done = !!finding;
            const working = active && !done;

            return (
              <div
                key={id}
                className={`rounded-lg border p-3 transition-colors ${
                  done ? "border-emerald-200 bg-emerald-50/40" : working ? "border-indigo-200 bg-indigo-50/40" : "border-slate-200 bg-slate-50"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-medium text-slate-900">
                    <span className="text-slate-500">{ICONS[id]}</span>
                    {label}
                  </div>
                  {done ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  ) : working ? (
                    <Loader2 className="h-4 w-4 animate-spin text-indigo-500" />
                  ) : null}
                </div>
                <p className="mt-0.5 text-xs text-slate-500">{BLURB[id]}</p>

                <div className="mt-2 text-xs text-slate-400">
                  {calls.length} tool {calls.length === 1 ? "call" : "calls"}
                </div>

                {finding ? (
                  <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-slate-700 line-clamp-[12]">
                    {finding}
                  </p>
                ) : working ? (
                  <p className="mt-2 text-xs italic text-indigo-500">Investigating…</p>
                ) : null}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

export default EvidenceSpecialists;
