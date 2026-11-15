import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const signin = readFileSync(new URL("../public/blanc/index.html", import.meta.url), "utf8");

test("Archive sign-in keeps real POST authentication and return-path/error handling", () => {
  assert.match(signin, /<form id="signin" action="\/api\/auth\/login" method="post">/);
  assert.match(signin, /name="password" type="password" autocomplete="current-password" required/);
  assert.doesNotMatch(signin, /design preview|View the matching workspace|Separate sign-in preview/);
  const properties = new Map<string, string>();
  let submit: (() => void) | undefined;
  const status = { textContent: "" }, next = { value: "/workspace" };
  const objects: Record<string, unknown> = {
    ".logo": { firstElementChild: { offsetWidth: 40, getBoundingClientRect: () => ({ width: 40 }) }, offsetWidth: 230, style: { setProperty: (key: string, value: string) => properties.set(key, value) } },
    "#signin": { offsetHeight: 340, parentElement: {}, addEventListener: (_: string, listener: () => void) => { submit = listener; } },
    "#status": status, "#next": next, "#username": { value: " Investigator " },
  };
  const session = new Map<string, string>();
  runInNewContext(signin.match(/<script>([\s\S]*)<\/script>/)![1], {
    document: { querySelector: (selector: string) => objects[selector] },
    getComputedStyle: () => ({ rowGap: "48px" }),
    URLSearchParams, location: { search: "?next=%2Fworkspace%3Fcase%3Dexample&error=invalid" },
    sessionStorage: { setItem: (key: string, value: string) => session.set(key, value) },
  });
  assert.equal(properties.get("--opening-offset"), "194px");
  assert.equal(next.value, "/workspace?case=example");
  assert.match(status.textContent, /did not match/);
  assert.ok(submit);submit();
  assert.equal(session.get("blanc.username"), "Investigator");
});
