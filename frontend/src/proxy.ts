import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIES = ["kmr_session", "__Host-kmr_session"];

/**
 * Early redirect for visitors with no session cookie at all. This is a convenience, not the
 * security boundary: it cannot tell a valid cookie from a stale one. Every page and every API
 * call re-validates the session on the server.
 */
export function proxy(request: NextRequest) {
  const hasCookie = SESSION_COOKIES.some((name) => request.cookies.has(name));
  if (!hasCookie) {
    const url = new URL("/login", request.url);
    url.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/projects/:path*",
    "/scans/:path*",
    "/issues/:path*",
    "/repairs/:path*",
    "/approvals/:path*",
    "/history/:path*",
    "/settings/:path*",
  ],
};
