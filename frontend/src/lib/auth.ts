// Sign-in for the local app (BLA-60). A server session, not a cloud store: users live in
// a local file of scrypt hashes, and a signed cookie proves the sign-in to both this
// Next server (the Proxy guards the app routes) and the backend API, which checks the
// same signature with the same BLANC_AUTH_SECRET. Case evidence never leaves the machine.
//
// Session token: "v1.<base64url username>.<expiry, unix seconds>.<hex HMAC-SHA256>", the
// HMAC taken over everything before the last dot. backend/app/auth.py verifies the same.
import { createHmac, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import { APP_ROUTES } from "./hosted";

export const SESSION_COOKIE = "blanc_session";
export const SESSION_TTL_SECONDS = 12 * 60 * 60;
export const MIN_SECRET_LENGTH = 32;
export const SIGN_IN_PAGE = "/blanc/index.html";

export type AuthConfig =
  | { enabled: false }
  | { enabled: true; secret: string; usersFile: string }
  | { enabled: true; error: string };

/**
 * Auth is on when BLANC_AUTH_SECRET is set. A secret that is too short is a
 * misconfiguration: the app routes stay closed (fail closed) rather than open.
 */
export function authConfig(get: (name: string) => string | undefined, defaultUsersFile: string): AuthConfig {
  const secret = get("BLANC_AUTH_SECRET")?.trim();
  if (!secret) return { enabled: false };
  if (secret.length < MIN_SECRET_LENGTH) {
    return { enabled: true, error: `BLANC_AUTH_SECRET must be at least ${MIN_SECRET_LENGTH} characters.` };
  }
  return { enabled: true, secret, usersFile: get("BLANC_USERS_FILE")?.trim() || defaultUsersFile };
}

const b64url = (s: string) => Buffer.from(s, "utf8").toString("base64url");
const sign = (body: string, secret: string) => createHmac("sha256", secret).update(body).digest("hex");

function equal(a: string, b: string): boolean {
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export function signSession(username: string, secret: string, nowSeconds: number, ttl = SESSION_TTL_SECONDS): string {
  const body = `v1.${b64url(username)}.${Math.floor(nowSeconds) + ttl}`;
  return `${body}.${sign(body, secret)}`;
}

/** The signed-in username, or null for a missing, forged, malformed or expired token. */
export function verifySession(token: string | undefined, secret: string, nowSeconds: number): string | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 4 || parts[0] !== "v1" || !/^\d{1,12}$/.test(parts[2]) || !/^[0-9a-f]{64}$/.test(parts[3])) return null;
  const body = parts.slice(0, 3).join(".");
  if (!equal(sign(body, secret), parts[3])) return null;
  if (Number(parts[2]) <= nowSeconds) return null;
  const username = Buffer.from(parts[1], "base64url").toString("utf8");
  return username ? username : null;
}

// scrypt with N=2^15, r=8, p=1 (~32 MiB, ~50-100 ms per check).
const SCRYPT = { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 };

export function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const hash = scryptSync(password.normalize("NFC"), salt, 32, SCRYPT);
  return `scrypt$${SCRYPT.N}$${SCRYPT.r}$${SCRYPT.p}$${salt.toString("base64url")}$${hash.toString("base64url")}`;
}

export function verifyPassword(password: string, stored: string): boolean {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const [N, r, p] = parts.slice(1, 4).map(Number);
  if (![N, r, p].every(Number.isSafeInteger) || N < 16384 || N > 1048576 || r < 1 || r > 32 || p < 1 || p > 16) return false;
  const expected = Buffer.from(parts[5], "base64url");
  if (expected.length !== 32) return false;
  const actual = scryptSync(password.normalize("NFC"), Buffer.from(parts[4], "base64url"), 32, { N, r, p, maxmem: 256 * N * r + 1024 * 1024 });
  return timingSafeEqual(actual, expected);
}

/** A hash no password matches, checked for unknown usernames so timing does not reveal them. */
let decoy: string | undefined;
const decoyHash = () => (decoy ??= hashPassword(randomBytes(16).toString("hex")));

export type UserEntry = { username: string; password_hash: string };

export function parseUsers(text: string): UserEntry[] {
  const data: unknown = JSON.parse(text);
  const users = (data as { users?: unknown })?.users;
  if (!Array.isArray(users) || !users.every(u => typeof u?.username === "string" && u.username && typeof u?.password_hash === "string")) {
    throw new Error("users file must be {\"users\": [{\"username\", \"password_hash\"}]}");
  }
  return users as UserEntry[];
}

export function readUsers(file: string): UserEntry[] {
  try {
    return parseUsers(readFileSync(file, "utf8"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
}

/** True only for a listed user with the right password. Always does one scrypt check. */
export function checkCredentials(users: UserEntry[], username: string, password: string): boolean {
  const user = users.find(u => u.username === username);
  const ok = verifyPassword(password, user?.password_hash ?? decoyHash());
  return Boolean(user) && ok;
}

/** True for paths the sign-in protects (the app routes that open case evidence). */
export function isProtected(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, "") || "/";
  return APP_ROUTES.some(route => path === route || path.startsWith(`${route}/`));
}

/** Where to go after signing in: a protected app path, never another origin. */
export function safeNext(next: unknown): string {
  if (typeof next !== "string" || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return "/workspace";
  try {
    const url = new URL(next, "http://blanc.invalid");
    if (url.origin !== "http://blanc.invalid" || !isProtected(url.pathname)) return "/workspace";
    return `${url.pathname}${url.search}`;
  } catch {
    return "/workspace";
  }
}

/** The sign-in page URL for a request that needs a session. */
export function signInRedirect(pathname: string, search: string, error?: "invalid" | "config"): string {
  const params = new URLSearchParams({ next: `${pathname}${search}` });
  if (error) params.set("error", error);
  return `${SIGN_IN_PAGE}?${params}`;
}
