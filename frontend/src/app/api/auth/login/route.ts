import { NextResponse, type NextRequest } from "next/server";
import {
  SESSION_COOKIE, SESSION_TTL_SECONDS, checkCredentials, readUsers, safeNext, signInRedirect, signSession,
} from "@/lib/auth";
import { serverAuthConfig } from "@/lib/server-env";

// Failed sign-ins per username: after LOCK_AFTER failures in a row, refuse that name for
// LOCK_SECONDS. In memory, so it resets when the server restarts (local app, one process).
const LOCK_AFTER = 5;
const LOCK_SECONDS = 60;
const failures = new Map<string, { count: number; lockedUntil: number }>();

const field = (form: FormData, name: string) => {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
};

/** The landing form posts here (BLA-60). 303 sends the browser on with a GET. */
export async function POST(request: NextRequest) {
  const form = await request.formData().catch(() => new FormData());
  const next = safeNext(field(form, "next"));
  const to = (path: string) => NextResponse.redirect(new URL(path, request.url), 303);
  const back = (error: "invalid" | "config") => {
    const url = new URL(next, "http://blanc.invalid");
    return to(signInRedirect(url.pathname, url.search, error));
  };

  const auth = serverAuthConfig();
  // Auth off (no BLANC_AUTH_SECRET): the form keeps its old behaviour and opens the app.
  if (!auth.enabled) return to(next);
  if ("error" in auth) return back("config");

  const username = field(form, "username").trim();
  const password = field(form, "password");
  const now = Date.now() / 1000;
  const state = failures.get(username);
  if (state && state.lockedUntil > now) return back("invalid");

  let users;
  try {
    users = readUsers(auth.usersFile);
  } catch {
    return back("config");
  }
  if (!username || !password || password.length > 1024 || !checkCredentials(users, username, password)) {
    if (failures.size > 1000) failures.clear();
    const count = (state?.count ?? 0) + 1;
    failures.set(username, { count, lockedUntil: count >= LOCK_AFTER ? now + LOCK_SECONDS : 0 });
    return back("invalid");
  }
  failures.delete(username);

  const response = to(next);
  response.cookies.set(SESSION_COOKIE, signSession(username, auth.secret, now), {
    httpOnly: true,
    sameSite: "lax",
    secure: request.nextUrl.protocol === "https:",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  });
  return response;
}
