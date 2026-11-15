import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, SIGN_IN_PAGE } from "@/lib/auth";

/** Clears the session cookie and returns to the sign-in page (BLA-60). */
function signOut(request: NextRequest) {
  const response = NextResponse.redirect(new URL(SIGN_IN_PAGE, request.url), 303);
  response.cookies.set(SESSION_COOKIE, "", { httpOnly: true, sameSite: "lax", path: "/", maxAge: 0 });
  return response;
}

export const POST = signOut;
export const GET = signOut;
