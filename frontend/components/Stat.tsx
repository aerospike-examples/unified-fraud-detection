import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from './ui/skeleton';
import Icon, { type IconName } from './Icon';
import Label from './Label';
export type Color = 'destructive' | 'warning' | 'foreground' | 'green-600' | 'blue-600' | 'yellow-600'

export interface StatProps {
    color?: Color
    stat?: string | number | null | {
        icon: {
            name: IconName
            color: Color
        }
        text: string
    }
    title: string
    subtitle?: string
    icon?: IconName
    loading?: boolean
    label?: boolean
}

const Stat = ({
    color = 'foreground',
    stat,
    title,
    subtitle, 
    icon,
    loading,
    label
}: StatProps) => {
    return (
        <Card>
            <CardHeader className='p-4 pb-1'>
                <CardTitle>
                    <p className="text-sm font-medium">{title}</p>
                </CardTitle>
            </CardHeader>
            <CardContent className='p-4 pt-1'>
                <div className={`flex items-center justify-between text-${color}`}>
                    <div>
                        {typeof stat === 'object' && stat !== null ? (
                            <Label icon={stat.icon.name} color={stat.icon.color} text={stat.text} size='lg' loading={loading} />
                        ) : (
                            loading ? (
                                <Skeleton className="h-[28px] w-[120px] rounded-2xl mb-[2px] mt-[2px]" />
                            ): (
                                <p className="text-2xl font-bold">{stat == null ? '—' : (typeof stat === 'number' ? stat.toLocaleString('en-US') : stat)}</p>
                            )
                        )}
                        {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
                    </div>
                    {icon && <Icon icon={icon} className={`h-8 w-8 text-muted-${color}`} />}
                </div>
            </CardContent>
        </Card>
    )
}

export default Stat