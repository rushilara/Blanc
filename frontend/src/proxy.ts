import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, signInRedirect, verifySession } from "@/lib/auth";
import { hostedRedirect, isHosted } from "@/lib/hosted";
import { serverAuthConfig } from "@/lib/server-env";

// BLA-61: on the public hosted deploy (BLANC_HOSTED=1) the app routes are closed; the
// landing page stays public. BLA-60: when BLANC_AUTH_SECRET is set, the app routes need
// a signed-in session (see src/lib/auth.ts); the backend API checks the same cookie.
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hosted = hostedRedirect(pathname, isHosted(process.env));
  if (hosted) return NextResponse.redirect(new URL(hosted, request.url));
  const auth = serverAuthConfig();
  if (!auth.enabled) return NextResponse.next();
  if ("error" in auth) return NextResponse.redirect(new URL(signInRedirect(pathname, search, "config"), request.url));
  const user = verifySession(request.cookies.get(SESSION_COOKIE)?.value, auth.secret, Date.now() / 1000);
  return user ? NextResponse.next() : NextResponse.redirect(new URL(signInRedirect(pathname, search), request.url));
}

export const config = {
  matcher: ["/workspace/:path*", "/live/:path*", "/sample/:path*"],
};
