'use client'

import { useState, useEffect, useRef } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { 
    Database, 
    RefreshCw, 
    CheckCircle, 
    XCircle, 
    Loader2,
    HardDrive,
    Users,
    CreditCard,
    Smartphone,
    GitBranch,
    AlertTriangle,
    Play,
    Server,
    Upload,
    FolderOpen,
    FileText,
    Trash2,
    Timer,
    Target,
    Calendar,
    Zap,
    TrendingUp,
    History,
    ChevronDown,
    ChevronUp,
    Cloud,
    Network
} from 'lucide-react'
import { toast } from 'sonner'

import { setDisplayLocale, getDisplayLocale, formatCurrencyWithLocale } from '@/lib/utils'

import { useOperationProgress } from '@/context/OperationProgressContext'
import { useAppConfig } from '@/hooks/useAppConfig'
import useSWR, { useSWRConfig } from 'swr'

interface BulkLoadStatus {
    loading: boolean
    success?: boolean
    message?: string
    error?: string
    status?: string
    step?: string
    complete?: boolean
    progress_percentage?: number
    elements_written?: number
    graph?: {
        success: boolean
        statistics?: {
            users: number
            accounts: number
            devices: number
        }
    }
    aerospike?: {
        success: boolean
        loaded?: number
        message?: string
    }
}

interface GraphStats {
    users: number
    accounts: number
    devices: number
    transactions: number
    total_vertices: number
    total_edges: number
}

interface AerospikeStats {
    connected: boolean
    users_count: number
    flagged_accounts_count: number
    account_facts_count?: number
    device_facts_count?: number
    pending_review: number
    confirmed_fraud: number
    cleared: number
}

interface DetectionConfig {
    schedule_enabled: boolean
    schedule_time: string
    cooldown_days: number
    risk_threshold: number
}

interface SchedulerStatus {
    scheduler_running: boolean
    detection_job_scheduled: boolean
    detection_job_running: boolean
    next_run: string | null
    last_run_result: any
}

interface JobHistoryItem {
    job_id: string
    start_time: string
    end_time?: string
    status: string
    accounts_evaluated: number
    users_evaluated?: number
    accounts_skipped_cooldown: number
    newly_flagged: number
    total_accounts: number
    total_users?: number
    duration_seconds?: number
    error?: string
}

