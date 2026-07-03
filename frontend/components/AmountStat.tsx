'use client'

import { formatCurrency } from '@/lib/utils'
import Stat, { type StatProps } from '@/components/Stat'

interface AmountStatProps extends Omit<StatProps, 'stat'> {
	amount?: number | null
}

/** Client component so currency symbol respects display locale (localStorage). */
export default function AmountStat({ amount, loading, ...rest }: AmountStatProps) {
	const stat = amount != null ? formatCurrency(amount) : (loading ? undefined : '—')
	return <Stat {...rest} stat={stat} loading={loading} />
}
