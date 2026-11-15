import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import boardFixture from "../../fixtures/synthetic_board.json";
import {
  boardErrorMessage,
  loadCaseBoard,
  otherVersionNodePinCount,
  pinnedNodeIds,
  setNodePin,
  type InvestigationBoard,
} from "../src/lib/boards-api";
import { ApiError } from "../src/lib/live-api";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

function cloneBoard(overrides: Partial<InvestigationBoard> = {}): InvestigationBoard {
  return structuredClone({ ...boardFixture, ...overrides }) as InvestigationBoard;
}

function withNodePin(
  board: InvestigationBoard,
  refId: string,
  analysisId: string,
  itemId: string,
): InvestigationBoard {
  return {
    ...board,
    items: [
      ...board.items.filter(
        (item) =>
          !(item.kind === "node" && item.ref_id === refId && item.source_analysis_id === analysisId),
      ),
      {
        item_id: itemId,
        kind: "node",
        source_analysis_id: analysisId,
        ref_id: refId,
        evidence_ids: ["rec-01"],
        added_at: "2026-11-14T20:04:00Z",
      },
    ],
  };
}

test("pinnedNodeIds keeps only node pins for the requested analysis", () => {
  const board = withNodePin(cloneBoard(), "alex", "analysis-other", "item-alex-other");
  assert.deepEqual(pinnedNodeIds(board, "analysis-synthetic-1"), ["jordan"]);
  assert.equal(otherVersionNodePinCount(board, "analysis-synthetic-1"), 1);
  assert.deepEqual(pinnedNodeIds(board, "analysis-other"), ["alex"]);
  assert.deepEqual(pinnedNodeIds(board, "analysis-missing"), []);
});

test("loadCaseBoard reads the newest board and never creates or patches", async () => {
  const board = cloneBoard();
  const calls: string[] = [];
  globalThis.fetch = async (input, init) => {
    calls.push(`${init?.method ?? "GET"} ${String(input)}`);
    return Response.json([board]);
  };
  assert.equal((await loadCaseBoard(board.case_id))?.board_id, board.board_id);
  assert.deepEqual(calls, [`GET http://localhost:8000/cases/${board.case_id}/boards`]);
});

test("setNodePin unpins by the board response item_id", async () => {
  const board = cloneBoard({ revision: 3 });
  const jordanItem = board.items.find(
    (item) => item.kind === "node" && item.ref_id === "jordan" && item.source_analysis_id === "analysis-synthetic-1",
  );
  assert.ok(jordanItem);
  const updated = cloneBoard({
    revision: 4,
    items: board.items.filter((item) => item.item_id !== jordanItem.item_id),
  });
  globalThis.fetch = async (_input, init) => {
    assert.equal(init?.method, "PATCH");
    assert.deepEqual(JSON.parse(String(init?.body)), {
      expected_revision: 3,
      active_analysis_id: "analysis-synthetic-1",
      unpin: [jordanItem.item_id],
    });
    return Response.json(updated);
  };
  const next = await setNodePin(board.case_id, board, "analysis-synthetic-1", "jordan", false);
  assert.equal(next.revision, 4);
  assert.deepEqual(pinnedNodeIds(next, "analysis-synthetic-1"), []);
});

test("setNodePin re-fetches and retries once after a 409", async () => {
  const stale = cloneBoard({ revision: 2, items: [] });
  const fresh = cloneBoard({ revision: 3, items: [] });
  const pinned = withNodePin(cloneBoard({ revision: 4, items: [] }), "jordan", "analysis-synthetic-1", "item-jordan");
  let patches = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (init?.method === "PATCH") {
      patches += 1;
      const body = JSON.parse(String(init.body));
      if (patches === 1) {
        assert.equal(body.expected_revision, 2);
        return Response.json({ detail: "is at revision 3, not 2" }, { status: 409 });
      }
      assert.equal(body.expected_revision, 3);
      assert.deepEqual(body.pin, [
        { kind: "node", source_analysis_id: "analysis-synthetic-1", ref_id: "jordan" },
      ]);
      return Response.json(pinned);
    }
    assert.ok(url.endsWith(`/boards/${stale.board_id}`));
    return Response.json(fresh);
  };
  const next = await setNodePin(stale.case_id, stale, "analysis-synthetic-1", "jordan", true);
  assert.equal(patches, 2);
  assert.deepEqual(pinnedNodeIds(next, "analysis-synthetic-1"), ["jordan"]);
});

test("setNodePin surfaces backend 422 detail for notes-blocked unpin", async () => {
  const board = cloneBoard({ revision: 2 });
  globalThis.fetch = async () =>
    Response.json(
      {
        detail: [
          "cannot unpin item-5: notes are attached to it, and investigator notes are never removed",
        ],
      },
      { status: 422 },
    );
  await assert.rejects(
    () => setNodePin(board.case_id, board, "analysis-synthetic-1", "jordan", false),
    (error: unknown) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 422);
      assert.match(error.message, /notes are attached/);
      assert.doesNotMatch(error.message, /HTTP 422/);
      return true;
    },
  );
});

test("boardErrorMessage falls back when 422 has no detail", () => {
  assert.match(boardErrorMessage(422, undefined), /notes must be removed/i);
  assert.equal(boardErrorMessage(409, "ignored"), "The board changed in another tab. Reload and try again.");
});
