'use client'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Activity, Shield } from 'lucide-react'
import Stat from '@/components/Stat'
import AmountStat from '@/components/AmountStat'
import { Skeleton } from '../ui/skeleton'
import useSWR from 'swr'

interface DashboardStats {
	users: number
	txns: number
	flagged: number
	amount: number
	fraud_rate: number
	health: string
}

export default function Main() {
    const { data: stats, isLoading } = useSWR<DashboardStats>('/api/dashboard/stats')
    
    return (
        <>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <Stat 
                title='Total Users'
                {...!isLoading && stats ? { stat: stats?.users || 0} : { loading: true }}
                subtitle='Registered users in the system' 
                icon='users' />
            <Stat 
                title='Total Transactions'
                {...!isLoading && stats ? { stat: stats?.txns || 0} : { loading: true }}
                subtitle='All processed transactions' 
                icon='credit-card' />
            <Stat 
                title='Flagged Transactions'
                {...!isLoading && stats ? { stat: stats?.flagged || 0} : { loading: true }}
                subtitle='Suspicious transactions detected' 
                icon='alert-triangle'
                color='destructive' />
            <AmountStat 
                title='Total Amount'

                amount={stats?.amount}
                loading={!stats}
                subtitle='Total transaction volume' 
                icon='trending-up'
                color='green-600' />
        </div>
        <div className="grid gap-4 md:grid-cols-2">
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Activity className="h-5 w-5" />
                        System Health
                    </CardTitle>
                </CardHeader>
                <CardContent className='flex flex-col gap-2 items-start'>
                    {isLoading ? (
                        <Skeleton className='w-[81px] h-[20px] rounded-full' />
                    ) : (
                        <Badge 
                        variant={stats?.health === 'connected' ? 'default' : 'destructive'}
                        className={stats?.health === 'connected' ? 'bg-green-600 hover:bg-green-700' : ''}
                        >
                            {stats?.health || 'unknown'}
                        </Badge>
                    )}
                    <div className="text-sm text-muted-foreground">
                        Database connection status
                    </div>
                </CardContent>
            </Card>
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Shield className="h-5 w-5" />
                        Fraud Detection Rate
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {isLoading ? (
                        <Skeleton className='w-[80px] h-[28px] rounded-full'/>
                    ) : (
                        <div className="text-2xl font-bold">
                            {stats?.fraud_rate != null ? `${stats.fraud_rate.toFixed(1)}%` : '—'}
                        </div>
                    )}
                    <p className="text-sm text-muted-foreground">Accuracy of fraud detection</p>
                </CardContent>
            </Card>
        </div>
        </>
    )
}
