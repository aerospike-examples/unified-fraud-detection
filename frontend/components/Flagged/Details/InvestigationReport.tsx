"use client";

import React, { useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const MermaidDiagram = dynamic(() => import("@/components/MermaidDiagram"), {
  ssr: false,
  loading: () => <div className="bg-slate-100 rounded-md p-4 text-center text-sm text-slate-500 animate-pulse">Loading diagram...</div>,
});
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  AlertTriangle,
  CheckCircle,
  XCircle,
  Clock,
  Shield,
  Scale,
  Brain,
  FileText,
  Network,
  TrendingUp,
  User,
  Activity,
  Download,
} from "lucide-react";
import { FraudRingGraph } from "./FraudRingGraph";
import type {
  TypologyAssessment,
  RiskAssessment,
  Decision,
  FinalAssessment,
  ToolCall,
} from "@/hooks/useInvestigation";

interface InvestigationReportProps {
  // User ID for fraud ring graph
  userId?: string;

  // New agentic workflow props
  finalAssessment?: FinalAssessment;
  toolCalls?: ToolCall[];
  agentIterations?: number;
  initialEvidence?: Record<string, any>;
  
  // Step completion tracking - only show content after step completes
  completedSteps?: string[];
  
  // Legacy props (for backwards compatibility)
  typology?: TypologyAssessment;
  risk?: RiskAssessment;
  decision?: Decision;
  report?: string;
  accountProfile?: Record<string, any>;
  networkEvidence?: Record<string, any>;
}

