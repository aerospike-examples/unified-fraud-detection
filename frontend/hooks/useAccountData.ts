'use client'

import { useState, useEffect, useCallback } from 'react'
import { formatCurrency } from '@/lib/utils'

// Types matching the backend API responses
interface UserProfile {
    id: string
    name: string
    email: string
    phone?: string
    location: string
    age?: number
    occupation?: string
    signup_date?: string
    risk_score?: number
}

interface Account {
    id?: string
    '1'?: string  // Graph DB uses '1' for ID
    type: string
    balance: number
    status: string
    bank_name?: string
    created_at?: string
    created_date?: string
}

interface Device {
    id: string
    type: string
    os: string
    browser?: string
    fraud_flag: boolean
    first_seen?: string
    last_login?: string
    location?: string
}

interface Transaction {
    id: string
    amount: number
    timestamp: string
    type: string
    fraud_score: number
    fraud_status: string
    recipient?: string
}

interface ConnectedUser {
    user_id: string
    name: string
    risk_score: number
    shared_devices: string[]
}

interface RiskFactor {
    factor: string
    severity: 'high' | 'medium' | 'low'
    score: number
}

interface AccountPrediction {
    account_id: string
    risk_score: number
}

interface AccountResolution {
    resolution: 'fraud' | 'safe' | null
    fraud: boolean | null
    fraud_date: string | null
    fraud_reason: string | null
    cleared_date: string | null
    cleared_notes: string | null
}

interface FlaggedAccountInfo {
    account_id: string
    user_id: string
    account_holder?: string
    risk_score: number
    flag_reason: string
    flagged_date: string
    status: string
    risk_factors: RiskFactor[]
    account_predictions?: AccountPrediction[]
    highest_risk_account_id?: string
}

interface ActivityLog {
    time: string
    action: string
    amount?: number
    status: 'alert' | 'warning' | 'pending' | 'info'
}

// Combined account data structure for the UI
export interface AccountData {
    id: string
    account_holder: string
    user_id: string
    bank_name: string
    account_type: string
    account_number: string
    balance: number
    risk_score: number
    flagged_date: string
    flag_reason: string
    status: string
    created_date: string
    last_activity: string
    
    user: {
        name: string
        email: string
        phone: string
        location: string
        signup_date: string
        age: number
        occupation: string
    }
    
    risk_factors: RiskFactor[]
    suspicious_transactions: Array<{
        id: string
        date: string
        amount: number
        recipient: string
        type: string
        risk: 'high' | 'medium' | 'low'
    }>
    devices: Array<{
        id: string
        type: string
        os: string
        last_seen: string
        location: string
        trusted: boolean
    }>
    activity_log: ActivityLog[]
    connected_users: ConnectedUser[]
    accounts: Account[]
    account_predictions?: AccountPrediction[]
    highest_risk_account_id?: string
    high_risk_accounts: Account[]
    account_resolutions: Record<string, 'fraud' | 'safe' | null>
}

interface UseAccountDataReturn {
    data: AccountData | null
    loading: boolean
    error: string | null
    refetch: () => void
}

