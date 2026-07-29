import { NextResponse } from 'next/server'
import { checkCredentials, expectedSessionToken, SESSION_COOKIE, SESSION_MAX_AGE } from '@/lib/auth'

export async function POST(request: Request) {
    let body: { username?: string; password?: string }
    try {
        body = await request.json()
    } catch {
        return NextResponse.json({ error: 'Invalid request body' }, { status: 400 })
    }

    const { username, password } = body
    if (!username || !password) {
        return NextResponse.json({ error: 'Username and password are required' }, { status: 400 })
    }

    if (!checkCredentials(username, password)) {
        return NextResponse.json({ error: 'Invalid username or password' }, { status: 401 })
    }

    const token = await expectedSessionToken()
    const response = NextResponse.json({ ok: true })
    // Browsers silently drop `Secure` cookies on plain-HTTP responses, which
    // would make login appear to "hang" (POST succeeds, cookie never sticks,
    // next navigation bounces straight back to /login). This demo is often
    // served over bare HTTP without a TLS-terminating proxy, so base the flag
    // on the actual request scheme rather than assuming NODE_ENV=production
    // means HTTPS.
    const isHttps =
        request.headers.get('x-forwarded-proto') === 'https' ||
        new URL(request.url).protocol === 'https:'
    response.cookies.set(SESSION_COOKIE, token as string, {
        httpOnly: true,
        sameSite: 'lax',
        secure: isHttps,
        path: '/',
        maxAge: SESSION_MAX_AGE,
    })
    return response
}
