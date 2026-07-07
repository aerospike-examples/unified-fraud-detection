'use server'

import { formatDate } from '@/lib/utils';
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import Details, { type UserSummary } from '@/components/Users/Details';
import Label from '@/components/Label'

const API_BASE_URL = process.env.BASE_URL || "http://localhost:8080/api"

// Overall account disposition, mirroring the flagged-accounts workflow statuses
// (kept in sync with frontend/app/flagged/page.tsx's statusConfig).
const ACCOUNT_STATUS_CONFIG: Record<string, {
	label: string
	icon: 'check-circle' | 'alert-triangle' | 'shield' | 'clock' | 'x-circle'
	color: 'green-600' | 'destructive' | 'blue-600' | 'indigo-600' | 'cyan-600' | 'amber-600'
	badgeClassName: string
}> = {
	active: { label: 'Active', icon: 'check-circle', color: 'green-600', badgeClassName: 'bg-green-100 text-green-800' },
	pending_review: { label: 'Pending Review', icon: 'alert-triangle', color: 'amber-600', badgeClassName: 'bg-amber-100 text-amber-800' },
	under_investigation: { label: 'Under Investigation', icon: 'shield', color: 'blue-600', badgeClassName: 'bg-blue-100 text-blue-800' },
	monitoring: { label: 'Monitoring', icon: 'shield', color: 'indigo-600', badgeClassName: 'bg-indigo-100 text-indigo-800' },
	temporarily_frozen: { label: 'Temporarily Frozen', icon: 'clock', color: 'cyan-600', badgeClassName: 'bg-cyan-100 text-cyan-800' },
	confirmed_fraud: { label: 'Confirmed Fraud', icon: 'x-circle', color: 'destructive', badgeClassName: 'bg-red-100 text-red-800' },
	cleared: { label: 'Cleared', icon: 'check-circle', color: 'green-600', badgeClassName: 'bg-green-100 text-green-800' },
}

export default async function UserDetailPage({ params }: { params: Promise<{ id: string }>}) {
  	const { id: userId } = await params;
  	const response = await fetch(`${API_BASE_URL}/users/${userId}`, { cache: 'no-store' })

	if (!response.ok) {
		return (
			<div className="flex flex-col items-center justify-center min-h-[400px] space-y-3 text-center">
				<h1 className="text-2xl font-bold tracking-tight">User Not Found</h1>
				<p className="text-muted-foreground max-w-md">
					{userId?.startsWith('Account')
						? `"${userId}" is an account ID, not a user ID — this profile only exists for users.`
						: `No user found with ID "${userId}".`}
				</p>
			</div>
		)
	}

    const { user, risk_level, ...userDetails }: UserSummary = await response.json();
	
	const riskScore = user?.risk_score ?? 0;
	const accountStatus = ACCOUNT_STATUS_CONFIG[user?.account_status ?? 'active'] ?? ACCOUNT_STATUS_CONFIG.active;

  	return (
    	<div className="space-y-6">
			<div className="flex items-center justify-between">
				<div className="flex items-center gap-4">
					<div>
						<h1 className="text-3xl font-bold tracking-tight">{user?.name ?? 'Unknown User'}</h1>
						<p className="text-muted-foreground">User ID: {user?.id ?? userId}</p>
					</div>
				</div>
				<Badge variant={risk_level === 'LOW' ? 'default' : 'destructive'} className="text-lg px-4 py-2">
					{risk_level ?? 'LOW'} Risk ({riskScore.toFixed(1)})
				</Badge>
			</div>
			<div className="grid gap-4 md:grid-cols-4">

			</div>
            <div className="grid gap-4 md:grid-cols-2">
				<Card>
					<CardHeader>
						<Label
							size='2xl'
							className='font-semibold'
							icon="user"
							text='Personal Information' />
					</CardHeader>
					<CardContent className="grid grid-cols-2 gap-4">
						<Label
							size='lg'
							title='Full Name'
							text={user?.name ?? 'N/A'} />
						<Label
							size='lg'
							title='Age'
							text={`${user?.age ?? 0} years`} />
						<Label
							size='sm'
							title='Email'
							text={user?.email ?? 'N/A'}
							icon='mail' />
						<Label
							size='sm'
							title='Phone'
							text={user?.phone ?? 'N/A'}
							icon='phone' />
						<Label
							size='lg'
							title='Location'
							text={user?.location ?? 'N/A'}
							icon='map-pin' />
						<Label
							size='lg'
							title='Occupation'
							text={user?.occupation ?? 'N/A'}
							icon='building' />
					</CardContent>
				</Card>
				<Card>
					<CardHeader>
						<Label
							size='2xl'
							className='font-semibold'
							icon="shield"
							text='Risk Assessment' />
					</CardHeader>
					<CardContent className="grid grid-cols-1 gap-4">
						<Label
							size='xl'
							title='Risk Score'
							className='font-semibold'
							text={riskScore.toFixed(1)}
							badge={{
								variant: risk_level === 'LOW' ? 'default' : 'destructive',
								text: risk_level ?? 'LOW'
							}} />
						<Label
							size='sm'
							title='Signup Date'
							icon='calendar'
							text={formatDate(user?.signup_date ?? '')} />
						<Label
							title='Account Status'
							icon={accountStatus.icon}
							color={accountStatus.color}
							badge={{
								variant: 'outline',
								className: accountStatus.badgeClassName,
								text: accountStatus.label
							}} />
					</CardContent>
				</Card>
			</div>
			<Details userDetails={{user, risk_level, ...userDetails}} />
		</div>
  	)
} 