import "./dom-setup";

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";

import records from "../../fixtures/synthetic_records.json";
import { Findings } from "../src/app/workspace/Findings";
import { LiveWorkspace } from "../src/components/LiveWorkspace";
import { QuestionInput } from "../src/components/QuestionInput";
import { isRetryableStatus } from "../src/lib/retryable";

const originalFetch = globalThis.fetch;
let root: Root | null = null;
let container: HTMLDivElement | null = null;

afterEach(() => {
  globalThis.fetch = originalFetch;
  act(() => {
    root?.unmount();
  });
  root = null;
  container?.remove();
  container = null;
});

function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(node);
  });
  return container;
}

function click(el: Element | null | undefined) {
  assert.ok(el, "expected clickable element");
  act(() => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

function setTextarea(el: HTMLTextAreaElement, value: string) {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    setter?.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function casesPayload() {
  return [{ case_id: "case-a", title: "Case A", import_status: "ready" }];
}

function evidencePage() {
  const record = { ...records[0], case_id: "case-a" };
  return { items: [record], total: 1 };
}

function routeFetch(handler: (url: string, init?: RequestInit) => Promise<Response> | Response) {
  globalThis.fetch = async (input, init) => handler(String(input), init);
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

test("isRetryableStatus hides Retry for ordinary 4xx responses", () => {
  assert.equal(isRetryableStatus(undefined), true);
  assert.equal(isRetryableStatus(null), true);
  assert.equal(isRetryableStatus(503), true);
  assert.equal(isRetryableStatus(429), true);
  assert.equal(isRetryableStatus(404), false);
  assert.equal(isRetryableStatus(400), false);
});

test("QuestionInput exposes a Retry control after onAsk fails and Retry calls onAsk again", async () => {
  let calls = 0;
  const onAsk = async () => {
    calls += 1;
    throw new Error("network");
  };
  const el = mount(createElement(QuestionInput, { question: "Who paid the invoice?", onAsk }));
  const form = el.querySelector("form");
  assert.ok(form);
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
  });
  assert.match(el.textContent ?? "", /could not be submitted/);
  const retry = [...el.querySelectorAll("button")].find((b) => b.textContent === "Retry");
  assert.ok(retry);
  assert.equal(calls, 1);
  click(retry);
  await flush();
  assert.equal(calls, 2);
  assert.match(el.textContent ?? "", /Retry/);
});

test("LiveWorkspace Cancel clears the loading status", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  routeFetch(async (url) => {
    if (url.endsWith("/cases")) return Response.json(casesPayload());
    if (url.includes("/search")) {
      await gate;
      return Response.json({
        case_id: "case-a",
        question: "What happened?",
        records: [],
        dropped_by_limit: 0,
        dropped_by_budget: 0,
      });
    }
    return new Response("not found", { status: 404 });
  });

  const el = mount(createElement(LiveWorkspace));
  await flush();
  const textarea = el.querySelector("#live-question") as HTMLTextAreaElement;
  assert.ok(textarea);
  setTextarea(textarea, "What happened?");
  click([...el.querySelectorAll("button")].find((b) => b.getAttribute("value") === "search"));
  await flush();
  assert.match(el.textContent ?? "", /Retrieving source excerpts/);
  click([...el.querySelectorAll("button")].find((b) => b.textContent === "Cancel"));
  await flush();
  assert.doesNotMatch(el.textContent ?? "", /Retrieving source excerpts/);
  assert.equal((el.querySelector("#live-question") as HTMLTextAreaElement).disabled, false);
  release();
  await flush();
});

test("LiveWorkspace Retry repeats the same kind of request", async () => {
  const calls: string[] = [];
  routeFetch(async (url, init) => {
    if (url.endsWith("/cases")) return Response.json(casesPayload());
    if (url.includes("/search")) {
      calls.push("search");
      return new Response("{}", { status: 500 });
    }
    if (url.includes("/analyses") && init?.method === "POST") {
      calls.push("analysis");
      return new Response("{}", { status: 500 });
    }
    return new Response("not found", { status: 404 });
  });

  const el = mount(createElement(LiveWorkspace));
  await flush();
  setTextarea(el.querySelector("#live-question") as HTMLTextAreaElement, "What happened?");
  click([...el.querySelectorAll("button")].find((b) => b.getAttribute("value") === "analysis"));
  await flush();
  assert.match(el.textContent ?? "", /request failed|Could not reach the backend/i);
  assert.deepEqual(calls, ["analysis"]);
  const retry = [...el.querySelectorAll("button")].find((b) => b.textContent === "Retry");
  assert.ok(retry, "Retry should appear for 500");
  click(retry);
  await flush();
  assert.deepEqual(calls, ["analysis", "analysis"]);
});

test("Findings Cancel clears loading", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  routeFetch(async (url, init) => {
    if (url.endsWith("/cases")) return Response.json(casesPayload());
    if (url.includes("/records?") || /\/records$/.test(url.split("?")[0] ?? "")) {
      return Response.json(evidencePage());
    }
    if (url.includes("/analyses") && init?.method === "POST") {
      await gate;
      return new Response("{}", { status: 500 });
    }
    return new Response("not found", { status: 404 });
  });

  const el = mount(createElement(Findings));
  await flush();
  const textarea = el.querySelector('textarea[aria-label="Ask a question about a case"]') as HTMLTextAreaElement;
  assert.ok(textarea);
  setTextarea(textarea, "What happened?");
  click(el.querySelector('button[aria-label="Ask question"]'));
  await flush();
  assert.match(el.textContent ?? "", /Searching case records/);
  click([...el.querySelectorAll("button")].find((b) => b.textContent === "Cancel"));
  await flush();
  assert.doesNotMatch(el.textContent ?? "", /Searching case records/);
  assert.equal(textarea.disabled, false);
  release();
  await flush();
});

test("Findings Retry analysis repeats ask and surfaces askFailed", async () => {
  let analyzeCalls = 0;
  routeFetch(async (url, init) => {
    if (url.endsWith("/cases")) return Response.json(casesPayload());
    if (url.includes("/records")) return Response.json(evidencePage());
    if (url.includes("/analyses") && init?.method === "POST") {
      analyzeCalls += 1;
      return new Response("{}", { status: 500 });
    }
    return new Response("not found", { status: 404 });
  });

  const el = mount(createElement(Findings));
  await flush();
  const textarea = el.querySelector('textarea[aria-label="Ask a question about a case"]') as HTMLTextAreaElement;
  setTextarea(textarea, "What happened?");
  click(el.querySelector('button[aria-label="Ask question"]'));
  await flush();
  assert.equal(analyzeCalls, 1);
  assert.match(el.textContent ?? "", /request failed|Could not reach the backend/i);
  const retry = [...el.querySelectorAll("button")].find((b) => b.textContent === "Retry analysis");
  assert.ok(retry, "Retry analysis requires askFailed");
  click(retry);
  await flush();
  assert.equal(analyzeCalls, 2);
});

test("Findings maps non-ApiError case load failures to friendly copy", async () => {
  routeFetch(async () => {
    throw new TypeError("Failed to fetch");
  });
  const el = mount(createElement(Findings));
  await flush();
  assert.match(el.textContent ?? "", /Could not load cases\. Check the backend connection\./);
  assert.doesNotMatch(el.textContent ?? "", /Failed to fetch/);
});
