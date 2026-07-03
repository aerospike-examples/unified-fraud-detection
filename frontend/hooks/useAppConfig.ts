'use client'

import useSWR from 'swr'

export interface AppCapabilities {
    bulkLoad: boolean
    injectTransactions: boolean
    computeFeatures: boolean
    runDetection: boolean
    clearData: boolean
    rtGeneration: boolean
    browseSource: 'kv' | 'graph'
    statsSource: 'kv' | 'graph_summary'
}

export interface AppConfig {
    mode: 'local' | 'remote'
    remote: boolean
    capabilities: AppCapabilities
}

const DEFAULT_CAPABILITIES: AppCapabilities = {
    bulkLoad: true,
    injectTransactions: true,
    computeFeatures: true,
    runDetection: true,
    clearData: true,
    rtGeneration: true,
    browseSource: 'kv',
    statsSource: 'kv',
}

const DEFAULT_CONFIG: AppConfig = {
    mode: 'local',
    remote: false,
    capabilities: DEFAULT_CAPABILITIES,
}

/**
 * Fetch runtime app config (data-source mode + capability flags) once and cache
 * it for the session. Defaults to permissive local-mode capabilities until the
 * config resolves so the UI never blocks on it.
 */
export function useAppConfig(): { config: AppConfig; isLoading: boolean } {
    const { data, isLoading } = useSWR<AppConfig>('/api/config', {
        revalidateOnFocus: false,
        revalidateIfStale: false,
        dedupingInterval: 300000,
    })

    return {
        config: data ?? DEFAULT_CONFIG,
        isLoading,
    }
}
