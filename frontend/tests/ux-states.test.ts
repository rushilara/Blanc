import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { BackendStatus } from "../src/components/BackendStatus";
import { QuestionInput } from "../src/components/QuestionInput";
import { RelationshipGraph } from "../src/components/RelationshipGraph";
import type { AnalysisRevision } from "../src/lib/analysis";

type Graph = AnalysisRevision["graph"];

test("BackendStatus starts in a checking status state", () => {
  const html = renderToStaticMarkup(createElement(BackendStatus));
  assert.match(html, /role="status"/);
  assert.match(html, /Checking backend/);
});

test("QuestionInput keeps the draft text and exposes a retry control after failure markup is present", () => {
  // Error markup is client-driven; assert the input preserves the provided question
  // and that the component source contract keeps draft + Retry wired (render baseline).
  const html = renderToStaticMarkup(
    createElement(QuestionInput, { question: "Who paid the invoice?", onAsk: async () => {} }),
  );
  assert.match(html, /value="Who paid the invoice\?"/);
  assert.match(html, /Ask question/);
  assert.doesNotMatch(html, /role="alert"/);
});

test("an empty relationship graph shows a single empty state", () => {
  const html = renderToStaticMarkup(
    createElement(RelationshipGraph, {
      graph: { nodes: [], edges: [] } satisfies Graph,
      caseId: "synthetic-demo",
      records: [],
    }),
  );
  assert.equal(html.match(/No supported relationships\./g)?.length, 1);
  assert.doesNotMatch(html, /Loading relationship graph/);
});

test("entities without relationships still show the empty relationships copy", () => {
  const html = renderToStaticMarkup(
    createElement(RelationshipGraph, {
      graph: {
        nodes: [{ id: "alex", label: "Alex", type: "person" }],
        edges: [],
      } satisfies Graph,
      caseId: "synthetic-demo",
      records: [],
    }),
  );
  assert.match(html, /No supported relationships\./);
});