const DataManagement = () => {
    const opCtx = useOperationProgress()
    const { mutate: globalMutate } = useSWRConfig()
    const { config: appConfig } = useAppConfig()
    const isRemoteMode = appConfig.remote

    const [bulkLoadStatusLocal, setBulkLoadStatusLocal] = useState<BulkLoadStatus>({ loading: false })

    // SWR-cached stats — no duplicate fetches, instant on revisit
    const { data: dashboardData, isLoading: loadingDashboard, mutate: mutateDashboard } = useSWR<{users: number, txns: number, flagged: number, accounts?: number, devices?: number}>('/api/dashboard/stats')
    const { data: aerospikeStats, isLoading: loadingAerospikeStats, mutate: mutateAerospikeStats } = useSWR<AerospikeStats>('/api/aerospike/stats')

    const graphStats: GraphStats | null = dashboardData ? {
        users: dashboardData.users || 0,
        accounts: dashboardData.accounts || 0,
        devices: dashboardData.devices || 0,
        transactions: dashboardData.txns || 0,
        total_vertices: 0,
        total_edges: 0
    } : null
    const loadingStats = loadingDashboard || loadingAerospikeStats
    
    // Load options
    const [loadGraph, setLoadGraph] = useState(true)
    const [loadAerospike, setLoadAerospike] = useState(true)
    const [bulkLoadCardCollapsed, setBulkLoadCardCollapsed] = useState(false)
    
    // Data source options
    const [useDefaultData, setUseDefaultData] = useState(true)
    const [defaultLocale, setDefaultLocale] = useState<string>(() =>
        typeof window !== 'undefined' ? getDisplayLocale() : 'american'
    )
    // Locale committed when user clicks Start bulk load (used for Inject); dropdown is independent until next bulk load
    const [committedLocale, setCommittedLocale] = useState<string | null>(null)
    const [uploadedFile, setUploadedFile] = useState<File | null>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)
    
    // Historical Transaction Injection state
    const [txnCount, setTxnCount] = useState(10000)
    const [spreadDays, setSpreadDays] = useState(30)
    const [fraudPercentage, setFraudPercentage] = useState(15)
    
    // Delete all data state
    const [deleteLoading, setDeleteLoading] = useState(false)
    const [confirmDelete, setConfirmDelete] = useState(false)

    // ML Detection
    const [detectionConfig, setDetectionConfig] = useState<DetectionConfig>({
        schedule_enabled: true,
        schedule_time: '21:30',
        cooldown_days: 7,
        risk_threshold: 70
    })
    const [schedulerStatus, setSchedulerStatus] = useState<SchedulerStatus | null>(null)
    const [detectionHistory, setDetectionHistory] = useState<JobHistoryItem[]>([])
    const [detectionLoading, setDetectionLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [skipCooldown, setSkipCooldown] = useState(false)
    const [historyLoading, setHistoryLoading] = useState(true)

    // ── Derive running/progress from context (survives page navigation) ──
    const bulkOp       = opCtx.get('bulk_load')
    const injectionOp  = opCtx.get('inject_transactions')
    const featureOp    = opCtx.get('compute_features')
    const detectionOp  = opCtx.get('ml_detection')

    // Derive bulkLoadStatus: prefer context result when available, else use local
    const bulkLoadStatus: BulkLoadStatus = bulkOp.result
        ? {
            loading: bulkOp.running,
            success: true,
            complete: true,
            message: bulkOp.result.message || 'Bulk load completed successfully',
            graph: bulkOp.result.graph,
            aerospike: bulkOp.result.aerospike,
          }
        : bulkOp.error
        ? { loading: false, success: false, error: bulkOp.error }
        : bulkLoadStatusLocal

    const isLoading         = bulkOp.running
    const bulkLoadProgress  = bulkOp.progress
    const injectionLoading  = injectionOp.running
    const injectionProgress = injectionOp.progress
    const injectionResult   = injectionOp.result
    const computingFeatures = featureOp.running
    const featureProgress   = featureOp.progress
    const featureResult     = featureOp.result
    const runningDetection  = detectionOp.running
    const detectionProgress = detectionOp.progress

    // Refresh stats — revalidate local SWR caches
    const refreshStats = () => {
        mutateDashboard()
        mutateAerospikeStats()
    }

    // Invalidate ALL SWR caches app-wide (call after data mutations like inject, bulk load, delete)
    // This ensures Users, Transactions, Flagged, Dashboard pages all show fresh data on next visit
    const invalidateAllCaches = () => {
        globalMutate(() => true, undefined, { revalidate: true })
    }

    // Fetch detection config and status
    const fetchConfig = async () => {
        try {
            const response = await fetch('/api/detection/config')
            if (response.ok) {
                const data = await response.json()
                if (data.config) setDetectionConfig(data.config)
                if (data.scheduler) setSchedulerStatus(data.scheduler)
            }
        } catch (error) {
            console.error('Error fetching config:', error)
        } finally {
            setDetectionLoading(false)
        }
    }

    // Fetch detection history
    const fetchHistory = async () => {
        setHistoryLoading(true)
        try {
            const response = await fetch('/api/detection/history?limit=10')
            if (response.ok) {
                const data = await response.json()
                setDetectionHistory(data.history || [])
            }
        } catch (error) {
            console.error('Error fetching history:', error)
        } finally {
            setHistoryLoading(false)
        }
    }

    useEffect(() => {
        fetchConfig()
        fetchHistory()
    }, [])

    // Sync display locale from localStorage on mount (so dropdown matches app-wide currency)
    useEffect(() => {
        const saved = getDisplayLocale()
        setDefaultLocale(saved)
    }, [])

    // Trigger bulk load
    const handleBulkLoad = async () => {
        if (!loadGraph && !loadAerospike) {
            toast.error('Please select at least one target system')
            return
        }
        
        if (!useDefaultData && !uploadedFile) {
            toast.error('Please select a file to upload or use default data')
            return
        }

        opCtx.start('bulk_load', { current: 0, total: 6, percentage: 0, message: 'Starting...', estimated_remaining_seconds: null })
        setBulkLoadStatusLocal({ loading: true, message: 'Starting bulk load...' })
        
        // Start polling for progress
        opCtx.startPolling('bulk_load', 'bulk_load')
        
        try {
            let response: Response
            
            if (useDefaultData) {
                // Use default data - commit locale now (Inject will use this, not the dropdown)
                setCommittedLocale(defaultLocale)
                setDisplayLocale(defaultLocale)
                const params = new URLSearchParams()
                params.set('load_graph', String(loadGraph))
                params.set('load_aerospike', String(aerospikeStats?.connected ? loadAerospike : false))
                params.set('locale', defaultLocale)
                
                response = await fetch(`/api/bulk-load-csv?${params.toString()}`, {
                    method: 'POST',
                })
            } else if (uploadedFile) {
                // Upload custom file
                const formData = new FormData()
                formData.append('file', uploadedFile)
                formData.append('load_graph', String(loadGraph))
                formData.append('load_aerospike', String(aerospikeStats?.connected ? loadAerospike : false))
                
                response = await fetch('/api/bulk-load-upload', {
                    method: 'POST',
                    body: formData,
                })
            } else {
                throw new Error('No data source selected')
            }
            
            const data = await response.json()
            
            if (data.success) {
                opCtx.complete('bulk_load', data)
                setBulkLoadStatusLocal({
                    loading: false,
                    success: true,
                    message: data.message || 'Bulk load completed successfully',
                    graph: data.graph,
                    aerospike: data.aerospike
                })
                toast.success('Bulk load completed!')
                
                // If graph was loaded, poll for status
                if (loadGraph && data.graph?.success) {
                    pollBulkLoadStatus()
                }
                
                // Refresh all data across the app
                refreshStats()
                invalidateAllCaches()
            } else {
                opCtx.fail('bulk_load', data.detail || data.message || 'Failed to start bulk load')
                setBulkLoadStatusLocal({
                    loading: false,
                    success: false,
                    error: data.detail || data.message || 'Failed to start bulk load',
                    graph: data.graph,
                    aerospike: data.aerospike
                })
                toast.error('Bulk load failed')
            }
        } catch (error) {
            opCtx.fail('bulk_load', 'Network error - failed to connect to backend')
            setBulkLoadStatusLocal({
                loading: false,
                success: false,
                error: 'Network error - failed to connect to backend'
            })
            toast.error('Failed to connect to backend')
        }
    }

    // Poll for bulk load status
    const pollBulkLoadStatus = async () => {
        let attempts = 0
        const maxAttempts = 60
        
        const checkStatus = async (): Promise<boolean> => {
            try {
                const response = await fetch('/api/bulk-load-status')
                const data = await response.json()
                
                setBulkLoadStatusLocal(prev => ({
                    ...prev,
                    loading: !data.complete,
                    status: data.status,
                    step: data.step,
                    complete: data.complete,
                    progress_percentage: data.progress_percentage,
                    elements_written: data.elements_written
                }))
                
                if (data.complete) {
                    toast.success('Graph bulk load completed!')
                    refreshStats()
                    invalidateAllCaches()
                    return true
                } else if (data.status?.toLowerCase().includes('failed') || data.status?.toLowerCase().includes('error')) {
                    setBulkLoadStatusLocal(prev => ({
                        ...prev,
                        loading: false,
                        success: false,
                        error: data.error || 'Bulk load failed'
                    }))
                    toast.error('Graph bulk load failed')
                    return true
                }
                
                return false
            } catch (error) {
                console.error('Error polling status:', error)
                return false
            }
        }
        
        const poll = async () => {
            if (attempts >= maxAttempts) {
                setBulkLoadStatusLocal(prev => ({
                    ...prev,
                    loading: false,
                    message: 'Bulk load is still running in the background. Click refresh to check status.'
                }))
                return
            }
            
            const done = await checkStatus()
            if (!done) {
                attempts++
                setTimeout(poll, 3000)
            }
        }
        
        setTimeout(poll, 2000)
    }

    // Check bulk load status manually
    const handleCheckStatus = async () => {
        try {
            const response = await fetch('/api/bulk-load-status')
            const data = await response.json()
            
            if (data.success) {
                setBulkLoadStatusLocal(prev => ({
                    ...prev,
                    loading: !data.complete,
                    success: data.complete,
                    message: data.message || `Status: ${data.status}`,
                    status: data.status,
                    step: data.step,
                    complete: data.complete,
                    progress_percentage: data.progress_percentage,
                    elements_written: data.elements_written
                }))
                
                if (data.complete) {
                    toast.success('Bulk load completed!')
                } else {
                    toast.info(`Bulk load ${data.status}: ${data.step || 'processing...'}`)
                }
            } else {
                setBulkLoadStatusLocal(prev => ({
                    ...prev,
                    loading: false,
                    success: false,
                    error: data.error || data.message || 'Unknown error'
                }))
                toast.error(data.error || 'Failed to get status')
            }
            
            refreshStats()
            invalidateAllCaches()
        } catch (error) {
            toast.error('Failed to check status')
        }
    }

    // pollProgress is now handled by the OperationProgressContext

    // Inject historical transactions with fraud patterns
    const handleInjectTransactions = async () => {
        opCtx.start('inject_transactions', { current: 0, total: txnCount, percentage: 0, message: 'Starting...', estimated_remaining_seconds: null })
        
        // Start polling for progress
        opCtx.startPolling('inject_transactions', 'inject_transactions')
        
        try {
            // Use locale committed when bulk load was run (not current dropdown)
            const localeToUse = committedLocale ?? defaultLocale
            const params = new URLSearchParams({
                transaction_count: String(txnCount),
                spread_days: String(spreadDays),
                fraud_percentage: String(fraudPercentage / 100) // Convert to decimal
            })
            params.set('locale', localeToUse)
            
            const response = await fetch(`/api/inject-transactions-bulk?${params.toString()}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ locale: localeToUse })
            })
            
            const data = await response.json()
            
            if (data.status === 'completed') {
                opCtx.complete('inject_transactions', data)
                toast.success(`Injected ${data.normal_transactions + data.fraud_transactions} transactions!`)
                
                // Show fraud pattern breakdown
                const patterns = data.fraud_patterns
                if (patterns) {
                    toast.info(`Fraud: ${data.fraud_transactions} (rings: ${patterns.fraud_rings}, velocity: ${patterns.velocity_anomalies}, amount: ${patterns.amount_anomalies}, new account: ${patterns.new_account_fraud})`)
                }
                
                // Refresh all data across the app
                refreshStats()
                invalidateAllCaches()
            } else {
                opCtx.fail('inject_transactions', data.error || data.detail || 'Failed to inject transactions')
                toast.error(data.error || data.detail || 'Failed to inject transactions')
            }
        } catch (error) {
            console.error('Failed to inject transactions:', error)
            opCtx.fail('inject_transactions', 'Failed to inject transactions')
            toast.error('Failed to inject transactions')
        }
    }

    // Delete all data from databases
    const handleDeleteAllData = async () => {
        if (!confirmDelete) {
            setConfirmDelete(true)
            toast.warning('Click again to confirm deletion of ALL data!')
            setTimeout(() => setConfirmDelete(false), 5000) // Reset after 5 seconds
            return
        }
        
        setDeleteLoading(true)
        setConfirmDelete(false)
        toast.info('Deleting all data from Graph and KV stores...')
        
        try {
            const response = await fetch('/api/delete-all-data?confirm=true', {
                method: 'DELETE'
            })
            
            const data = await response.json()
            
            if (response.ok) {
                toast.success('All data deleted successfully!')
                // Clear all operation results in context
                opCtx.complete('inject_transactions', null)
                opCtx.complete('compute_features', null)
                opCtx.complete('ml_detection', null)
                opCtx.complete('bulk_load', null)
                
                // Refresh all data across the app
                refreshStats()
                invalidateAllCaches()
            } else {
                toast.error(data.detail || data.message || 'Failed to delete data')
            }
        } catch (error) {
            console.error('Failed to delete data:', error)
            toast.error('Failed to delete data')
        } finally {
            setDeleteLoading(false)
        }
    }

    // Save detection config
    const handleSaveConfig = async () => {
        setSaving(true)
        try {
            const response = await fetch('/api/detection/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(detectionConfig)
            })
            if (response.ok) {
                const data = await response.json()
                setDetectionConfig(data.config)
                setSchedulerStatus(data.scheduler)
                toast.success('Configuration saved successfully')
            } else {
                toast.error('Failed to save configuration')
            }
        } catch (error) {
            toast.error('Error saving configuration')
        } finally {
            setSaving(false)
        }
    }

    // Compute features from KV transactions
    const handleComputeFeatures = async () => {
        opCtx.start('compute_features', { current: 0, total: 100, percentage: 0, message: 'Starting...', estimated_remaining_seconds: null })
        toast.info(`Computing features with ${detectionConfig.cooldown_days}-day window...`)
        opCtx.startPolling('compute_features', 'compute_features')
        try {
            const response = await fetch(`/api/compute-features?window_days=${detectionConfig.cooldown_days}`, { method: 'POST' })
            if (response.ok) {
                const data = await response.json()
                opCtx.complete('compute_features', data)
                toast.success(`Features computed! ${data.accounts_processed || 0} accounts, ${data.devices_processed || 0} devices.`)
                invalidateAllCaches()
            } else {
                const error = await response.json()
                opCtx.fail('compute_features', error.detail || 'Feature computation failed')
                toast.error(error.detail || 'Feature computation failed')
            }
        } catch (error) {
            opCtx.fail('compute_features', 'Error computing features')
            toast.error('Error computing features')
        }
    }

    // Run detection manually
    const handleRunDetection = async () => {
        opCtx.start('ml_detection', { current: 0, total: 100, percentage: 0, message: 'Starting...', estimated_remaining_seconds: null })
        toast.info(skipCooldown ? 'Starting detection job (skipping cooldown)...' : 'Starting detection job...')
        opCtx.startPolling('ml_detection', 'ml_detection')
        try {
            const url = skipCooldown ? '/api/flagged-accounts/detect?skip_cooldown=true' : '/api/flagged-accounts/detect'
            const response = await fetch(url, { method: 'POST' })
            if (response.ok) {
                const data = await response.json()
                opCtx.complete('ml_detection', data)
                toast.success(`Detection completed! ${data.result?.newly_flagged || 0} users flagged.`)
                invalidateAllCaches()
                fetchHistory()
                fetchConfig()
            } else {
                const error = await response.json()
                opCtx.fail('ml_detection', error.detail || 'Detection job failed')
                toast.error(error.detail || 'Detection job failed')
            }
        } catch (error) {
            opCtx.fail('ml_detection', 'Error running detection job')
            toast.error('Error running detection job')
        }
    }

    const formatDateTime = (dateStr: string) => new Date(dateStr).toLocaleString()
    const formatDuration = (seconds: number) => {
        if (seconds < 60) return `${seconds.toFixed(1)}s`
        const mins = Math.floor(seconds / 60)
        const secs = seconds % 60
        return `${mins}m ${secs.toFixed(0)}s`
    }

    // Workflow progress: derive completion from state
    const step1Done = !!(bulkLoadStatus.success || bulkLoadStatus.complete)
    const step2Done = !!injectionResult
    const step3Done = !!featureResult
    const step4Done = detectionHistory.length > 0
    const workflowSteps = [
        { label: 'Bulk load data', done: step1Done },
        { label: 'Inject transactions', done: step2Done },
        { label: 'Calculate features', done: step3Done },
        { label: 'Run ML model', done: step4Done },
    ]

    // ── Remote load-gen mode: a read-only overview. Ingest/detection are external. ──
    if (isRemoteMode) {
        return (
            <div className="space-y-6 pb-4">
                {/* Remote mode banner */}
                <Card className="overflow-hidden border border-amber-300/70 dark:border-amber-700/60 shadow-sm bg-amber-50/70 dark:bg-amber-950/20">
                    <CardContent className="py-3 px-4 flex items-start gap-3">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-500/15">
                            <Cloud className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                        </div>
                        <div className="min-w-0">
                            <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
                                Remote load-generation mode
                            </p>
                            <p className="text-xs text-amber-800/90 dark:text-amber-300/90 mt-0.5">
                                Data is generated and bulk loaded into the graph externally at scale.
                                Bulk load, transaction injection, feature computation, and ML detection are
                                managed by the external pipeline and are disabled here. Counts come from the
                                graph summary; the review queue is populated externally.
                            </p>
                        </div>
                    </CardContent>
                </Card>

                {/* Storage overview (graph summary + KV working set) */}
                <section className="space-y-3">
                    <div className="flex items-center gap-2 text-muted-foreground">
                        <Network className="h-4 w-4" />
                        <span className="text-sm font-medium text-foreground">Storage overview</span>
                    </div>
                    <Card className="overflow-hidden border-0 shadow-sm bg-card">
                        <CardHeader className="pb-2 pt-4 px-4 flex flex-row items-center justify-between">
                            <div>
                                <CardTitle className="text-base">Graph (source of truth)</CardTitle>
                                <CardDescription className="text-xs mt-0.5">Vertex/edge counts from the Aerospike Graph summary API</CardDescription>
                            </div>
                            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={refreshStats} disabled={loadingStats}>
                                {loadingStats ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                            </Button>
                        </CardHeader>
                        <CardContent className="px-4 pb-4">
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                {[
                                    { icon: Users, label: 'Users', value: graphStats?.users },
                                    { icon: CreditCard, label: 'Accounts', value: graphStats?.accounts },
                                    { icon: Smartphone, label: 'Devices', value: graphStats?.devices },
                                    { icon: GitBranch, label: 'Transactions', value: graphStats?.transactions },
                                ].map(({ icon: Icon, label, value }) => (
                                    <div key={label} className="rounded-lg border border-border/80 bg-muted/20 p-3">
                                        <div className="flex items-center gap-1.5 text-muted-foreground text-xs">
                                            <Icon className="h-3.5 w-3.5" />
                                            {label}
                                        </div>
                                        <p className="mt-1 text-lg font-semibold">
                                            {loadingStats ? '…' : (value?.toLocaleString() ?? '0')}
                                        </p>
                                    </div>
                                ))}
                            </div>
                            <div className="mt-3 rounded-lg border border-border/80 bg-muted/20 p-3 text-xs text-muted-foreground">
                                <span className="font-medium text-foreground">KV working set:</span>{' '}
                                <strong>{aerospikeStats?.flagged_accounts_count?.toLocaleString() ?? '0'}</strong> flagged,{' '}
                                <strong>{aerospikeStats?.account_facts_count?.toLocaleString() ?? '0'}</strong> account facts,{' '}
                                <strong>{aerospikeStats?.device_facts_count?.toLocaleString() ?? '0'}</strong> device facts
                                {aerospikeStats?.connected && (
                                    <span className="ml-1 text-muted-foreground/80">(P:{aerospikeStats.pending_review ?? 0} F:{aerospikeStats.confirmed_fraud ?? 0} C:{aerospikeStats.cleared ?? 0})</span>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                </section>

                {/* Detection history (read-only) */}
                <section className="space-y-3">
                    <div className="flex items-center gap-2 text-muted-foreground">
                        <History className="h-4 w-4" />
                        <span className="text-sm font-medium text-foreground">Detection history</span>
                    </div>
                    <Card className="overflow-hidden border-0 shadow-sm bg-card">
                        <CardContent className="px-4 py-4">
                            {historyLoading ? (
                                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <Loader2 className="h-4 w-4 animate-spin" /> Loading history…
                                </div>
                            ) : detectionHistory.length === 0 ? (
                                <p className="text-sm text-muted-foreground">
                                    No detection runs recorded yet. Detection is run by the external pipeline.
                                </p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>When</TableHead>
                                            <TableHead className="text-right">Newly flagged</TableHead>
                                            <TableHead className="text-right">Accounts</TableHead>
                                            <TableHead className="text-right">Duration</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {detectionHistory.slice(0, 10).map((h, i) => (
                                            <TableRow key={i}>
                                                <TableCell className="text-xs">{h.start_time ? formatDateTime(h.start_time) : '—'}</TableCell>
                                                <TableCell className="text-right text-xs">{h.newly_flagged?.toLocaleString() ?? '—'}</TableCell>
                                                <TableCell className="text-right text-xs">{h.total_accounts?.toLocaleString() ?? '—'}</TableCell>
                                                <TableCell className="text-right text-xs">{h.duration_seconds != null ? formatDuration(h.duration_seconds) : '—'}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </section>
            </div>
        )
    }

    return (
        <div className="space-y-6 pb-4">
            {/* Workflow progress */}
            <Card className="overflow-hidden border-0 shadow-sm bg-muted/30">
                <CardContent className="py-3 px-4">
                    <p className="text-xs font-medium text-muted-foreground mb-3 uppercase tracking-wider">Pipeline progress</p>
                    <div className="flex items-center gap-0 flex-wrap">
                        {workflowSteps.map((step, i) => {
                            const currentStep = workflowSteps.findIndex(s => !s.done)
                            const isCurrent = currentStep === i
                            return (
                                <div key={i} className="flex items-center">
                                    <div
                                        className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
                                            step.done
                                                ? 'bg-green-500/15 text-green-700 dark:text-green-400'
                                                : isCurrent
                                                    ? 'bg-primary/15 text-primary ring-1 ring-primary/30'
                                                    : 'bg-muted text-muted-foreground'
                                        }`}
                                    >
                                        {step.done ? (
                                            <CheckCircle className="h-3.5 w-3.5 shrink-0" />
                                        ) : (
                                            <span className="w-3.5 h-3.5 flex items-center justify-center rounded-full bg-current/20 text-[10px] font-bold">
                                                {i + 1}
                                            </span>
                                        )}
                                        <span>{step.label}</span>
                                    </div>
                                    {i < workflowSteps.length - 1 && (
                                        <span className="mx-1 text-muted-foreground/40" aria-hidden>→</span>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                </CardContent>
            </Card>

            {/* Sections 1 & 2: side-by-side on large screens */}
            <div className="grid gap-6 lg:grid-cols-2">
            {/* Section: Ingest Data */}
            <section className="space-y-3">
                <div className="flex items-center gap-2 text-muted-foreground">
                    <span className="text-xs font-semibold uppercase tracking-wider">1</span>
                    <span className="text-sm">Bulk load data</span>
                </div>
                <Card className="overflow-hidden border-0 shadow-sm bg-card h-full flex flex-col">
                    <CardHeader className="pb-2 pt-4 px-4 flex flex-row items-start justify-between gap-2">
                        <div>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                                    <Database className="h-4 w-4 text-primary" />
                                </div>
                        Bulk Data Load
                    </CardTitle>
                            <CardDescription className="mt-0.5 text-xs">
                                Load sample data into Graph DB and/or Aerospike KV.
                    </CardDescription>
                        </div>
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 shrink-0"
                            onClick={() => setBulkLoadCardCollapsed(c => !c)}
                            aria-label={bulkLoadCardCollapsed ? 'Expand' : 'Collapse'}
                        >
                            {bulkLoadCardCollapsed ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
                        </Button>
                </CardHeader>
                {!bulkLoadCardCollapsed && (
                <CardContent className="space-y-3 px-4 pb-4 flex-1 flex flex-col">
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        {/* Data Source Selection */}
                        <div className="rounded-lg border border-border/80 bg-muted/30 p-3 space-y-2">
                            <p className="text-xs font-medium text-foreground">Data source</p>
                            <div className="space-y-1.5">
                                <button
                                    onClick={() => {
                                        setUseDefaultData(true)
                                        setUploadedFile(null)
                                    }}
                                    className={`w-full p-2 rounded-md border transition-all text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                                        useDefaultData 
                                            ? 'border-primary bg-primary/5 dark:bg-primary/10'
                                            : 'border-transparent bg-background hover:bg-muted/50'
                                    }`}
                                >
                                    <div className="flex items-center gap-1.5">
                                        <FolderOpen className={`h-3.5 w-3.5 shrink-0 ${useDefaultData ? 'text-primary' : 'text-muted-foreground'}`} />
                                        <span className="text-xs font-medium">Use default data</span>
                                    </div>
                                </button>
                                <button
                                    onClick={() => {
                                        setUseDefaultData(false)
                                        fileInputRef.current?.click()
                                    }}
                                    className={`w-full p-2 rounded-md border transition-all text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                                        !useDefaultData 
                                            ? 'border-primary bg-primary/5 dark:bg-primary/10'
                                            : 'border-transparent bg-background hover:bg-muted/50'
                                    }`}
                                >
                                    <div className="flex items-center gap-1.5">
                                        <Upload className={`h-3.5 w-3.5 shrink-0 ${!useDefaultData ? 'text-primary' : 'text-muted-foreground'}`} />
                                        <span className="text-xs font-medium truncate">{uploadedFile ? uploadedFile.name : 'Upload ZIP'}</span>
                                    </div>
                                </button>
                                <input
                                    ref={fileInputRef}
                                    type="file"
                                    accept=".zip"
                                    className="hidden"
                                    onChange={(e) => {
                                        const file = e.target.files?.[0]
                                        if (file) {
                                            setUploadedFile(file)
                                            setUseDefaultData(false)
                                            toast.success(`Selected: ${file.name}`)
                                        }
                                    }}
                                />
                            </div>
                        </div>

                        {/* Default Data Info + Locale */}
                        <div className={`rounded-lg border border-border/80 bg-muted/30 p-3 space-y-2 transition-opacity ${!useDefaultData ? 'opacity-50' : ''}`}>
                            <p className="text-xs font-medium text-foreground">Default data</p>
                            {useDefaultData && (
                                <div className="space-y-1">
                                    <Label htmlFor="default-locale" className="text-xs text-muted-foreground">Locale</Label>
                                    <select
                                        id="default-locale"
                                        value={defaultLocale}
                                        onChange={(e) => setDefaultLocale(e.target.value)}
                                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                                    >
                                        <option value="american">American</option>
                                        <option value="indian">Indian</option>
                                        <option value="en_GB">UK</option>
                                        <option value="en_AU">Australia</option>
                                        <option value="zh_CN">China</option>
                                    </select>
                                </div>
                            )}
                            <ul className="text-xs text-muted-foreground space-y-1">
                                <li className="flex items-center gap-1.5"><Users className="h-3 w-3 shrink-0" />10k users</li>
                                <li className="flex items-center gap-1.5"><CreditCard className="h-3 w-3 shrink-0" />~21k accounts</li>
                                <li className="flex items-center gap-1.5"><Smartphone className="h-3 w-3 shrink-0" />~25k devices</li>
                                <li className="flex items-center gap-1.5"><GitBranch className="h-3 w-3 shrink-0" />Edges</li>
                            </ul>
                        </div>

                        {/* Target Systems */}
                        <div className="rounded-lg border border-border/80 bg-muted/30 p-3 space-y-2">
                            <p className="text-xs font-medium text-foreground">Targets</p>
                            <div className="space-y-2">
                                <div className="flex items-center justify-between gap-2">
                                    <Label htmlFor="load-graph" className="text-xs cursor-pointer flex items-center gap-1.5">
                                        <HardDrive className="h-3 w-3 shrink-0 text-blue-500" />Graph
                                        </Label>
                                    <Switch id="load-graph" checked={loadGraph} onCheckedChange={setLoadGraph} className="scale-75 origin-right" />
                                    </div>
                                <div className={`flex items-center justify-between gap-2 ${!aerospikeStats?.connected ? 'opacity-50' : ''}`}>
                                    <Label htmlFor="load-aerospike" className={`text-xs flex items-center gap-1.5 ${aerospikeStats?.connected ? 'cursor-pointer' : 'cursor-not-allowed text-muted-foreground'}`}>
                                        <Server className={`h-3 w-3 shrink-0 ${aerospikeStats?.connected ? 'text-green-500' : ''}`} />KV
                                        </Label>
                                    <Switch id="load-aerospike" checked={aerospikeStats?.connected ? loadAerospike : false} onCheckedChange={setLoadAerospike} disabled={!aerospikeStats?.connected} className="scale-75 origin-right" />
                                    </div>
                                </div>
                        </div>
                    </div>

                    {/* Storage stats (Graph + KV) + clear */}
                    <div className="rounded-lg border border-border/80 bg-muted/20 p-2.5 space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex flex-wrap items-center gap-3 text-xs">
                                <span className="font-medium text-foreground">Storage</span>
                                <span className="text-muted-foreground">
                                    Graph: <strong>{loadingStats ? '…' : (graphStats?.users?.toLocaleString() ?? '0')}</strong> users, <strong>{loadingStats ? '…' : (graphStats?.transactions?.toLocaleString() ?? '0')}</strong> txns
                                        </span>
                                <span className="text-muted-foreground">
                                    KV: <strong>{loadingStats ? '…' : (aerospikeStats?.users_count?.toLocaleString() ?? '0')}</strong> users, <strong>{aerospikeStats?.flagged_accounts_count?.toLocaleString() ?? '0'}</strong> flagged
                                    {aerospikeStats?.connected && (
                                        <span className="ml-1 text-muted-foreground/80">(P:{aerospikeStats.pending_review ?? 0} F:{aerospikeStats.confirmed_fraud ?? 0} C:{aerospikeStats.cleared ?? 0})</span>
                                    )}
                                </span>
                            </div>
                            <div className="flex items-center gap-1">
                                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={refreshStats} disabled={loadingStats}>
                                    {loadingStats ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 px-2 text-xs text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/20"
                                    onClick={handleDeleteAllData}
                                    disabled={deleteLoading}
                                >
                                    {deleteLoading ? (
                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    ) : confirmDelete ? (
                                        <><AlertTriangle className="h-3.5 w-3.5 mr-1" />Confirm?</>
                                    ) : (
                                        <><Trash2 className="h-3.5 w-3.5 mr-1" />Clear all</>
                                    )}
                                </Button>
                            </div>
                        </div>
                    </div>

                    {/* Status Display */}
                    {(bulkLoadStatus.message || bulkLoadStatus.status || bulkLoadStatus.graph || bulkLoadStatus.aerospike) && (
                        <div className={`p-4 rounded-xl flex items-start gap-3 ${
                            bulkLoadStatus.complete || bulkLoadStatus.success
                                ? 'bg-green-50 dark:bg-green-950/20 border border-green-200 dark:border-green-800' 
                                : bulkLoadStatus.error 
                                    ? 'bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800'
                                    : 'bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800'
                        }`}>
                            {bulkLoadStatus.loading ? (
                                <Loader2 className="h-5 w-5 animate-spin text-blue-600 flex-shrink-0" />
                            ) : bulkLoadStatus.complete || bulkLoadStatus.success ? (
                                <CheckCircle className="h-5 w-5 text-green-600 flex-shrink-0" />
                            ) : bulkLoadStatus.error ? (
                                <XCircle className="h-5 w-5 text-red-600 flex-shrink-0" />
                            ) : (
                                <AlertTriangle className="h-5 w-5 text-blue-600 flex-shrink-0" />
                            )}
                            <div className="flex-1 min-w-0">
                                <p className={`text-sm font-medium ${
                                    bulkLoadStatus.complete || bulkLoadStatus.success ? 'text-green-800 dark:text-green-300' :
                                    bulkLoadStatus.error ? 'text-red-800 dark:text-red-300' :
                                    'text-blue-800 dark:text-blue-300'
                                }`}>
                                    {bulkLoadStatus.error || bulkLoadStatus.message}
                                </p>
                                
                                {/* Individual system results */}
                                <div className="mt-2 space-y-1">
                                    {bulkLoadStatus.graph && (
                                        <p className="text-xs flex items-center gap-1">
                                            {bulkLoadStatus.graph.success ? (
                                                <CheckCircle className="h-3 w-3 text-green-500" />
                                            ) : (
                                                <XCircle className="h-3 w-3 text-red-500" />
                                            )}
                                            <span className="text-muted-foreground">
                                                Graph: {bulkLoadStatus.graph.success 
                                                    ? `${bulkLoadStatus.graph.statistics?.users || 0} users loaded`
                                                    : 'Failed'}
                                            </span>
                                        </p>
                                    )}
                                    {bulkLoadStatus.aerospike && (
                                        <p className="text-xs flex items-center gap-1">
                                            {bulkLoadStatus.aerospike.success ? (
                                                <CheckCircle className="h-3 w-3 text-green-500" />
                                            ) : (
                                                <XCircle className="h-3 w-3 text-amber-500" />
                                            )}
                                            <span className="text-muted-foreground">
                                                Aerospike KV: {bulkLoadStatus.aerospike.success 
                                                    ? `${bulkLoadStatus.aerospike.loaded || 0} users loaded`
                                                    : bulkLoadStatus.aerospike.message || 'Skipped'}
                                            </span>
                                        </p>
                                    )}
                                </div>
                                
                                {bulkLoadStatus.step && !bulkLoadStatus.complete && (
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Current step: <span className="font-medium">{bulkLoadStatus.step}</span>
                                    </p>
                                )}
                                {bulkLoadStatus.elements_written && (
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Elements written: <span className="font-medium">{bulkLoadStatus.elements_written.toLocaleString()}</span>
                                    </p>
                                )}
                                {bulkLoadStatus.progress_percentage !== undefined && (
                                    <div className="mt-2">
                                        <div className="flex justify-between text-xs text-muted-foreground mb-1">
                                            <span>Progress</span>
                                            <span>{bulkLoadStatus.progress_percentage}%</span>
                                        </div>
                                        <div className="h-2 bg-muted rounded-full overflow-hidden">
                                            <div 
                                                className="h-full bg-blue-500 transition-all duration-300"
                                                style={{ width: `${bulkLoadStatus.progress_percentage}%` }}
                                            />
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Warning */}
                    <div className="rounded-lg border border-amber-200/80 dark:border-amber-800/80 bg-amber-50/80 dark:bg-amber-950/20 px-2.5 py-1.5">
                        <p className="text-xs text-amber-800 dark:text-amber-300 flex items-center gap-1.5">
                            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                            Load/reload will affect existing data.
                        </p>
                    </div>

                    {/* Progress Bar */}
                    {isLoading && bulkLoadProgress && (
                        <div className="p-3 rounded-lg border border-green-200/80 dark:border-green-800/80 bg-green-50/80 dark:bg-green-950/20 space-y-1.5">
                            <div className="flex justify-between text-xs">
                                <span className="font-medium text-green-800 dark:text-green-200">{bulkLoadProgress.message}</span>
                                <span className="text-green-600 dark:text-green-400">{bulkLoadProgress.percentage}%</span>
                            </div>
                            <div className="h-3 bg-green-100 dark:bg-green-900/50 rounded-full overflow-hidden">
                                <div 
                                    className="h-full bg-green-500 transition-all duration-300 rounded-full"
                                    style={{ width: `${bulkLoadProgress.percentage}%` }}
                                />
                            </div>
                            <div className="flex justify-between text-xs text-green-600 dark:text-green-400">
                                <span>
                                    Step {bulkLoadProgress.current} / {bulkLoadProgress.total}
                                </span>
                                {bulkLoadProgress.estimated_remaining_seconds !== null && (
                                    <span>
                                        Est. ~{Math.round(bulkLoadProgress.estimated_remaining_seconds)}s remaining
                                    </span>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Actions */}
                    <div className="flex gap-2 mt-auto pt-2">
                        <Button 
                            size="sm"
                            onClick={handleBulkLoad} 
                            disabled={isLoading || (!loadGraph && !(aerospikeStats?.connected && loadAerospike)) || (!useDefaultData && !uploadedFile)}
                            className="flex-1"
                        >
                            {isLoading ? (
                                <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />{bulkLoadProgress ? `${bulkLoadProgress.percentage}%` : 'Starting...'}</>
                            ) : (
                                <>{useDefaultData ? <Play className="h-3.5 w-3.5 mr-1.5" /> : <Upload className="h-3.5 w-3.5 mr-1.5" />}{useDefaultData ? 'Start bulk load' : 'Upload & load'}</>
                            )}
                        </Button>
                        <Button size="sm" variant="outline" onClick={handleCheckStatus} disabled={loadingStats}>
                            {loadingStats ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                        </Button>
                    </div>
                </CardContent>
                )}
            </Card>
            </section>

            {/* Section: Generate Transactions */}
            <section className="space-y-3">
                <div className="flex items-center gap-2 text-muted-foreground">
                    <span className="text-xs font-semibold uppercase tracking-wider">2</span>
                    <span className="text-sm">Inject transactions</span>
                </div>
                <Card className="overflow-hidden border border-blue-200/60 dark:border-blue-800/60 shadow-sm h-full flex flex-col">
                    <CardHeader className="pb-2 pt-4 px-4">
                        <CardTitle className="flex items-center gap-2 text-base">
                            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/10">
                                <GitBranch className="h-4 w-4 text-blue-500" />
                            </div>
                            Inject transactions
                            <Badge variant="secondary" className="ml-auto text-xs font-normal text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800">
                                After 1
                            </Badge>
                    </CardTitle>
                        <CardDescription className="mt-0.5 text-xs">
                            Generate historical txns with fraud patterns → Graph DB & KV.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 px-4 pb-4 flex-1 flex flex-col">
                    {/* Parameters */}
                    <div className="grid grid-cols-3 gap-2">
                        <div className="space-y-1">
                            <Label htmlFor="txn-count" className="text-xs font-medium">Total txns</Label>
                            <Input id="txn-count" type="number" min={100} max={100000} value={txnCount} onChange={(e) => setTxnCount(Number(e.target.value))} className="h-8 text-sm" />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="spread-days" className="text-xs font-medium">Spread (days)</Label>
                            <Input id="spread-days" type="number" min={1} max={365} value={spreadDays} onChange={(e) => setSpreadDays(Number(e.target.value))} className="h-8 text-sm" />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="fraud-pct" className="text-xs font-medium">Fraud %</Label>
                            <Input id="fraud-pct" type="number" min={0} max={50} value={fraudPercentage} onChange={(e) => setFraudPercentage(Number(e.target.value))} className="h-8 text-sm" />
                        </div>
                    </div>
                    
                    {/* Fraud Patterns Explanation */}
                    <div className="p-3 rounded-lg border border-border/80 bg-muted/30">
                        <p className="font-medium text-xs mb-2">Fraud patterns ({fraudPercentage}% ≈ {Math.round(txnCount * fraudPercentage / 100)} txns):</p>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                            <div className="p-3 rounded-lg border border-red-200/80 dark:border-red-800/80 bg-red-50/80 dark:bg-red-950/30">
                                <p className="font-medium text-red-700 dark:text-red-300">Fraud rings (40%)</p>
                                <p className="text-red-600 dark:text-red-400 mt-1">~{Math.round(txnCount * fraudPercentage / 100 * 0.4)} txns</p>
                                <p className="text-muted-foreground mt-1">Tight groups trading among themselves</p>
                            </div>
                            <div className="p-3 rounded-lg border border-orange-200/80 dark:border-orange-800/80 bg-orange-50/80 dark:bg-orange-950/30">
                                <p className="font-medium text-orange-700 dark:text-orange-300">Velocity (25%)</p>
                                <p className="text-orange-600 dark:text-orange-400 mt-1">~{Math.round(txnCount * fraudPercentage / 100 * 0.25)} txns</p>
                                <p className="text-muted-foreground mt-1">30+ txns in one day</p>
                            </div>
                            <div className="p-3 rounded-lg border border-purple-200/80 dark:border-purple-800/80 bg-purple-50/80 dark:bg-purple-950/30">
                                <p className="font-medium text-purple-700 dark:text-purple-300">High amounts (20%)</p>
                                <p className="text-purple-600 dark:text-purple-400 mt-1">~{Math.round(txnCount * fraudPercentage / 100 * 0.2)} txns</p>
                                <p className="text-muted-foreground mt-1">{formatCurrencyWithLocale(15000)}–{formatCurrencyWithLocale(100000)} outliers</p>
                            </div>
                            <div className="p-3 rounded-lg border border-blue-200/80 dark:border-blue-800/80 bg-blue-50/80 dark:bg-blue-950/30">
                                <p className="font-medium text-blue-700 dark:text-blue-300">New account (15%)</p>
                                <p className="text-blue-600 dark:text-blue-400 mt-1">~{Math.round(txnCount * fraudPercentage / 100 * 0.15)} txns</p>
                                <p className="text-muted-foreground mt-1">Immediate high activity</p>
                            </div>
                        </div>
                    </div>

                    {/* Injection Result */}
                    {injectionResult && (
                        <div className="p-3 rounded-xl border border-green-200/80 dark:border-green-800/80 bg-green-50/80 dark:bg-green-950/20">
                            <p className="text-sm font-medium text-green-800 dark:text-green-200 flex items-center gap-2">
                                <CheckCircle className="h-4 w-4" />
                                Injection Complete!
                            </p>
                            <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                                <div>
                                    <span className="text-muted-foreground">Normal:</span>{' '}
                                    <span className="font-medium">{injectionResult.normal_transactions?.toLocaleString()}</span>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Fraud:</span>{' '}
                                    <span className="font-medium text-red-600">{injectionResult.fraud_transactions?.toLocaleString()}</span>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">Graph Writes:</span>{' '}
                                    <span className="font-medium">{injectionResult.graph_writes?.toLocaleString()}</span>
                                </div>
                                <div>
                                    <span className="text-muted-foreground">KV Writes:</span>{' '}
                                    <span className="font-medium">{injectionResult.kv_writes?.toLocaleString()}</span>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Progress Bar */}
                    {injectionLoading && injectionProgress && (
                        <div className="p-4 rounded-xl border border-blue-200/80 dark:border-blue-800/80 bg-blue-50/80 dark:bg-blue-950/20 space-y-2">
                            <div className="flex justify-between text-sm">
                                <span className="font-medium text-blue-800 dark:text-blue-200">
                                    {injectionProgress.message}
                                </span>
                                <span className="text-blue-600 dark:text-blue-400">
                                    {injectionProgress.percentage}%
                                </span>
                            </div>
                            <div className="h-3 bg-blue-100 dark:bg-blue-900/50 rounded-full overflow-hidden">
                                <div 
                                    className="h-full bg-blue-500 transition-all duration-300 rounded-full"
                                    style={{ width: `${injectionProgress.percentage}%` }}
                                />
                            </div>
                            <div className="flex justify-between text-xs text-blue-600 dark:text-blue-400">
                                <span>
                                    {injectionProgress.current.toLocaleString()} / {injectionProgress.total.toLocaleString()} transactions
                                </span>
                                {injectionProgress.estimated_remaining_seconds !== null && (
                                    <span>
                                        Est. ~{Math.round(injectionProgress.estimated_remaining_seconds)}s remaining
                                    </span>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Workflow Guide
                    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-blue-200/60 dark:border-blue-800/60 bg-blue-50/50 dark:bg-blue-950/20 p-3 text-xs text-blue-800 dark:text-blue-200">
                        <span className="font-medium shrink-0">Workflow:</span>
                        <span>1. Bulk Load</span>
                        <span className="text-blue-500">→</span>
                        <span>2. Inject</span>
                        <span className="text-blue-500">→</span>
                        <span>3. Compute Features & Run Detection (below)</span>
                        <span className="text-blue-500">→</span>
                        <span>4. Review Flagged Accounts tab</span>
                    </div> */}
                    
                    <Button
                        size="sm"
                        onClick={handleInjectTransactions}
                        disabled={injectionLoading || txnCount < 100}
                        className="w-full mt-auto pt-2"
                    >
                        {injectionLoading ? (
                            <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />{injectionProgress ? `${injectionProgress.percentage}%` : 'Starting...'}</>
                        ) : (
                            <><Play className="h-3.5 w-3.5 mr-1.5" />Inject {txnCount.toLocaleString()} txns ({fraudPercentage}% fraud)</>
                        )}
                    </Button>
                </CardContent>
            </Card>
            </section>
            </div>

            {/* Section 3 & 4: Features + ML run */}
            <section className="space-y-4">
                <div className="space-y-0.5">
                    <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">3 & 4</p>
                    <p className="text-sm font-medium text-foreground">Calculate features & run ML model</p>
                </div>
            {aerospikeStats?.connected === false ? (
                <Card className="overflow-hidden border-0 shadow-sm md:col-span-2">
                    <CardHeader className="pb-2 pt-4 px-4">
                        <CardTitle className="flex items-center gap-2 text-base">
                            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted">
                                <Zap className="h-4 w-4 text-muted-foreground" />
                            </div>
                            Run ML detection
                    </CardTitle>
                        <CardDescription className="mt-0.5 text-xs">
                            Requires Aerospike KV for parameters and detection run
                    </CardDescription>
                </CardHeader>
                    <CardContent className="px-4 pb-4">
                        <div className="p-4 rounded-lg bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800">
                            <div className="flex items-start gap-3">
                                <XCircle className="h-6 w-6 text-red-500 flex-shrink-0 mt-0.5" />
                                <div>
                                    <h4 className="text-sm font-medium text-red-800 dark:text-red-300">
                                        Aerospike KV unavailable
                                    </h4>
                                    <p className="text-xs text-red-700 dark:text-red-400 mt-1">
                                        Connect Aerospike KV to use detection parameters and run ML.
                                    </p>
                                    <Button variant="outline" size="sm" className="mt-2" onClick={() => mutateAerospikeStats()}>
                                        <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                                        Check connection
                                    </Button>
                        </div>
                        </div>
                    </div>
                    </CardContent>
                </Card>
                            ) : (
                                <>
                    <div className="grid gap-6 md:grid-cols-2 md:col-span-2">
                        {/* LEFT: Compute features (Step 1) – equal height */}
                        <Card className="overflow-hidden border border-purple-200/70 dark:border-purple-800/70 shadow-sm h-full flex flex-col">
                            <CardHeader className="pb-2 pt-4 px-4 shrink-0">
                                <CardTitle className="flex items-center gap-2 text-base">
                                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-500/10">
                                        <TrendingUp className="h-4 w-4 text-purple-500" />
                                    </div>
                                    Compute features
                                    <Badge variant="secondary" className="ml-auto text-xs font-normal text-purple-600 dark:text-purple-400 border-purple-200 dark:border-purple-800">
                                        Step 1
                                    </Badge>
                                </CardTitle>
                                <CardDescription className="mt-0.5 text-xs">
                                    Build account and device features from KV for manual ML runs
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="px-4 pb-4 flex-1 flex flex-col gap-3 min-h-0">
                                <div className="flex-1 min-h-0 flex flex-col gap-3">
                                    <div className="rounded-lg border border-purple-200/60 dark:border-purple-800/60 bg-gradient-to-br from-purple-50/80 to-transparent dark:from-purple-950/30 dark:to-transparent p-3">
                                        <div className="flex flex-wrap gap-2 mb-1">
                                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300">15 account</span>
                                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300">5 device</span>
                                        </div>
                                        <p className="text-xs text-purple-700/90 dark:text-purple-400/90 leading-relaxed">Velocity, amounts, recipients, device exposure, lifecycle (from KV over selected window).</p>
                                    </div>
                                    {featureResult && (
                                        <div className="p-2.5 rounded-lg border border-green-200/80 dark:border-green-800/80 bg-green-50/80 dark:bg-green-950/20 flex items-center justify-between gap-2 flex-wrap text-xs">
                                            <span className="font-medium text-green-800 dark:text-green-200 flex items-center gap-1.5"><CheckCircle className="h-3.5 w-3.5" />Done</span>
                                            <span className="text-muted-foreground">{featureResult.accounts_processed?.toLocaleString() ?? 0} accounts · {featureResult.devices_processed?.toLocaleString() ?? 0} devices</span>
                                        </div>
                                    )}
                                    {computingFeatures && featureProgress && (
                                        <div className="p-2.5 rounded-lg border border-purple-200/80 dark:border-purple-800/80 bg-purple-50/80 dark:bg-purple-950/20 space-y-1">
                                            <div className="flex justify-between text-xs"><span className="font-medium text-purple-800 dark:text-purple-200">{featureProgress.message}</span><span className="tabular-nums text-purple-600 dark:text-purple-400">{featureProgress.percentage}%</span></div>
                                            <div className="h-2 bg-purple-100 dark:bg-purple-900/50 rounded-full overflow-hidden"><div className="h-full bg-purple-500 transition-all duration-300 rounded-full" style={{ width: `${featureProgress.percentage}%` }} /></div>
                    </div>
                                    )}
                                </div>
                                <Button size="sm" onClick={handleComputeFeatures} disabled={computingFeatures} className="w-full bg-purple-600 hover:bg-purple-700 text-white shrink-0 mt-auto">
                                    {computingFeatures ? <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />{featureProgress ? `${featureProgress.percentage}%` : 'Starting…'}</> : <><TrendingUp className="h-3.5 w-3.5 mr-1.5" />Compute features ({detectionConfig.cooldown_days}d window)</>}
                                </Button>
                </CardContent>
            </Card>

                        {/* RIGHT: Run ML detection (Step 2) – equal height, compact */}
                        <Card className="overflow-hidden border-0 shadow-sm h-full flex flex-col">
                            <CardHeader className="pb-2 pt-4 px-4 shrink-0">
                                <CardTitle className="flex items-center gap-2 text-base">
                                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/10">
                                        <Zap className="h-4 w-4 text-blue-500" />
                                    </div>
                                    Run ML detection
                                    <Badge variant="secondary" className="ml-auto text-xs font-normal text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800">Step 2</Badge>
                    </CardTitle>
                                <CardDescription className="mt-0.5 text-xs">Set parameters and run detection to flag accounts</CardDescription>
                </CardHeader>
                            <CardContent className="px-4 pb-4 flex-1 flex flex-col gap-2.5 min-h-0">
                                {/* Parameters: one compact row */}
                                <div className="rounded-lg border border-border/80 bg-muted/30 p-2.5 space-y-2 shrink-0">
                                    <div className="flex flex-wrap items-end gap-3">
                                        <div className="space-y-0.5">
                                            <Label className="text-xs font-medium">Cooldown (d)</Label>
                                            <Input type="number" min={1} max={90} value={detectionConfig.cooldown_days} onChange={(e) => setDetectionConfig(prev => ({ ...prev, cooldown_days: parseInt(e.target.value) || 7 }))} className="h-7 w-14 text-xs" />
                                </div>
                                        <div className="flex-1 min-w-[100px] space-y-0.5">
                                            <Label className="text-xs font-medium">Risk: {detectionConfig.risk_threshold}</Label>
                                            <Slider value={[detectionConfig.risk_threshold]} onValueChange={([value]) => setDetectionConfig(prev => ({ ...prev, risk_threshold: value }))} min={0} max={100} step={5} className="w-full" />
                                </div>
                                        <Button size="sm" variant="outline" onClick={handleSaveConfig} disabled={saving} className="h-7 shrink-0">
                                            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle className="h-3 w-3" />}
                                        </Button>
                            </div>
                                </div>
                                {schedulerStatus?.detection_job_running && (
                                    <div className="p-1.5 rounded-lg bg-blue-50/80 dark:bg-blue-950/20 border border-blue-200/80 flex items-center gap-2 text-xs text-blue-700 dark:text-blue-300 shrink-0">
                                        <Loader2 className="h-3 w-3 animate-spin shrink-0" />Detection run in progress…
                                </div>
                                )}
                                <div className="flex items-center justify-between p-2 bg-muted/30 rounded-lg border gap-2 shrink-0">
                                    <Label className="text-xs font-medium">Skip cooldown</Label>
                                    <Switch checked={skipCooldown} onCheckedChange={setSkipCooldown} className="scale-75 origin-right" />
                                </div>
                                {runningDetection && detectionProgress && (
                                    <div className="p-2 rounded-lg bg-blue-50/80 dark:bg-blue-950/20 border border-blue-200/80 dark:border-blue-800/80 space-y-1 shrink-0">
                                        <div className="flex justify-between text-xs"><span className="font-medium text-blue-800 dark:text-blue-200">{detectionProgress.message}</span><span className="tabular-nums text-blue-600 dark:text-blue-400">{detectionProgress.percentage}%</span></div>
                                        <div className="h-2 bg-blue-100 dark:bg-blue-900/50 rounded-full overflow-hidden"><div className="h-full bg-blue-500 transition-all duration-300 rounded-full" style={{ width: `${detectionProgress.percentage}%` }} /></div>
                        </div>
                    )}
                                <div className="flex gap-2 mt-auto pt-1 shrink-0">
                                    <Button size="sm" onClick={handleRunDetection} disabled={runningDetection || schedulerStatus?.detection_job_running} className="flex-1">
                                        {runningDetection || schedulerStatus?.detection_job_running ? <><Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />{detectionProgress ? `${detectionProgress.percentage}%` : 'Starting...'}</> : <><Play className="h-3.5 w-3.5 mr-1.5" />Run detection now</>}
                                    </Button>
                                    <Button size="sm" variant="outline" onClick={() => { fetchConfig(); fetchHistory(); }}>
                                        <RefreshCw className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                </CardContent>
            </Card>
                    </div>

                    <Card className="md:col-span-2 overflow-hidden border-0 shadow-sm">
                        <CardHeader className="pb-2 pt-4 px-4">
                            <CardTitle className="flex items-center gap-2 text-base">
                                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted">
                                    <History className="h-4 w-4 text-muted-foreground" />
                                </div>
                                Detection history
                    </CardTitle>
                            <CardDescription className="mt-0.5 text-xs">Recent detection runs and results</CardDescription>
                </CardHeader>
                        <CardContent className="px-4 pb-4">
                            {historyLoading ? (
                                <div className="space-y-2">
                                    {[...Array(3)].map((_, i) => (
                                        <Skeleton key={i} className="h-12 w-full" />
                                    ))}
                    </div>
                            ) : detectionHistory.length === 0 ? (
                                <div className="text-center py-8 text-muted-foreground">
                                    <History className="h-12 w-12 mx-auto mb-4 opacity-50" />
                                    <p>No detection jobs have been run yet.</p>
                                    <p className="text-sm">Click &quot;Run Detection Now&quot; to start the first job.</p>
                                </div>
                            ) : (
                                <div className="overflow-x-auto rounded-lg border border-border/80">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Run Time</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead className="text-center">
                                                    <span className="flex items-center justify-center gap-1"><Users className="h-4 w-4" />Users Evaluated</span>
                                                </TableHead>
                                                <TableHead className="text-center">
                                                    <span className="flex items-center justify-center gap-1"><Timer className="h-4 w-4" />Users Skipped</span>
                                                </TableHead>
                                                <TableHead className="text-center">
                                                    <span className="flex items-center justify-center gap-1"><TrendingUp className="h-4 w-4" />Users Flagged</span>
                                                </TableHead>
                                                <TableHead className="text-right">Duration</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {detectionHistory.map((job) => (
                                                <TableRow key={job.job_id}>
                                                    <TableCell className="font-medium">
                                                        <div className="flex items-center gap-2">
                                                            <Calendar className="h-4 w-4 text-muted-foreground" />
                                                            {formatDateTime(job.start_time)}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>
                                                        {job.status === 'completed' ? (
                                                            <Badge variant="default" className="bg-green-500">
                                                                <CheckCircle className="h-3 w-3 mr-1" />Completed
                                                            </Badge>
                                                        ) : job.status === 'failed' ? (
                                                            <Badge variant="destructive">
                                                                <XCircle className="h-3 w-3 mr-1" />Failed
                                                            </Badge>
                                                        ) : (
                                                            <Badge variant="secondary">
                                                                <Loader2 className="h-3 w-3 mr-1 animate-spin" />Running
                                                            </Badge>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-center">
                                                        {(job.users_evaluated ?? job.accounts_evaluated).toLocaleString()}
                                                    </TableCell>
                                                    <TableCell className="text-center text-muted-foreground">
                                                        {job.accounts_skipped_cooldown.toLocaleString()}
                                                    </TableCell>
                                                    <TableCell className="text-center">
                                                        <span className={job.newly_flagged > 0 ? 'text-red-600 font-semibold' : ''}>
                                                            {job.newly_flagged.toLocaleString()}
                                                        </span>
                                                    </TableCell>
                                                    <TableCell className="text-right text-muted-foreground">
                                                        {job.duration_seconds ? formatDuration(job.duration_seconds) : '-'}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}
                </CardContent>
            </Card>
                </>
            )}
            </section>

        </div>
    )
}

export default DataManagement
