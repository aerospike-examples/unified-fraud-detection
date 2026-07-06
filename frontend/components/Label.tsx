'use client'

import Icon, { type IconName } from "./Icon"
import { Badge, type BadgeProps } from "./ui/badge"
import { Color } from "./Stat"
import { Skeleton } from "./ui/skeleton"

export interface LabelProps {
    className?: string
    title?: string
    subtitle?: string
    icon?: IconName
    color?: Color
    text?: string
    badge?: Omit<BadgeProps, 'children'> & { text: string }
    size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl',
    loading?: boolean
    hasIcon?: boolean
    hasBadge?: boolean
}

const Label = ({
    className,
    title,
    subtitle,
    icon,
    color = 'foreground',
    text,
    badge,
    size = 'md',
    loading,
    hasIcon,
    hasBadge
}: LabelProps) => {
    const iconSize = size.includes('xl') ? '5' : '4';
    return (
        <div className="flex flex-col gap-1">
            {title && <p className="text-sm font-medium text-muted-foreground">{title}</p>}
            <div>
                <div className='flex items-center gap-2 min-w-0'>
                    {icon && <Icon className={`h-${iconSize} w-${iconSize} shrink-0 text-${color}`} icon={icon} />}
                    {text && <p className={`text-${size} capitalize min-w-0 truncate ${className ?? ''}`} title={text}>{text}</p>}
                    {badge && (
                        loading ? (
                            <Skeleton />
                        ) : (
                            <Badge variant={badge.variant} className={badge.className}>
                                <span className='text-nowrap'>{badge.text}</span>
                            </Badge>
                        )
                    )}
                </div>
                {subtitle && <p className={`text-sm font-medium text-muted-foreground min-w-0 truncate ${className ?? ''}`} title={subtitle}>{subtitle}</p>}
            </div>
        </div>
    )
}

export default Label