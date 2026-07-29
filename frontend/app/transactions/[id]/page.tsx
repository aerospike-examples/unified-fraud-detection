'use server'

import { notFound } from 'next/navigation'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { 
	formatCurrency,
	formatDateTime,
	getRiskLevel,
	getTransactionTypeIcon,
	getStatusIcon
} from '@/lib/utils'
import TxnDetails, { type TxnDetail } from "@/components/Transactions/Details"
import Stat from '@/components/Stat'
import Label from '@/components/Label'

const API_BASE_URL = process.env.BACKEND_URL
	? `${process.env.BACKEND_URL}`
	: (process.env.BASE_URL || "http://localhost:8080/api")

export default async function TransactionsPage({ params }: { params: Promise<{ id: string }>}) {
	const { id: transactionId } = await params;
	const encodedID = encodeURIComponent(transactionId)
  	
	const response = await fetch(`${API_BASE_URL}/transaction/${encodedID}`, { cache: 'no-store' })
	if (!response.ok) {
		notFound()
	}
    const data = await response.json()
	const { txn, src, dest }: TxnDetail = data
	if (!txn) {
		notFound()
	}
	
  	const calculateOverallRisk = (fraud_score: number = 0) => {
    	const riskLevel = getRiskLevel(fraud_score)
    	return { 
			score: fraud_score,
			level: riskLevel.level,
			color: riskLevel.color
		}
  	}

    const overallRisk = calculateOverallRisk(txn.fraud_score)
	const transactionTypeIcon = getTransactionTypeIcon(txn.type);
	const transactionStatusIcon = getStatusIcon(txn.status)

	return (
    	<div className="space-y-6">
      		<div className="flex items-center justify-between">
				<div>
					<h1 className="text-3xl font-bold tracking-tight">Transaction Details</h1>
					<p className="text-muted-foreground">ID: {txn.txn_id}</p>
				</div>
				<div className="flex items-center gap-2">
					{txn.is_fraud &&
					<Label 
						icon="flag" 
						color='destructive' 
						badge={{ 
							variant: 'destructive', 
							text: "Fraudulent",
							className: 'text-sm px-3 py-1' 
						}} />}
					<Badge variant={overallRisk.color as any} className="text-sm px-3 py-1 font-medium border">
						{overallRisk.level} Risk ({(overallRisk.score || 0).toFixed(1)})
					</Badge>
				</div>
      		</div>
      		<div className="grid gap-4 md:grid-cols-4">
				<Stat
					color={txn.amount < 0 ? 'destructive' : 'green-600'}
					title='Amount'
					stat={formatCurrency(Math.abs(txn.amount))} />
				<Stat
					title='Status'
					stat={{ 
						icon: transactionStatusIcon,
						text: txn.status 
					}} />
        		<Stat
					title='Type'
					stat={{
						icon: transactionTypeIcon,
						text: txn?.type ?? 'Transfer'
					}} />
        		<Stat
					title='Fraud Rules'
					stat={txn.is_fraud ? (txn.details?.length ?? 0) : 0}
					icon='shield' />
      		</div>
			<div className="grid gap-4 md:grid-cols-2">
            	<Card>
              		<CardHeader>
						<Label
							size='2xl'
							text='Transaction Information'
							className='font-semibold'
							icon='activity' />
              		</CardHeader>
              		<CardContent className="grid grid-cols-2 gap-4">
						<Label
							className="font-mono"
							title='Transaction ID'
							text={txn.txn_id} />
						<Label
							size='lg'
							className={`font-semibold ${txn.amount < 0 ? 'text-destructive' : 'text-green-600'}`}
							title='Amount'
							text={formatCurrency(Math.abs(txn.amount))} />
						<Label
							size='lg'
							title='Method'
							text={txn.method} />
						<Label
							size='lg'
							title="Type"
							icon={transactionTypeIcon.name} 
							color={transactionTypeIcon.color} 
							text={txn.type || 'Transfer'} />
						<Label
							size='lg'
							title='Status'
							icon={transactionStatusIcon.name}
							color={transactionStatusIcon.color}
							text={txn.status} />
						<Label
							title='Date & Time'
							icon='calendar' 
							color='foreground' 
							text={formatDateTime(txn.timestamp)} />
              		</CardContent>
            	</Card>
            	<Card>
              		<CardHeader>
						<Label
							size='2xl'
							text='Risk Assessment'
							className='font-semibold'
							icon='shield' />
              		</CardHeader>
              		<CardContent className='flex flex-col gap-4'>
						<Label
							size='xl'
							title='Fraud Score'
							text={overallRisk.score?.toFixed(1) || '0.0'}
							className='font-semibold'
							badge={{
								variant: overallRisk.color as any,
								text: overallRisk.level
							}} />
						<Label
							title='Fraud Status'
							icon={txn.is_fraud ? 'alert-triangle' : 'check-circle'}
							color={txn.is_fraud ? 'destructive' : 'green-600'}
							badge={{
								variant: txn.is_fraud ? 'destructive' : 'default',
								text: txn.is_fraud ? 'Flagged' : 'Clean'
							}} />
              		</CardContent>
            	</Card>
          	</div>
			<TxnDetails txn={txn} src={src} dest={dest} />
    	</div>
  	)
} 
