import Results, { type Option } from '@/components/ResultTable'
import UserStats from '@/components/Users/Stats'

const options: Option[] = [
	{
		name: "Name",
		item: 'name',
		width: "275px",
		sortable: true,
		defaultSort: true,
		label: {
			size: 'md',
			text: 'name',
			icon: 'user'
		}
	},
	{
		name: "ID",
		item: 'id',
		width: "140px",
		label: {
			subtitle: 'id',
			className: 'truncate font-mono text-xs normal-case'
		}
	},
	{
		name: "Email",
		item: 'email',
		width: "300px",
		label: {
			size: 'sm',
			text: 'email',
			icon: 'mail',
			className: 'lowercase truncate'
		}
	},
	{
		name: "Location",
		item: 'location',
		width: "175px",
		label: {
			size: 'sm',
			text: 'location',
			icon: 'map-pin'
		}
	},
	{
		name: "Age",
		item: 'age',
		width: "100px",
		label: {
			size: 'sm',
			text: 'age'
		}
	},
	{
		name: "Risk Score",
		item: 'risk_score',
		width: "150px",
		type: 'risk',
		sortable: true,
		label: {
			badge: {
				text: 'risk_score',
			}
		}
	},
	{
		name: "Signup Date",
		item: 'signup_date',
		type: 'date',
		width: "200px",
		sortable: true,
		label: {
			size: 'sm',
			text: 'signup_date',
			icon: 'calendar'
		}
	}
]

export default function UsersPage() {
  	return (
    	<div className="space-y-6 flex flex-col grow">
			<div>
				<h1 className="text-3xl font-bold tracking-tight">User Explorer</h1>
				<p className="text-muted-foreground">Browse and search user profiles with detailed information</p>
			</div>
			<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
				<UserStats />
			</div>
			<Results 
				apiUrl="/api/users"
				title='Users'
				options={options} />
    	</div>
  	)
}