export function useAccountData(userId: string): UseAccountDataReturn {
    const [data, setData] = useState<AccountData | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const fetchData = useCallback(async () => {
        if (!userId) return
        
        setLoading(true)
        setError(null)
        
        try {
            // Fetch all data in parallel
            const [userResponse, flaggedResponse, connectedResponse] = await Promise.all([
                fetch(`/api/users/${userId}`),
                fetch(`/api/flagged-accounts/${userId}`).catch(() => null),
                fetch(`/api/users/${userId}/connected-devices`).catch(() => null)
            ])
            
            // Parse user summary (required)
            if (!userResponse.ok) {
                throw new Error(`Failed to fetch user data: ${userResponse.statusText}`)
            }
            const userSummary = await userResponse.json()
            
            // Parse flagged account (optional - may not exist)
            let flaggedAccount: FlaggedAccountInfo | null = null
            if (flaggedResponse && flaggedResponse.ok) {
                flaggedAccount = await flaggedResponse.json()
            }
            
            // Parse connected users (optional)
            let connectedUsers: ConnectedUser[] = []
            if (connectedResponse && connectedResponse.ok) {
                const connectedData = await connectedResponse.json()
                connectedUsers = connectedData.connected_users || []
            }
            
            // Extract data from user summary
            const user = userSummary.user || {}
            const rawAccounts: Account[] = userSummary.accounts || []
            const rawDevices = userSummary.devices || []
            const rawTransactions = userSummary.txns || []
            
            // Helper to get ID from various formats (API returns "1" for ID in graph objects)
            const getId = (obj: any): string => obj?.id || obj?.['1'] || obj?.['~id'] || 'Unknown'
            
            // Deduplication helper - removes duplicate items by ID
            const deduplicateById = <T extends Record<string, any>>(arr: T[]): T[] => {
                const seen = new Set<string>()
                return arr.filter(item => {
                    const id = getId(item)
                    if (seen.has(id)) return false
                    seen.add(id)
                    return true
                })
            }
            
            // Deduplicate accounts, devices, and transactions (fixes duplicate edges issue in DB)
            const accounts = deduplicateById(rawAccounts)
            const devices = deduplicateById(rawDevices)
            
            // For transactions, deduplicate by transaction ID
            const transactions = rawTransactions.filter((txn: any, idx: number, arr: any[]) => {
                const txnData = txn.txn || txn
                const txnId = txnData.txn_id || getId(txnData)
                return arr.findIndex((t: any) => {
                    const tData = t.txn || t
                    return (tData.txn_id || getId(tData)) === txnId
                }) === idx
            })
            
            // Get primary account info
            const primaryAccount = accounts[0] || { type: 'Unknown', balance: 0, created_at: '', '1': '' }
            
            // Transform transactions to suspicious transactions format
            const suspiciousTransactions = transactions.map((txn: any) => {
                const txnData = txn.txn || txn
                const fraudScore = txnData.fraud_score || 0
                const riskLevel = fraudScore >= 70 ? 'high' : fraudScore >= 40 ? 'medium' : 'low'
                return {
                    id: txnData.txn_id || getId(txnData) || 'Unknown',
                    date: txnData.timestamp || new Date().toISOString(),
                    amount: txnData.amount || 0,
                    recipient: txn.other_party?.name || 'Unknown',
                    type: txnData.type || 'transfer',
                    risk: riskLevel as 'high' | 'medium' | 'low'
                }
            })
            
            // Transform devices
            const transformedDevices = devices.map((device: any) => ({
                id: getId(device),
                type: `${device.type || 'Unknown'} ${device.browser || ''}`.trim(),
                os: device.os || 'Unknown',
                last_seen: device.last_login || device.first_seen || new Date().toISOString(),
                location: device.location || 'Unknown',
                trusted: !device.fraud_flag
            }))
            
            // Build activity log from transactions and flagged info
            const activityLog: ActivityLog[] = []
            
            // Add flagged event if exists
            if (flaggedAccount?.flagged_date) {
                activityLog.push({
                    time: new Date(flaggedAccount.flagged_date).toLocaleString(),
                    action: 'Account flagged by system',
                    status: 'alert'
                })
            }
            
            // Add recent transactions to activity
            suspiciousTransactions.slice(0, 5).forEach((txn: any) => {
                activityLog.push({
                    time: new Date(txn.date).toLocaleString(),
                    action: `${txn.type} to ${txn.recipient}`,
                    amount: txn.amount,
                    status: txn.risk === 'high' ? 'warning' : 'info'
                })
            })
            
            // Sort by time descending
            activityLog.sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())
            
            // Get risk factors from flagged account or generate from data
            // API may return strings or objects, normalize to objects
            let riskFactors: RiskFactor[] = []
            const apiRiskFactors: any[] = flaggedAccount?.risk_factors || []
            
            if (apiRiskFactors.length > 0) {
                // Check if it's an array of strings or objects
                if (typeof apiRiskFactors[0] === 'string') {
                    // Convert string array to RiskFactor objects
                    riskFactors = (apiRiskFactors as string[]).map((factor: string, idx: number) => ({
                        factor: factor.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase()),
                        severity: (idx === 0 ? 'high' : idx === 1 ? 'medium' : 'low') as 'high' | 'medium' | 'low',
                        score: Math.round((apiRiskFactors.length - idx) * 15)
                    }))
                } else {
                    // Already in object format
                    riskFactors = apiRiskFactors as RiskFactor[]
                }
            }
            
            // If no risk factors from API, generate based on data analysis
            if (riskFactors.length === 0) {
                const highRiskTxns = suspiciousTransactions.filter((t: any) => t.risk === 'high')
                const flaggedDevices = transformedDevices.filter((d: any) => !d.trusted)
                const highRiskConnections = connectedUsers.filter(u => u.risk_score >= 70)
                
                if (highRiskTxns.length > 0) {
                    riskFactors.push({
                        factor: `${highRiskTxns.length} high-risk transaction(s) detected`,
                        severity: 'high',
                        score: highRiskTxns.length * 15
                    })
                }
                if (flaggedDevices.length > 0) {
                    riskFactors.push({
                        factor: `${flaggedDevices.length} untrusted device(s) detected`,
                        severity: 'medium',
                        score: flaggedDevices.length * 10
                    })
                }
                if (highRiskConnections.length > 0) {
                    riskFactors.push({
                        factor: `Connected to ${highRiskConnections.length} high-risk user(s)`,
                        severity: 'high',
                        score: highRiskConnections.length * 20
                    })
                }
                if (connectedUsers.length > 3) {
                    riskFactors.push({
                        factor: `Shares devices with ${connectedUsers.length} users`,
                        severity: 'medium',
                        score: 15
                    })
                }
            }
            
            // Calculate total risk score
            const calculatedRiskScore = flaggedAccount?.risk_score || 
                user.risk_score || 
                Math.min(100, riskFactors.reduce((sum, f) => sum + f.score, 0))
            
            // Get primary account ID
            const primaryAccountId = getId(primaryAccount)
            
            // Extract account predictions and highest risk account from flagged account
            const accountPredictions = flaggedAccount?.account_predictions || []
            const highestRiskAccountId = flaggedAccount?.highest_risk_account_id || getId(primaryAccount)
            
            // Compute high risk accounts (risk_score >= 50)
            const RISK_THRESHOLD = 50
            const highRiskAccountIds = new Set(
                accountPredictions
                    .filter(p => p.risk_score >= RISK_THRESHOLD)
                    .map(p => p.account_id)
            )
            const highRiskAccounts = accounts.filter((acc: any) => highRiskAccountIds.has(getId(acc)))
            
            // Fetch account resolutions for high-risk accounts
            let accountResolutions: Record<string, 'fraud' | 'safe' | null> = {}
            if (accountPredictions.length > 0) {
                try {
                    const accountIds = accountPredictions.map(p => p.account_id)
                    const resolutionsResponse = await fetch('/api/accounts/resolutions', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(accountIds)
                    })
                    if (resolutionsResponse.ok) {
                        const resolutionsData = await resolutionsResponse.json()
                        const resolutions = resolutionsData.resolutions || {}
                        // Convert to simple resolution map
                        for (const [accountId, data] of Object.entries(resolutions)) {
                            const resData = data as AccountResolution
                            accountResolutions[accountId] = resData.resolution
                        }
                    }
                } catch (err) {
                    console.warn('Failed to fetch account resolutions:', err)
                }
            }
            
            // Build combined data object
            const combinedData: AccountData = {
                id: userId,
                account_holder: user.name || 'Unknown User',
                user_id: userId,
                bank_name: primaryAccount.bank_name || 'Aerospike Bank',
                account_type: primaryAccount.type || 'Checking',
                account_number: `****${primaryAccountId.slice(-4) || '0000'}`,
                balance: accounts.reduce((sum: number, acc: any) => sum + (acc.balance || 0), 0),
                risk_score: calculatedRiskScore,
                flagged_date: flaggedAccount?.flagged_date || new Date().toISOString(),
                flag_reason: flaggedAccount?.flag_reason || 'Under investigation',
                status: flaggedAccount?.status || 'pending_review',
                created_date: primaryAccount.created_date || primaryAccount.created_at || user.signup_date || '',
                last_activity: transactions[0]?.timestamp || transactions[0]?.txn?.timestamp || new Date().toISOString(),
                
                user: {
                    name: user.name || 'Unknown',
                    email: user.email || 'unknown@email.com',
                    phone: user.phone || 'N/A',
                    location: user.location || 'Unknown',
                    signup_date: user.signup_date || '',
                    age: user.age || 0,
                    occupation: user.occupation || 'Unknown'
                },
                
                risk_factors: riskFactors,
                suspicious_transactions: suspiciousTransactions,
                devices: transformedDevices,
                activity_log: activityLog,
                connected_users: connectedUsers,
                accounts: accounts,
                account_predictions: accountPredictions,
                highest_risk_account_id: highestRiskAccountId,
                high_risk_accounts: highRiskAccounts,
                account_resolutions: accountResolutions
            }
            
            setData(combinedData)
            
        } catch (err) {
            console.error('Error fetching account data:', err)
            setError(err instanceof Error ? err.message : 'Failed to fetch account data')
        } finally {
            setLoading(false)
        }
    }, [userId])

    useEffect(() => {
        fetchData()
    }, [fetchData])

    return {
        data,
        loading,
        error,
        refetch: fetchData
    }
}

