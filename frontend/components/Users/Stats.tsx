'use client'

import Stat from "@/components/Stat"
import useSWR from 'swr'

interface UserStats {
    total_users: number
}

export default function UserStats(){
    const { data, isLoading } = useSWR<UserStats>('/api/users/stats')

    return (
        <Stat
            title='Total Users'
            subtitle='Total users in system'
            {...!isLoading && data ? { stat: data.total_users } : { loading: true }}
            icon='users' />
    )
}
