// Client for GET /cases/{case_id}/records/{record_id} (BLA-59 / BLA-40).

import type { EvidenceRecord } from "@/lib/analysis";
import { API_BASE_URL } from "@/lib/api";

export type RecordExcerpt = {
  text: string;
  char_start: number;
  char_end: number;
  chunk_id: string | null;
};

export type RecordRead = {
  record: EvidenceRecord;
  excerpt: RecordExcerpt | null;
};

function isRecord(value: unknown): value is EvidenceRecord {
  if (!value || typeof value !== "object") return false;
  const r = value as Record<string, unknown>;
  return (
    typeof r.record_id === "string" &&
    r.record_id.length > 0 &&
    typeof r.case_id === "string" &&
    r.case_id.length > 0 &&
    typeof r.source_file === "string" &&
    r.source_file.length > 0 &&
    typeof r.source_locator === "string" &&
    r.source_locator.length > 0 &&
    typeof r.content === "string"
  );
}

function isExcerpt(value: unknown): value is RecordExcerpt | null {
  if (value === null) return true;
  if (!value || typeof value !== "object") return false;
  const e = value as Record<string, unknown>;
  return (
    typeof e.text === "string" &&
    typeof e.char_start === "number" &&
    typeof e.char_end === "number" &&
    (e.chunk_id === null || typeof e.chunk_id === "string")
  );
}

export class RecordFetchError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "RecordFetchError";
    this.status = status;
  }
}

/** Fetch one case-scoped evidence record. Throws RecordFetchError on 404/network/shape errors. */
export async function fetchRecord(
  caseId: string,
  recordId: string,
  signal?: AbortSignal,
): Promise<RecordRead> {
  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/records/${encodeURIComponent(recordId)}`,
      { cache: "no-store", signal },
    );
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new RecordFetchError(
      "Could not reach the backend to load this source record.",
      null,
    );
  }

  if (response.status === 404) {
    throw new RecordFetchError("Record not found for this case.", 404);
  }
  if (!response.ok) {
    throw new RecordFetchError(
      `Failed to load source record: HTTP ${response.status}`,
      response.status,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new RecordFetchError("Source record response was not valid JSON.");
  }

  if (!body || typeof body !== "object") {
    throw new RecordFetchError("Source record response had an unexpected shape.");
  }
  const data = body as Record<string, unknown>;
  if (!isRecord(data.record) || !isExcerpt(data.excerpt)) {
    throw new RecordFetchError("Source record response failed shape validation.");
  }

  return { record: data.record, excerpt: data.excerpt };
}
