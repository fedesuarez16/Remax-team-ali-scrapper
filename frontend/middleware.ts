import { createServerClient } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'
import { ACCESS_COOKIE, hasValidAccess, isPublicPath } from '@/lib/access-gate'

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl
  if (!isPublicPath(pathname) && !(await hasValidAccess(request.cookies.get(ACCESS_COOKIE)?.value))) {
    const unlockUrl = new URL('/unlock', request.url)
    unlockUrl.searchParams.set('next', pathname + search)
    return NextResponse.redirect(unlockUrl)
  }

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
  const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!supabaseUrl || !supabaseKey) {
    return NextResponse.next({ request })
  }
  let response = NextResponse.next({ request })
  const supabase = createServerClient(
    supabaseUrl,
    supabaseKey,
    {
      cookies: {
        getAll() { return request.cookies.getAll() },
        setAll(toSet) {
          toSet.forEach(({ name, value }) => request.cookies.set(name, value))
          response = NextResponse.next({ request })
          toSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options))
        },
      },
    },
  )
  await supabase.auth.getUser() // refreshes the session cookie
  return response
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
