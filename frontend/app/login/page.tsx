'use client'

import { Suspense, useState, type FormEvent } from 'react'
import { useSearchParams } from 'next/navigation'
import { Activity, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export default function LoginPage() {
    return (
        <Suspense fallback={null}>
            <LoginForm />
        </Suspense>
    )
}

function LoginForm() {
    const searchParams = useSearchParams()
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState<string | null>(null)
    const [submitting, setSubmitting] = useState(false)

    async function handleSubmit(e: FormEvent) {
        e.preventDefault()
        setError(null)
        setSubmitting(true)
        try {
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password }),
            })
            if (!res.ok) {
                const body = await res.json().catch(() => ({}))
                setError(body.error || 'Invalid username or password')
                setSubmitting(false)
                return
            }
            // A full page navigation (rather than the client router) guarantees
            // the browser has committed the just-set session cookie before the
            // next request goes out — avoids a redirect-loop-looking hang if a
            // client-side transition races the Set-Cookie.
            const from = searchParams.get('from')
            window.location.href = from && from.startsWith('/') ? from : '/'
        } catch {
            setError('Something went wrong. Please try again.')
            setSubmitting(false)
        }
    }

    return (
        <div className="flex flex-1 items-center justify-center">
            <Card className="w-full max-w-sm">
                <CardHeader className="items-center text-center space-y-2">
                    <Activity className="h-8 w-8" />
                    <CardTitle>Fraud Detection Dashboard</CardTitle>
                    <CardDescription>Sign in to continue</CardDescription>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="username">Username</Label>
                            <Input
                                id="username"
                                name="username"
                                autoComplete="username"
                                autoFocus
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                disabled={submitting}
                                required
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="password">Password</Label>
                            <Input
                                id="password"
                                name="password"
                                type="password"
                                autoComplete="current-password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                disabled={submitting}
                                required
                            />
                        </div>
                        {error && <p className="text-sm text-destructive">{error}</p>}
                        <Button type="submit" className="w-full" disabled={submitting}>
                            {submitting ? (
                                <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    Signing in…
                                </>
                            ) : (
                                'Sign in'
                            )}
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    )
}