function normalizeMarkdown(text: string): string {
  if (!text) return text;

  let s = text.trim();

  // Strip markdown code fences that some LLMs wrap their response in
  if (s.includes("```markdown")) {
    s = s.replace(/```markdown\s*\n/g, "");
    s = s.replace(/```\s*$/g, "");
    s = s.trim();
  }

  // Normalize em/en-dashes used as bullet markers
  s = s.replace(/[—–]\s*\*\*/g, "- **");
  s = s.replace(/[—–]\s+([A-Z])/g, "- $1");

  // Remove stray lone `#` that appear between sections (e.g. "--- # # Heading" → "---\n\n## Heading")
  // Pattern: `# # Heading` (lone # separator before actual heading) → `## Heading`
  s = s.replace(/(?:^|\n)\s*#\s*\n?\s*(#{1,4}\s)/gm, "\n\n$1");
  // Inline version: `text # # Heading` → `text\n\n## Heading`
  s = s.replace(/(\S)\s+#\s+(#{1,4}\s)/g, "$1\n\n$2");

  // Ensure newlines before heading markers (# to ####)
  s = s.replace(/([^\n])(#{1,4}\s)/g, "$1\n\n$2");
  // Ensure newlines before bullet points
  s = s.replace(/([^\n])(- )/g, "$1\n$2");
  // Ensure newlines around horizontal rules
  s = s.replace(/([^\n])(---+)/g, "$1\n\n$2");
  s = s.replace(/(---+)([^\n])/g, "$1\n\n$2");

  // Split heading lines that have body text appended
  s = s.split("\n").map((line) => {
    const m = line.match(
      /^(#{1,4}\s+.{3,80}?(?:Summary|Factors|Analysis|Recommendation|Steps|Evidence|Assessment|Report|Rationale|Conclusion|Details|Overview|Findings|Profile|Typology|Information))\s+([A-Z(])/
    );
    if (m) return m[1] + "\n\n" + m[2] + line.slice(m[0].length);
    return line;
  }).join("\n");

  // Ensure blank line after headings when followed by body text
  s = s.replace(/^(#{1,4}\s+[^\n]+)\n([^#\-\*\n])/gm, "$1\n\n$2");
  // Collapse excess newlines
  s = s.replace(/\n{3,}/g, "\n\n");

  return s.trim();
}

const riskLevelColors: Record<string, string> = {
  low: "bg-emerald-100 text-emerald-700 border-emerald-200",
  medium: "bg-amber-100 text-amber-700 border-amber-200",
  high: "bg-orange-100 text-orange-700 border-orange-200",
  critical: "bg-red-100 text-red-700 border-red-200",
};

const riskLevelIcons: Record<string, React.ReactNode> = {
  low: <CheckCircle className="w-5 h-5 text-emerald-500" />,
  medium: <AlertTriangle className="w-5 h-5 text-amber-500" />,
  high: <AlertTriangle className="w-5 h-5 text-orange-500" />,
  critical: <XCircle className="w-5 h-5 text-red-500" />,
};

const actionLabels: Record<string, string> = {
  allow_monitor: "Allow with Enhanced Monitoring",
  step_up_auth: "Require Step-up Authentication",
  temporary_freeze: "Temporary Account Freeze",
  full_block: "Full Account Block",
  escalate_compliance: "Escalate to Compliance",
};

const actionColors: Record<string, string> = {
  allow_monitor: "bg-emerald-600 text-white",
  step_up_auth: "bg-amber-500 text-white",
  temporary_freeze: "bg-orange-500 text-white",
  full_block: "bg-red-600 text-white",
  escalate_compliance: "bg-purple-600 text-white",
};

export function InvestigationReport({
  userId,
  finalAssessment,
  toolCalls,
  agentIterations,
  initialEvidence,
  completedSteps = [],
  typology,
  risk,
  decision,
  report,
  accountProfile,
  networkEvidence,
}: InvestigationReportProps) {
  // Check which steps are completed to control what to show
  const dataCollectionComplete = completedSteps.includes('data_collection');
  const llmAgentComplete = completedSteps.includes('llm_agent');
  const reportGenerationComplete = completedSteps.includes('report_generation');
  
  // ALL steps must be complete before showing AI assessment results
  const allStepsComplete = dataCollectionComplete && llmAgentComplete && reportGenerationComplete;
  
  // Check if we have any data to show
  const hasAgentResults = finalAssessment || (toolCalls && toolCalls.length > 0);
  const hasLegacyResults = risk || decision || report;
  
  const reportContentRef = useRef<HTMLDivElement>(null);
  
  const handleDownloadReport = useCallback(async () => {
    if (!reportContentRef.current) return;

    // Dynamic imports to avoid SSR issues
    const html2canvasModule = await import("html2canvas-pro");
    const html2canvas = html2canvasModule.default;
    const { jsPDF } = await import("jspdf");

    const element = reportContentRef.current;
    const fileName = `fraud-investigation-report-${new Date().toISOString().split("T")[0]}.pdf`;

    // Temporarily remove height cap & overflow so html2canvas captures the FULL report
    const prevMaxH = element.style.maxHeight;
    const prevOverflow = element.style.overflow;
    element.style.maxHeight = "none";
    element.style.overflow = "visible";

    // Render the element to a canvas (html2canvas-pro supports oklch natively)
    const canvas = await html2canvas(element, {
      scale: 2,
      useCORS: true,
      logging: false,
    });

    // Restore original scroll constraints
    element.style.maxHeight = prevMaxH;
    element.style.overflow = prevOverflow;

    const pdf = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });

    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    const margin = 10;
    const contentWidth = pageWidth - 2 * margin;

    // Scale the canvas image to fit the page width
    const imgWidth = contentWidth;
    const imgHeight = (canvas.height * imgWidth) / canvas.width;

    // Handle multi-page: slice the tall image into page-sized chunks
    const usableHeight = pageHeight - 2 * margin;
    let yOffset = 0;
    let page = 0;

    while (yOffset < imgHeight) {
      if (page > 0) pdf.addPage();

      // Create a cropped canvas for this page slice
      const sliceCanvas = document.createElement("canvas");
      sliceCanvas.width = canvas.width;
      const srcYStart = Math.round((yOffset / imgHeight) * canvas.height);
      const srcSliceH = Math.round((usableHeight / imgHeight) * canvas.height);
      const actualSliceH = Math.min(srcSliceH, canvas.height - srcYStart);
      sliceCanvas.height = actualSliceH;

      const ctx = sliceCanvas.getContext("2d");
      if (ctx) {
        ctx.drawImage(
          canvas,
          0, srcYStart, canvas.width, actualSliceH,
          0, 0, canvas.width, actualSliceH
        );
      }
      const sliceData = sliceCanvas.toDataURL("image/jpeg", 0.95);
      const slicePageH = (actualSliceH * imgWidth) / canvas.width;
      pdf.addImage(sliceData, "JPEG", margin, margin, imgWidth, slicePageH);

      yOffset += usableHeight;
      page++;
    }

    pdf.save(fileName);
  }, []);
  
  if (!hasAgentResults && !hasLegacyResults) {
    return (
      <Card className="bg-white border-slate-200 shadow-sm">
        <CardContent className="pt-6">
          <div className="text-center text-slate-500">
            <FileText className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p className="text-slate-600">Investigation report will appear here when analysis is complete</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* FIRST: Evidence Collected - only show after data_collection step completes */}
      {initialEvidence && dataCollectionComplete && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg flex items-center gap-2 text-slate-900">
              <Activity className="w-5 h-5 text-cyan-600" />
              Evidence Collected
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              {initialEvidence.profile && (
                <>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Name</span>
                    <span className="text-slate-900">{initialEvidence.profile.name || "Unknown"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Location</span>
                    <span className="text-slate-900">{initialEvidence.profile.location || "Unknown"}</span>
                  </div>
                </>
              )}
              {initialEvidence.account_metrics && (
                <>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Account Age</span>
                    <span className="text-slate-900">{initialEvidence.account_metrics.account_age_days || 0} days</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Devices</span>
                    <span className="text-slate-900">{initialEvidence.account_metrics.device_count || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Shared Devices</span>
                    <span className={initialEvidence.account_metrics.shared_device_count > 0 ? "text-amber-600 font-medium" : "text-slate-900"}>
                      {initialEvidence.account_metrics.shared_device_count || 0}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Flagged Device</span>
                    <span className={initialEvidence.account_metrics.has_flagged_device ? "text-red-600 font-medium" : "text-slate-900"}>
                      {initialEvidence.account_metrics.has_flagged_device ? "Yes" : "No"}
                    </span>
                  </div>
                </>
              )}
              <div className="flex justify-between">
                <span className="text-slate-500">Accounts</span>
                <span className="text-slate-900">{initialEvidence.accounts ? Object.keys(initialEvidence.accounts).length : 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Devices</span>
                <span className="text-slate-900">{initialEvidence.devices ? Object.keys(initialEvidence.devices).length : 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Flagged Accounts</span>
                <span className={initialEvidence.account_metrics?.flagged_account_count > 0 ? "text-red-600 font-medium" : "text-slate-900"}>
                  {initialEvidence.account_metrics?.flagged_account_count || 0}
                </span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* SECOND: AI Agent Assessment - only show after ALL steps complete */}
      {finalAssessment && allStepsComplete && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg flex items-center gap-2 text-slate-900">
                <Brain className="w-5 h-5 text-purple-600" />
                AI Agent Assessment
              </CardTitle>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-xs border-purple-200 text-purple-600">
                  {finalAssessment.iteration} iterations
                </Badge>
                <Badge variant="outline" className="text-xs border-indigo-200 text-indigo-600">
                  {finalAssessment.tool_calls_made} tools
                </Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Risk Score */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-600">Risk Score</span>
                <div className="flex items-center gap-2">
                  {riskLevelIcons[finalAssessment.risk_level]}
                  <span className="font-mono font-semibold text-lg text-slate-900">
                    {finalAssessment.risk_score}/100
                  </span>
                </div>
              </div>
              <Progress value={finalAssessment.risk_score} className="h-3" />
            </div>

            {/* Typology and Decision */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <span className="text-xs text-slate-500">Fraud Typology</span>
                <p className="text-sm font-medium text-slate-900 capitalize">
                  {finalAssessment.typology?.replace(/_/g, " ") || "Unknown"}
                </p>
              </div>
              <div className="space-y-1">
                <span className="text-xs text-slate-500">Risk Level</span>
                <Badge className={`${riskLevelColors[finalAssessment.risk_level] || "bg-slate-100 text-slate-700"}`}>
                  {finalAssessment.risk_level?.toUpperCase()}
                </Badge>
              </div>
            </div>

            {/* Recommended Action */}
            <div className="pt-3 border-t border-slate-200">
              <span className="text-xs text-slate-500">Recommended Action</span>
              <div className="mt-1">
                <Badge
                  className={`text-sm py-1.5 px-3 ${
                    actionColors[finalAssessment.decision] || "bg-slate-600 text-white"
                  }`}
                >
                  {actionLabels[finalAssessment.decision] || finalAssessment.decision?.replace(/_/g, " ")}
                </Badge>
              </div>
            </div>

            {/* Reasoning */}
            {finalAssessment.reasoning && (
              <div className="pt-3 border-t border-slate-200">
                <span className="text-xs text-slate-500 block mb-2">Agent&apos;s Reasoning</span>
                <p className="text-sm text-slate-600">{finalAssessment.reasoning}</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Legacy: Risk Assessment Summary */}
      {risk && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg flex items-center gap-2 text-slate-900">
                <Scale className="w-5 h-5 text-slate-600" />
                Risk Assessment
              </CardTitle>
              <Badge
                className={`${riskLevelColors[risk.risk_level] || "bg-slate-100 text-slate-700"}`}
              >
                {risk.risk_level?.toUpperCase()}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Overall Risk Score */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-slate-600">Final Risk Score</span>
                <span className="font-mono font-semibold text-lg text-slate-900">
                  {risk.final_risk_score?.toFixed(0) || 0}/100
                </span>
              </div>
              <Progress
                value={risk.final_risk_score || 0}
                className="h-3"
              />
              {risk.amplification_factor > 1 && (
                <p className="text-xs text-orange-600">
                  ⚠️ Risk amplified {risk.amplification_factor.toFixed(1)}x due to compounding factors
                </p>
              )}
            </div>

            {/* Dimensional Scores */}
            <div className="grid grid-cols-2 gap-3 pt-2">
              {risk.dimension_scores && Object.entries(risk.dimension_scores).map(([key, value]) => (
                <div key={key} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-500 capitalize">
                      {key.replace(/_/g, " ")}
                    </span>
                    <span className="text-slate-700">{(value as number).toFixed(0)}</span>
                  </div>
                  <Progress value={value as number} className="h-1.5" />
                </div>
              ))}
            </div>

            {/* Reasoning */}
            {risk.reasoning && (
              <div className="pt-2 border-t border-slate-200">
                <p className="text-sm text-slate-600">{risk.reasoning}</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Fraud Typology */}
      {typology && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg flex items-center gap-2 text-slate-900">
                <Brain className="w-5 h-5 text-purple-600" />
                Fraud Typology
              </CardTitle>
              <Badge variant="outline" className="border-purple-200 text-purple-600">
                {(typology.confidence * 100).toFixed(0)}% confidence
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-3">
              <div className="flex-1">
                <p className="text-lg font-medium text-slate-900 capitalize">
                  {typology.primary_typology?.replace(/_/g, " ") || "Unknown"}
                </p>
                {typology.secondary_typology && (
                  <p className="text-sm text-slate-500">
                    Secondary: {typology.secondary_typology.replace(/_/g, " ")}
                  </p>
                )}
              </div>
            </div>

            {/* Indicators */}
            {typology.indicators && typology.indicators.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {typology.indicators.map((indicator, i) => (
                  <Badge key={i} variant="secondary" className="text-xs bg-slate-100 text-slate-700">
                    {indicator}
                  </Badge>
                ))}
              </div>
            )}

            {/* Reasoning */}
            {typology.reasoning && (
              <p className="text-sm text-slate-600 pt-2 border-t border-slate-200">
                {typology.reasoning}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Decision Recommendation */}
      {decision && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg flex items-center gap-2 text-slate-900">
                <Shield className="w-5 h-5 text-slate-600" />
                Recommended Action
              </CardTitle>
              {decision.requires_human_review && (
                <Badge variant="outline" className="border-amber-200 text-amber-600">
                  <User className="w-3 h-3 mr-1" />
                  Human Review Required
                </Badge>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-4">
              <Badge
                className={`text-sm py-1.5 px-3 ${
                  actionColors[decision.recommended_action] || "bg-slate-600 text-white"
                }`}
              >
                {actionLabels[decision.recommended_action] || decision.recommended_action}
              </Badge>
              <span className="text-sm text-slate-500">
                {(decision.confidence * 100).toFixed(0)}% confidence
              </span>
            </div>

            {decision.alternative_action && (
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <span>Alternative:</span>
                <Badge variant="outline" className="border-slate-300 text-slate-600">
                  {actionLabels[decision.alternative_action] || decision.alternative_action}
                </Badge>
              </div>
            )}

            {decision.reasoning && (
              <p className="text-sm text-slate-600 pt-2 border-t border-slate-200">
                {decision.reasoning}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Evidence Summary */}
      {(accountProfile || networkEvidence) && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg flex items-center gap-2 text-slate-900">
              <Activity className="w-5 h-5 text-slate-600" />
              Evidence Summary
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Account Profile */}
            {accountProfile && (
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-slate-500">Account Age</span>
                  <span className="text-slate-900">{accountProfile.account_age_days || 0} days</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Devices</span>
                  <span className="text-slate-900">{accountProfile.device_count || 0}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Flagged Device</span>
                  <span className={accountProfile.has_flagged_device ? "text-red-600 font-medium" : "text-slate-900"}>
                    {accountProfile.has_flagged_device ? "Yes" : "No"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Profile Risk</span>
                  <span className="text-slate-900">{accountProfile.profile_risk_score?.toFixed(0) || 0}</span>
                </div>
              </div>
            )}

            {/* Network Evidence */}
            {networkEvidence && (
              <div className="pt-3 border-t border-slate-200">
                <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-slate-500">Shared Devices</span>
                    <span className="text-slate-900">{networkEvidence.shared_device_count || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Flagged Connections</span>
                    <span className={
                      networkEvidence.flagged_connections?.length > 0 ? "text-red-600 font-medium" : "text-slate-900"
                    }>
                      {networkEvidence.flagged_connections?.length || 0}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Network Type</span>
                    <span className="text-slate-900 capitalize">{networkEvidence.network_topology || "Unknown"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Fraud Ring</span>
                    <span className={
                      networkEvidence.fraud_ring_members?.length > 0 ? "text-red-600 font-medium" : "text-slate-900"
                    }>
                      {networkEvidence.fraud_ring_members?.length > 0 
                        ? `${networkEvidence.fraud_ring_members.length} members`
                        : "None"}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Full Report (Markdown) - only show after report_generation step completes */}
      {report && reportGenerationComplete && (
        <Card className="bg-white border-slate-200 shadow-sm">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg flex items-center gap-2 text-slate-900">
                <FileText className="w-5 h-5 text-slate-600" />
                Full Investigation Report
              </CardTitle>
              <Button 
                variant="outline" 
                size="sm" 
                onClick={handleDownloadReport}
                className="border-slate-300 text-slate-700 hover:bg-slate-50"
              >
                <Download className="w-4 h-4 mr-2" />
                Download PDF
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div ref={reportContentRef} className="bg-slate-50 p-5 rounded-lg overflow-auto max-h-[500px] border border-slate-200">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({ children }) => (
                    <h1 className="text-xl font-bold text-slate-900 mb-3 mt-4 first:mt-0 pb-2 border-b border-slate-200">
                      {children}
                    </h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="text-lg font-bold text-slate-900 mb-2 mt-4 first:mt-0">
                      {children}
                    </h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="text-base font-semibold text-slate-800 mb-2 mt-4 first:mt-0 flex items-center gap-2">
                      <span className="w-1 h-4 bg-blue-500 rounded-full"></span>
                      {children}
                    </h3>
                  ),
                  p: ({ children }) => (
                    <p className="text-sm text-slate-700 leading-relaxed mb-3">
                      {children}
                    </p>
                  ),
                  ul: ({ children }) => (
                    <ul className="space-y-1.5 mb-3 ml-1">
                      {children}
                    </ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="space-y-1.5 mb-3 ml-1 list-decimal list-inside">
                      {children}
                    </ol>
                  ),
                  li: ({ children }) => (
                    <li className="text-sm text-slate-700 flex items-start gap-2">
                      <span className="w-1.5 h-1.5 bg-slate-400 rounded-full mt-1.5 flex-shrink-0"></span>
                      <span>{children}</span>
                    </li>
                  ),
                  table: ({ children }) => (
                    <div className="overflow-x-auto mb-3">
                      <table className="w-full text-sm border-collapse border border-slate-200 rounded-md">
                        {children}
                      </table>
                    </div>
                  ),
                  thead: ({ children }) => (
                    <thead className="bg-slate-100">{children}</thead>
                  ),
                  tbody: ({ children }) => (
                    <tbody className="divide-y divide-slate-200">{children}</tbody>
                  ),
                  tr: ({ children }) => (
                    <tr className="hover:bg-slate-50 transition-colors">{children}</tr>
                  ),
                  th: ({ children }) => (
                    <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider border-b border-slate-200">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="px-3 py-2 text-sm text-slate-700 border-b border-slate-100">
                      {children}
                    </td>
                  ),
                  strong: ({ children }) => (
                    <strong className="font-semibold text-slate-900">{children}</strong>
                  ),
                  code: ({ className, children, ...props }) => {
                    const match = /language-mermaid/.exec(className || "");
                    if (match) {
                      const chart = String(children).replace(/\n$/, "");
                      return <MermaidDiagram chart={chart} />;
                    }
                    return (
                      <code className="bg-slate-200 text-slate-800 px-1.5 py-0.5 rounded text-xs font-mono" {...props}>
                        {children}
                      </code>
                    );
                  },
                  pre: ({ children }) => (
                    <div className="my-3">{children}</div>
                  ),
                  a: ({ children, href }) => (
                    <a href={href} className="text-blue-600 hover:underline" target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                  hr: () => <hr className="my-4 border-slate-200" />,
                }}
              >
                {normalizeMarkdown(report || "")}
              </ReactMarkdown>
            </div>

            {/* Fraud Ring Graph - shown below report but NOT included in PDF */}
            {toolCalls && toolCalls.length > 0 && userId && (
              <div className="mt-5">
                <FraudRingGraph toolCalls={toolCalls} userId={userId} />
              </div>
            )}
          </CardContent>
        </Card>
      )}

    </div>
  );
}
