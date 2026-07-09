import Results, { type Option } from '@/components/ResultTable'
import TransactionStats from '@/components/Transactions/Stats'

const options: Option[] = [
	{
		name: 'Transaction ID',
		item: 'id',
		width: '250px',
		label: {
			size: 'sm',
			text: 'txn_id',
			icon: 'credit-card'
		}
	},
	{
		name: 'Sender',
		item: 'sender',
		width: '100px',
		label: {
			size: 'sm',
			text: 'sender',
			className: 'font-mono'
		}
	},
	{
		name: 'Receiver',
		item: 'receiver',
		width: '100px',
		label: {
			size: 'sm',
			text: 'receiver',
			className: 'font-mono'
		}
	},
	{
		name: 'Amount',
		item: 'amount',
		width: '125px',
		type: 'currency',
		sortable: true,
		label: {
			text: 'amount'
		}
	},
	{
		name: 'Risk Score',
		item: 'fraud_score',
		width: '125px',
		type: 'risk',
		sortable: true,
		label: {
			badge: {
				text: 'fraud_score'
			}
		}
	},
	{
		name: 'Date',
		item: 'timestamp',
		type: 'datetime',
		width: '225px',
		label: {
			size: 'sm',
			text: 'timestamp',
			icon: 'calendar',
		},
		sortable: true,
		defaultSort: true,
		defaultOrder: 'desc'
	},
	{
		name: 'Location',
		item: 'location',
		width: '225px',
		label: {
			size: 'sm',
			text: 'location',
			icon: 'map-pin',
		},
	},
	{
		name: 'Status',
		item: 'fraud_status',
		width: '125px',
		type: 'fraud',
		label: {
			badge: {
				text: 'fraud_status'
			}
		}
	}
]

export default function TransactionsPage() { 
	return (
    	<div className="space-y-6 flex flex-col grow">
      		<div className="flex items-center justify-between">
        		<div>
          			<h1 className="text-3xl font-bold tracking-tight">Transaction Explorer</h1>
          			<p className="text-muted-foreground">Search and explore transaction details and patterns</p>
        		</div>
      		</div>
			<div className="grid gap-4 md:grid-cols-4">
				<TransactionStats />
			</div>
			<Results 
				apiUrl="/api/transactions"
				title="Transactions"
				options={options} />
		</div>
  	)
} 
