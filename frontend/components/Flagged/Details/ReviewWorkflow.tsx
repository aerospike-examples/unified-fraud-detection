'use client'

import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import {
    CheckCircle,
    FileText,
    Circle,
    Clock,
    Loader2,
    Database,
    Brain,
    ShieldAlert,
    Wrench,
    ChevronDown,
    ChevronUp,
    StopCircle
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { WorkflowStep as InvestigationStep, TraceEvent, ToolCall } from '@/hooks/useInvestigation'

type StepStatus = 'completed' | 'current' | 'upcoming' | 'ai_running'

interface AISubStep {
    id: string
    name: string
    status: 'pending' | 'running' | 'completed'
    icon?: React.ReactNode
}

interface Props {
    currentStep: number
    onStepChange: (step: number) => void
    // AI Investigation props
    investigationStatus?: 'idle' | 'connecting' | 'running' | 'awaiting_confirmation' | 'completed' | 'error'
    investigationSteps?: InvestigationStep[]
    completedInvestigationSteps?: string[]
    currentNode?: string
    toolCalls?: ToolCall[]
    traceEvents?: TraceEvent[]
    getStepStatus?: (stepId: string) => 'pending' | 'running' | 'completed'
    // The decision the AI agent enacted (e.g. allow_monitor, temporary_freeze).
    // When set, the AI has reached and enacted a decision (completes the Decision step).
    aiDecision?: string
    // Start/stop the investigation — this panel hosts the only control.
    onStart?: () => void
    onStop?: () => void
}

// Human-readable labels for the agent's enacted decisions.
const decisionLabels: Record<string, string> = {
    allow_monitor: 'Allow & Monitor',
    step_up_auth: 'Step-up Authentication',
    temporary_freeze: 'Temporary Freeze',
    full_block: 'Full Block',
    escalate_compliance: 'Escalate to Compliance',
    clear: 'Clear (not fraud)',
}

const workflowSteps = [
    {
        title: 'Initial Review',
        description: 'Review account details, transaction history, and flag reasons',
        aiSteps: ['alert_validation']
    },
    {
        title: 'Transaction Analysis',
        description: 'Analyze suspicious transactions and identify patterns',
        aiSteps: ['data_collection']
    },
    {
        title: 'Risk Assessment',
        description: 'Evaluate overall risk level and potential impact',
        aiSteps: ['llm_agent', 'report_generation']
    },
    {
        title: 'Decision & Documentation',
        description: 'Make final determination and document findings',
        aiSteps: []
    }
]

const stepIcons: Record<string, React.ReactNode> = {
    alert_validation: <ShieldAlert className="w-3.5 h-3.5" />,
    data_collection: <Database className="w-3.5 h-3.5" />,
    llm_agent: <Brain className="w-3.5 h-3.5" />,
    report_generation: <FileText className="w-3.5 h-3.5" />,
}

const stepLabels: Record<string, string> = {
    alert_validation: 'Alert Validation',
    data_collection: 'Data Collection',
    llm_agent: 'AI Agent Investigation',
    report_generation: 'Report Generation',
}

const ReviewWorkflow = ({ 
    currentStep, 
    onStepChange,
    investigationStatus = 'idle',
    investigationSteps = [],
    completedInvestigationSteps = [],
    currentNode = '',
    toolCalls = [],
    traceEvents = [],
    getStepStatus,
    aiDecision = '',
    onStart,
    onStop
}: Props) => {
    const [showToolCalls, setShowToolCalls] = useState(false)

    // Auto-advance to Decision step when AI investigation completes
    useEffect(() => {
        if (investigationStatus === 'completed' && currentStep < 3) {
            onStepChange(3)
        }
    }, [investigationStatus, currentStep, onStepChange])

    // Extract tool call info from trace events
    const extractedToolCalls = traceEvents
        .filter(e => e.type === 'tool_call' && e.data)
        .map(e => ({
            tool: e.data?.tool || 'unknown',
            params: e.data?.params || {},
            result_summary: e.data?.result_summary,
        }))

    const getMainStepStatus = (stepIndex: number): StepStatus => {
        const step = workflowSteps[stepIndex]
        
        // When AI investigation is complete, mark AI steps (0-2) as completed
        // and show Human Decision step (3) as current
        if (investigationStatus === 'completed') {
            if (stepIndex < 3) return 'completed' // Steps 0, 1, 2 (AI steps) are done
            // Step 3 (Decision): the AI reached and enacted a decision → complete.
            // If somehow no decision was enacted, leave it as the current (human) step.
            if (stepIndex === 3) return aiDecision ? 'completed' : 'current'
            return 'upcoming'
        }

        // Paused for analyst approval: analysis + report are done (steps 1-3),
        // and the Decision step (4) is the approval that's in progress.
        if (investigationStatus === 'awaiting_confirmation') {
            if (stepIndex < 3) return 'completed'
            if (stepIndex === 3) return 'ai_running'
            return 'upcoming'
        }
        
        // Check if AI steps for this workflow step are running
        if (investigationStatus === 'running') {
            // Step 3 (Human Decision) has no AI substeps - always upcoming during AI run
            if (step.aiSteps.length === 0) return 'upcoming'
            
            const isAIRunning = step.aiSteps.some(aiStep => currentNode === aiStep)
            if (isAIRunning) return 'ai_running'
            
            // Mark previous steps as completed during running
            const allAICompleted = step.aiSteps.every(aiStep => 
                completedInvestigationSteps.includes(aiStep)
            )
            if (allAICompleted) return 'completed'
        }
        
        // Default logic for manual navigation
        if (stepIndex < currentStep) return 'completed'
        if (stepIndex === currentStep) return 'current'
        return 'upcoming'
    }

    const getAISubStepStatus = (aiStepId: string): 'pending' | 'running' | 'completed' => {
        if (getStepStatus) return getStepStatus(aiStepId)
        if (completedInvestigationSteps.includes(aiStepId)) return 'completed'
        if (currentNode === aiStepId) return 'running'
        return 'pending'
    }

    const isAwaitingApproval = investigationStatus === 'awaiting_confirmation'
    const isAIActive = investigationStatus === 'running' || investigationStatus === 'connecting' || isAwaitingApproval
    const isAIComplete = investigationStatus === 'completed'

    return (
        <div className="space-y-6">
            {/* Workflow Progress */}
            <Card className="bg-white border-slate-200 shadow-sm">
                <CardHeader className="pb-4">
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2 text-slate-900">
                                <FileText className="h-5 w-5 text-indigo-600" />
                                Review Workflow
                            </CardTitle>
                            <CardDescription className="text-slate-500">
                                Complete each step to process this flagged account
                            </CardDescription>
                        </div>
                        {isAIActive && (
                            <Badge className="bg-indigo-100 text-indigo-700 border-indigo-200">
                                <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                                AI Running
                            </Badge>
                        )}
                        {isAIComplete && (
                            <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200">
                                <CheckCircle className="w-3 h-3 mr-1" />
                                AI Complete
                            </Badge>
                        )}
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="space-y-0">
                        {workflowSteps.map((step, index) => {
                            const status = getMainStepStatus(index)
                            const isLast = index === workflowSteps.length - 1
                            const hasAISteps = step.aiSteps.length > 0
                            const showSubSteps = hasAISteps && (isAIActive || isAIComplete)

                            return (
                                <div key={index} className="flex gap-4">
                                    {/* Step indicator */}
                                    <div className="flex flex-col items-center">
                                        <div className={cn(
                                            "w-10 h-10 rounded-full flex items-center justify-center border-2 transition-colors",
                                            status === 'completed' && "bg-emerald-500 border-emerald-500 text-white",
                                            status === 'current' && "bg-indigo-600 border-indigo-600 text-white",
                                            status === 'ai_running' && "bg-purple-500 border-purple-500 text-white",
                                            status === 'upcoming' && "bg-slate-100 border-slate-300 text-slate-400"
                                        )}>
                                            {status === 'completed' ? (
                                                <CheckCircle className="h-5 w-5" />
                                            ) : status === 'current' ? (
                                                <Clock className="h-5 w-5" />
                                            ) : status === 'ai_running' ? (
                                                <Loader2 className="h-5 w-5 animate-spin" />
                                            ) : (
                                                <Circle className="h-5 w-5" />
                                            )}
                                        </div>
                                        {!isLast && (
                                            <div className={cn(
                                                "w-0.5 flex-1 min-h-[40px]",
                                                status === 'completed' ? "bg-emerald-500" : "bg-slate-200"
                                            )} />
                                        )}
                                    </div>

                                    {/* Step content */}
                                    <div className="pb-6 flex-1">
                                        <div className="flex items-center gap-2">
                                            <span className={cn(
                                                "text-xs font-medium px-2 py-0.5 rounded",
                                                status === 'completed' && "bg-emerald-100 text-emerald-700",
                                                status === 'current' && "bg-indigo-100 text-indigo-700",
                                                status === 'ai_running' && "bg-purple-100 text-purple-700",
                                                status === 'upcoming' && "bg-slate-100 text-slate-500"
                                            )}>
                                                Step {index + 1}
                                            </span>
                                        </div>
                                        <h4 className={cn(
                                            "font-semibold mt-1 text-slate-900",
                                            status === 'upcoming' && "text-slate-400"
                                        )}>
                                            {step.title}
                                        </h4>
                                        <p className="text-sm text-slate-500 mt-1">{step.description}</p>

                                        {/* Decision step reflects the agent's decision + approval */}
                                        {index === 3 && isAwaitingApproval && aiDecision && (
                                            <div className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-amber-50 border border-amber-200 px-2 py-1 text-xs font-medium text-amber-700">
                                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                                Awaiting your approval: {decisionLabels[aiDecision] || aiDecision}
                                            </div>
                                        )}
                                        {index === 3 && isAIComplete && aiDecision && (
                                            <div className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-emerald-50 border border-emerald-200 px-2 py-1 text-xs font-medium text-emerald-700">
                                                <CheckCircle className="w-3.5 h-3.5" />
                                                AI decision: {decisionLabels[aiDecision] || aiDecision}
                                            </div>
                                        )}

                                        {/* AI Sub-steps */}
                                        {hasAISteps && showSubSteps && (
                                            <div className="mt-3 ml-2 space-y-1.5 border-l-2 border-slate-200 pl-3">
                                                {step.aiSteps.map((aiStepId) => {
                                                    const aiStatus = getAISubStepStatus(aiStepId)
                                                    const isAgent = aiStepId === 'llm_agent'
                                                    
                                                    return (
                                                        <div key={aiStepId}>
                                                            <div className={cn(
                                                                "flex items-center gap-2 text-sm py-1",
                                                                aiStatus === 'completed' && "text-emerald-600",
                                                                aiStatus === 'running' && "text-purple-600",
                                                                aiStatus === 'pending' && "text-slate-400"
                                                            )}>
                                                                {aiStatus === 'completed' ? (
                                                                    <CheckCircle className="w-3.5 h-3.5" />
                                                                ) : aiStatus === 'running' ? (
                                                                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                                                ) : (
                                                                    <Circle className="w-3.5 h-3.5" />
                                                                )}
                                                                {stepIcons[aiStepId]}
                                                                <span className="font-medium">{stepLabels[aiStepId]}</span>
                                                                {isAgent && extractedToolCalls.length > 0 && (
                                                                    <Badge variant="outline" className="text-xs ml-1 border-purple-300 text-purple-600">
                                                                        {extractedToolCalls.length} tools
                                                                    </Badge>
                                                                )}
                                                            </div>
                                                            
                                                            {/* Tool calls for agent step */}
                                                            {isAgent && extractedToolCalls.length > 0 && (aiStatus === 'running' || aiStatus === 'completed') && (
                                                                <div className="ml-6 mt-1">
                                                                    <button
                                                                        onClick={() => setShowToolCalls(!showToolCalls)}
                                                                        className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"
                                                                    >
                                                                        <Wrench className="w-3 h-3" />
                                                                        <span>Tool Calls ({extractedToolCalls.length})</span>
                                                                        {showToolCalls ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                                                                    </button>
                                                                    
                                                                    {showToolCalls && (
                                                                        <div className="mt-1.5 space-y-1 max-h-32 overflow-y-auto">
                                                                            {extractedToolCalls.map((call, idx) => (
                                                                                <div key={idx} className="flex items-start gap-2 text-xs bg-slate-50 rounded px-2 py-1">
                                                                                    <span className="text-slate-400 font-mono w-3">{idx + 1}.</span>
                                                                                    <div className="flex-1 min-w-0">
                                                                                        <span className="font-medium text-purple-600">{call.tool}</span>
                                                                                        {call.result_summary && (
                                                                                            <span className="text-slate-400 ml-1">→ {call.result_summary}</span>
                                                                                        )}
                                                                                    </div>
                                                                                </div>
                                                                            ))}
                                                                        </div>
                                                                    )}
                                                                </div>
                                                            )}
                                                        </div>
                                                    )
                                                })}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )
                        })}
                    </div>

                    {/* Investigation control — the single action for this panel */}
                    <div className="mt-6 pt-4 border-t border-slate-200">
                        {(investigationStatus === 'idle' || investigationStatus === 'completed' || investigationStatus === 'error') && onStart && (
                            <Button
                                onClick={onStart}
                                className="w-full bg-indigo-600 hover:bg-indigo-700 text-white"
                            >
                                <Brain className="h-4 w-4 mr-2" />
                                {investigationStatus === 'completed' ? 'Re-run AI Investigation' : 'Start AI Investigation'}
                            </Button>
                        )}
                        {(investigationStatus === 'running' || investigationStatus === 'connecting') && onStop && (
                            <Button onClick={onStop} variant="destructive" className="w-full">
                                <StopCircle className="h-4 w-4 mr-2" />
                                Stop Investigation
                            </Button>
                        )}
                        {investigationStatus === 'awaiting_confirmation' && (
                            <p className="flex items-center justify-center gap-2 text-sm font-medium text-amber-700">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Awaiting your approval — see the decision above
                            </p>
                        )}
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}

export default ReviewWorkflow
