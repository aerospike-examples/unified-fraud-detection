'use client'

import Stat from "@/components/Stat"
import useSWR from 'swr'
import { useAppConfig } from '@/hooks/useAppConfig'

interface TransactionStats {
	total_txns: number
}

export default function TxnStats(){
    const { config } = useAppConfig()
    const { data, isLoading } = useSWR<TransactionStats>('/api/transactions/stats', {
        refreshInterval: config?.remote ? 5000 : 0,
    })

    return (
        <Stat
            title='Total Transactions'
            subtitle='Total transactions processed'
            {...!isLoading && data ? { stat: data.total_txns } : { loading: true } }
            icon='credit-card' />
    )
}
