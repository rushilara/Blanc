import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import boardSchema from "../../../contracts/investigation_board.schema.json";
import { API_BASE_URL } from "./api";
import { ApiError, SIGNED_OUT } from "./live-api";

const ajv = new Ajv2020({ allErrors: true });
addFormats(ajv);
const validBoard = ajv.compile(boardSchema);

export type BoardItem = {
  item_id: string;
  kind: "claim" | "edge" | "node" | "event";
  source_analysis_id: string;
  ref_id: string;
  evidence_ids: string[];
  status?: string;
  /** Investigator review (BLA-48); absent means unreviewed. */
  review_status?: "unreviewed" | "reviewed" | "disputed";
  added_at: string;
};

export type InvestigationBoard = {
  schema_version: "1.0";
  board_id: string;
  case_id: string;
  title: string;
  revision: number;
  parent_revision?: number | null;
  active_analysis_id: string | null;
  items: BoardItem[];
  notes: unknown[];
  created_at: string;
  updated_at: string;
};

export type PinRequest = {
  kind: BoardItem["kind"];
  source_analysis_id: string;
  ref_id: string;
};

const path = (caseId: string, boardId?: string) =>
  boardId
    ? `/cases/${encodeURIComponent(caseId)}/boards/${encodeURIComponent(boardId)}`
    : `/cases/${encodeURIComponent(caseId)}/boards`;

function parseBoard(data: unknown, caseId: string): InvestigationBoard {
  if (!validBoard(data) || (data as InvestigationBoard).case_id !== caseId) {
    throw new ApiError("The board response is invalid or belongs to a different case.");
  }
  return data as InvestigationBoard;
}

/** Prefer a readable backend `detail`; fall back for known client-facing cases. */
export function boardErrorMessage(status: number, detail: unknown): string {
  if (status === 409) {
    return "The board changed in another tab. Reload and try again.";
  }
  const fromDetail = formatDetail(detail);
  if (fromDetail) return fromDetail;
  if (status === 422) {
    return "Notes must be removed before unpinning this entity.";
  }
  if (status === 401) return SIGNED_OUT;
  return `The board request failed (HTTP ${status}).`;
}

function formatDetail(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (Array.isArray(detail) && detail.length > 0 && detail.every((item) => typeof item === "string")) {
    return detail.join("; ");
  }
  return null;
}

async function readErrorDetail(response: Response): Promise<unknown> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return body?.detail;
  } catch {
    return undefined;
  }
}

async function boardRequest(
  url: string,
  init: { method: string; body?: unknown; signal?: AbortSignal },
): Promise<unknown> {
  const response = await fetch(`${API_BASE_URL}${url}`, {
    method: init.method,
    cache: "no-store",
    credentials: "include",
    signal: init.signal,
    ...(init.body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(init.body) }),
  });
  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(boardErrorMessage(response.status, detail), response.status);
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError("The backend returned invalid JSON.");
  }
}

export async function listBoards(caseId: string, signal?: AbortSignal): Promise<InvestigationBoard[]> {
  const data = await boardRequest(path(caseId), { method: "GET", signal });
  if (!Array.isArray(data)) throw new ApiError("The board list response is invalid.");
  return data.map((item) => parseBoard(item, caseId));
}

export async function getBoard(
  caseId: string,
  boardId: string,
  signal?: AbortSignal,
): Promise<InvestigationBoard> {
  return parseBoard(await boardRequest(path(caseId, boardId), { method: "GET", signal }), caseId);
}

export async function createBoard(
  caseId: string,
  title: string,
  activeAnalysisId?: string | null,
  signal?: AbortSignal,
): Promise<InvestigationBoard> {
  return parseBoard(
    await boardRequest(path(caseId), {
      method: "POST",
      body: { title, ...(activeAnalysisId === undefined ? {} : { active_analysis_id: activeAnalysisId }) },
      signal,
    }),
    caseId,
  );
}

export async function patchBoard(
  caseId: string,
  boardId: string,
  change: {
    expected_revision?: number;
    active_analysis_id?: string | null;
    pin?: PinRequest[];
    unpin?: string[];
  },
  signal?: AbortSignal,
): Promise<InvestigationBoard> {
  return parseBoard(
    await boardRequest(path(caseId, boardId), { method: "PATCH", body: change, signal }),
    caseId,
  );
}

/** Node ref_ids pinned on the board for a given analysis revision. */
export function pinnedNodeIds(board: InvestigationBoard, analysisId: string): string[] {
  return board.items
    .filter((item) => item.kind === "node" && item.source_analysis_id === analysisId)
    .map((item) => item.ref_id);
}

/** Node pins that belong to a different analysis revision than the one being viewed. */
export function otherVersionNodePinCount(board: InvestigationBoard, analysisId: string): number {
  return board.items.filter(
    (item) => item.kind === "node" && item.source_analysis_id !== analysisId,
  ).length;
}

/**
 * Read the newest board for the case without creating or retargeting it.
 * Opening/browsing versions must not PATCH `active_analysis_id` or create boards.
 */
export async function loadCaseBoard(
  caseId: string,
  signal?: AbortSignal,
): Promise<InvestigationBoard | null> {
  const boards = await listBoards(caseId, signal);
  return boards[0] ?? null;
}

async function patchNodePin(
  caseId: string,
  board: InvestigationBoard,
  analysisId: string,
  nodeId: string,
  pinned: boolean,
  signal?: AbortSignal,
): Promise<InvestigationBoard> {
  const existing = board.items.find(
    (item) =>
      item.kind === "node" && item.source_analysis_id === analysisId && item.ref_id === nodeId,
  );
  if (pinned) {
    if (existing) return board;
    return patchBoard(
      caseId,
      board.board_id,
      {
        expected_revision: board.revision,
        active_analysis_id: analysisId,
        pin: [{ kind: "node", source_analysis_id: analysisId, ref_id: nodeId }],
      },
      signal,
    );
  }
  if (!existing) return board;
  return patchBoard(
    caseId,
    board.board_id,
    { expected_revision: board.revision, active_analysis_id: analysisId, unpin: [existing.item_id] },
    signal,
  );
}

/**
 * Pin or unpin a node. On 409, re-fetch the board and retry the change once
 * before surfacing the conflict to the caller.
 */
export async function setNodePin(
  caseId: string,
  board: InvestigationBoard,
  analysisId: string,
  nodeId: string,
  pinned: boolean,
  signal?: AbortSignal,
): Promise<InvestigationBoard> {
  try {
    return await patchNodePin(caseId, board, analysisId, nodeId, pinned, signal);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409) throw error;
    const fresh = await getBoard(caseId, board.board_id, signal);
    return patchNodePin(caseId, fresh, analysisId, nodeId, pinned, signal);
  }
}
