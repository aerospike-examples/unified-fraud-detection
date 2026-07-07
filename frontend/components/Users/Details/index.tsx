'use client'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useState } from 'react'
import Accounts, { type Account } from './Accounts'
import Transactions, { type TransactionDetail } from './Transactions'
import Devices, { type Device } from './Devices'
import Connections, { type Connection } from './Connections'

export interface UserSummary {
    user: User
    accounts: Account[]
    txns?: TransactionDetail[]  // Optional - not available from KV
    total_txns?: number  // Optional - not available from KV
    total_sent?: number  // Optional - not available from KV
    total_recd?: number  // Optional - not available from KV
    risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    connected_users?: Connection[]  // Optional - not available from KV
    devices?: Device[]
}

export interface User {
    "1"?: string
    id?: string
    name: string
    email: string
    age: number
    signup_date: string
    location: string
    risk_score: number
    is_flagged: boolean
    /** Overall disposition (pending_review, under_investigation, monitoring,
     * temporarily_frozen, confirmed_fraud, cleared, or "active" if never flagged). */
    account_status?: string
    phone?: string
    occupation?: string
}

const UserDetails = ({ userDetails }: { userDetails: UserSummary }) => {
    const [active, setActive] = useState('accounts');
    const hasTransactions = userDetails.txns && userDetails.txns.length > 0;
    const hasConnections = userDetails.connected_users && userDetails.connected_users.length > 0;
    
    return (
        <Tabs value={active} onValueChange={setActive} className="space-y-4">
            <TabsList className="grid w-full grid-cols-4">
                <TabsTrigger value="accounts">Accounts ({userDetails.accounts?.length ?? 0})</TabsTrigger>
                <TabsTrigger value="transactions">Transactions {hasTransactions ? `(${userDetails.txns?.length})` : ''}</TabsTrigger>
                <TabsTrigger value="devices">Devices ({userDetails.devices?.length ?? 0})</TabsTrigger>
                <TabsTrigger value="connections">Connections {hasConnections ? `(${userDetails.connected_users?.length})` : ''}</TabsTrigger>
            </TabsList>
            <TabsContent value="accounts" className="space-y-4">
                <Accounts accounts={userDetails.accounts ?? []} />
            </TabsContent>
            <TabsContent value="transactions" className="space-y-4">
                {hasTransactions ? (
                    <Transactions txns={userDetails.txns!} accounts={userDetails.accounts ?? []} name={userDetails.user.name} />
                ) : (
                    <div className="text-center py-8 text-muted-foreground">
                        <p>Transaction history not available in this view.</p>
                        <p className="text-sm mt-2">View the Transactions page for full transaction history.</p>
                    </div>
                )}
            </TabsContent>
            <TabsContent value="devices" className="space-y-4">
                <Devices devices={userDetails.devices ?? []} />
            </TabsContent>
            <TabsContent value="connections" className="space-y-4">
                {hasConnections ? (
                    <Connections devices={userDetails.devices ?? []} connections={userDetails.connected_users!} />
                ) : (
                    <div className="text-center py-8 text-muted-foreground">
                        <p>Connection data not available in this view.</p>
                        <p className="text-sm mt-2">Connected users are identified through shared devices.</p>
                    </div>
                )}
            </TabsContent>
        </Tabs>
    )
}

export default UserDetails;