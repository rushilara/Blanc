import assert from "node:assert/strict";
import { test } from "node:test";
import { APP_ROUTES, HOSTED_NOTICE, hostedRedirect, isHosted } from "../src/lib/hosted";

test("a hosted deploy closes every app route and keeps the landing public", () => {
  for (const route of APP_ROUTES) {
    assert.equal(hostedRedirect(route, true), HOSTED_NOTICE, route);
    assert.equal(hostedRedirect(`${route}/`, true), HOSTED_NOTICE, route);
    assert.equal(hostedRedirect(`${route}/anything`, true), HOSTED_NOTICE, route);
  }
  for (const open of ["/", "/blanc/index.html", HOSTED_NOTICE, "/workspaces", "/lively"]) {
    assert.equal(hostedRedirect(open, true), null, open);
  }
});

test("a local run never redirects", () => {
  for (const route of [...APP_ROUTES, "/"]) assert.equal(hostedRedirect(route, false), null);
});

test("hosted mode is opt-in", () => {
  assert.equal(isHosted({}), false);
  assert.equal(isHosted({ BLANC_HOSTED: "0" }), false);
  assert.equal(isHosted({ BLANC_HOSTED: "" }), false);
  assert.equal(isHosted({ BLANC_HOSTED: "1" }), true);
  assert.equal(isHosted({ BLANC_HOSTED: "true" }), true);
});
