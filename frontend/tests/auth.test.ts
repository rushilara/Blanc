import assert from "node:assert/strict";
import test from "node:test";
import {
  authConfig, checkCredentials, hashPassword, isProtected, parseUsers, safeNext, signInRedirect,
  signSession, verifyPassword, verifySession,
} from "../src/lib/auth";

const SECRET = "s".repeat(40);
const now = 1_900_000_000;

test("session tokens verify, and the format matches the backend", () => {
  // Same literal as backend/tests/test_auth.py, so both sides agree on the format.
  assert.equal(signSession("sarah", SECRET, 0, 2_000_000_000),
    "v1.c2FyYWg.2000000000.08edef82080061ad120b2c17ad05bf9a62c2e8feb1697eee8b8df7e693ea804d");
  const token = signSession("sarah", SECRET, now);
  assert.equal(verifySession(token, SECRET, now), "sarah");
  assert.equal(verifySession(token, "t".repeat(40), now), null);
  assert.equal(verifySession(token, SECRET, now + 12 * 3600), null, "expired");
  const [v, , exp, sig] = token.split(".");
  assert.equal(verifySession([v, Buffer.from("admin").toString("base64url"), exp, sig].join("."), SECRET, now), null);
  assert.equal(verifySession([v, "c2FyYWg", String(Number(exp) + 9999), sig].join("."), SECRET, now), null);
  for (const bad of [undefined, "", "v1.a.b", `${token}0`, token.toUpperCase()]) assert.equal(verifySession(bad, SECRET, now), null);
});

test("passwords are scrypt-hashed and checked", () => {
  const hash = hashPassword("correct horse");
  assert.match(hash, /^scrypt\$32768\$8\$1\$/);
  assert.ok(!hash.includes("correct horse"));
  assert.ok(verifyPassword("correct horse", hash));
  assert.ok(!verifyPassword("correct hors", hash));
  assert.ok(!verifyPassword("x", "plain-text"));
  assert.ok(!verifyPassword("x", "scrypt$2$8$1$AA$AA"), "weak parameters are refused");
  const users = parseUsers(JSON.stringify({ users: [{ username: "sarah", password_hash: hash }] }));
  assert.ok(checkCredentials(users, "sarah", "correct horse"));
  assert.ok(!checkCredentials(users, "sarah", "wrong"));
  assert.ok(!checkCredentials(users, "nobody", "correct horse"));
  assert.throws(() => parseUsers('{"users":[{"username":"a"}]}'));
});

test("auth is off without a secret and fails closed with a short one", () => {
  const env = (vars: Record<string, string>) => (name: string) => vars[name];
  assert.deepEqual(authConfig(env({}), "/u.json"), { enabled: false });
  assert.deepEqual(authConfig(env({ BLANC_AUTH_SECRET: "  " }), "/u.json"), { enabled: false });
  assert.ok("error" in authConfig(env({ BLANC_AUTH_SECRET: "short" }), "/u.json"));
  assert.deepEqual(authConfig(env({ BLANC_AUTH_SECRET: SECRET }), "/u.json"), { enabled: true, secret: SECRET, usersFile: "/u.json" });
  assert.equal((authConfig(env({ BLANC_AUTH_SECRET: SECRET, BLANC_USERS_FILE: "/x.json" }), "/u.json") as { usersFile: string }).usersFile, "/x.json");
});

test("only app routes are protected, and next= never leaves them", () => {
  for (const path of ["/workspace", "/workspace/", "/live/abc", "/sample"]) assert.ok(isProtected(path), path);
  for (const path of ["/", "/blanc/index.html", "/api/auth/login", "/workspacex"]) assert.ok(!isProtected(path), path);
  assert.equal(safeNext("/live/case-1?q=a"), "/live/case-1?q=a");
  for (const bad of ["https://evil.test/workspace", "//evil.test/workspace", "/\\evil.test", "/blanc/index.html", "", undefined, 7]) {
    assert.equal(safeNext(bad), "/workspace", String(bad));
  }
  assert.equal(signInRedirect("/live/x", "?a=1", "invalid"), "/blanc/index.html?next=%2Flive%2Fx%3Fa%3D1&error=invalid");
});
