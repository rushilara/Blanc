import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import revisionSchema from "../../../contracts/analysis_revision.schema.json";
import recordSchema from "../../../contracts/evidence_record.schema.json";
import { API_BASE_URL } from "./api";
import type { AnalysisRevision, EvidenceRecord } from "./analysis";

const ajv = new Ajv2020({ allErrors: true });
addFormats(ajv);
const validRevision = ajv.compile<AnalysisRevision>(revisionSchema);
const validRecord = ajv.compile<EvidenceRecord>(recordSchema);

export class ApiError extends Error {
  constructor(message: string, public status?: number) { super(message); }
}

export type CaseSummary = { case_id: string; title: string; import_status: string };
export type SearchRecord = {
  record_id: string; source_file: string; source_locator: string;
  excerpts: { text: string; char_start: number; char_end: number; truncated?: boolean }[];
};
export type SearchResult = {
  case_id: string; question: string; records: SearchRecord[];
  dropped_by_limit: number; dropped_by_budget: number;
};
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string";
const nonnegative = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const path = (caseId: string) => `/cases/${encodeURIComponent(caseId)}`;

async function request(url: string, signal?: AbortSignal, body?: unknown): Promise<unknown> {
  const response = await fetch(`${API_BASE_URL}${url}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store", signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    // Do not display raw provider errors, which may contain credentials or internals.
    throw new ApiError(response.status === 503 ? "The analysis service is not ready. Please try again later." : `The request failed (HTTP ${response.status}).`, response.status);
  }
  try { return await response.json(); }
  catch { throw new ApiError("The backend returned invalid JSON."); }
}

export async function listCases(signal?: AbortSignal): Promise<CaseSummary[]> {
  const data = await request("/cases", signal);
  if (!Array.isArray(data) || !data.every(v => object(v) && text(v.case_id) && text(v.title) && text(v.import_status))) {
    throw new ApiError("The case list response is invalid.");
  }
  return data as CaseSummary[];
}

export async function searchCase(caseId: string, question: string, signal?: AbortSignal): Promise<SearchResult> {
  const data = await request(`${path(caseId)}/search`, signal, { question });
  if (!object(data) || data.case_id !== caseId || data.question !== question || !Array.isArray(data.records)
    || !nonnegative(data.dropped_by_limit) || !nonnegative(data.dropped_by_budget)
    || !data.records.every(r => object(r) && text(r.record_id) && text(r.source_file) && text(r.source_locator)
      && Array.isArray(r.excerpts) && r.excerpts.length > 0 && r.excerpts.every(e => object(e)
        && text(e.text) && nonnegative(e.char_start) && nonnegative(e.char_end) && e.char_end > e.char_start))) {
    throw new ApiError("The search response is invalid or belongs to a different case/question.");
  }
  return data as SearchResult;
}

export async function listCaseRecords(caseId: string, signal?: AbortSignal): Promise<EvidenceRecord[]> {
  const records: EvidenceRecord[] = [];
  while (true) {
    const page = await request(`${path(caseId)}/records?limit=100&offset=${records.length}`, signal);
    if (!object(page) || !nonnegative(page.total) || !Array.isArray(page.items)) {
      throw new ApiError("The case evidence list is invalid.");
    }
    for (const item of page.items) {
      if (!validRecord(item) || item.case_id !== caseId) {
        throw new ApiError("The evidence list contains an invalid record.");
      }
      records.push(item);
    }
    if (records.length >= page.total) return records;
    if (page.items.length === 0) throw new ApiError("The case evidence list is incomplete.");
  }
}

export async function getSource(caseId: string, recordId: string, signal?: AbortSignal): Promise<EvidenceRecord> {
  const data = await request(`${path(caseId)}/records/${encodeURIComponent(recordId)}`, signal);
  if (!object(data) || !validRecord(data.record) || data.record.case_id !== caseId || data.record.record_id !== recordId) {
    throw new ApiError("The source response is invalid or belongs to a different case/record.");
  }
  return data.record;
}

export function parseRevision(data: unknown, caseId: string, question: string): AnalysisRevision {
  if (!validRevision(data) || data.case_id !== caseId || data.question !== question) {
    throw new ApiError("The analysis response does not match the shared contract or selected case/question.");
  }
  const permitted = new Set(data.retrieved_record_ids.filter(id => !data.excluded_record_ids.includes(id)));
  const nodes = new Set(data.graph.nodes.map(node => node.id));
  const findings = [...data.claims, ...data.graph.edges, ...data.timeline_events];
  if (nodes.size !== data.graph.nodes.length
    || findings.some(f => !f.evidence_ids.every(id => permitted.has(id)))
    || data.graph.nodes.some(node => node.evidence_ids?.some(id => !permitted.has(id)))
    || data.graph.edges.some(edge => !nodes.has(edge.source) || !nodes.has(edge.target))) {
    throw new ApiError("The analysis contains invalid or excluded source references.");
  }
  return data;
}

export async function analyzeCase(caseId: string, question: string, signal?: AbortSignal): Promise<AnalysisRevision> {
  try {
    return parseRevision(await request(`${path(caseId)}/analyses`, signal, { question }), caseId, question);
  } catch (error) {
    if (error instanceof ApiError && [404, 405, 501].includes(error.status ?? 0)) {
      throw new ApiError("Answer generation is not available on this backend yet. You can search and inspect the evidence now.", error.status);
    }
    throw error;
  }
}
