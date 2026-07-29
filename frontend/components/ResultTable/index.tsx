'use client'

import { useState } from 'react'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Eye } from 'lucide-react'
import Pagination from './Pagination'
import clsx from 'clsx'
import Search from './Search'
import { type LabelProps } from '../Label'
import { Skeleton } from '../ui/skeleton'
import TableData from './TableData'
import useSWR from 'swr'

export interface Option {
    name: string
    item: string
    width?: string 
    sortable?: boolean
    defaultSort?: boolean
    defaultOrder?: 'asc' | 'desc'
    className?: string
    type?: 'date' | 'datetime' | 'currency' | 'risk' | 'fraud'
    label: LabelProps
}

interface SearchResult {
    result: Record<string, any>[]
    total_pages: number
    total: number
}

interface Props {
    apiUrl: string
    title: string
    options: Option[]
}

const Results = ({ 
    apiUrl,
    title,
    options
}: Props) => {
    const pathname = usePathname();
    const [currentPage, setCurrentPage] = useState(1)
    const [pageSize, setPageSize] = useState(10)
    const [orderBy, setOrderBy] = useState<string>(options.filter(opt => opt.defaultSort)[0]?.item ?? options.find(opt => opt.sortable)?.item ?? "")
    const [order, setOrder] = useState<'asc' | 'desc'>(options.filter(opt => opt.defaultSort)[0]?.defaultOrder ?? 'asc')
    const [query, setQuery] = useState<string | undefined>(undefined)

    // Build the SWR cache key from all params — when any param changes, SWR auto-refetches
    const swrKey = `${apiUrl}?page=${currentPage}&page_size=${pageSize}&order_by=${orderBy}&order=${order}${query ? `&query=${query}` : ''}`

    const { data, isLoading, isValidating } = useSWR<SearchResult>(swrKey, {
        keepPreviousData: true, // Show old data while fetching new page
    })

    const results = data?.result ?? []
    const totalPages = data?.total_pages ?? 0
    const totalEntries = data?.total ?? 0
    const loading = isLoading

    const handleSort = (key: string) => {
        if(loading) return
        let o: 'asc' | 'desc' = 'asc';
        if(orderBy === key) o = order === 'asc' ? 'desc' : 'asc'        
        setOrderBy(key)
        setOrder(o)
    }

    const handlePageSize = (size: number) => {
        if(loading) return
        setPageSize(size)
    }

    const handlePagination = (page: number) => {
        if(loading) return
        setCurrentPage(page)
    }

    const handleSearch = (q?: string) => {
        setQuery(q || undefined)
        setCurrentPage(1)
    }

    return (
        <Card className='grow flex flex-col'>
            <CardHeader className='gap-4'>
                <CardTitle>{title}</CardTitle>
                <Search
                    fetchData={handleSearch}
                    placeholder={`Search ${title}`}
                    setCurrentPage={() => setCurrentPage(1)} />
            </CardHeader>
            <CardContent className='grow overflow-x-auto flex flex-col'>
                <table className="w-full grow table-fixed">
                    <thead>
                        <tr className="border-b">
                            {options.map(({name, item, sortable, width}) => (
                                <th 
                                    key={item}
                                    className={`text-left p-3 font-medium ${sortable ? "cursor-pointer hover:bg-muted/50" : ""}`}
                                    {...width ? { style: { width } } : {}}
                                    {...sortable ? { onClick: () => handleSort(item) } : {} }
                                >
                                    {name + (sortable ? (orderBy !== item ? '' : order === 'asc' ? ' ↑' : ' ↓') : "")}
                                </th>
                            ))}
                            <th className='max-w-[150px] min-w-[150px] w-[150px]'></th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            Array.from({ length: pageSize }).map((_, idx) => (
                                <tr key={idx} className="border-b hover:bg-muted/50">
                                    {Array.from({ length: options.length }).map((_, idx) => (
                                        <td className="p-3 h-[61px]" key={idx}>
                                            <Skeleton className="h-[20px] w-[90%] rounded-full" />
                                        </td> 
                                    ))}
                                    <td className="p-3">
                                        <Button variant="outline" size="sm">
                                            <Eye className="h-4 w-4 mr-1" />
                                            View Details
                                        </Button>
                                    </td>
                                </tr>
                            ))
                        ) : (
                            results.length > 0 ? (
                                results.map((result: Record<string, any>) => {
                                    // For transactions, construct URL with account_id/day/txn_id
                                    const detailUrl = pathname === '/transactions' && result.account_id && result.day
                                        ? `${pathname}/${encodeURIComponent(result.account_id)}/${encodeURIComponent(result.day)}/${encodeURIComponent(result.id)}`
                                        : pathname === '/transactions' && (result.account_id || result.sender)
                                        ? `${pathname}/${encodeURIComponent(result.account_id || result.sender)}/txn/${encodeURIComponent(result.id)}`
                                        : `${pathname}/${encodeURIComponent(result.id)}`;
                                    
                                    return (
                                    <tr 
                                        key={result.id} 
                                        className={clsx(
                                            "border-b hover:bg-muted/50", 
                                            (
                                                result?.fraud_status === 'review' || 
                                                result?.fraud_status === 'blocked'
                                            ) && "bg-red-50/30 dark:bg-red-950/10 border-l-2 border-l-red-200"
                                        )}
                                    >
                                        {options.map((opts, idx) => (
                                            <TableData {...opts} key={idx} result={result} />
                                        ))}
                                        <td className="p-3">
                                            <Link href={detailUrl}>
                                                <Button variant="outline" size="sm">
                                                    <Eye className="h-4 w-4 mr-1" />
                                                    View Details
                                                </Button>
                                            </Link>
                                        </td>
                                    </tr>
                                )})
                            ) : (
                                <tr className="w-full h-full">
                                    <td className="text-muted-foreground w-full h-full text-center" colSpan={options.length + 1} rowSpan={pageSize}>
                                        No {title} found
                                    </td>
                                </tr>
                            )
                        )}
                    </tbody>
                </table>
            </CardContent>
            <CardFooter>
                <Pagination
                    title={title}
                    currentPage={currentPage}
                    totalPages={totalPages}
                    pageSize={pageSize}
                    totalEntries={totalEntries}
                    setPageSize={handlePageSize}
                    handlePagination={handlePagination} />
            </CardFooter>
        </Card>
    )
}

export default Results;
