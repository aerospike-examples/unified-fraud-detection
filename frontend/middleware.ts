import { NextRequest, NextResponse } from 'next/server'
import { SESSION_COOKIE, expectedSessionToken, isAuthConfigured } from '@/lib/auth'

// Excludes: Next internals/static assets, the login page itself, and the
// auth API routes (which must stay reachable to log in / out in the first
// place). Everything else — pages and /api/* backend-proxy routes alike —
// requires a valid session.
export const config = {
    matcher: ['/((?!_next/static|_next/image|favicon.ico|login|api/auth).*)'],
}

export async function middleware(request: NextRequest) {
    if (!isAuthConfigured()) {
        return NextResponse.next()
    }

    const token = request.cookies.get(SESSION_COOKIE)?.value
    const expected = await expectedSessionToken()
    if (token && expected && token === expected) {
        return NextResponse.next()
    }

    if (request.nextUrl.pathname.startsWith('/api/')) {
        return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
    }

    const loginUrl = new URL('/login', request.url)
    loginUrl.searchParams.set('from', request.nextUrl.pathname)
    return NextResponse.redirect(loginUrl)
}