// Hook for fetching graph data specifically
export function useGraphData(userId: string) {
    const [graphData, setGraphData] = useState<{
        nodes: any[]
        edges: any[]
    } | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const fetchGraphData = useCallback(async () => {
        if (!userId) return
        
        setLoading(true)
        setError(null)
        
        try {
            // Fetch user data, device-shared connections, and the transaction-partner
            // network. The transaction network deliberately reuses the exact same
            // traversal shape as the detect_fraud_ring investigation tool (see
            // get_transaction_ring_network on the backend), so this view can't
            // silently disagree with the Fraud Ring panel the way a separate,
            // shallower "last 5 transactions" traversal used to.
            const [userResponse, connectedResponse, txnNetworkResponse] = await Promise.all([
                fetch(`/api/users/${userId}`),
                fetch(`/api/users/${userId}/connected-devices`),
                fetch(`/api/users/${userId}/transaction-network`)
            ])
            
            if (!userResponse.ok) {
                throw new Error('Failed to fetch user data')
            }
            
            const userSummary = await userResponse.json()
            const user = userSummary.user || {}
            const accounts = userSummary.accounts || []
            const devices = userSummary.devices || []
            
            let connectedUsers: any[] = []
            if (connectedResponse.ok) {
                const connectedData = await connectedResponse.json()
                connectedUsers = connectedData.connected_users || []
            }

            let txnNetworkNodes: any[] = []
            let txnNetworkEdges: any[] = []
            if (txnNetworkResponse.ok) {
                const txnNetworkData = await txnNetworkResponse.json()
                txnNetworkNodes = txnNetworkData.nodes || []
                txnNetworkEdges = txnNetworkData.edges || []
            }
            
            // Helper to get ID from various formats
            const getId = (obj: any): string => obj?.id || obj?.['1'] || obj?.['~id'] || 'Unknown'
            
            // DEDUPLICATE arrays by ID (fixes duplicate edges issue in database)
            const deduplicateById = (arr: any[]): any[] => {
                const seen = new Set<string>()
                return arr.filter(item => {
                    const id = getId(item)
                    if (seen.has(id)) return false
                    seen.add(id)
                    return true
                })
            }
            
            // Deduplicate accounts and devices
            const uniqueAccounts = deduplicateById(accounts)
            const uniqueDevices = deduplicateById(devices)
            
            // Build graph nodes
            const nodes: any[] = []
            const edges: any[] = []
            const nodeIds = new Set<string>() // Track added node IDs
            const edgeKeys = new Set<string>() // Track added edges to prevent duplicates
            const centerX = 400
            const centerY = 300
            
            // Helper to add node only if not already added
            const addNode = (node: any) => {
                if (!nodeIds.has(node.id)) {
                    nodeIds.add(node.id)
                    nodes.push(node)
                }
            }
            
            // Helper to add edge only if not already added
            const addEdge = (edge: any) => {
                const key = `${edge.source}-${edge.type}-${edge.target}`
                if (!edgeKeys.has(key)) {
                    edgeKeys.add(key)
                    edges.push(edge)
                }
            }
            
            // Add central user node
            addNode({
                id: userId,
                label: `${user.name || 'User'}\n${userId}`,
                type: 'user',
                x: centerX,
                y: centerY,
                risk: user.risk_score >= 70 ? 'high' : user.risk_score >= 40 ? 'medium' : 'low',
                isFlagged: true,
                details: {
                    'User ID': userId,
                    'Name': user.name || 'Unknown',
                    'Location': user.location || 'Unknown',
                    'Risk Score': String(user.risk_score || 0)
                }
            })
            
            // Add account nodes (using deduplicated array)
            uniqueAccounts.forEach((acc: any, idx: number) => {
                const accId = getId(acc)
                const angle = (idx * 2 * Math.PI) / Math.max(uniqueAccounts.length, 1) - Math.PI / 2
                const radius = 140
                addNode({
                    id: accId,
                    label: `${acc.type}\n${accId}`,
                    type: 'account',
                    x: centerX + Math.cos(angle) * radius,
                    y: centerY + Math.sin(angle) * radius,
                    details: {
                        'Account': accId,
                        'Type': acc.type,
                        'Balance': formatCurrency(acc.balance || 0),
                        'Status': acc.status || 'active'
                    }
                })
                
                addEdge({
                    source: userId,
                    target: accId,
                    type: 'owns',
                    label: 'owns'
                })
            })
            
            // Add device nodes (using deduplicated array with better positioning)
            uniqueDevices.forEach((dev: any, idx: number) => {
                const devId = getId(dev)
                const angle = (idx * 2 * Math.PI) / Math.max(uniqueDevices.length, 1) + Math.PI
                const radius = 160
                addNode({
                    id: devId,
                    label: `${dev.type}\n${dev.os}`,
                    type: 'device',
                    x: centerX + Math.cos(angle) * radius,
                    y: centerY + Math.sin(angle) * radius,
                    risk: dev.fraud_flag ? 'high' : undefined,
                    details: {
                        'Device': dev.type,
                        'OS': dev.os,
                        'Browser': dev.browser || 'Unknown',
                        'First Seen': dev.first_seen || 'Unknown',
                        'Flagged': dev.fraud_flag ? 'Yes' : 'No'
                    }
                })
                
                addEdge({
                    source: userId,
                    target: devId,
                    type: 'uses',
                    label: 'uses'
                })
            })
            
            // Add connected users (1-hop) - filter out self
            const uniqueConnected = connectedUsers.filter((c: any) => c.user_id !== userId).slice(0, 10)
            uniqueConnected.forEach((conn: any, idx: number) => {
                const angle = (idx * Math.PI) / Math.max(uniqueConnected.length - 1, 1) - Math.PI / 4
                addNode({
                    id: conn.user_id,
                    label: `${conn.name || 'User'}\n${conn.user_id}`,
                    type: 'user',
                    x: centerX + 220 + Math.cos(angle) * 80,
                    y: centerY - 30 + Math.sin(angle) * 80,
                    risk: conn.risk_score >= 70 ? 'high' : conn.risk_score >= 40 ? 'medium' : 'low',
                    details: {
                        'User ID': conn.user_id,
                        'Name': conn.name || 'Unknown',
                        'Risk Score': String(conn.risk_score || 0),
                        'Connection': 'Shared device'
                    }
                })
                
                // Connect via shared devices
                if (conn.shared_devices && conn.shared_devices.length > 0) {
                    conn.shared_devices.forEach((devId: string) => {
                        if (nodeIds.has(devId)) {
                            addEdge({
                                source: devId,
                                target: conn.user_id,
                                type: 'uses'
                            })
                        }
                    })
                } else {
                    // Direct connection line
                    addEdge({
                        source: userId,
                        target: conn.user_id,
                        type: 'connected',
                        label: 'shared device'
                    })
                }
            })
            
            // Add transaction-partner nodes/edges from the ring-consistent network
            // endpoint: the target user plus every direct TRANSACTS partner, laid
            // out in an outer ring, then any edges discovered BETWEEN those
            // partners (the structure that makes something a ring rather than a
            // star — a plain "recent transactions" list can't show this).
            const partnerNodes = txnNetworkNodes.filter((n: any) => n.user_id !== userId)
            partnerNodes.forEach((partner: any, idx: number) => {
                const angle = (idx * 2 * Math.PI) / Math.max(partnerNodes.length, 1)
                const radius = 300
                addNode({
                    id: partner.user_id,
                    label: `${partner.name || 'User'}\n${partner.user_id}`,
                    type: 'user',
                    x: centerX + Math.cos(angle) * radius,
                    y: centerY + Math.sin(angle) * radius,
                    risk: (partner.risk_score || 0) >= 70 ? 'high' :
                          (partner.risk_score || 0) >= 40 ? 'medium' : 'low',
                    details: {
                        'User ID': partner.user_id,
                        'Name': partner.name || 'Unknown',
                        'Risk Score': String(partner.risk_score || 0),
                        'Connection': 'Transaction partner'
                    }
                })
            })

            txnNetworkEdges.forEach((edge: any) => {
                if (nodeIds.has(edge.from) && nodeIds.has(edge.to)) {
                    addEdge({
                        source: edge.from,
                        target: edge.to,
                        type: 'transaction',
                        label: 'transacts'
                    })
                }
            })
            
            setGraphData({ nodes, edges })
            
        } catch (err) {
            console.error('Error fetching graph data:', err)
            setError(err instanceof Error ? err.message : 'Failed to fetch graph data')
        } finally {
            setLoading(false)
        }
    }, [userId])

    useEffect(() => {
        fetchGraphData()
    }, [fetchGraphData])

    return { graphData, loading, error, refetch: fetchGraphData }
}
