'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import Toggle from './Toggle'
import { usePathname } from 'next/navigation'
import { Activity, LogOut } from 'lucide-react'
import { ThemeProvider } from 'next-themes'
import clsx from 'clsx'
import { Button } from '@/components/ui/button'

const navigation: { name: string; href: string; disabled?: boolean }[] = [
  // { name: 'Dashboard', href: '/' },
  { name: 'Users', href: '/users' },
  { name: 'Transactions', href: '/transactions' },
  { name: 'Flagged Accounts', href: '/flagged' },
  // { name: 'Fraud Patterns', href: '/fraud-patterns' },
  // { name: 'Graph View', href: '/graph' },
  { name: 'Admin', href: '/admin', disabled: true },
  { name: 'API Docs', href: '/docs' },
  { name: 'Zipkin', href: '/tracing', disabled: true },
]

export default function Navbar() {
  	const pathname = usePathname()
  	const router = useRouter()

  	// The login page renders its own centered layout — no nav chrome.
  	if (pathname === '/login') return null

  	async function handleLogout() {
  		await fetch('/api/auth/logout', { method: 'POST' })
  		router.push('/login')
  		router.refresh()
  	}

  	return (
    	<ThemeProvider attribute='data-theme' enableSystem>
      		<nav className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        		<div className="container mx-auto px-4">
          			<div className="flex h-16 items-center justify-between">
            			<div className="flex items-center space-x-4">
              				<Link href="/" className="flex items-center space-x-2">
                				<Activity className="h-6 w-6" />
                				<span className="font-bold">Fraud Detection</span>
              				</Link>
              				<div className="hidden md:flex space-x-4">
							{navigation.map((item) =>
								item.disabled ? (
									<span
										key={item.name}
										className="px-3 py-2 rounded-md text-sm font-medium text-muted-foreground/40 cursor-not-allowed select-none"
										aria-disabled="true"
									>
										{item.name}
									</span>
								) : (
									<Link
										key={item.name}
										href={item.href}
										className={clsx(
											'px-3 py-2 rounded-md text-sm font-medium transition-colors',
											pathname.startsWith(item.href)
												? 'bg-primary text-primary-foreground'
												: 'text-muted-foreground hover:text-foreground hover:bg-accent'
										)}
									>
										{item.name}
									</Link>
								)
							)}
							</div>
 			           	</div>
            			<div className="flex items-center space-x-2">
                			<Toggle />
                			<Button variant="ghost" size="icon" onClick={handleLogout} title="Log out">
                				<LogOut className="h-5 w-5" />
                				<span className="sr-only">Log out</span>
                			</Button>
            			</div>
          			</div>
        		</div>
      		</nav>
    	</ThemeProvider>
  	)
} 