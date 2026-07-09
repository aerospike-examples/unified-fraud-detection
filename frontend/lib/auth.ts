/**
 * Minimal env-var-based auth for gating the demo UI.
 *
 * There's no user database — a single username/password pair is configured
 * via AUTH_USERNAME / AUTH_PASSWORD (server-only env vars, never NEXT_PUBLIC_).
 * A successful login sets an httpOnly cookie whose value is a hash of the
 * configured credentials, so it can't be guessed without knowing them, and
 * rotating the password invalidates every existing session.
 *
 * If either env var is unset, auth is disabled entirely (useful for local
 * dev) — see isAuthConfigured().
 *
 * Uses the Web Crypto API (`crypto.subtle`) so the same code works in both
 * the Edge middleware runtime and the Node route-handler runtime.
 */

export const SESSION_COOKIE = 'fraud_demo_session'
export const SESSION_MAX_AGE = 60 * 60 * 24 * 7 // 7 days

function getConfiguredCredentials(): { username: string; password: string } | null {
    const username = process.env.AUTH_USERNAME
    const password = process.env.AUTH_PASSWORD
    if (!username || !password) return null
    return { username, password }
}

export function isAuthConfigured(): boolean {
    return getConfiguredCredentials() !== null
}

async function sha256Hex(input: string): Promise<string> {
    const data = new TextEncoder().encode(input)
    const digest = await crypto.subtle.digest('SHA-256', data)
    return Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('')
}

/** The cookie value a valid session must carry, or null when auth is disabled. */
export async function expectedSessionToken(): Promise<string | null> {
    const creds = getConfiguredCredentials()
    if (!creds) return null
    return sha256Hex(`${creds.username}:${creds.password}`)
}

export function checkCredentials(username: string, password: string): boolean {
    const creds = getConfiguredCredentials()
    return Boolean(creds && username === creds.username && password === creds.password)
}
