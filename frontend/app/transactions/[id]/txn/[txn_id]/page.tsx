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
import TxnDetails, { type TxnDetail } from '@/components/Transactions/Details'
import Stat from '@/components/Stat'
import Label from '@/components/Label'

const API_BASE_URL = process.env.BACKEND_URL
	? `${process.env.BACKEND_URL}`
	: (process.env.BASE_URL || 'http://localhost:8080/api')

export default async function TransactionGraphDetailPage({
	params
}: {
	params: Promise<{ id: string; txn_id: string }>
}) {
	const { id: account_id, txn_id } = await params

	const response = await fetch(
		`${API_BASE_URL}/transaction/${encodeURIComponent(account_id)}/txn/${encodeURIComponent(txn_id)}`,
		{ cache: 'no-store' }
	)
	if (!response.ok) {
		notFound()
	}

	const { txn, src, dest }: TxnDetail = await response.json()
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

	const overallRisk = calculateOverallRisk(txn?.fraud_score ?? 0)
	const transactionTypeIcon = getTransactionTypeIcon(txn?.type ?? 'transfer')
	const transactionStatusIcon = getStatusIcon(txn?.status ?? 'completed')

	return (
		<div className="space-y-6">
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-3xl font-bold tracking-tight">Transaction Details</h1>
					<p className="text-muted-foreground">ID: {txn?.txn_id ?? txn_id}</p>
				</div>
				<div className="flex items-center gap-2">
					{txn?.is_fraud && (
						<Label
							icon="flag"
							color="destructive"
							badge={{
								variant: 'destructive',
								text: 'Fraudulent',
								className: 'text-sm px-3 py-1'
							}}
						/>
					)}
					<Badge variant={overallRisk.color as any} className="text-sm px-3 py-1 font-medium border">
						{overallRisk.level} Risk ({(overallRisk.score || 0).toFixed(1)})
					</Badge>
				</div>
			</div>
			<div className="grid gap-4 md:grid-cols-4">
				<Stat
					color={(txn?.amount ?? 0) < 0 ? 'destructive' : 'green-600'}
					title="Amount"
					stat={formatCurrency(Math.abs(txn?.amount ?? 0))}
				/>
				<Stat
					title="Status"
					stat={{
						icon: transactionStatusIcon,
						text: txn?.status ?? 'completed'
					}}
				/>
				<Stat
					title="Type"
					stat={{
						icon: transactionTypeIcon,
						text: txn?.type ?? 'Transfer'
					}}
				/>
				<Stat
					title="Fraud Rules"
					stat={txn?.is_fraud ? (txn?.details?.length ?? 0) : 0}
					icon="shield"
				/>
			</div>
			<div className="grid gap-4 md:grid-cols-2">
				<Card>
					<CardHeader>
						<Label
							size="2xl"
							text="Transaction Information"
							className="font-semibold"
							icon="activity"
						/>
					</CardHeader>
					<CardContent className="grid grid-cols-2 gap-4">
						<Label className="font-mono" title="Transaction ID" text={txn?.txn_id ?? ''} />
						<Label
							size="lg"
							className={`font-semibold ${(txn?.amount ?? 0) < 0 ? 'text-destructive' : 'text-green-600'}`}
							title="Amount"
							text={formatCurrency(Math.abs(txn?.amount ?? 0))}
						/>
						<Label size="lg" title="Method" text={txn?.method ?? 'electronic'} />
						<Label
							size="lg"
							title="Type"
							icon={transactionTypeIcon.name}
							color={transactionTypeIcon.color}
							text={txn?.type || 'Transfer'}
						/>
						<Label
							size="lg"
							title="Status"
							icon={transactionStatusIcon.name}
							color={transactionStatusIcon.color}
							text={txn?.status ?? 'completed'}
						/>
						<Label
							title="Date & Time"
							icon="calendar"
							color="foreground"
							text={formatDateTime(txn?.timestamp ?? '')}
						/>
					</CardContent>
				</Card>
				<Card>
					<CardHeader>
						<Label size="2xl" text="Risk Assessment" className="font-semibold" icon="shield" />
					</CardHeader>
					<CardContent className="flex flex-col gap-4">
						<Label
							size="xl"
							title="Fraud Score"
							text={overallRisk.score?.toFixed(1) || '0.0'}
							className="font-semibold"
							badge={{
								variant: overallRisk.color as any,
								text: overallRisk.level
							}}
						/>
						<Label
							title="Fraud Status"
							icon={txn?.is_fraud ? 'alert-triangle' : 'check-circle'}
							color={txn?.is_fraud ? 'destructive' : 'green-600'}
							badge={{
								variant: txn?.is_fraud ? 'destructive' : 'default',
								text: txn?.is_fraud ? 'Flagged' : 'Clean'
							}}
						/>
					</CardContent>
				</Card>
			</div>
			{txn && src && dest && <TxnDetails txn={txn} src={src} dest={dest} />}
		</div>
	)
}
