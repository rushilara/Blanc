import { NextResponse, type NextRequest } from "next/server";
import { hostedRedirect, isHosted } from "@/lib/hosted";

// BLA-61: on the public hosted deploy (BLANC_HOSTED=1) the app routes are closed until
// auth (BLA-60) is wired; the landing page stays public. Local runs are unaffected.
export function proxy(request: NextRequest) {
  const target = hostedRedirect(request.nextUrl.pathname, isHosted(process.env));
  return target ? NextResponse.redirect(new URL(target, request.url)) : NextResponse.next();
}

export const config = {
  matcher: ["/workspace/:path*", "/live/:path*", "/sample/:path*"],
};
