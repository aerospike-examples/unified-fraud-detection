import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { Button, type ButtonProps } from '@/components/ui/button'
import { ChevronLeft, ChevronRight, ChevronLast, ChevronFirst } from "lucide-react"

interface BtnProps extends ButtonProps {
    variant?: "outline" | "link" | "default" | "destructive" | "secondary" | "ghost" | null | undefined
    onClick: () => void
    disabled?: boolean
    contents: ReactNode | string
}

const BtnLayout = ({
    variant = 'outline',
    onClick,
    disabled,
    contents,
    ...rest
}: BtnProps) => (
    <Button
        {...rest}
        variant={variant}
        size="sm"
        onClick={onClick}
        disabled={disabled}
        className="w-8 h-8 p-0 transition"
    >
        {contents}
    </Button>
)


interface Props {
    title: string
    currentPage: number
    pageSize: number
    totalPages: number
    totalEntries: number
    handlePagination: (newPage: number) => void
    setPageSize: (newSize: number) => void
}

const Pagination = ({
    title,
    currentPage,
    totalPages,
    pageSize,
    totalEntries,
    handlePagination,
    setPageSize
}: Props) => {
    const safeTotalPages = Math.max(totalPages, 1)

    const handlePageSize = (size: number) => {
        setPageSize(size)
        if((((currentPage - 1) * size) + 1) > totalEntries) {
            handlePagination(1)
        }
    }

    return (
        <div className="flex items-center justify-between w-full mt-4">
            <div className="flex gap-6 items-center">
                <span className='text-sm text-muted-foreground'>Showing {((currentPage - 1) * pageSize) + 1} to {Math.min(currentPage * pageSize, totalEntries)} of {totalEntries.toLocaleString('en-US')} {title.toLowerCase()}</span>
                <div className='flex gap-2 items-center'>
                    {[10, 20, 30].map((size) => (
                        <BtnLayout 
                            key={size}
                            aria-label={`${size} per page`}
                            title={`${size} per page`}
                            variant={pageSize === size ? "default" : "outline"}
                            onClick={() => handlePageSize(size)}
                            contents={size} />
                    ))}
                    <span className='text-sm text-muted-foreground'>per page</span>
                </div>
            </div>
            <div className="flex items-center gap-2 items-center">
                <BtnLayout
                    aria-label='First'
                    title='First'
                    onClick={() => handlePagination(1)}
                    disabled={currentPage === 1}
                    contents={<ChevronFirst className="h-4 w-4" />} />
                <BtnLayout
                    aria-label='Previous'
                    title='Previous'
                    onClick={() => handlePagination(currentPage - 1)}
                    disabled={currentPage === 1}
                    contents={<ChevronLeft className="h-4 w-4" />} />
                <div className="flex items-center gap-1">
                    {Array.from({ length: Math.min(5, safeTotalPages) }, (_, i) => {
                        let pageNum
                        if(totalPages <= 5) pageNum = i + 1;
                        else if(currentPage <= 3) pageNum = i + 1;
                        else if(currentPage >= totalPages - 2) pageNum = totalPages - 4 + i;
                        else pageNum = currentPage - 2 + i;
                        
                        return (
                            <BtnLayout
                                key={pageNum}
                                aria-label={`Page ${pageNum}`}
                                title={`Page ${pageNum}`}
                                variant={currentPage === pageNum ? "default" : "outline"}
                                onClick={() => handlePagination(pageNum)}
                                contents={pageNum} />
                        )
                    })}
                </div>
                <BtnLayout
                    aria-label='Next'
                    title='Next'
                    onClick={() => handlePagination(currentPage + 1)}
                    disabled={currentPage === totalPages || totalPages === 0}
                    contents={<ChevronRight className="h-4 w-4" />} />
                <BtnLayout
                    aria-label='Last'
                    title='Last'
                    onClick={() => handlePagination(totalPages)}
                    disabled={currentPage === totalPages || totalPages === 0}
                    contents={<ChevronLast className="h-4 w-4" />} />
            </div>
        </div>
    )
}

export default Pagination